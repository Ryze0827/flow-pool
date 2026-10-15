"""Batch membership changes preserve unrelated groups and verify upstream writes."""
import asyncio

from fastapi import HTTPException

from .sub2api import Sub2API, UpstreamError, image_only_account, public_account


class AdvancedGroups:
    def __init__(self, store):
        self.store = store

    async def groups(self, client):
        return [{'id': group['id'], 'name': group['name'], 'status': group.get('status', '')}
                for group in await client.all('groups', platform='openai')
                if group.get('platform') == 'openai' and not group.get('deleted_at')]

    async def config(self):
        async with Sub2API(self.store.settings()) as client:
            groups = await self.groups(client)
        return {'group_ids': self.store.settings().advanced_group_ids, 'groups': groups}

    async def save_config(self, value):
        current = self.store.settings()
        async with Sub2API(current) as client:
            groups = await self.groups(client)
        known = {group['id'] for group in groups}
        if set(value.group_ids) - known - set(current.advanced_group_ids):
            raise HTTPException(409, '高级分组包含不存在或不属于 GPT / OpenAI 的分组，请刷新后重试')
        current.advanced_group_ids = value.group_ids
        self.store.save_settings(current)
        self.store.event('advanced_groups_config', f'已配置高级分组 ID：{value.group_ids}')
        return {'group_ids': value.group_ids, 'groups': groups}

    async def apply(self, value):
        settings = self.store.settings()
        group_ids = set(settings.advanced_group_ids)
        if not group_ids:
            raise HTTPException(409, '请先配置 Sub2API 高级分组')
        if group_ids != set(value.group_ids):
            raise HTTPException(409, '高级分组配置已变化，请重新确认操作')
        targets = [account for account in self.store.accounts() if account['pool'] in value.pools]
        if {account['id'] for account in targets} != set(value.account_ids):
            raise HTTPException(409, '所选号池的账号已变化，请刷新后重新确认操作')
        async with Sub2API(settings) as client:
            if value.operation == 'add':
                active = {group['id'] for group in await self.groups(client) if group['status'] == 'active'}
                if group_ids - active:
                    raise HTTPException(409, '高级分组已停用或删除，请调整配置后再移入')
            results = []
            for offset in range(0, len(targets), 4):
                results.extend(await asyncio.gather(*(
                    self.update(client, account, group_ids, value.operation)
                    for account in targets[offset:offset + 4])))
        counts = {status: sum(item['status'] == status for item in results)
                  for status in ('changed', 'unchanged', 'skipped', 'failed')}
        verb = '移入' if value.operation == 'add' else '移出'
        self.store.event('advanced_groups', f"批量{verb}高级分组 {sorted(group_ids)}："
                         f"更新 {counts['changed']} 个，无需修改 {counts['unchanged']} 个，"
                         f"跳过 {counts['skipped']} 个，失败或未确认 {counts['failed']} 个")
        return {'operation': value.operation, 'group_ids': sorted(group_ids), **counts, 'results': results}

    async def update(self, client, account, group_ids, operation):
        result = {'id': account['id'], 'name': account['name'], 'pool': account['pool'],
                  'status': 'failed', 'error': ''}
        try:
            if account['state'] in {'pausing', 'resuming'}:
                return {**result, 'status': 'skipped', 'error': '账号正在切换调度状态，请完成后重试'}
            remote = await client.account(account['id'])
            before = self.memberships(remote)
            after = before | group_ids if operation == 'add' else before - group_ids
            if before == after:
                self.store.update(account['id'], remote=public_account(remote))
                return {**result, 'status': 'unchanged'}
            if account['state'] == 'cooldown' and (
                    account.get('cooldown_mode') == 'image'
                    or (account['pool'] == 'third_party' and image_only_account(remote))):
                return {**result, 'status': 'skipped', 'error': '账号处于仅生图冷却保护，冷却结束后可重试'}
            await client.request('PUT', f"accounts/{account['id']}", json={'group_ids': sorted(after)})
            verified = await client.account(account['id'])
            if self.memberships(verified) != after:
                raise UpstreamError('上游分组修改尚未确认，请刷新后重试', 409)
            self.store.update(account['id'], remote=public_account(verified))
            self.store.event('advanced_groups',
                             f"{'移入' if operation == 'add' else '移出'}高级分组 {sorted(group_ids)}："
                             f'{sorted(before)} → {sorted(after)}', account['id'])
            return {**result, 'status': 'changed'}
        except UpstreamError as error:
            result['error'] = error.message
        except Exception:
            result['error'] = '操作失败或结果未确认，请刷新后重试'
        return result

    @staticmethod
    def memberships(remote):
        ids = remote.get('group_ids')
        if not isinstance(ids, list) or any(type(value) is not int or value <= 0 for value in ids):
            raise UpstreamError('上游账号未返回完整分组 ID，未修改分组', 409)
        return set(ids)
