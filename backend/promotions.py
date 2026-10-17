"""Manage promotional GPT groups through the Sub2API admin API."""
import asyncio
import time
from datetime import datetime, time as clock_time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from . import updater
from .advanced_groups import AdvancedGroups
from .postgres import Sub2Postgres, postgres_configured
from .sub2api import Sub2API, UpstreamError, image_only_account, public_account

OPENAI_GROUPS_SQL = "SELECT id, name, status, rate_multiplier FROM groups WHERE deleted_at IS NULL AND platform='openai' ORDER BY id"
SHANGHAI = ZoneInfo('Asia/Shanghai')
INTERVAL_SECONDS = 60


class Promotions:
    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()
        self.checkin_lock = asyncio.Lock()
        self.last_check_at = None
        self.next_check_at = None
        self.error = ''
        self.error_logged = ''

    @staticmethod
    def scheduled_enabled(schedule, now=None):
        now = now or datetime.now(SHANGHAI)
        current = now.hour * 60 + now.minute
        start = schedule.start_minute
        end = schedule.end_minute
        return start <= current < end if start < end else current >= start or current < end

    @staticmethod
    def next_transition(schedule, now=None):
        now = now or datetime.now(SHANGHAI)
        candidates = []
        for day in range(3):
            date = now.date() + timedelta(days=day)
            for minute in (schedule.start_minute, schedule.end_minute):
                candidate = datetime.combine(date, clock_time(minute // 60, minute % 60), SHANGHAI)
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

    def checkin_status(self):
        settings = self.store.checkin_settings()
        counts = self.store.checkin_counts(30)
        today = self.store.checkin_today().isoformat()
        today_count = next((item['count'] for item in counts if item['date'] == today), 0)
        return {'settings': settings.model_dump(), 'daily_counts': counts,
                'today_count': today_count,
                'total_checkins': self.store.db.execute('SELECT COUNT(*) FROM checkins').fetchone()[0],
                **self.checkin_legacy_fields(settings)}

    @staticmethod
    def checkin_legacy_fields(settings):
        tiers = settings.reward_tiers
        return {'min_streak_days': tiers[0].min_streak_days,
                'max_streak_days': tiers[-1].max_streak_days,
                'reward_amount': tiers[0].reward_amount if len(tiers) == 1 else None}

    @staticmethod
    def checkin_reward(settings, streak):
        return next((tier.reward_amount for tier in settings.reward_tiers
                     if tier.min_streak_days <= streak <= tier.max_streak_days), 0)

    def user_checkin_status(self, user_id):
        settings = self.store.checkin_settings()
        return {'enabled': settings.enabled, 'reward_tiers': [tier.model_dump() for tier in settings.reward_tiers],
                **self.checkin_legacy_fields(settings),
                **self.store.checkin_status(user_id)}

    async def user_checkin(self, user_id):
        async with self.checkin_lock:
            settings = self.store.checkin_settings()
            if not settings.enabled:
                raise HTTPException(409, '签到活动暂未开启')
            current = self.store.checkin_status(user_id)
            if current['checked_in_today']:
                result = self.store.record_checkin(user_id)
            else:
                yesterday = (self.store.checkin_today() - timedelta(days=1)).isoformat()
                streak = current['streak'] + 1 if current['last_checkin_date'] == yesterday else 1
                reward = self.checkin_reward(settings, streak)
                result = self.store.record_checkin(user_id, reward)
            rewarded_now = False
            if result['reward_status'] in {'pending', 'uncertain'}:
                reward_key = f"flowpool-checkin-{user_id}-{result['checkin_date']}"
                try:
                    async with Sub2API(self.store.settings()) as client:
                        await client.request('POST', f'users/{user_id}/balance',
                                             headers={'Idempotency-Key': reward_key},
                                             json={'balance': result['reward_amount'], 'operation': 'add',
                                                   'notes': f"每日签到奖励 {result['checkin_date']} · 连续 {result['streak']} 天"})
                except asyncio.CancelledError:
                    raise
                except UpstreamError:
                    self.store.update_checkin_reward(user_id, result['checkin_date'], 'uncertain')
                    raise
                self.store.update_checkin_reward(user_id, result['checkin_date'], 'credited')
                result['reward_status'] = 'credited'
                rewarded_now = True
            return {**self.user_checkin_status(user_id), **result, 'last_checkin_date': result['checkin_date'],
                    'rewarded': rewarded_now}

    def save_checkin_settings(self, value, owner_id):
        self.store.save_checkin_settings(value)
        tiers = '；'.join(f'{tier.min_streak_days}–{tier.max_streak_days} 天 +{tier.reward_amount:g}' for tier in value.reward_tiers)
        self.store.event('checkin_settings', f'管理员 #{owner_id} 已更新签到活动配置：{tiers}，{"开启" if value.enabled else "关闭"}')
        return self.checkin_status()

    def user_schedule_status(self):
        schedule = self.store.welfare_schedule()
        now = datetime.now(SHANGHAI)
        opening = datetime.combine(now.date(), clock_time(schedule.start_minute // 60, schedule.start_minute % 60), SHANGHAI)
        if opening <= now:
            opening += timedelta(days=1)
        return {**schedule.model_dump(), 'active_now': self.scheduled_enabled(schedule, now) if schedule.enabled else None,
                'server_now': now.isoformat(), 'next_open_at': opening.isoformat(),
                'next_transition_at': self.next_transition(schedule, now) if schedule.enabled else None,
                'countdown_copy': {
                    'zh': {'opening': schedule.countdown_copy.zh_opening, 'closing': schedule.countdown_copy.zh_closing},
                    'en': {'opening': schedule.countdown_copy.en_opening, 'closing': schedule.countdown_copy.en_closing},
                }}

    async def openai_groups(self):
        if postgres_configured(self.store):
            async with Sub2Postgres(self.store).snapshot() as cursor:
                await cursor.execute(OPENAI_GROUPS_SQL)
                groups = await cursor.fetchall()
        else:
            async with Sub2API(self.store.settings()) as client:
                groups = [group for group in await client.all('groups', platform='openai')
                          if group.get('platform') == 'openai' and not group.get('deleted_at')
                          ]
        return [{'id': group['id'], 'name': group['name'], 'status': group.get('status', ''), 'rate_multiplier': group.get('rate_multiplier', 1)} for group in groups]

    async def welfare_groups(self):
        return [group for group in await self.openai_groups() if '福利' in (group.get('name') or '')]

    async def groups(self):
        source_groups = await self.openai_groups()
        return {'items': [group for group in source_groups if '福利' in (group.get('name') or '')],
                'source_groups': source_groups, 'schedule': self.schedule_status()}

    async def save_schedule(self, value, owner_id):
        async with self.lock:
            self.store.save_welfare_schedule(value)
            result = await self.reconcile_locked()
        state = '已开启' if value.enabled else '已关闭'
        self.store.event('welfare_schedule', f"管理员 #{owner_id} {state}夜间福利分组定时控制：{value.start_minute // 60:02d}:{value.start_minute % 60:02d}–{value.end_minute // 60:02d}:{value.end_minute % 60:02d}（Asia/Shanghai）")
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

    async def set_welfare_group_rate(self, group_id, rate_multiplier, owner_id):
        if self.lock.locked():
            raise HTTPException(409, '正在更新福利分组，请稍后重试')
        async with self.lock:
            current = {group['id']: group for group in await self.welfare_groups()}
            if group_id not in current:
                raise HTTPException(409, '该分组已删除、改名或不属于 GPT / OpenAI 平台，请刷新后重试')
            async with Sub2API(self.store.settings()) as client:
                result = await self.update_rate_with_client(client, group_id, current[group_id]['name'], rate_multiplier)
        if result['verified']:
            self.store.event('welfare_group_rate', f"管理员 #{owner_id} 已更新夜间福利分组 {result['name']}（#{group_id}）倍率为 {rate_multiplier:g}")
        return result

    async def welfare_group_name(self, group_id):
        current = {group['id']: group for group in await self.welfare_groups()}
        if group_id not in current:
            raise HTTPException(409, '该分组已删除、改名或不属于 GPT / OpenAI 平台，请刷新后重试')
        return current[group_id]['name']

    async def group_members(self, group_id):
        name = await self.welfare_group_name(group_id)
        async with Sub2API(self.store.settings()) as client:
            members = await self.members(client, group_id)
        return {'group': {'id': group_id, 'name': name},
                'items': [{'id': account['id'], 'name': account.get('name', '')} for account in members]}

    async def remove_group_members(self, group_id, owner_id):
        name = await self.welfare_group_name(group_id)
        async with Sub2API(self.store.settings()) as client:
            members = await self.members(client, group_id)
            results = []
            for offset in range(0, len(members), 4):
                results.extend(await asyncio.gather(*(
                    self.remove_member(client, account, group_id)
                    for account in members[offset:offset + 4])))
        counts = {status: sum(item['status'] == status for item in results)
                  for status in ('changed', 'unchanged', 'skipped', 'failed')}
        self.store.event('welfare_group_members', f"管理员 #{owner_id} 已移除夜间福利分组 {name}（#{group_id}）的账号："
                         f"更新 {counts['changed']} 个，无需修改 {counts['unchanged']} 个，"
                         f"跳过 {counts['skipped']} 个，失败或未确认 {counts['failed']} 个",
                         level='warning' if counts['skipped'] or counts['failed'] else 'info')
        return {'group': {'id': group_id, 'name': name}, **counts, 'results': results}

    async def copy_group_members(self, group_id, source_group_id, owner_id):
        name = await self.welfare_group_name(group_id)
        source_groups = {group['id']: group for group in await self.openai_groups()}
        if source_group_id not in source_groups:
            raise HTTPException(409, '来源分组已删除、改名或不属于 GPT / OpenAI 平台，请刷新后重试')
        if source_group_id == group_id:
            raise HTTPException(409, '来源分组不能与夜间福利分组相同')
        source_name = source_groups[source_group_id]['name']
        async with Sub2API(self.store.settings()) as client:
            members = await self.members(client, source_group_id)
            results = []
            for offset in range(0, len(members), 4):
                results.extend(await asyncio.gather(*(
                    self.copy_member(client, account, group_id)
                    for account in members[offset:offset + 4])))
        counts = {status: sum(item['status'] == status for item in results)
                  for status in ('changed', 'unchanged', 'skipped', 'failed')}
        self.store.event('welfare_group_members_copy', f"管理员 #{owner_id} 已从分组 {source_name}（#{source_group_id}）复制账号到夜间福利分组 {name}（#{group_id}）："
                         f"更新 {counts['changed']} 个，无需修改 {counts['unchanged']} 个，"
                         f"跳过 {counts['skipped']} 个，失败或未确认 {counts['failed']} 个",
                         level='warning' if counts['skipped'] or counts['failed'] else 'info')
        return {'group': {'id': group_id, 'name': name},
                'source_group': {'id': source_group_id, 'name': source_name}, **counts, 'results': results}

    @staticmethod
    async def members(client, group_id):
        return [account for account in await client.accounts()
                if group_id in ({group['id'] for group in account.get('groups') or []} | set(account.get('group_ids') or []))]

    async def remove_member(self, client, account, group_id):
        result = {'id': account['id'], 'name': account.get('name', ''), 'status': 'failed', 'error': ''}
        try:
            local = self.store.account(account['id'])
            if local and local['state'] in {'pausing', 'resuming'}:
                return {**result, 'status': 'skipped', 'error': '账号正在切换调度状态，请完成后重试'}
            remote = await client.account(account['id'])
            before = AdvancedGroups.memberships(remote)
            after = before - {group_id}
            if before == after:
                if local:
                    self.store.update(account['id'], remote=public_account(remote))
                return {**result, 'status': 'unchanged'}
            if local and local['state'] == 'cooldown' and (
                    local.get('cooldown_mode') == 'image'
                    or (local['pool'] == 'third_party' and image_only_account(remote))):
                return {**result, 'status': 'skipped', 'error': '账号处于仅生图冷却保护，冷却结束后可重试'}
            await client.request('PUT', f"accounts/{account['id']}", json={'group_ids': sorted(after)})
            verified = await client.account(account['id'])
            if AdvancedGroups.memberships(verified) != after:
                raise UpstreamError('上游分组修改尚未确认，请刷新后重试', 409)
            if local:
                self.store.update(account['id'], remote=public_account(verified))
            return {**result, 'status': 'changed'}
        except UpstreamError as cause:
            result['error'] = cause.message
        except Exception:
            result['error'] = '操作失败或结果未确认，请刷新后重试'
        return result

    async def copy_member(self, client, account, group_id):
        result = {'id': account['id'], 'name': account.get('name', ''), 'status': 'failed', 'error': ''}
        try:
            local = self.store.account(account['id'])
            if local and local['state'] in {'pausing', 'resuming'}:
                return {**result, 'status': 'skipped', 'error': '账号正在切换调度状态，请完成后重试'}
            remote = await client.account(account['id'])
            before = AdvancedGroups.memberships(remote)
            if group_id in before:
                if local:
                    self.store.update(account['id'], remote=public_account(remote))
                return {**result, 'status': 'unchanged'}
            if local and local['state'] == 'cooldown' and (
                    local.get('cooldown_mode') == 'image'
                    or (local['pool'] == 'third_party' and image_only_account(remote))):
                return {**result, 'status': 'skipped', 'error': '账号处于仅生图冷却保护，冷却结束后可重试'}
            after = before | {group_id}
            await client.request('PUT', f"accounts/{account['id']}", json={'group_ids': sorted(after)})
            verified = await client.account(account['id'])
            if AdvancedGroups.memberships(verified) != after:
                raise UpstreamError('上游分组修改尚未确认，请刷新后重试', 409)
            if local:
                self.store.update(account['id'], remote=public_account(verified))
            return {**result, 'status': 'changed'}
        except UpstreamError as cause:
            result['error'] = cause.message
        except Exception:
            result['error'] = '操作失败或结果未确认，请刷新后重试'
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

    async def update_rate_with_client(self, client, group_id, name, rate_multiplier):
        error = ''
        try:
            await client.request('PUT', f'groups/{group_id}', json={'rate_multiplier': rate_multiplier})
        except UpstreamError as cause:
            error = cause.message + ('；结果尚未确认，请刷新后重试' if cause.uncertain else '')
        try:
            body = await client.request('GET', f'groups/{group_id}')
        except UpstreamError as cause:
            return {'id': group_id, 'name': name, 'rate_multiplier': None, 'verified': False,
                    'error': error or f'{cause.message}；无法复核写入结果，请刷新后重试'}
        stored = body.get('rate_multiplier') if isinstance(body, dict) else None
        try:
            verified = float(stored) == float(rate_multiplier)
        except (TypeError, ValueError):
            verified = False
        if verified:
            error = ''
        elif not error:
            error = '写入后复核不一致，分组倍率可能已被其他管理员修改'
        return {'id': group_id, 'name': body.get('name', name) if isinstance(body, dict) else name,
                'rate_multiplier': stored, 'verified': verified, 'error': error}

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
