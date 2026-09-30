"""Sub2API metrics: parameterized PostgreSQL reads, API fallback when unconfigured."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import asyncio
import os
import time

from fastapi import HTTPException

from .postgres import Sub2Postgres, postgres_configured
from .sub2api import Sub2API, UpstreamError, SAMPLE_WINDOW_SECONDS

POOLS = ('priority', 'risk', 'third_party')
# Never select credentials, API keys, request bodies or upstream error messages.
USAGE_COLUMNS = '''u.id, u.account_id,
    COALESCE(NULLIF(a.name, ''), '#' || u.account_id::text) AS account_name,
    u.model, u.reasoning_effort,
    COALESCE(NULLIF(g.name, ''), '#' || u.group_id::text) AS group_name,
    u.first_token_ms, u.duration_ms, u.created_at'''
USAGE_JOINS = '''LEFT JOIN accounts a ON a.id = u.account_id AND a.deleted_at IS NULL
    LEFT JOIN groups g ON g.id = u.group_id AND g.deleted_at IS NULL'''


def records_json(rows):
    return [{key: value.isoformat() if isinstance(value, datetime) else value
             for key, value in row.items()} for row in rows]


class Analytics:
    def __init__(self, store, client=None):
        self.store = store
        self.client = client

    @asynccontextmanager
    async def api(self):
        if self.client is not None:
            yield self.client
        else:
            async with Sub2API(self.store.settings()) as client:
                yield client

    @asynccontextmanager
    async def snapshot(self):
        # Scheduler already handles UpstreamError per account; a DB failure must
        # stop that account's decision instead of becoming empty/healthy samples.
        try:
            async with Sub2Postgres(self.store).snapshot() as cursor:
                yield cursor
        except HTTPException as error:
            raise UpstreamError(error.detail, error.status_code) from None

    async def usage(self, page=1, page_size=50):
        if not postgres_configured(self.store):
            async with self.api() as client:
                return await client.usage(page, page_size)
        async with self.snapshot() as cursor:
            # 总数短暂缓存，分页记录始终实时读取；连接配置变化自动失效。
            identity = (self.store.settings().base_url, self.store.settings().admin_key,
                        self.store.postgres_source(), self.store.postgres_settings().model_dump_json(),
                        os.environ.get('FLOWPOOL_SUB2API_PG_DSN', '') if self.store.postgres_source() == 'environment' else '')
            if not hasattr(self.store, '_usage_count_lock'):
                self.store._usage_count_lock = asyncio.Lock()
            async with self.store._usage_count_lock:
                cached = getattr(self.store, '_usage_count_cache', None)
                if cached and cached[0] == identity and time.monotonic() - cached[1] < 30:
                    total = cached[2]
                else:
                    await cursor.execute('SELECT COUNT(*) AS total FROM usage_logs')
                    total = int((await cursor.fetchone())['total'])
                    self.store._usage_count_cache = (identity, time.monotonic(), total)
            await cursor.execute(f'''SELECT {USAGE_COLUMNS} FROM (
                SELECT id, account_id, group_id, model, reasoning_effort, first_token_ms, duration_ms, created_at
                FROM usage_logs ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s
            ) u {USAGE_JOINS} ORDER BY u.created_at DESC, u.id DESC''',
                                 (page_size, (page - 1) * page_size))
            items = records_json(await cursor.fetchall())
        return {'items': items, 'total': total, 'page': page, 'page_size': page_size,
                'pages': max(1, (total + page_size - 1) // page_size)}

    async def account_health(self, account_ids):
        """Only the requested batch's health fields; never credentials or notes."""
        ids = list(dict.fromkeys(account_ids))
        if not ids:
            return {}, time.time()
        if not postgres_configured(self.store):
            async with self.api() as client:
                remotes = {}
                for account_id in ids:
                    try:
                        remotes[account_id] = await client.account(account_id)
                    except UpstreamError as error:
                        if error.status != 404:
                            raise
                return remotes, client.now()
        async with self.snapshot() as cursor:
            await cursor.execute('''SELECT id, platform, type, status, schedulable, error_message,
                    expires_at, auto_pause_on_expired, rate_limit_reset_at, overload_until,
                    temp_unschedulable_until
                FROM accounts WHERE id = ANY(%s::bigint[]) AND deleted_at IS NULL AND platform = 'openai' ''', (ids,))
            rows = records_json(await cursor.fetchall())
            await cursor.execute('SELECT EXTRACT(EPOCH FROM CURRENT_TIMESTAMP) AS now')
            now = float((await cursor.fetchone())['now'])
        return {row['id']: row for row in rows}, now

    async def account_snapshot(self, account_id):
        """Persisted fields needed by routine scheduling, without runtime enrichment."""
        if not postgres_configured(self.store):
            async with self.api() as client:
                return await client.account(account_id)
        async with self.snapshot() as cursor:
            await cursor.execute('''SELECT EXTRACT(EPOCH FROM CURRENT_TIMESTAMP) AS database_now,
                    a.id, a.name, a.platform, a.type, a.status, a.schedulable,
                    a.priority, a.concurrency, a.created_at, a.expires_at, a.auto_pause_on_expired,
                    a.rate_limit_reset_at, a.overload_until, a.temp_unschedulable_until, a.error_message,
                    COALESCE((SELECT jsonb_agg(jsonb_build_object('id', g.id, 'name', g.name) ORDER BY g.id)
                        FROM account_groups ag JOIN groups g ON g.id = ag.group_id
                        WHERE ag.account_id = a.id AND g.deleted_at IS NULL), '[]'::jsonb) AS groups,
                    jsonb_build_object(
                        'codex_5h_used_percent', a.extra->'codex_5h_used_percent',
                        'codex_5h_reset_at', a.extra->'codex_5h_reset_at',
                        'codex_5h_reset_after_seconds', a.extra->'codex_5h_reset_after_seconds',
                        'codex_7d_used_percent', a.extra->'codex_7d_used_percent',
                        'codex_7d_reset_at', a.extra->'codex_7d_reset_at',
                        'codex_7d_reset_after_seconds', a.extra->'codex_7d_reset_after_seconds',
                        'codex_usage_updated_at', a.extra->'codex_usage_updated_at') AS extra
                FROM accounts a WHERE a.id = %s AND a.deleted_at IS NULL AND a.platform = 'openai' ''', (account_id,))
            row = await cursor.fetchone()
        if not row:
            raise UpstreamError('上游 GPT 账号不存在或已删除', 404)
        database_now = row.pop('database_now', None)
        if self.client is not None and database_now is not None:
            self.client.clock_offset = float(database_now) - time.time()
        account = records_json([row])[0]
        account['group_ids'] = [group['id'] for group in account['groups']]
        return account

    async def rpm(self):
        """Use exactly the RPM displayed by Sub2API's dashboard (five-minute average)."""
        async with self.api() as client:
            result = await client.request('GET', 'dashboard/snapshot-v2', params={
                'granularity': 'hour', 'timezone': 'Asia/Shanghai',
                'include_stats': 'true', 'include_trend': 'false',
                'include_model_stats': 'false', 'include_group_stats': 'false',
                'include_users_trend': 'false',
            })
            try:
                rpm = result['stats']['rpm']
                if type(rpm) is not int or rpm < 0:
                    raise ValueError('Invalid RPM')
                stamp = datetime.fromisoformat(result['generated_at'].replace('Z', '+00:00'))
                if stamp.tzinfo is None:
                    raise ValueError('Missing timezone')
                sampled_at = stamp.timestamp()
                age = client.now() - sampled_at
                if age > 65 or age < -5:
                    raise ValueError('Stale snapshot')
            except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
                raise UpstreamError('Sub2API RPM 快照缺失、无效或已过期，本轮不计算增长率') from None
            return {'rpm': rpm, 'sampled_at': sampled_at, 'source': 'snapshot-v2'}

    async def pools(self, limit):
        records = {pool: [] for pool in POOLS}
        more = {pool: False for pool in POOLS}
        mapping = {int(a['id']): a['pool'] for a in self.store.accounts()}
        if mapping and postgres_configured(self.store):
            async with self.snapshot() as cursor:
                for pool in POOLS:
                    ids = [key for key, value in mapping.items() if value == pool]
                    if not ids:
                        continue
                    # Fetch at most limit+1 per account using its existing index,
                    # then take the newest calls in this pool (no global page cap).
                    await cursor.execute(f'''SELECT {USAGE_COLUMNS} FROM (
                        SELECT recent.* FROM unnest(%s::bigint[]) AS managed(account_id)
                        CROSS JOIN LATERAL (
                            SELECT id, account_id, group_id, model, reasoning_effort,
                                   first_token_ms, duration_ms, created_at
                            FROM usage_logs WHERE account_id = managed.account_id
                            ORDER BY created_at DESC, id DESC LIMIT %s
                        ) recent ORDER BY recent.created_at DESC, recent.id DESC LIMIT %s
                    ) u {USAGE_JOINS} ORDER BY u.created_at DESC, u.id DESC''', (ids, limit + 1, limit + 1))
                    rows = records_json(await cursor.fetchall())
                    records[pool], more[pool] = rows[:limit], len(rows) > limit
        elif mapping:
            async with self.api() as client:
                for page in range(1, 21):
                    result = await client.usage(page=page, page_size=100)
                    items = result.get('items') or []
                    for item in items:
                        pool = mapping.get(int(item['account_id'])) if item.get('account_id') is not None else None
                        if pool in records and len(records[pool]) < limit:
                            records[pool].append(item)
                    if all(len(records[pool]) >= limit for pool in POOLS) or not items or page >= result.get('pages', page):
                        break
            more = {pool: len(records[pool]) >= limit for pool in POOLS}
        return {'pools': {pool: {'items': records[pool], 'total': len(records[pool]), 'has_more': more[pool]} for pool in POOLS}}

    async def ranking(self):
        ranking = {int(a['id']): {'account_id': int(a['id']), 'account_name': a['name'],
                   'pool': a['pool'], 'state': a['state'], 'share': 0, 'last_created_at': None}
                   for a in self.store.accounts()}
        if not ranking:
            return {'items': [], 'sample_size': 0}
        if postgres_configured(self.store):
            async with self.snapshot() as cursor:
                await cursor.execute('''WITH recent AS (
                    SELECT account_id, created_at FROM usage_logs ORDER BY created_at DESC, id DESC LIMIT 100
                ) SELECT account_id, COUNT(*) AS count, MAX(created_at) AS last_created_at,
                         SUM(COUNT(*)) OVER () AS total FROM recent GROUP BY account_id''')
                counts = records_json(await cursor.fetchall())
                total = int(counts[0]['total']) if counts else 0
        else:
            async with self.api() as client:
                items = (await client.usage(page=1, page_size=100)).get('items') or []
            total, seen = len(items), {}
            for item in items:
                key = item.get('account_id')
                if key is not None:
                    row = seen.setdefault(int(key), {'account_id': int(key), 'count': 0, 'last_created_at': item.get('created_at')})
                    row['count'] += 1
            counts = seen.values()
        for row in counts:
            if row['account_id'] in ranking:
                ranking[row['account_id']].update(share=round(row['count'] / total * 100, 1) if total else 0,
                                                  last_created_at=row['last_created_at'])
        return {'items': sorted(ranking.values(), key=lambda row: (-row['share'], row['account_name'].casefold(), row['account_id'])),
                'sample_size': total}

    async def watermark(self, account_id):
        if not postgres_configured(self.store):
            async with self.api() as client:
                return await client.watermark(account_id)
        async with self.snapshot() as cursor:
            await cursor.execute('SELECT COALESCE(MAX(id), 0) AS watermark FROM usage_logs WHERE account_id = %s', (account_id,))
            return int((await cursor.fetchone())['watermark'])

    async def samples(self, account_id, count, epoch=None, watermark=0):
        if not postgres_configured(self.store):
            async with self.api() as client:
                return await client.samples(account_id, count, epoch, watermark)
        async with self.snapshot() as cursor:
            # Database time avoids host clock skew; exclude requests that began
            # before resume, missing/negative timings, future and old records.
            await cursor.execute('''SELECT id, first_token_ms, created_at FROM usage_logs
                WHERE account_id = %(account_id)s AND id > %(watermark)s
                  AND created_at > CURRENT_TIMESTAMP - %(window)s * INTERVAL '1 second'
                  AND created_at <= CURRENT_TIMESTAMP AND first_token_ms >= 0
                  AND (%(epoch)s::timestamptz IS NULL OR (
                      duration_ms >= 0 AND created_at > %(epoch)s::timestamptz
                      AND created_at - duration_ms * INTERVAL '1 millisecond' > %(epoch)s::timestamptz))
                ORDER BY created_at DESC, id DESC LIMIT %(count)s''',
                {'account_id': account_id, 'watermark': watermark, 'window': SAMPLE_WINDOW_SECONDS,
                 'epoch': datetime.fromtimestamp(epoch, timezone.utc) if epoch else None, 'count': count})
            return records_json(await cursor.fetchall())
