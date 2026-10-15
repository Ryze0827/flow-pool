"""Manage promotional GPT groups through the Sub2API admin API."""
import asyncio

from fastapi import HTTPException

from .postgres import Sub2Postgres, postgres_configured
from .sub2api import Sub2API, UpstreamError

WELFARE_GROUPS_SQL = "SELECT id, name, status FROM groups WHERE deleted_at IS NULL AND platform='openai' AND name LIKE '%福利%' ORDER BY id"


class Promotions:
    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()

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
        return {'items': await self.welfare_groups()}

    async def set_welfare_group_status(self, group_id, enabled, owner_id):
        if self.lock.locked():
            raise HTTPException(409, '正在更新福利分组，请稍后重试')
        async with self.lock:
            current = {group['id']: group for group in await self.welfare_groups()}
            if group_id not in current:
                raise HTTPException(409, '该分组已删除、改名或不属于 GPT / OpenAI 平台，请刷新后重试')
            result = await self.update(group_id, current[group_id]['name'], enabled)
        if result['verified']:
            action = '启用' if enabled else '停用'
            self.store.event('welfare_group', f"管理员 #{owner_id} 已{action}夜间福利分组 {result['name']}（#{group_id}）")
        return result

    async def update(self, group_id, name, enabled):
        status = 'active' if enabled else 'inactive'
        error = ''
        async with Sub2API(self.store.settings()) as client:
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
