"""Read and adjust Sub2API GPT rates with verified PostgreSQL snapshots."""
import asyncio
import hashlib
import os
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException

from .auth import new_session_token
from .models import RateAdjustmentRequest, RateRules
from .postgres import Sub2Postgres, postgres_configured
from .sub2api import Sub2API, UpstreamError

GROUP_NAMES = {'Pro号池', 'Pro号池 (强制开启Fast)'}
SNAPSHOT_TTL = 30 * 60
GROUPS_SQL = "SELECT id, name, platform, rate_multiplier FROM groups WHERE deleted_at IS NULL AND status='active' AND platform='openai' ORDER BY id"

# Positive balance recharges and all admin balance adjustments are included.
# Balance-order refunds that are requested or completed are deducted from that amount.
# All inputs bind.
INSPECTION_SQL = '''
WITH target_users AS (
    SELECT id, username, email, restrict_public_groups FROM users
    WHERE deleted_at IS NULL
      AND (%(all_ids)s OR id = ANY(%(user_ids)s::bigint[]))
), recharge AS (
    SELECT r.used_by, SUM(r.value) AS total
    FROM redeem_codes r JOIN target_users u ON u.id = r.used_by
    WHERE (r.type = 'balance' AND r.value > 0) OR r.type = 'admin_balance'
    GROUP BY r.used_by
), refunds AS (
    SELECT o.user_id, SUM(o.refund_amount) AS total
    FROM payment_orders o JOIN target_users u ON u.id = o.user_id
    WHERE o.order_type = 'balance'
      AND o.status IN ('REFUND_REQUESTED', 'REFUNDING', 'REFUND_PENDING',
                       'PARTIALLY_REFUNDED', 'REFUNDED')
      AND o.refund_amount > 0
    GROUP BY o.user_id
)
SELECT u.id AS user_id, u.username, u.email,
       GREATEST(COALESCE(r.total, 0) - COALESCE(f.total, 0), 0) AS total,
       g.id AS group_id, g.name AS group_name,
       g.rate_multiplier AS default_rate,
       COALESCE(m.rate_multiplier, g.rate_multiplier) AS current,
       m.rate_multiplier IS NOT NULL AS custom,
       (NOT (g.is_exclusive OR u.restrict_public_groups) OR EXISTS (
           SELECT 1 FROM user_allowed_groups a WHERE a.user_id = u.id AND a.group_id = g.id
       )) AS eligible
FROM target_users u LEFT JOIN recharge r ON r.used_by = u.id
LEFT JOIN refunds f ON f.user_id = u.id
CROSS JOIN groups g
LEFT JOIN user_group_rate_multipliers m ON m.user_id = u.id AND m.group_id = g.id
WHERE g.id = ANY(%(group_ids)s::bigint[]) AND g.deleted_at IS NULL AND g.status = 'active' AND g.platform = 'openai'
ORDER BY u.id, g.id
'''

ADJUST_GROUPS_SQL = '''
SELECT id, name, platform, status, deleted_at, rate_multiplier
FROM groups
WHERE id = ANY(%(group_ids)s::bigint[])
  AND deleted_at IS NULL AND status = 'active' AND platform = 'openai'
FOR UPDATE
'''

ADJUST_USERS_SQL = '''
SELECT user_id, group_id, rate_multiplier
FROM user_group_rate_multipliers
WHERE group_id = ANY(%(group_ids)s::bigint[]) AND rate_multiplier IS NOT NULL
FOR UPDATE
'''


def number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def expected_rate(total, rules, default_rate=None):
    return next((Decimal(str(tier.rate)) for tier in sorted(rules.tiers, key=lambda tier: tier.minimum, reverse=True)
                 if total >= Decimal(str(tier.minimum))), default_rate)


def default_groups(groups):
    return [group for group in groups if group.get('platform') == 'openai' and unicodedata.normalize('NFKC', group['name']).strip() in GROUP_NAMES]


