"""List GPT groups via SQL-first reads; set OpenAI Fast priority through the Sub2API API and verify it."""
import asyncio

from fastapi import HTTPException

from .postgres import Sub2Postgres, postgres_configured
from .sub2api import Sub2API, UpstreamError

GROUPS_SQL = "SELECT id, name, status, force_openai_fast FROM groups WHERE deleted_at IS NULL AND platform='openai' AND status='active' AND name NOT LIKE '%生图%' ORDER BY id"


def flag(value):
    return value if isinstance(value, bool) else None


class GroupControl:
    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()

    async def gpt_groups(self):
        if postgres_configured(self.store):
            async with Sub2Postgres(self.store).snapshot() as cursor:
                await cursor.execute(GROUPS_SQL)
                groups = await cursor.fetchall()
        else:
            async with Sub2API(self.store.settings()) as client:
                groups = [group for group in await client.all('groups', platform='openai')
                          if group.get('platform') == 'openai' and group.get('status') == 'active' and not group.get('deleted_at')
                          and '生图' not in (group.get('name') or '')]
        return [{'id': group['id'], 'name': group['name'], 'status': group.get('status', ''),
                 'force_openai_fast': flag(group.get('force_openai_fast'))} for group in groups]

    async def groups(self):
        return {'items': await self.gpt_groups()}

    async def set_openai_fast(self, group_ids, enabled, owner_id):
        if self.lock.locked():
            raise HTTPException(409, '正在更新分组，请稍后重试')
        async with self.lock:
            current = {group['id']: group for group in await self.gpt_groups()}
            if any(group_id not in current for group_id in group_ids):
                raise HTTPException(409, '所选分组已停用、删除或不属于 GPT / OpenAI 平台，请刷新后重试')
            results = []
            async with Sub2API(self.store.settings()) as client:
                for offset in range(0, len(group_ids), 4):
                    results.extend(await asyncio.gather(*(
                        self.update(client, group_id, current[group_id]['name'], enabled)
                        for group_id in group_ids[offset:offset + 4])))
        succeeded = sum(result['verified'] for result in results)
        action = '开启' if enabled else '关闭'
        self.store.event('group_openai_fast', f"管理员 #{owner_id} 批量{action} {len(results)} 个 GPT 分组的 OpenAI Fast 强制 priority：确认成功 {succeeded} 条，未确认 {len(results) - succeeded} 条")
        return {'enabled': enabled, 'succeeded': succeeded, 'failed': len(results) - succeeded, 'results': results}

    async def update(self, client, group_id, name, enabled):
        error = ''
        try:
            await client.request('PUT', f'groups/{group_id}', json={'force_openai_fast': enabled})
        except UpstreamError as cause:
            error = cause.message + ('；结果尚未确认，请刷新后重试' if cause.uncertain else '')
        # Sub2API simple mode accepts this PUT but discards the field, so the stored value is authoritative.
        try:
            body = await client.request('GET', f'groups/{group_id}')
        except UpstreamError as cause:
            return {'id': group_id, 'name': name, 'force_openai_fast': None, 'verified': False,
                    'error': error or f'{cause.message}；无法复核写入结果，请刷新后重试'}
        stored = flag(body.get('force_openai_fast')) if isinstance(body, dict) else None
        verified = stored == enabled
        if verified:
            error = ''
        elif not error:
            error = '写入后复核不一致，Sub2API 可能处于简易模式或已被其他管理员修改'
        return {'id': group_id, 'name': name, 'force_openai_fast': stored, 'verified': verified, 'error': error}
