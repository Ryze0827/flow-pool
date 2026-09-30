"""Shared Sub2API PostgreSQL access: encrypted settings and read-only snapshots."""
import asyncio
import hmac
import os
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

from .models import PostgresSettings
from .sub2api import Sub2API


def postgres_configured(store):
    # Saving a blank username explicitly disables PG, including the legacy DSN.
    return store.postgres_source() == 'environment' or bool(store.postgres_settings().username)


def resolve_postgres_settings(store, value):
    current = store.postgres_settings()
    result = PostgresSettings(**value.model_dump(exclude={'clear_password'}))
    if not result.username:
        result.password = ''
        return result
    if value.clear_password:
        if value.password:
            raise HTTPException(422, '不能同时填写新密码和清除密码')
        result.password = ''
    elif not result.password and current.password:
        if any(getattr(result, key) != getattr(current, key) for key in ('host', 'port', 'database', 'username')):
            raise HTTPException(400, '连接目标或用户名已变化，请填写密码，或明确选择不使用密码')
        result.password = current.password
    return result


_read_scope = ContextVar('postgres_read_scope', default=None)


@asynccontextmanager
async def postgres_read_scope():
    # 只在一次巡检中复用连接；子任务不能继承父任务正在使用的连接。
    async with AsyncExitStack() as stack:
        token = _read_scope.set({'owner': asyncio.current_task(), 'stack': stack, 'connections': {}, 'verified': set()})
        try:
            yield
        finally:
            _read_scope.reset(token)


class Sub2Postgres:
    def __init__(self, store, config=None):
        self.settings = store.settings()
        self.config = config if config is not None else store.postgres_settings()
        # Preserve all libpq options for legacy deployments until the UI configuration is saved.
        self.dsn = os.environ.get('FLOWPOOL_SUB2API_PG_DSN', '') if config is None and store.postgres_source() == 'environment' else ''

    @asynccontextmanager
    async def snapshot(self):
        value = self.config
        if not self.dsn and (not value.username or not value.host or not value.database):
            raise HTTPException(503, '请先在连接设置中配置 Sub2API PostgreSQL')
        if not self.settings.admin_key or not self.settings.base_url:
            raise HTTPException(400, '请先保存 Sub2API 地址和管理员 API Key，用于核对数据库所属后端')
        kwargs = {} if self.dsn else dict(host=value.host, port=value.port, dbname=value.database,
                                         user=value.username, password=value.password, sslmode=value.sslmode)
        scope = _read_scope.get()
        if scope and scope['owner'] is not asyncio.current_task():
            scope = None
        identity = (self.settings.base_url, self.settings.admin_key, self.dsn, self.config.model_dump_json())
        try:
            async with AsyncExitStack() as local_stack:
                conn = scope['connections'].get(identity) if scope else None
                if conn is None:
                    conn = await psycopg.AsyncConnection.connect(
                        self.dsn, **kwargs, connect_timeout=value.connect_timeout, row_factory=dict_row,
                        autocommit=True,
                        options=f'-c default_transaction_read_only=on -c statement_timeout={value.statement_timeout * 1000}')
                    await (scope['stack'] if scope else local_stack).enter_async_context(conn)
                    if scope:
                        scope['connections'][identity] = conn
                # 每次查询重新开启事务，恢复调度后的水位不能沿用整轮巡检的旧快照。
                async with conn.transaction():
                    async with conn.cursor() as cursor:
                        await cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                        await cursor.execute("SELECT value FROM settings WHERE key = 'admin_api_key'")
                        key = await cursor.fetchone()
                        if not key or not isinstance(key['value'], str) or not hmac.compare_digest(key['value'].encode(), self.settings.admin_key.encode()):
                            raise HTTPException(409, '数据库与配置的 Sub2API 管理员 Key 不匹配，请检查是否为同一后端')
                        await cursor.execute('SELECT pg_is_in_recovery() AS is_replica')
                        if (await cursor.fetchone())['is_replica']:
                            raise HTTPException(409, '请连接 Sub2API PostgreSQL 主库，以便及时复核接口写入结果')
                        if not scope or identity not in scope['verified']:
                            async with Sub2API(self.settings) as client:
                                await client.request('GET', 'settings/admin-api-key')
                            if scope:
                                scope['verified'].add(identity)
                        yield cursor
        except psycopg.Error:
            # Never expose libpq diagnostics, connection strings or SQL parameters.
            raise HTTPException(502, 'PostgreSQL 查询失败或超时，请检查连接、只读权限及 Sub2API 数据库版本') from None

    async def test(self):
        async with self.snapshot() as cursor:
            await cursor.execute('SELECT current_database() AS database, pg_is_in_recovery() AS is_replica')
            info = await cursor.fetchone()
            if info['is_replica']:
                raise HTTPException(409, '请连接 Sub2API PostgreSQL 主库，以便及时复核接口写入结果')
            # Validate the actual columns used by aggregation, without reading user data.
            for query in (
                'SELECT id, username, email, restrict_public_groups, deleted_at FROM users LIMIT 0',
                'SELECT id, name, platform, rate_multiplier, is_exclusive, status, deleted_at FROM groups LIMIT 0',
                'SELECT used_by, value, type FROM redeem_codes LIMIT 0',
                'SELECT user_id, group_id FROM user_allowed_groups LIMIT 0',
                'SELECT user_id, group_id, rate_multiplier FROM user_group_rate_multipliers LIMIT 0',
                'SELECT id, account_id, group_id, model, reasoning_effort, first_token_ms, duration_ms, created_at FROM usage_logs LIMIT 0',
                'SELECT id, name, platform, type, status, schedulable, priority, concurrency, created_at, expires_at, auto_pause_on_expired, rate_limit_reset_at, overload_until, temp_unschedulable_until, error_message, extra, deleted_at FROM accounts LIMIT 0',
                'SELECT account_id, group_id FROM account_groups LIMIT 0',
            ):
                await cursor.execute(query)
        return {'ok': True, 'message': f"已连接数据库 {info['database']}，只读查询及 Sub2API 后端一致性校验通过"}
