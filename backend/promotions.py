"""Manage promotional GPT groups through the Sub2API admin API."""
import asyncio
import time
from datetime import datetime, time as clock_time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from . import updater
from .postgres import Sub2Postgres, postgres_configured
from .sub2api import Sub2API, UpstreamError

WELFARE_GROUPS_SQL = "SELECT id, name, status FROM groups WHERE deleted_at IS NULL AND platform='openai' AND name LIKE '%福利%' ORDER BY id"
SHANGHAI = ZoneInfo('Asia/Shanghai')
INTERVAL_SECONDS = 60


class Promotions:
    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()
        self.last_check_at = None
        self.next_check_at = None
        self.error = ''
        self.error_logged = ''

    @staticmethod
    def scheduled_enabled(schedule, now=None):
        now = now or datetime.now(SHANGHAI)
        current = now.hour
        start = schedule.start_hour
        end = schedule.end_hour
        return start <= current < end if start < end else current >= start or current < end

    @staticmethod
    def next_transition(schedule, now=None):
        now = now or datetime.now(SHANGHAI)
        candidates = []
        for day in range(3):
            date = now.date() + timedelta(days=day)
            for hour in (schedule.start_hour, schedule.end_hour):
                candidate = datetime.combine(date, clock_time(hour), SHANGHAI)
                if candidate > now:
                    candidates.append(candidate)
        return min(candidates).isoformat() if candidates else None

    def schedule_status(self):
        schedule = self.store.welfare_schedule()
        active_now = self.scheduled_enabled(schedule)
        return {**schedule.model_dump(), 'active_now': active_now if schedule.enabled else None,
                'next_transition_at': self.next_transition(schedule) if schedule.enabled else None,
                'last_check_at': self.last_check_at, 'next_check_at': self.next_check_at,
                'error': self.error, 'interval_seconds': INTERVAL_SECONDS}

    def user_schedule_status(self):
        schedule = self.store.welfare_schedule()
        now = datetime.now(SHANGHAI)
        if not schedule.enabled:
            return {'enabled': False}
        opening = datetime.combine(now.date(), clock_time(schedule.start_hour), SHANGHAI)
        if opening <= now:
            opening += timedelta(days=1)
        return {**schedule.model_dump(), 'active_now': self.scheduled_enabled(schedule, now),
                'server_now': now.isoformat(), 'next_open_at': opening.isoformat(),
                'next_transition_at': self.next_transition(schedule, now),
                'countdown_copy': {
                    'zh': {'opening': schedule.countdown_copy.zh_opening, 'closing': schedule.countdown_copy.zh_closing},
                    'en': {'opening': schedule.countdown_copy.en_opening, 'closing': schedule.countdown_copy.en_closing},
                }}

    async def welfare_groups(self):
        if postgres_configured(self.store):
            async with Sub2Postgres(self.store).snapshot() as cursor:
                await cursor.execute(WELFARE_GROUPS_SQL)
                groups = await cursor.fetchall()
        else:
            async with Sub2API(self.store.settings()) as client:
                groups = [group for group in await client.all('groups', platform='openai')
                          if group.get('platform') == 'openai' and not group.get('deleted_at')
                          and '福利' in (group.get('name') or '')]
        return [{'id': group['id'], 'name': group['name'], 'status': group.get('status', '')} for group in groups]

    async def groups(self):
        return {'items': await self.welfare_groups(), 'schedule': self.schedule_status()}

    async def save_schedule(self, value, owner_id):
        async with self.lock:
            self.store.save_welfare_schedule(value)
            result = await self.reconcile_locked()
        state = '已开启' if value.enabled else '已关闭'
        self.store.event('welfare_schedule', f"管理员 #{owner_id} {state}夜间福利分组定时控制：{value.start_hour:02d}:00–{value.end_hour:02d}:00（Asia/Shanghai）")
        return {**self.schedule_status(), 'sync': result}

    async def set_welfare_group_status(self, group_id, enabled, owner_id):
        if self.lock.locked():
            raise HTTPException(409, '正在更新福利分组，请稍后重试')
        async with self.lock:
            if self.store.welfare_schedule().enabled:
                raise HTTPException(409, '定时控制已开启，请先关闭定时控制再手动修改分组状态')
            current = {group['id']: group for group in await self.welfare_groups()}
            if group_id not in current:
                raise HTTPException(409, '该分组已删除、改名或不属于 GPT / OpenAI 平台，请刷新后重试')
            result = await self.update(group_id, current[group_id]['name'], enabled)
        if result['verified']:
            action = '启用' if enabled else '停用'
            self.store.event('welfare_group', f"管理员 #{owner_id} 已{action}夜间福利分组 {result['name']}（#{group_id}）")
        return result

    async def update(self, group_id, name, enabled):
        async with Sub2API(self.store.settings()) as client:
            return await self.update_with_client(client, group_id, name, enabled)

    async def update_with_client(self, client, group_id, name, enabled):
        status = 'active' if enabled else 'inactive'
        error = ''
        try:
            await client.request('PUT', f'groups/{group_id}', json={'status': status})
        except UpstreamError as cause:
            error = cause.message + ('；结果尚未确认，请刷新后重试' if cause.uncertain else '')
        try:
            body = await client.request('GET', f'groups/{group_id}')
        except UpstreamError as cause:
            return {'id': group_id, 'name': name, 'status': '', 'verified': False,
                    'error': error or f'{cause.message}；无法复核写入结果，请刷新后重试'}
        stored = body.get('status', '') if isinstance(body, dict) else ''
        verified = stored == status
        if verified:
            error = ''
        elif not error:
            error = '写入后复核不一致，分组可能已被其他管理员修改'
        return {'id': group_id, 'name': body.get('name', name) if isinstance(body, dict) else name,
                'status': stored, 'verified': verified, 'error': error}

    async def reconcile_locked(self):
        schedule = self.store.welfare_schedule()
        if not schedule.enabled:
            self.error = ''
            self.error_logged = ''
            self.last_check_at = time.time()
            return {'enabled': None, 'changed': 0, 'failed': 0, 'results': []}
        enabled = self.scheduled_enabled(schedule)
        try:
            groups = await self.welfare_groups()
            target = 'active' if enabled else 'inactive'
            pending = [group for group in groups if group['status'] != target]
            results = []
            if pending:
                async with Sub2API(self.store.settings()) as client:
                    for offset in range(0, len(pending), 4):
                        results.extend(await asyncio.gather(*(
                            self.update_with_client(client, group['id'], group['name'], enabled)
                            for group in pending[offset:offset + 4])))
            failed = sum(not result['verified'] for result in results)
            previous_error = self.error_logged
            self.error = '' if not failed else f'{failed} 个福利分组状态未确认，将在下轮自动重试'
            if results and (len(results) > failed or self.error != previous_error):
                action = '启用' if enabled else '停用'
                self.store.event('welfare_schedule', f"定时任务自动{action}福利分组：确认成功 {len(results) - failed} 个，未确认 {failed} 个",
                                 level='warning' if failed else 'info')
            self.error_logged = self.error
            return {'enabled': enabled, 'changed': len(results) - failed, 'failed': failed, 'results': results}
        except asyncio.CancelledError:
            raise
        except Exception as cause:
            message = cause.detail if isinstance(cause, HTTPException) else cause.message if isinstance(cause, UpstreamError) else '福利分组定时同步失败，将在下轮自动重试'
            self.error = message
            if message != self.error_logged:
                self.store.event('welfare_schedule', message, level='error')
                self.error_logged = message
            return {'enabled': enabled, 'changed': 0, 'failed': 0, 'results': []}
        finally:
            self.last_check_at = time.time()

    async def tick(self):
        async with self.lock:
            return await self.reconcile_locked()

    async def run(self):
        while True:
            started = time.monotonic()
            self.next_check_at = time.time() + INTERVAL_SECONDS
            try:
                if not updater.locked(self.store.directory) and not (self.store.directory / 'upgrade-deploying').exists():
                    await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                message = '福利分组定时任务执行异常，将在下轮自动重试'
                self.error = message
                if message != self.error_logged:
                    self.store.event('welfare_schedule', message, level='error')
                    self.error_logged = message
            await asyncio.sleep(max(0, INTERVAL_SECONDS - (time.monotonic() - started)))
