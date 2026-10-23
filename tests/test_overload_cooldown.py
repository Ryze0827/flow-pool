"""Offline overload threshold and protection scope checks."""
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from backend.models import Rule
from backend.scheduler import Scheduler
from backend.store import Store


class OverloadCooldownTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.remote = dict(id=1, name='fixture', platform='openai', status='active',
                           schedulable=True, groups=[{'id': 1, 'name': 'Pro号池'}])
        self.store.enroll(self.remote, 'risk')
        self.scheduler = Scheduler(self.store)
        self.client = Mock()
        self.client.account = AsyncMock(return_value=self.remote)
        self.client.set_schedulable = AsyncMock()
        self.rule = Rule(enabled=True, cooldown_requires_replacement=False)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    async def check(self, count):
        with patch('backend.scheduler.Analytics.account_snapshot', new=AsyncMock(return_value=self.remote)), \
             patch('backend.scheduler.Analytics.overload_error_count', new=AsyncMock(return_value=count)), \
             patch('backend.scheduler.Analytics.samples', new=AsyncMock(return_value=[])):
            await self.scheduler.check(self.client, self.store.account(1), self.rule)

    async def test_third_error_cools_without_latency_samples(self):
        await self.check(2)
        self.client.set_schedulable.assert_not_awaited()
        await self.check(3)
        self.client.set_schedulable.assert_awaited_once_with(1, False)
        self.assertEqual(self.store.account(1)['state'], 'cooldown')
        self.assertIn('过载错误 3 次', self.store.account(1)['reason'])

    async def test_disabled_controls_do_not_cool(self):
        for values in ({'enabled': False}, {'guarded_pools': []}, {'alert_only': True}):
            self.rule = Rule(**{'enabled': True, 'cooldown_requires_replacement': False, **values})
            await self.check(3)
            self.client.set_schedulable.assert_not_awaited()
        self.rule = Rule(enabled=True)
        self.store.set_guard(1, False)
        await self.check(3)
        self.client.set_schedulable.assert_not_awaited()

    async def test_unavailable_statistics_do_not_cool(self):
        await self.check(None)
        self.client.set_schedulable.assert_not_awaited()
