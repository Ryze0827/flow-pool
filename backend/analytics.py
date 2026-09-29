"""Sub2API metrics: parameterized PostgreSQL reads, API fallback when unconfigured."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone

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
            await cursor.execute('SELECT COUNT(*) AS total FROM usage_logs')
            total = (await cursor.fetchone())['total']
            await cursor.execute(f'''SELECT {USAGE_COLUMNS} FROM (
                SELECT id, account_id, group_id, model, reasoning_effort, first_token_ms, duration_ms, created_at
                FROM usage_logs ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s
            ) u {USAGE_JOINS} ORDER BY u.created_at DESC, u.id DESC''',
                                 (page_size, (page - 1) * page_size))
            items = records_json(await cursor.fetchall())
        return {'items': items, 'total': total, 'page': page, 'page_size': page_size,
                'pages': max(1, (total + page_size - 1) // page_size)}

    async def rpm(self):
        """Completed GPT calls in (sample time - 60s, sample time], including unmanaged accounts."""
        if postgres_configured(self.store):
            async with self.snapshot() as cursor:
                await cursor.execute('''SELECT COUNT(*) AS rpm, CURRENT_TIMESTAMP AS sampled_at
                    FROM usage_logs u JOIN accounts a ON a.id = u.account_id AND a.deleted_at IS NULL
                    WHERE a.platform = %s AND u.created_at > CURRENT_TIMESTAMP - INTERVAL '60 seconds'
                      AND u.created_at <= CURRENT_TIMESTAMP''', ('openai',))
                row = await cursor.fetchone()
            return {'rpm': int(row['rpm']), 'sampled_at': row['sampled_at'].timestamp(), 'source': 'postgres'}
        async with self.api() as client:
            # Usage AccountSummary exposes only id/name, not platform. Resolve
            # membership through the existing platform-filtered accounts API.
            account_ids = {account['id'] for account in await client.accounts()}
            seen, count, end = set(), 0, None
            for page in range(1, 101):
                result = await client.request('GET', 'usage', params={
                    'page': page, 'page_size': 100, 'sort_by': 'created_at', 'sort_order': 'desc'})
                if end is None:
                    end = client.now()
                items = result.get('items')
                if not isinstance(items, list):
                    raise UpstreamError('RPM 调用记录响应无效，本轮不计算增长率')
                crossed = False
                for item in items:
                    try:
                        created = datetime.fromisoformat(item['created_at'].replace('Z', '+00:00')).timestamp()
                        key = int(item['id'])
                    except (KeyError, ValueError, TypeError, AttributeError):
                        raise UpstreamError('RPM 调用记录时间无效，本轮不计算增长率') from None
                    if created <= end - 60:
                        crossed = True
                    if key in seen:
                        continue
                    seen.add(key)
                    if end - 60 < created <= end and item.get('account_id') in account_ids:
                        count += 1
                if crossed or len(items) < 100:
                    return {'rpm': count, 'sampled_at': end, 'source': 'api'}
            raise UpstreamError('RPM 调用记录超过 10,000 条扫描上限，请配置 PG；本轮不计算增长率')

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