def select_groups(groups, rules):
    groups = [group for group in groups if group.get('platform') == 'openai']
    if rules.group_ids is None:
        selected = default_groups(groups)
    else:
        selected = [group for group in groups if group['id'] in rules.group_ids]
        if set(rules.group_ids) != {group['id'] for group in selected}:
            raise HTTPException(409, '所选巡检分组已停用、不存在或不属于 GPT / OpenAI 平台，请更新充值档位规则中的分组')
    if not selected:
        raise HTTPException(409, '请在充值档位规则中选择至少一个正常的 GPT / OpenAI 分组')
    return selected


def summarize(records, rules=None):
    rules = rules if rules is not None else RateRules()
    users, checked_users, abnormal = set(), set(), set()
    rows, checked = [], 0
    for record in records:
        user_id = record['user_id']
        users.add(user_id)
        total = number(record['total'])
        if total is None:
            raise HTTPException(502, '历史充值数据无效，本次巡检未完成')
        default_rate = number(record.get('default_rate'))
        expected = expected_rate(total, rules, default_rate)
        if expected is None or not record['eligible']:
            continue
        checked += 1
        checked_users.add(user_id)
        current = number(record['current'])
        if current is not None and abs(current - expected) < Decimal('1e-8'):
            continue
        abnormal.add(user_id)
        rows.append({'id': f"{user_id}:{record['group_id']}", 'user_id': user_id,
                     'username': record['username'], 'email': record['email'], 'group_id': record['group_id'],
                     'group': record['group_name'], 'total': float(total), 'expected': float(expected),
                     'current': float(current) if current is not None else None, 'custom': record['custom'],
                     'status': 'mismatch' if current is not None else 'invalid'})
    return {'rows': rows, 'processed': len(users), 'checked': checked, 'skipped': len(users - checked_users),
            'abnormal_users': len(abnormal), 'completed_at': datetime.now(timezone.utc).isoformat()}


