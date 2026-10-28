"""Offline refund contracts with disposable usage data and mocked upstream HTTP."""
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from backend import main
from backend.models import QuotaRefundPreview, Settings
from backend.promotions import Promotions
from backend.quota_refunds import QuotaRefunds
from backend.store import Store

HTTP_CLIENT = httpx.AsyncClient
START = '2026-01-01T08:00:00+08:00'
END = '2026-01-01T12:00:00+08:00'


class UsageDatabase:
    """Execute the production aggregation SQL with SQLite parameter adaptations."""
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, email TEXT, deleted_at REAL);
            CREATE TABLE usage_logs(user_id INTEGER, created_at REAL, billing_type INTEGER, actual_cost NUMERIC);
            INSERT INTO users VALUES (10, '用户A', 'a@example.test', NULL),
                (11, '用户B', 'b@example.test', NULL), (12, '无消费用户', 'c@example.test', NULL),
                (13, '已删除用户', 'd@example.test', 1);
        ''')

    def usage(self, user_id, at, cost, billing=0):
        self.db.execute('INSERT INTO usage_logs VALUES (?, ?, ?, ?)',
                        (user_id, datetime.fromisoformat(at).timestamp(), billing, cost))

    @asynccontextmanager
    async def snapshot(self):
        yield self

    async def execute(self, sql, params=()):
        sql = sql.replace('=ANY(%s)', ' IN (SELECT value FROM json_each(%s))')
        sql = sql.replace('ILIKE', 'LIKE').replace('id::text', 'CAST(id AS TEXT)').replace('%s', '?')
        params = tuple(value.timestamp() if isinstance(value, datetime) else json.dumps(value) if isinstance(value, list) else value for value in params)
        self.cursor = self.db.execute(sql, params)

    async def fetchall(self):
        return [dict(row) for row in self.cursor.fetchall()]


class QuotaRefundTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='flowpool-refunds-')
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'FLOWPOOL_SUB2API_URL': 'https://refund.example.test'})
        env.start()
        self.addCleanup(env.stop)
        self.store = Store(self.temp.name)
        self.addCleanup(self.store.db.close)
        self.store.save_settings(Settings(base_url='https://refund.example.test', admin_key='fixture-key'))
        self.promotions = Promotions(self.store)
        self.refunds = self.promotions.refunds
        self.usage = UsageDatabase()
        self.addCleanup(self.usage.db.close)
        self.usage.usage(10, START, 7)
        self.usage.usage(10, '2026-01-01T11:59:59+08:00', 14)
        self.usage.usage(10, END, 100)
        self.usage.usage(10, '2026-01-01T07:59:59+08:00', 200)
        self.usage.usage(10, START, 300, billing=1)
        self.usage.usage(10, START, 0)
        self.usage.usage(11, START, 5)
        self.usage.usage(13, START, 500)
        self.calls = []
        self.failures = {}
        self.auth = Mock(session=AsyncMock(return_value={'id': 1, 'role': 'admin'}))
        for patcher in (
            patch('backend.quota_refunds.Sub2Postgres', return_value=self.usage),
            patch('backend.sub2api.httpx.AsyncClient', side_effect=lambda **kwargs: HTTP_CLIENT(**{**kwargs, 'transport': httpx.MockTransport(self.respond)})),
            patch.multiple(main, promotions=self.promotions, store=self.store, upstream_auth=self.auth, DATA=Path(self.temp.name)),
            patch.object(main.app, 'middleware_stack', None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = HTTP_CLIENT(transport=httpx.ASGITransport(app=main.app), base_url='https://testserver', headers={'X-Scheduler-Request': '1'})
        self.addAsyncCleanup(self.client.aclose)

    def respond(self, request):
        self.assertEqual(request.url.host, 'refund.example.test')
        self.assertEqual(request.method, 'POST')
        user_id = int(request.url.path.split('/')[-2])
        self.calls.append((user_id, json.loads(request.content), request.headers['Idempotency-Key']))
        failure = self.failures.get(user_id)
        if failure == 'timeout':
            raise httpx.ReadTimeout('fixture', request=request)
        return httpx.Response(failure or 200, json={'code': failure or 0, 'data': {'id': user_id}})

    async def preview(self, users=None, start=START, end=END):
        return await self.refunds.preview(QuotaRefundPreview(user_ids=users or [], start_at=start, end_at=end), 1)

    async def test_example_and_time_boundaries_use_actual_balance_consumption(self):
        result = await self.preview([10])
        self.assertEqual(result['total_amount'], '21')
        self.assertEqual(result['items'][0]['requests'], 2)
        self.assertEqual(self.calls, [])
        result = await self.refunds.execute(result['id'], 1)
        self.assertEqual(result['credited_amount'], '21')
        self.assertEqual(self.calls[0][0], 10)
        self.assertEqual(self.calls[0][1]['balance'], 21)
        self.assertEqual(self.calls[0][1]['operation'], 'add')
        self.assertIn(result['id'], self.calls[0][1]['notes'])

    async def test_empty_selection_includes_all_live_users_with_consumption(self):
        result = await self.preview()
        self.assertEqual([item['user_id'] for item in result['items']], [10, 11])
        self.assertEqual(result['total_amount'], '26')
        result = await self.refunds.execute(result['id'], 1)
        self.assertEqual(result['counts']['credited'], 2)

    async def test_selected_users_deduplicate_and_unknown_user_rejected(self):
        result = await self.preview([11, 11])
        self.assertEqual(result['user_ids'], [11])
        self.assertEqual(result['total_amount'], '5')
        for user_id in (13, 999):
            with self.assertRaises(HTTPException) as raised:
                await self.preview([user_id])
            self.assertEqual(raised.exception.status_code, 409)

    async def test_no_consumption_is_empty_and_never_posts(self):
        result = await self.preview([12])
        self.assertEqual(result['items'], [])
        await self.refunds.execute(result['id'], 1)
        self.assertEqual(self.calls, [])

    async def test_repeat_execution_and_restart_do_not_credit_again(self):
        result = await self.preview([10])
        await self.refunds.execute(result['id'], 1)
        service = QuotaRefunds(self.store)
        result = await service.execute(result['id'], 1)
        self.assertEqual(result['counts']['credited'], 1)
        self.assertEqual(len(self.calls), 1)

    async def test_overlapping_preview_skips_credited_user_but_adds_other_users(self):
        first = await self.preview([10])
        second = await self.preview()
        await self.refunds.execute(first['id'], 1)
        result = await self.refunds.execute(second['id'], 1)
        self.assertEqual(result['counts']['skipped'], 1)
        self.assertEqual(result['counts']['credited'], 1)
        self.assertEqual([call[0] for call in self.calls], [10, 11])

    async def test_adjacent_period_can_be_refunded(self):
        first = await self.preview([10])
        await self.refunds.execute(first['id'], 1)
        second = await self.preview([10], start=END, end='2026-01-01T13:00:00+08:00')
        result = await self.refunds.execute(second['id'], 1)
        self.assertEqual(result['credited_amount'], '100')

    async def test_partial_failure_only_retries_failed_item(self):
        result = await self.preview()
        self.failures[11] = 429
        result = await self.refunds.execute(result['id'], 1)
        self.assertEqual((result['counts']['credited'], result['counts']['failed']), (1, 1))
        self.failures.clear()
        result = await self.refunds.execute(result['id'], 1)
        self.assertEqual(result['counts']['credited'], 2)
        self.assertEqual([call[0] for call in self.calls], [10, 11, 11])
        self.assertEqual(self.calls[1][2], self.calls[2][2])

    async def test_timeout_and_server_error_are_not_automatically_retried(self):
        self.failures = {10: 'timeout', 11: 500}
        result = await self.preview()
        result = await self.refunds.execute(result['id'], 1)
        self.assertEqual(result['counts']['uncertain'], 2)
        self.failures.clear()
        await QuotaRefunds(self.store).execute(result['id'], 1)
        second = await self.preview()
        result = await self.refunds.execute(second['id'], 1)
        self.assertEqual(result['counts']['skipped'], 2)
        self.assertEqual(len(self.calls), 2)

    async def test_busy_execution_rejected(self):
        result = await self.preview()
        async with self.refunds.lock:
            with self.assertRaises(HTTPException) as raised:
                await self.refunds.execute(result['id'], 1)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.calls, [])

    async def test_source_change_blocks_old_batch(self):
        result = await self.preview()
        self.store.save_settings(Settings(base_url='https://refund.example.test', admin_key='changed-key'))
        with self.assertRaises(HTTPException) as raised:
            await self.refunds.execute(result['id'], 1)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.calls, [])

    async def test_http_contract_defaults_history_and_auth(self):
        response = await self.client.post('/api/promotions/quota-refunds/preview', json={'start_at': START, 'end_at': END})
        self.assertEqual(response.status_code, 200, response.text)
        batch_id = response.json()['id']
        response = await self.client.post(f'/api/promotions/quota-refunds/{batch_id}/execute')
        self.assertEqual(response.json()['credited_amount'], '26')
        response = await self.client.get('/api/promotions/quota-refunds')
        self.assertEqual(response.json()['items'][0]['id'], batch_id)
        self.assertNotIn('source', response.text)
        self.assertNotIn('fixture-key', response.text)
        response = await self.client.get('/api/promotions/quota-refunds/users', params={'search': '用户A'})
        self.assertEqual([item['id'] for item in response.json()['items']], [10])
        response = await self.client.post('/api/promotions/quota-refunds/preview', json={'start_at': END, 'end_at': START})
        self.assertEqual(response.status_code, 422)
        self.auth.session.return_value = None
        for method, path in [('GET', ''), ('GET', '/users'), ('POST', '/preview'), ('GET', f'/{batch_id}'), ('POST', f'/{batch_id}/execute')]:
            response = await self.client.request(method, '/api/promotions/quota-refunds' + path)
            self.assertEqual(response.status_code, 401)

    async def test_invalid_periods_and_ids(self):
        for values in ({'start_at': END, 'end_at': START}, {'end_at': START},
                       {'start_at': '2026-01-01T08:00:00'}, {'end_at': '2999-01-01T00:00:00Z'},
                       {'user_ids': [-1]}, {'user_ids': [True]}):
            with self.assertRaises(ValidationError):
                QuotaRefundPreview(**{'start_at': START, 'end_at': END, **values})
        result = await self.preview([10])
        # Storage keeps decimal strings rather than rounding the refund to cents.
        self.store.db.execute('UPDATE quota_refund_items SET amount=? WHERE batch_id=?', ('0.1234567890', result['id']))
        self.store.db.commit()
        result = await self.refunds.execute(result['id'], 1)
        self.assertEqual(Decimal(result['credited_amount']), Decimal('0.1234567890'))
        self.assertAlmostEqual(self.calls[0][1]['balance'], 0.1234567890, places=10)


if __name__ == '__main__':
    unittest.main()
