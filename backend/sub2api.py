import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

SAMPLE_WINDOW_SECONDS = 300


class UpstreamError(Exception):
    def __init__(self, message, status=502, uncertain=False):
        self.message = message
        self.status = status
        self.uncertain = uncertain
        super().__init__(message)


def image_only_account(account):
    groups = account.get('groups') or []
    if not groups or any(group.get('name', '').strip() != '生图' for group in groups):
        return False
    group_ids = account.get('group_ids')
    return group_ids is None or set(group_ids) == {group['id'] for group in groups}


def public_account(account):
    keys = ('id', 'name', 'platform', 'type', 'status', 'schedulable', 'priority', 'concurrency', 'group_ids', 'created_at', 'expires_at', 'auto_pause_on_expired',
            'rate_limit_reset_at', 'overload_until', 'temp_unschedulable_until')
    result = {key: account.get(key) for key in keys}
    result['groups'] = [{'id': group['id'], 'name': group['name']} for group in account.get('groups', []) or []]
    result['image_only'] = image_only_account(account)
    return result


def unavailable_reason(account, now):
    if account.get('status') != 'active':
        return '上游状态非 active'
    if account.get('schedulable') is not True:
        return '调度开关已关闭'
    try:
        def timestamp(value):
            return float(value) if isinstance(value, (int, float)) else datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        if account.get('auto_pause_on_expired', True) and account.get('expires_at') and timestamp(account['expires_at']) <= now:
            return '账号已到期'
        for key, label in (('rate_limit_reset_at', '限流中'), ('overload_until', '过载中'), ('temp_unschedulable_until', '临时禁调中')):
            if account.get(key) and timestamp(account[key]) > now:
                return label
        if account.get('type') == 'apikey':
            for prefix, label in (('quota', '总配额'), ('quota_daily', '日配额'), ('quota_weekly', '周配额')):
                limit = account.get(prefix + '_limit') or 0
                used = account.get(prefix + '_used') or 0
                reset = account.get(prefix + '_reset_at')
                if limit > 0 and used >= limit and not (reset and timestamp(reset) <= now):
                    return label + '已用尽'
    except (ValueError, TypeError, OverflowError):
        return '无法解析上游可用状态'
    return ''