class RateInspection:
    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()
        store.db.execute('''CREATE TABLE IF NOT EXISTS rate_inspections (
            id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, payload TEXT NOT NULL, expires_at REAL NOT NULL
        )''')
        store.db.commit()

    def connection_identity(self):
        settings = self.store.settings()
        # Reviewed results bind to the exact backend, PG connection and recharge rules.
        legacy_dsn = os.environ.get('FLOWPOOL_SUB2API_PG_DSN', '') if self.store.postgres_source() == 'environment' else ''
        return hashlib.sha256('\n'.join((settings.base_url, settings.admin_key, legacy_dsn,
            self.store.postgres_settings().model_dump_json(), self.store.rate_rules().model_dump_json())).encode()).hexdigest()

    async def read(self, user_ids=None, include_values=False):
        rules = self.store.rate_rules()
        records = await self.read_postgres(user_ids, rules) if postgres_configured(self.store) else await self.read_api(user_ids, rules)
        result = summarize(records, rules)
        result['rules'] = rules.model_dump()
        if include_values:
            result['values'] = {f"{row['user_id']}:{row['group_id']}": number(row['current'])
                                for row in records if row['eligible'] and row['custom']}
        return result

    async def groups(self):
        if postgres_configured(self.store):
            async with Sub2Postgres(self.store).snapshot() as cursor:
                await cursor.execute(GROUPS_SQL)
                groups = await cursor.fetchall()
        else:
            async with Sub2API(self.store.settings()) as client:
                groups = [group for group in await client.all('groups', platform='openai')
                          if group.get('platform') == 'openai' and group.get('status') == 'active' and not group.get('deleted_at')]
        groups = [group for group in groups if group.get('platform') == 'openai']
        # The group API contains internal settings; expose only selector metadata.
        items = []
        for group in groups:
            item = {'id': group['id'], 'name': group['name'], 'platform': group.get('platform', '')}
            if 'rate_multiplier' in group:
                rate = number(group.get('rate_multiplier'))
                item['rate_multiplier'] = float(rate) if rate is not None else None
            items.append(item)
        return {'items': items, 'default_group_ids': [group['id'] for group in default_groups(groups)]}

    async def read_api(self, user_ids, rules):
        # Compatibility mode: use the server's lifetime aggregate, never infer
        # recharge from balance or a partial page of balance-history items.
        async with Sub2API(self.store.settings()) as client:
            groups = select_groups([group for group in await client.all('groups', platform='openai')
                                    if group.get('platform') == 'openai' and group.get('status') == 'active' and not group.get('deleted_at')], rules)
            overrides = {}
            for group in groups:
                entries = await client.request('GET', f"groups/{group['id']}/rate-multipliers")
                if not isinstance(entries, list):
                    raise HTTPException(502, '用户分组倍率响应无效，本次巡检未完成')
                overrides[group['id']] = {row['user_id']: row['rate_multiplier'] for row in entries
                                          if row.get('rate_multiplier') is not None}
            if user_ids is None:
                users = await client.all('users', sort_by='id', sort_order='asc')
            else:
                users = []
                for offset in range(0, len(user_ids), 4):
                    users.extend(await asyncio.gather(*(client.request('GET', f'users/{key}') for key in user_ids[offset:offset + 4])))
            users = [user for user in users if not user.get('deleted_at')]
            if len({user['id'] for user in users}) != len(users):
                raise HTTPException(409, '分页期间用户列表已变化，请重新巡检')

            async def user_records(user):
                history = await client.request('GET', f"users/{user['id']}/balance-history", params={'page': 1, 'page_size': 1})
                total = number(history.get('total_recharged'))
                if total is None:
                    raise HTTPException(502, '历史充值汇总缺失或无效，本次巡检未完成')
                return [{'user_id': user['id'], 'username': user.get('username', ''), 'email': user['email'],
                         'total': total, 'group_id': group['id'], 'group_name': group['name'],
                         'current': overrides[group['id']].get(user['id'], group['rate_multiplier']),
                         'custom': user['id'] in overrides[group['id']],
                         'eligible': not (group['is_exclusive'] or user['restrict_public_groups'])
                                     or group['id'] in (user.get('allowed_groups') or [])}
                        for group in groups]
            records = []
            for offset in range(0, len(users), 4):
                for rows in await asyncio.gather(*(user_records(user) for user in users[offset:offset + 4])):
                    records.extend(rows)
            return records

    async def read_postgres(self, user_ids, rules):
        async with Sub2Postgres(self.store).snapshot() as cursor:
            await cursor.execute(GROUPS_SQL)
            groups = select_groups(await cursor.fetchall(), rules)
            await cursor.execute(INSPECTION_SQL, {'all_ids': user_ids is None, 'user_ids': user_ids or [],
                'group_ids': [g['id'] for g in groups]})
            return await cursor.fetchall()

    async def verify_backend(self):
        # Validate the same key against HTTP as against PostgreSQL; no user pagination.
        # Sub2API 0.2.19+ forbids API-Key access to settings/admin-api-key;
        # groups is a read-only endpoint required by this feature and works with
        # both legacy full-access keys and scoped keys granted groups.read.
        async with Sub2API(self.store.settings()) as client:
            await client.request('GET', 'groups', params={'page': 1, 'page_size': 1})

    @staticmethod
    def adjusted_rate(current, value, operation, unit):
        current = number(current)
        if current is None or current <= 0:
            raise HTTPException(422, '现有倍率无效，无法统一调整')
        delta = Decimal(str(value))
        if unit == 'percentage':
            result = current * (Decimal('1') + (delta if operation == 'increase' else -delta) / Decimal('100'))
        else:
            result = current + delta if operation == 'increase' else current - delta
        # Sub2API stores these columns as DECIMAL(10,4), and its API rejects
        # non-positive user overrides.
        result = result.quantize(Decimal('0.0001'))
        if result <= 0 or result > Decimal('999999.9999'):
            raise HTTPException(422, '调整后的倍率必须大于 0 且不超过 999999.9999')
        return result

    async def adjust(self, value: RateAdjustmentRequest, owner_id):
        if self.lock.locked():
            raise HTTPException(409, '正在巡检或纠正，请稍后重试')
        async with self.lock:
            group_ids = value.group_ids
            if group_ids is None:
                group_ids = self.store.rate_rules().group_ids
                if group_ids is None:
                    group_ids = [item['id'] for item in (await self.groups())['items']]
            group_ids = list(dict.fromkeys(group_ids))
            if not group_ids:
                raise HTTPException(409, '请至少选择一个 GPT / OpenAI 分组')
            if len(group_ids) > 200:
                raise HTTPException(422, '单次最多调整 200 个 GPT / OpenAI 分组')
            if not value.include_defaults and not value.include_users:
                raise HTTPException(422, '至少选择分组默认倍率或用户专属倍率')
            if postgres_configured(self.store):
                return await self.adjust_postgres(group_ids, value, owner_id)
            return await self.adjust_api(group_ids, value, owner_id)

    async def adjust_postgres(self, group_ids, value, owner_id):
        async with Sub2Postgres(self.store).transaction() as cursor:
            await cursor.execute(ADJUST_GROUPS_SQL, {'group_ids': group_ids})
            groups = await cursor.fetchall()
            by_id = {group['id']: group for group in groups}
            if set(by_id) != set(group_ids):
                raise HTTPException(409, '所选分组已停用、删除或不属于 GPT / OpenAI 平台，请刷新后重试')
            await cursor.execute(ADJUST_USERS_SQL, {'group_ids': group_ids})
            entries = await cursor.fetchall()
            custom_by_group = defaultdict(list)
            for entry in entries:
                custom_by_group[entry['group_id']].append(entry)

            plans = []
            for group_id in group_ids:
                group = by_id[group_id]
                new_default = self.adjusted_rate(group['rate_multiplier'], value.value, value.operation, value.unit) \
                    if value.include_defaults else number(group['rate_multiplier'])
                if value.include_defaults and new_default is None:
                    raise HTTPException(422, '分组默认倍率无效，无法统一调整')
                user_plans = []
                if value.include_users:
                    for entry in custom_by_group[group_id]:
                        user_plans.append((entry['user_id'], self.adjusted_rate(
                            entry['rate_multiplier'], value.value, value.operation, value.unit)))
                plans.append((group, new_default, user_plans))

            if value.include_defaults:
                await cursor.executemany(
                    'UPDATE groups SET rate_multiplier = %s, updated_at = NOW() WHERE id = %s',
                    [(new_default, group['id']) for group, new_default, _ in plans])
            if value.include_users:
                user_updates = [(rate, user_id, group['id'])
                                for group, _, user_plans in plans
                                for user_id, rate in user_plans]
                if user_updates:
                    await cursor.executemany(
                        '''UPDATE user_group_rate_multipliers
                           SET rate_multiplier = %s, updated_at = NOW()
                           WHERE user_id = %s AND group_id = %s''', user_updates)

            # Read back in the same transaction so the response only reports
            # values that were actually persisted before commit.
            if value.include_defaults:
                await cursor.execute(
                    'SELECT id, rate_multiplier FROM groups WHERE id = ANY(%(group_ids)s::bigint[])',
                    {'group_ids': group_ids})
                stored_defaults = {row['id']: number(row['rate_multiplier']) for row in await cursor.fetchall()}
            else:
                stored_defaults = {group['id']: number(group['rate_multiplier']) for group, _, _ in plans}
            stored_users = {}
            if value.include_users:
                user_ids = sorted({user_id for _, _, user_plans in plans for user_id, _ in user_plans})
                if user_ids:
                    await cursor.execute(
                        '''SELECT user_id, group_id, rate_multiplier
                           FROM user_group_rate_multipliers
                           WHERE group_id = ANY(%(group_ids)s::bigint[])
                             AND user_id = ANY(%(user_ids)s::bigint[])''',
                        {'group_ids': group_ids, 'user_ids': user_ids})
                    stored_users = {(row['user_id'], row['group_id']): number(row['rate_multiplier'])
                                    for row in await cursor.fetchall()}
            results = []
            for group, new_default, user_plans in plans:
                stored = stored_defaults.get(group['id'])
                verified_default = (not value.include_defaults) or stored == new_default
                verified_users = all(stored_users.get((user_id, group['id'])) == rate
                                     for user_id, rate in user_plans)
                verified = verified_default and verified_users
                results.append({
                    'id': group['id'], 'name': group['name'],
                    'old_default': float(group['rate_multiplier']) if group['rate_multiplier'] is not None else None,
                    'new_default': float(stored) if stored is not None else None,
                    'users_updated': len(user_plans) if value.include_users else 0,
                    'verified': verified,
                    'error': '' if verified else '写入后复核不一致，请重新巡检',
                })
            if any(not item['verified'] for item in results):
                raise HTTPException(502, '倍率写入后复核不一致，事务已回滚，请重试')
        succeeded = sum(item['verified'] for item in results)
        self.store.event('rate_adjustment', f'管理员 #{owner_id} 统一调整 {len(results)} 个 GPT 分组倍率，确认成功 {succeeded} 个')
        return {'operation': value.operation, 'unit': value.unit, 'value': value.value,
                'include_defaults': value.include_defaults, 'include_users': value.include_users,
                'succeeded': succeeded, 'failed': len(results) - succeeded, 'results': results}

    async def adjust_api(self, group_ids, value, owner_id):
        """Compatibility path for installations without a configured PG connection."""
        async with Sub2API(self.store.settings()) as client:
            available = [group for group in await client.all('groups', platform='openai')
                         if group.get('id') in group_ids and group.get('platform') == 'openai'
                         and group.get('status') == 'active' and not group.get('deleted_at')]
            if {group['id'] for group in available} != set(group_ids):
                raise HTTPException(409, '所选分组已停用、删除或不属于 GPT / OpenAI 平台，请刷新后重试')
            plans = []
            for group in available:
                current = await client.request('GET', f"groups/{group['id']}")
                entries = await client.request('GET', f"groups/{group['id']}/rate-multipliers")
                if not isinstance(current, dict) or not isinstance(entries, list):
                    raise HTTPException(502, '倍率数据响应无效，本次调整未完成')
                new_default = self.adjusted_rate(current.get('rate_multiplier'), value.value, value.operation, value.unit) \
                    if value.include_defaults else number(current.get('rate_multiplier'))
                user_plans = []
                if value.include_users:
                    for entry in entries:
                        if entry.get('rate_multiplier') is not None:
                            user_plans.append({'user_id': entry.get('user_id'), 'rate_multiplier': float(self.adjusted_rate(
                                entry['rate_multiplier'], value.value, value.operation, value.unit))})
                plans.append((group, current, new_default, user_plans))
            results = []
            for group, current, new_default, user_plans in plans:
                if value.include_defaults:
                    await client.request('PUT', f"groups/{group['id']}", json={'rate_multiplier': float(new_default)})
                if value.include_users:
                    await client.request('PUT', f"groups/{group['id']}/rate-multipliers", json={'entries': user_plans})
                old_default = number(current.get('rate_multiplier'))
                results.append({'id': group['id'], 'name': group['name'],
                                'old_default': float(old_default) if old_default is not None else None,
                                'new_default': float(new_default) if new_default is not None else None,
                                'users_updated': len(user_plans),
                                'verified': True, 'error': ''})
        self.store.event('rate_adjustment', f'管理员 #{owner_id} 统一调整 {len(results)} 个 GPT 分组倍率')
        return {'operation': value.operation, 'unit': value.unit, 'value': value.value,
                'include_defaults': value.include_defaults, 'include_users': value.include_users,
                'succeeded': len(results), 'failed': 0, 'results': results}

    async def inspect(self, owner_id):
        if self.lock.locked():
            raise HTTPException(409, '正在巡检或纠正，请稍后重试')
        async with self.lock:
            identity = self.connection_identity()
            result = await self.read()
            await self.verify_backend()
            if identity != self.connection_identity():
                raise HTTPException(409, '连接配置或充值档位已变化，请重新巡检')
            inspection_id = new_session_token()
            snapshot = {'identity': identity, 'rows': result['rows']}
            self.store.db.execute('DELETE FROM rate_inspections WHERE expires_at<=?', (time.time(),))
            self.store.db.execute('INSERT INTO rate_inspections VALUES (?, ?, ?, ?)',
                                  (inspection_id, owner_id, self.store.seal(snapshot), time.time() + SNAPSHOT_TTL))
            self.store.db.execute('''DELETE FROM rate_inspections WHERE id NOT IN
                (SELECT id FROM rate_inspections ORDER BY expires_at DESC LIMIT 20)''')
            self.store.db.commit()
            return {**result, 'inspection_id': inspection_id}

    async def correct(self, inspection_id, row_ids, owner_id, access_token=''):
        if self.lock.locked():
            raise HTTPException(409, '正在巡检或纠正，请稍后重试')
        async with self.lock:
            stored = self.store.db.execute('SELECT payload FROM rate_inspections WHERE id=? AND owner_id=? AND expires_at>?',
                                          (inspection_id, owner_id, time.time())).fetchone()
            if not stored:
                raise HTTPException(409, '巡检结果已过期或不可用，请重新巡检')
            snapshot = self.store.unseal(stored['payload'])
            if snapshot['identity'] != self.connection_identity():
                raise HTTPException(409, '连接配置或充值档位已变化，请重新巡检')
            old_rows = {row['id']: row for row in snapshot['rows']}
            selected_ids = list(dict.fromkeys(row_ids))
            if any(row_id not in old_rows for row_id in selected_ids):
                raise HTTPException(422, '仅可纠正本次巡检中的异常记录')
            selected = [old_rows[row_id] for row_id in selected_ids]
            user_ids = sorted({row['user_id'] for row in selected})
            latest = await self.read(user_ids=user_ids)
            latest_rows = {row['id']: row for row in latest['rows']}
            # All selected changes are rejected together if the review is stale.
            for row in selected:
                fresh = latest_rows.get(row['id'])
                if fresh != row:
                    raise HTTPException(409, '用户、分组、充值或倍率已变化，请重新巡检后确认')
            await self.verify_backend()
            if snapshot['identity'] != self.connection_identity():
                raise HTTPException(409, '连接配置或充值档位已变化，请重新巡检')
            # Consume before writing. Unknown outcomes must be inspected again, never blindly replayed.
            self.store.db.execute('DELETE FROM rate_inspections WHERE id=?', (inspection_id,))
            self.store.db.commit()
            grouped = defaultdict(list)
            for row in selected:
                grouped[row['user_id']].append(row)
            results = []
            # Restricted admin API keys are intentionally read-scoped. Use the
            # current Sub2API admin JWT for the user-rate write, while all
            # snapshots and post-write verification continue using the API key.
            async with Sub2API(self.store.settings(), access_token=access_token) as client:
                async def update_user(user_id, rows):
                    error = ''
                    try:
                        if snapshot['identity'] != self.connection_identity():
                            raise UpstreamError('连接配置或充值档位已变化，请重新巡检')
                        await client.request('PUT', f'users/{user_id}', json={
                            'group_rates': {str(row['group_id']): row['expected'] for row in rows}})
                    except UpstreamError as cause:
                        error = cause.message + ('；结果尚未确认，请重新巡检' if cause.uncertain else '')
                    return [{'id': row['id'], 'error': error, 'verified': False} for row in rows]

                users = list(grouped.items())
                for offset in range(0, len(users), 4):
                    batch = users[offset:offset + 4]
                    # Recheck just before each small write batch, even during a long correction.
                    try:
                        fresh = await self.read(user_ids=[user_id for user_id, _ in batch])
                        fresh_rows = {row['id']: row for row in fresh['rows']}
                    except (HTTPException, UpstreamError):
                        fresh_rows = {}
                    pending = []
                    for user_id, rows in batch:
                        if any(fresh_rows.get(row['id']) != row for row in rows):
                            results.extend({'id': row['id'], 'error': '数据已变化或无法复核，请重新巡检',
                                            'verified': False} for row in rows)
                        else:
                            pending.append(update_user(user_id, rows))
                    for items in await asyncio.gather(*pending):
                        results.extend(items)
            # UpdateUser can log a failed write yet return 200; verify actual overrides.
            try:
                verified = await self.read(user_ids=user_ids, include_values=True)
                outstanding = {row['id'] for row in verified['rows']}
                for result in results:
                    row = old_rows[result['id']]
                    if verified['values'].get(result['id']) == number(row['expected']) and result['id'] not in outstanding:
                        result.update(error='', verified=True)
                    elif not result['error']:
                        result['error'] = '写入结果未确认，请重新巡检'
            except (HTTPException, UpstreamError):
                for result in results:
                    result['error'] = result['error'] or '写入后复核失败，请重新巡检'
            succeeded = sum(result['verified'] for result in results)
            self.store.event('rate_correction', f'管理员 #{owner_id} 纠正用户倍率：确认成功 {succeeded} 条，未确认 {len(results) - succeeded} 条')
            return {'succeeded': succeeded, 'failed': len(results) - succeeded, 'results': results}
