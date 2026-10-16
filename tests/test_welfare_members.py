"""Local disposable fixtures only; no live PostgreSQL or Sub2API access."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from backend.models import Settings, WelfareSchedule
from backend.promotions import Promotions
from backend.store import Store

HTTP_CLIENT = httpx.AsyncClient
WELFARE = 10
UNRELATED = 11


def account(account_id, group_ids):
    return {'id': account_id, 'name': f'acct-{account_id}', 'platform': 'openai', 'type': 'oauth', 'status': 'active',
            'schedulable': True, 'priority': 1, 'concurrency': 1, 'group_ids': list(group_ids),
            'groups': [{'id': group_id, 'name': f'g{group_id}'} for group_id in group_ids]}


class WelfareMembersTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='flowpool-tests-')
        self.env = patch.dict(os.environ, {'FLOWPOOL_SUB2API_URL': 'https://sub2api.example.test',
                                          'FLOWPOOL_SUB2API_PG_DSN': 'postgresql://fixture.invalid/fixture'})
        self.env.start()
        self.store = Store(self.temp.name)
        self.store.save_settings(Settings(base_url='https://sub2api.example.test', admin_key='fixture-key'))
        self.groups = {WELFARE: {'id': WELFARE, 'name': '夜间福利分组', 'platform': 'openai', 'status': 'active'},
                       UNRELATED: {'id': UNRELATED, 'name': 'Pro号池', 'platform': 'openai', 'status': 'active'}}
        self.remote = {}
        self.calls = []
        self.listing = None
        self.put_failures = set()
        self.ignored_writes = set()
        self.promotions = Promotions(self.store)
        self.api_only = patch('backend.promotions.postgres_configured', return_value=False)
        self.api_only.start()
        self.client_patch = patch('httpx.AsyncClient', side_effect=lambda **kwargs: HTTP_CLIENT(
            **{**kwargs, 'transport': httpx.MockTransport(self.respond)}))
        self.client_patch.start()

    async def asyncTearDown(self):
        self.client_patch.stop()
        self.api_only.stop()
        self.env.stop()
        self.store.db.close()
        self.temp.cleanup()

    def add(self, account_id, group_ids):
        self.remote[account_id] = account(account_id, group_ids)

    def manage(self, account_id, **values):
        self.store.enroll(self.remote[account_id], 'third_party')
        if values:
            self.store.update(account_id, **values)

    def puts(self):
        return [(path, body) for method, path, body in self.calls if method == 'PUT']

    def respond(self, request):
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append((request.method, path, body))
        self.assertEqual(request.url.host, 'sub2api.example.test')
        if path == '/api/v1/admin/groups':
            items = [item for item in self.groups.values() if item['platform'] == request.url.params.get('platform')]
            return httpx.Response(200, json={'code': 0, 'data': {'items': items, 'pages': 1}})
        if path == '/api/v1/admin/accounts':
            items = list(self.remote.values()) if self.listing is None else self.listing
            return httpx.Response(200, json={'code': 0, 'data': {'items': items, 'pages': 1}})
        account_id = int(path.rsplit('/', 1)[1])
        if request.method == 'GET':
            return httpx.Response(200, json={'code': 0, 'data': self.remote[account_id]})
        if account_id in self.put_failures:
            return httpx.Response(500, json={'code': 500, 'message': 'fixture-secret upstream body'})
        if account_id not in self.ignored_writes:
            self.remote[account_id] = account(account_id, body['group_ids'])
        return httpx.Response(200, json={'code': 0, 'data': self.remote[account_id]})

    async def test_members_match_list_membership_for_welfare_group_only(self):
        self.add(101, [WELFARE, 20])
        self.add(102, [20])
        self.add(103, [WELFARE])
        self.remote[103].pop('groups')
        result = await self.promotions.group_members(WELFARE)
        self.assertEqual(result['group'], {'id': WELFARE, 'name': '夜间福利分组'})
        self.assertEqual([item['id'] for item in result['items']], [101, 103])

    async def test_removal_keeps_other_groups_and_logs_one_summary(self):
        self.add(101, [WELFARE, 20])
        self.add(102, [WELFARE])
        self.manage(101)
        result = await self.promotions.remove_group_members(WELFARE, owner_id=1)
        self.assertEqual((result['changed'], result['unchanged'], result['skipped'], result['failed']), (2, 0, 0, 0))
        self.assertEqual(self.remote[101]['group_ids'], [20])
        self.assertEqual(self.remote[102]['group_ids'], [])
        self.assertEqual(sorted(self.puts()), [('/api/v1/admin/accounts/101', {'group_ids': [20]}),
                                               ('/api/v1/admin/accounts/102', {'group_ids': []})])
        self.assertEqual(self.store.account(101)['remote']['group_ids'], [20])
        event = self.store.events(1)[0]
        self.assertEqual((event['action'], event['level']), ('welfare_group_members', 'info'))
        self.assertIn('更新 2 个', event['message'])

    async def test_stale_listing_reports_unchanged_without_write(self):
        self.add(101, [20])
        self.listing = [{**account(101, [WELFARE, 20]), 'groups': [{'id': WELFARE, 'name': '夜间福利分组'}]}]
        result = await self.promotions.remove_group_members(WELFARE, owner_id=1)
        self.assertEqual(result['results'], [{'id': 101, 'name': 'acct-101', 'status': 'unchanged', 'error': ''}])
        self.assertEqual(self.puts(), [])

    async def test_failed_write_is_isolated_to_that_account(self):
        self.add(101, [WELFARE, 20])
        self.add(102, [WELFARE])
        self.put_failures.add(102)
        result = await self.promotions.remove_group_members(WELFARE, owner_id=1)
        by_id = {item['id']: item for item in result['results']}
        self.assertEqual(by_id[101]['status'], 'changed')
        self.assertEqual(by_id[102]['status'], 'failed')
        self.assertTrue(by_id[102]['error'])
        self.assertNotIn('fixture-secret', by_id[102]['error'])
        self.assertEqual(self.remote[101]['group_ids'], [20])
        self.assertEqual(self.store.events(1)[0]['level'], 'warning')

    async def test_unconfirmed_write_is_reported_as_failed(self):
        self.add(101, [WELFARE, 20])
        self.ignored_writes.add(101)
        result = await self.promotions.remove_group_members(WELFARE, owner_id=1)
        self.assertEqual(result['results'][0]['status'], 'failed')
        self.assertIn('尚未确认', result['results'][0]['error'])

    async def test_switching_and_image_cooldown_accounts_are_skipped(self):
        self.add(101, [WELFARE])
        self.manage(101, state='pausing')
        self.add(102, [WELFARE])
        self.manage(102, state='cooldown', cooldown_mode='image')
        result = await self.promotions.remove_group_members(WELFARE, owner_id=1)
        self.assertEqual([item['status'] for item in sorted(result['results'], key=lambda item: item['id'])],
                         ['skipped', 'skipped'])
        self.assertEqual(self.puts(), [])
        self.assertEqual(self.remote[102]['group_ids'], [WELFARE])

    async def test_empty_welfare_group_logs_zero_counts(self):
        self.add(101, [20])
        result = await self.promotions.remove_group_members(WELFARE, owner_id=1)
        self.assertEqual((result['changed'], result['unchanged'], result['skipped'], result['failed']), (0, 0, 0, 0))
        self.assertEqual(result['results'], [])
        self.assertEqual(self.puts(), [])
        self.assertEqual(self.store.events(1)[0]['level'], 'info')

    async def test_non_welfare_group_is_rejected_before_any_write(self):
        self.add(101, [UNRELATED])
        with self.assertRaises(HTTPException) as members:
            await self.promotions.group_members(UNRELATED)
        self.assertEqual(members.exception.status_code, 409)
        with self.assertRaises(HTTPException) as removal:
            await self.promotions.remove_group_members(UNRELATED, owner_id=1)
        self.assertEqual(removal.exception.status_code, 409)
        self.assertEqual(self.puts(), [])

    def test_disabled_schedule_still_publishes_announcement_and_copy(self):
        self.store.save_welfare_schedule(WelfareSchedule(enabled=False, show_announcement=True))
        status = self.promotions.user_schedule_status()
        self.assertFalse(status['enabled'])
        self.assertTrue(status['show_announcement'])
        self.assertIsNone(status['active_now'])
        self.assertIsNone(status['next_transition_at'])
        self.assertTrue(status['countdown_copy']['zh']['opening'])
        self.assertTrue(status['countdown_copy']['en']['closing'])


if __name__ == '__main__':
    unittest.main()