class Sub2API:
    def __init__(self, settings):
        if not settings.base_url or not settings.admin_key:
            raise UpstreamError('请先配置 Sub2API 地址和管理员 API Key', 400)
        self.http = httpx.AsyncClient(base_url=settings.base_url + '/api/v1/admin/', headers={'X-API-Key': settings.admin_key}, timeout=30, follow_redirects=False, trust_env=False)
        self.clock_offset = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.http.aclose()

    def now(self):
        return time.time() + self.clock_offset

    async def request(self, method, path, **kwargs):
        try:
            response = await self.http.request(method, path, **kwargs)
        except httpx.RequestError:
            raise UpstreamError('无法连接 Sub2API 或请求超时，请检查地址和网络', uncertain=method != 'GET') from None
        if response.headers.get('date'):
            try:
                self.clock_offset = parsedate_to_datetime(response.headers['date']).timestamp() - time.time()
            except (ValueError, TypeError):
                pass
        if response.status_code >= 400:
            # 不透传上游响应体，避免账号凭据出现在页面和操作日志中。
            reason = {401: '管理员 Key 无效', 403: '管理员 Key 没有权限', 404: '接口或账号不存在', 409: '数据冲突或混合渠道风险未确认', 429: '请求过于频繁'}.get(response.status_code, '请检查参数及 Sub2API 服务日志')
            raise UpstreamError(f'Sub2API 返回 {response.status_code}：{reason}', uncertain=response.status_code >= 500 and method != 'GET')
        try:
            body = response.json()
        except ValueError:
            raise UpstreamError('Sub2API 返回了非 JSON 响应，请检查服务地址', uncertain=method != 'GET') from None
        if not isinstance(body, dict) or body.get('code') != 0 or 'data' not in body:
            raise UpstreamError('Sub2API 响应格式不符合管理员 API 约定', uncertain=method != 'GET')
        return body['data']

    async def all(self, path, **filters):
        result = []
        for page in range(1, 1001):
            data = await self.request('GET', path, params={'page': page, 'page_size': 100, **filters})
            items = data.get('items') or []
            result.extend(items)
            if not items or page >= data.get('pages', float('inf')) or len(items) < 100:
                return result
        raise UpstreamError('分页数量超过安全上限，请缩小筛选范围')

    async def accounts(self, **filters):
        requested_type = filters.get('type')
        accounts = await self.all('accounts', platform='openai', **filters)
        # Sub2API 当前版本支持 type 查询参数；这里再做一次本地过滤，
        # 避免旧版本或代理层忽略参数时把其它认证类型带回页面。
        return [
            item for item in accounts
            if item.get('platform') == 'openai'
            and (not requested_type or item.get('type') == requested_type)
        ]

    async def account(self, account_id):
        account = await self.request('GET', f'accounts/{account_id}')
        if account.get('platform') != 'openai':
            raise UpstreamError('仅支持 GPT / OpenAI 平台账号', 400)
        return account

    async def metadata(self):
        groups = await self.all('groups', platform='openai')
        proxies = await self.request('GET', 'proxies/all')
        return {'groups': [{'id': g['id'], 'name': g['name'], 'status': g['status']} for g in groups if g.get('platform') == 'openai' and g.get('status') == 'active'], 'proxies': [{'id': p['id'], 'name': p['name']} for p in proxies]}

    async def cooldown_groups(self, image_only=False):
        groups = [group for group in await self.all('groups', platform='openai')
                  if group.get('platform') == 'openai' and group.get('status') == 'active']
        if image_only:
            groups = [group for group in groups if group.get('name', '').strip() == '生图']
            if len(groups) != 1:
                raise UpstreamError('需存在唯一且正常的 GPT「生图」分组，本次不切换分组', 409)
        elif not groups:
            raise UpstreamError('没有正常状态的 GPT 分组，保留恢复任务等待下轮重试', 409)
        return groups

    async def set_groups(self, account_id, groups):
        group_ids = sorted({group['id'] for group in groups})
        remote = await self.account(account_id)
        if sorted(remote.get('group_ids') or []) != group_ids:
            await self.request('PUT', f'accounts/{account_id}', json={'group_ids': group_ids})
            remote = await self.account(account_id)
        if sorted(remote.get('group_ids') or []) != group_ids:
            raise UpstreamError('上游分组更新尚未确认，保留任务等待下轮重试')
        return remote

    async def usage(self, page=1, page_size=50):
        data = await self.request('GET', 'usage', params={
            'page': page,
            'page_size': page_size,
            'sort_by': 'created_at',
            'sort_order': 'desc',
        })
        records = []
        for item in data.get('items') or []:
            account = item.get('account') or {}
            group = item.get('group') or {}
            records.append({
                'id': item.get('id'),
                'account_id': item.get('account_id'),
                'account_name': account.get('name') or f"#{item.get('account_id')}",
                'model': item.get('model'),
                'reasoning_effort': item.get('reasoning_effort'),
                'group_name': group.get('name') or (f"#{item['group_id']}" if item.get('group_id') else None),
                'first_token_ms': item.get('first_token_ms'),
                'duration_ms': item.get('duration_ms'),
                'created_at': item.get('created_at'),
            })
        return {
            'items': records,
            'total': data.get('total', 0),
            'page': data.get('page', page),
            'page_size': data.get('page_size', page_size),
            'pages': data.get('pages', 1),
        }

    async def set_schedulable(self, account_id, value):
        return await self.request('POST', f'accounts/{account_id}/schedulable', json={'schedulable': value})

    async def watermark(self, account_id):
        data = await self.request('GET', 'usage', params={'account_id': account_id, 'page': 1, 'page_size': 1, 'sort_by': 'id', 'sort_order': 'desc'})
        items = data.get('items') or []
        return max((int(item['id']) for item in items), default=0)

    async def samples(self, account_id, count, epoch=None, watermark=0):
        result = []
        now = self.now()
        cutoff = now - SAMPLE_WINDOW_SECONDS
        start = max(cutoff, epoch or cutoff)
        # created_at 是完成时间；扣除 duration_ms 才能排除恢复前已在途的请求。
        for page in range(1, 21):
            params = {'account_id': account_id, 'page': page, 'page_size': 100, 'sort_by': 'created_at', 'sort_order': 'desc'}
            params.update(start_date=datetime.fromtimestamp(start, timezone.utc).strftime('%Y-%m-%d'), timezone='UTC')
            data = await self.request('GET', 'usage', params=params)
            items = data.get('items') or []
            crossed = False
            for item in items:
                try:
                    created = datetime.fromisoformat(item['created_at'].replace('Z', '+00:00')).timestamp()
                    duration = item.get('duration_ms')
                    ttft = item.get('first_token_ms')
                    if created <= start:
                        crossed = True
                    if created <= cutoff or created > now:
                        continue
                    if int(item['id']) <= watermark or ttft is None or ttft < 0:
                        continue
                    if epoch and (duration is None or duration < 0 or created - duration / 1000 <= epoch):
                        continue
                    result.append({'id': item['id'], 'first_token_ms': ttft, 'created_at': item['created_at']})
                    if len(result) == count:
                        return result
                except (KeyError, ValueError, TypeError):
                    continue
            if crossed or not items or len(items) < 100 or page >= data.get('pages', float('inf')):
                return result
        raise UpstreamError('调用记录分页超过 2000 条仍不足有效样本，本轮不做调度判断')
