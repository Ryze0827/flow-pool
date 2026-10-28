"""Preview actual balance consumption and credit a durable refund batch."""
import asyncio
import hashlib
import time
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException

from .postgres import Sub2Postgres
from .sub2api import Sub2API, UpstreamError


class QuotaRefunds:
    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()
        store.db.executescript('''
            CREATE TABLE IF NOT EXISTS quota_refund_batches (
                id TEXT PRIMARY KEY, start_at REAL NOT NULL, end_at REAL NOT NULL,
                payload TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS quota_refund_items (
                batch_id TEXT NOT NULL, user_id INTEGER NOT NULL, amount TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(batch_id, user_id)
            );
            CREATE INDEX IF NOT EXISTS quota_refund_user ON quota_refund_items(user_id, status);
        ''')

    def source(self):
        settings = self.store.settings()
        return hashlib.sha256(f'{settings.base_url}\n{settings.admin_key}'.encode()).hexdigest()

    async def users(self, search):
        async with Sub2Postgres(self.store).snapshot() as cursor:
            term = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            await cursor.execute('''SELECT id, username, email FROM users WHERE deleted_at IS NULL
                AND (username ILIKE %s OR email ILIKE %s OR id::text=%s) ORDER BY id LIMIT 51''',
                                 (term, term, search))
            rows = await cursor.fetchall()
        return {'items': rows[:50], 'has_more': len(rows) > 50}

    async def preview(self, value, owner_id):
        source = self.source()
        async with Sub2Postgres(self.store).snapshot() as cursor:
            if value.user_ids:
                await cursor.execute('SELECT id FROM users WHERE deleted_at IS NULL AND id=ANY(%s)', (value.user_ids,))
                if {row['id'] for row in await cursor.fetchall()} != set(value.user_ids):
                    raise HTTPException(409, '所选用户已删除或不存在，请重新选择')
            selected = ' AND u.id=ANY(%s)' if value.user_ids else ''
            await cursor.execute('''SELECT u.id AS user_id, u.username, u.email,
                    SUM(l.actual_cost) AS amount, COUNT(*) AS requests
                FROM usage_logs l JOIN users u ON u.id=l.user_id
                WHERE u.deleted_at IS NULL AND l.created_at >= %s AND l.created_at < %s
                    AND l.billing_type=0 AND l.actual_cost > 0''' + selected + '''
                GROUP BY u.id, u.username, u.email ORDER BY u.id''',
                                 (value.start_at, value.end_at, *([value.user_ids] if value.user_ids else [])))
            rows = await cursor.fetchall()
        batch_id = uuid4().hex
        payload = {**value.model_dump(mode='json'), 'owner_id': owner_id, 'source': source,
                   'users': {str(row['user_id']): {key: row[key] for key in ('username', 'email', 'requests')} for row in rows}}
        with self.store.db:
            self.store.db.execute('INSERT INTO quota_refund_batches VALUES (?, ?, ?, ?, ?)',
                                  (batch_id, value.start_at.timestamp(), value.end_at.timestamp(), self.store.seal(payload), time.time()))
            self.store.db.executemany('INSERT INTO quota_refund_items(batch_id, user_id, amount) VALUES (?, ?, ?)',
                                     [(batch_id, row['user_id'], str(Decimal(str(row['amount'])))) for row in rows])
        return self.get(batch_id)

    def batch(self, batch_id):
        row = self.store.db.execute('SELECT * FROM quota_refund_batches WHERE id=?', (batch_id,)).fetchone()
        if not row:
            raise HTTPException(404, '返还记录不存在')
        return dict(row), self.store.unseal(row['payload'])

    def get(self, batch_id):
        batch, payload = self.batch(batch_id)
        items = [{**dict(row), **payload['users'][str(row['user_id'])]} for row in self.store.db.execute(
            'SELECT user_id, amount, status, error FROM quota_refund_items WHERE batch_id=? ORDER BY user_id', (batch_id,))]
        counts = {status: sum(item['status'] == status for item in items)
                  for status in ('pending', 'credited', 'failed', 'uncertain', 'skipped')}
        return {'id': batch_id, 'start_at': payload['start_at'], 'end_at': payload['end_at'],
                'user_ids': payload['user_ids'], 'created_at': batch['created_at'], 'owner_id': payload['owner_id'],
                'total_amount': str(sum((Decimal(item['amount']) for item in items), Decimal(0))),
                'credited_amount': str(sum((Decimal(item['amount']) for item in items if item['status'] == 'credited'), Decimal(0))),
                'counts': counts, 'items': items}

    def history(self):
        ids = [row['id'] for row in self.store.db.execute('SELECT id FROM quota_refund_batches ORDER BY created_at DESC LIMIT 20')]
        return {'items': [{key: value for key, value in self.get(batch_id).items() if key != 'items'} for batch_id in ids]}

    def update(self, batch_id, user_id, status, error=''):
        with self.store.db:
            self.store.db.execute('UPDATE quota_refund_items SET status=?, error=? WHERE batch_id=? AND user_id=?',
                                  (status, error, batch_id, user_id))

    async def execute(self, batch_id, owner_id):
        if self.lock.locked():
            raise HTTPException(409, '正在执行额度返还，请稍后查看结果')
        async with self.lock:
            batch, payload = self.batch(batch_id)
            if payload['source'] != self.source():
                raise HTTPException(409, 'Sub2API 连接已变化，请恢复原连接后处理此记录')
            items = [item for item in self.get(batch_id)['items'] if item['status'] in {'pending', 'failed'}]
            if items:
                async with Sub2API(self.store.settings()) as client:
                    for offset in range(0, len(items), 4):
                        await asyncio.gather(*(self.credit(client, batch, payload, item) for item in items[offset:offset + 4]))
            result = self.get(batch_id)
            counts = result['counts']
            self.store.event('quota_refund', f"管理员 #{owner_id} 执行额度返还 {batch_id}："
                             f"成功 {counts['credited']} 人，已返还 {result['credited_amount']}，"
                             f"失败 {counts['failed']} 人，待核对 {counts['uncertain']} 人，跳过 {counts['skipped']} 人",
                             level='warning' if counts['failed'] or counts['uncertain'] else 'info')
            return result

    async def credit(self, client, batch, payload, item):
        batch_id, user_id = batch['id'], item['user_id']
        # 重复预览或改选用户后，重叠时段也不能再次发放给同一用户。
        overlaps = self.store.db.execute('''SELECT b.payload FROM quota_refund_items i
            JOIN quota_refund_batches b ON b.id=i.batch_id
            WHERE i.user_id=? AND i.batch_id!=? AND i.status IN ('credited', 'uncertain')
                AND b.start_at < ? AND b.end_at > ?''', (user_id, batch_id, batch['end_at'], batch['start_at'])).fetchall()
        if any(self.store.unseal(row['payload'])['source'] == payload['source'] for row in overlaps):
            self.update(batch_id, user_id, 'skipped', '该用户有重叠时段已返还或待核对，请选择不重叠的时间段')
            return
        # 发请求前持久化；断线或进程退出后不自动重放结果不明的加款。
        self.update(batch_id, user_id, 'uncertain', '加款结果待核对，请检查 Sub2API 用户余额记录，勿重复返还')
        try:
            await client.request('POST', f'users/{user_id}/balance',
                                 headers={'Idempotency-Key': f'flowpool-refund-{batch_id}-{user_id}'},
                                 json={'balance': float(Decimal(item['amount'])), 'operation': 'add',
                                       'notes': f"额度返还 {payload['start_at']} 至 {payload['end_at']} · {batch_id}"})
        except UpstreamError as error:
            self.update(batch_id, user_id, 'uncertain' if error.uncertain else 'failed', error.message)
            return
        self.update(batch_id, user_id, 'credited')
