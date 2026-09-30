import base64
import json
import re
import time
import uuid
from datetime import datetime

from pydantic import ValidationError

from .models import account_guarded, ImportOptions, ReloginImport
from .model_catalog import OPENAI_MODELS
from .sub2api import UpstreamError, public_account

POOL_NAMES = {'priority': '高权重组', 'risk': '风控组', 'third_party': '三方账号组'}


class ReloginCancelled(UpstreamError):
    pass


def recovery_material(value, options):
    secret = value.totp_secret.get_secret_value().strip()
    # 六位动态验证码不能用于未来登录，只接受可复用密钥或未启用 2FA。
    if re.fullmatch(r'\d{6}', secret):
        return None
    return {'email': value.email, 'password': value.password.get_secret_value(), 'totp_secret': secret,
            'workspace_id': value.workspace_id, 'provider': value.provider, 'options': options.model_dump()}


def recovery_from_note(note):
    if not isinstance(note, str):
        return None
    for line in note.splitlines():
        parts = line.strip().split('----')
        if len(parts) != 3 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', parts[0]) or not parts[1]:
            continue
        secret = parts[2].strip()
        if re.fullmatch(r'\d{6}', secret):
            return None
        return {'email': parts[0], 'password': parts[1], 'totp_secret': secret,
                'workspace_id': '', 'provider': 'session_studio'}
    return None


def token_claims(token):
    # 仅提取导入元信息，不将未验证 JWT 用于鉴权。
    try:
        part = token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
    except (ValueError, IndexError, TypeError):
        return {}


def normalize(payload, options, start_index=1):
    if isinstance(payload, dict):
        entries = payload.get('accounts', payload.get('tokens', payload))
        if isinstance(entries, dict):
            entries = [payload]
    else:
        entries = payload
    if not isinstance(entries, list) or not entries or len(entries) > 500:
        raise UpstreamError('JSON 应为单个账号、账号数组或包含 accounts 数组的对象（最多 500 条）', 400)
    result = []
    names = set()
    for index, entry in enumerate(entries, start_index):
        if not isinstance(entry, dict):
            raise UpstreamError(f'第 {index} 条账号不是对象', 400)
        if entry.get('platform', 'openai') not in {'openai', 'gpt'}:
            raise UpstreamError(f'第 {index} 条不是 GPT / OpenAI 账号', 400)
        source = entry.get('credentials') or entry.get('tokens') or entry
        if not isinstance(source, dict):
            raise UpstreamError(f'第 {index} 条 credentials / tokens 格式不正确', 400)
        credentials = dict(source) if entry.get('credentials') else {key: source[key] for key in ('access_token', 'refresh_token', 'id_token', 'api_key', 'base_url', 'account_id', 'email', 'expires_at', 'chatgpt_account_id', 'chatgpt_user_id', 'organization_id', 'model_mapping') if key in source}
        if options.model_whitelist or options.model_mappings:
            credentials['model_mapping'] = {model: model for model in options.model_whitelist}
            # 与 Sub2API 的组合模式一致，同名冲突以显式映射为准。
            credentials['model_mapping'].update({item.source: item.target for item in options.model_mappings})
        if entry.get('OPENAI_API_KEY'):
            credentials['api_key'] = entry['OPENAI_API_KEY']
        kind = 'apikey' if credentials.get('api_key') else 'oauth'
        if entry.get('type') in {'oauth', 'apikey', 'upstream'}:
            kind = entry['type']
        required = ('api_key',) if kind in {'apikey', 'upstream'} else ('access_token', 'refresh_token')
        if not any(isinstance(credentials.get(key), str) and credentials[key].strip() for key in required):
            raise UpstreamError(f'第 {index} 条缺少 access_token / refresh_token 或 api_key', 400)
        claims = token_claims(credentials.get('id_token') or credentials.get('access_token', ''))
        auth = claims.get('https://api.openai.com/auth', {})
        email = entry.get('email') or credentials.get('email') or claims.get('email') or ''
        identity = entry.get('id') or credentials.get('chatgpt_account_id') or credentials.get('account_id') or auth.get('chatgpt_account_id') or index
        if credentials.get('account_id') and not credentials.get('chatgpt_account_id'):
            credentials['chatgpt_account_id'] = credentials['account_id']
        if auth.get('chatgpt_account_id'):
            credentials.setdefault('chatgpt_account_id', auth['chatgpt_account_id'])
        if email:
            credentials['email'] = email
        name = options.name_template.format(email=email or identity, id=identity, index=index, workspace=entry.get('workspace') or identity)
        if name in names:
            raise UpstreamError(f'名称模板产生重复名称「{name}」，请加入 {{index}} 或 {{id}}', 400)
        if len(name) > 120:
            raise UpstreamError(f'第 {index} 条账号名称超过 120 字符', 400)
        names.add(name)
        account = options.model_dump(exclude={'pool', 'name_template', 'duplicate', 'model_mappings', 'model_whitelist'})
        account.update(name=name, platform='openai', type=kind, credentials=credentials)
        expires = entry.get('expires_at')
        if expires:
            try:
                account['expires_at'] = int(expires) if isinstance(expires, (int, float)) or str(expires).isdigit() else int(datetime.fromisoformat(expires.replace('Z', '+00:00')).timestamp())
            except (ValueError, TypeError):
                raise UpstreamError(f'第 {index} 条 expires_at 格式不正确', 400) from None
        result.append({'index': index, 'name': name, 'email': email, 'payload': account, 'status': 'pending', 'account_id': None, 'message': '', 'source': 'json'})
    return result


def public_batch(batch, store=None, accounts=None):
    result = {'id': batch['id'], 'created_at': batch['created_at'], 'pool': batch['options']['pool'],
            'options': batch['options'],
            'items': [{key: item.get(key) for key in ('index', 'name', 'email', 'status', 'account_id', 'message', 'health', 'source')} for item in batch['items']]}
    if store:
        for item in result['items']:
            account_id = item.get('account_id')
            account = accounts.get(account_id) if accounts is not None else store.account(account_id) if account_id else None
            if account and account.get('post_import_batch_id') == batch['id']:
                health = account.get('post_import_health') or {}
                if health.get('checked_at', 0) >= (item.get('health') or {}).get('checked_at', 0) and health:
                    item['health'] = health
                item['observation'] = {'until': account.get('post_import_until'),
                                       'error': account.get('error'),
                                       'relogin_count': account.get('relogin_count', 0),
                                       'relogin_failures': account.get('relogin_failures', 0)}
    return result


class Importer:
    def __init__(self, store, scheduler):
        self.store = store
        self.scheduler = scheduler

    def saved_relogin(self, item):
        # A pending retry may contain newer credentials than account_recovery.
        # Resolve only this item's recovery or its bound account ID, never an
        # arbitrary email supplied by the browser. Nothing secret is returned to UI.
        recovery = item.get('recovery') or (self.store.recovery(item['account_id']) if item.get('account_id') else None)
        if not isinstance(recovery, dict) or 'totp_secret' not in recovery:
            return None
        try:
            value = ReloginImport(**recovery)
        except (ValidationError, TypeError):
            return None
        if item.get('email') and value.email.casefold() != item['email'].casefold():
            return None
        if re.fullmatch(r'\d{6}', value.totp_secret.get_secret_value().strip()):
            return None
        return value

    def ensure_pool_available(self, account_id, pool):
        local = self.store.account(account_id)
        if local and local['pool'] != pool:
            raise UpstreamError(f"账号已在{POOL_NAMES[local['pool']]}，不能加入{POOL_NAMES[pool]}", 409)

    async def validate_options(self, client, options):
        if not options.group_ids:
            raise UpstreamError('至少选择一个 GPT 上游分组后再导入', 400)
        metadata = await client.metadata()
        if not set(options.group_ids) <= {g['id'] for g in metadata['groups']}:
            raise UpstreamError('所选分组不属于 GPT 平台或已不存在', 400)
        if options.proxy_id and options.proxy_id not in {p['id'] for p in metadata['proxies']}:
            raise UpstreamError('所选代理已不存在', 400)

    def create_preview(self, request, recovery=None, source='json'):
        batch = {'id': str(uuid.uuid4()), 'created_at': time.time(), 'options': request.options.model_dump(), 'items': normalize(request.payload, request.options)}
        for item in batch['items']:
            item['source'] = source
            if recovery:
                item['recovery'] = recovery
        self.store.save_batch(batch)
        return public_batch(batch)

    def login_batch(self, batch_id, options):
        batch = self.store.batch(batch_id)
        if not batch:
            raise UpstreamError('待确认批次不存在，请重新开始导入', 409)
        if ImportOptions(**batch['options']).model_dump() != options.model_dump() or any(item['status'] != 'pending' for item in batch['items']):
            raise UpstreamError('批次已推送或参数发生变化，请重新开始导入', 409)
        if len(batch['items']) >= 500:
            raise UpstreamError('单批最多 500 个账号', 400)
        return batch

    def append_login_preview(self, batch_id, request, recovery=None):
        batch = self.login_batch(batch_id, request.options)
        items = normalize(request.payload, request.options, start_index=len(batch['items']) + 1)
        names = {item['name'] for item in batch['items']}
        emails = {item['email'].lower() for item in batch['items'] if item['email']}
        if any(item['name'] in names or (item['email'] and item['email'].lower() in emails) for item in items):
            raise UpstreamError('批次内名称或邮箱重复，请调整名称模板或去除重复账号', 409)
        if len(batch['items']) + len(items) > 500:
            raise UpstreamError('单批最多 500 个账号', 400)
        batch['items'].extend(items)
        for item in items:
            item['source'] = 'login'
            if recovery:
                item['recovery'] = recovery
        self.store.save_batch(batch)
        return public_batch(batch)

    async def preview(self, client, request):
        await self.validate_options(client, request.options)
        return self.create_preview(request)

    async def relogin_target(self, client, item, email, pool):
        if item.get('email') and item['email'].casefold() != email.casefold():
            raise UpstreamError('登录邮箱必须与所选批次账号一致', 400)
        if item.get('account_id'):
            remote = await client.account(item['account_id'])
        else:
            existing = await client.accounts()
            matches = [account for account in existing if account['name'] == item['name'] or ((account.get('credentials') or {}).get('email') or '').casefold() == email.casefold()]
            if len(matches) > 1:
                raise UpstreamError('发现多个同名 / 同邮箱账号，请先消除重复', 409)
            remote = matches[0] if matches else None
        if remote:
            self.ensure_pool_available(remote['id'], pool)
            if remote.get('platform') != 'openai' or remote.get('type') != 'oauth':
                raise UpstreamError('仅支持 GPT OAuth 账号重新登录', 400)
            remote_email = remote.get('credentials', {}).get('email')
            if remote_email and remote_email.casefold() != email.casefold():
                raise UpstreamError('上游账号邮箱与登录邮箱不一致，未执行重新登录', 409)
        return remote

    async def commit(self, client, batch, item_index=None):
        from .batch_health import POST_IMPORT_SECONDS
        existing = await client.accounts()
        options = ImportOptions(**batch['options'])
        completed_ids = []
        for item in batch['items']:
            if item_index is not None and item['index'] != item_index:
                continue
            if item['status'] in {'done', 'skipped'}:
                continue
            item.pop('health', None)
            if item.get('payload') and recovery_from_note(item['payload'].get('notes')):
                item['payload']['notes'] = '\n'.join(line for line in item['payload']['notes'].splitlines() if not recovery_from_note(line))
            try:
                if item['status'] in {'creating', 'uncertain'}:
                    # 崩溃或超时后不盲目重发创建；同名唯一账号可用于恢复进度。
                    matches = [a for a in existing if a['name'] == item['name']]
                    if len(matches) != 1:
                        item.update(status='uncertain', message='创建结果不确定，请先在 Sub2API 核对同名账号，再重试此批次')
                        self.store.save_batch(batch)
                        continue
                    item['account_id'] = matches[0]['id']
                    self.ensure_pool_available(item['account_id'], options.pool)
                if not item['account_id']:
                    matches = [a for a in existing if a['name'] == item['name'] or (item['email'] and a.get('credentials', {}).get('email') == item['email'])]
                    if len(matches) > 1:
                        raise UpstreamError('发现多个同名或同邮箱账号，请先消除重复', 400)
                    if matches:
                        self.ensure_pool_available(matches[0]['id'], options.pool)
                        if options.duplicate == 'skip' and not item.get('relogin'):
                            item.update(status='skipped', message='已存在同名 / 同邮箱账号，已跳过')
                            item.pop('payload', None)
                            item.pop('recovery', None)
                            self.store.save_batch(batch)
                            continue
                        item['account_id'] = matches[0]['id']
                        item['status'] = 'updating'
                        self.store.save_batch(batch)
                        await client.request('PUT', f"accounts/{item['account_id']}", json=item['payload'])
                    else:
                        item['status'] = 'creating'
                        self.store.save_batch(batch)
                        remote = await client.request('POST', 'accounts', json=item['payload'], headers={'Idempotency-Key': f"scheduler-{batch['id']}-{item['index']}"})
                        item['account_id'] = remote['id']
                        existing.append(remote)
                elif item['status'] == 'updating':
                    await client.request('PUT', f"accounts/{item['account_id']}", json=item['payload'])
                item['status'] = 'created'
                self.store.save_batch(batch)
                remote = await client.account(item['account_id'])
                self.ensure_pool_available(remote['id'], options.pool)
                # 先同步账号实时模型，再读取凭据，保留同步接口写入的模型元数据。
                upstream_models = await client.sync_upstream_models(remote['id'])
                remote = await client.account(remote['id'])
                models = list(dict.fromkeys([*OPENAI_MODELS, *upstream_models, *options.model_whitelist]))
                if len(models) > 200:
                    raise UpstreamError('内置模型与上游模型合计超过 200 项，无法写入白名单', 400)
                mapping = {model: model for model in models}
                mapping.update((remote.get('credentials') or {}).get('model_mapping') or {})
                mapping.update({entry.source: entry.target for entry in options.model_mappings})
                credentials = {**(remote.get('credentials') or {}), 'model_mapping': mapping}
                await client.request('PUT', f"accounts/{remote['id']}", json={'credentials': credentials})
                remote = await client.account(remote['id'])
                if (remote.get('credentials') or {}).get('model_mapping') != mapping:
                    raise UpstreamError('账号模型白名单写入尚未确认，请重试推送')
                if item.get('relogin') and remote.get('status') == 'error':
                    # 新凭据已成功写入后再清除旧错误及上游令牌缓存，支持 401 账号恢复。
                    await client.request('POST', f"accounts/{remote['id']}/clear-error")
                    remote = await client.account(remote['id'])
                if remote.get('status') != 'active':
                    raise UpstreamError('账号在上游不是 active，请先在 Sub2API 处理状态', 400)
                if remote.get('auto_pause_on_expired', True) and remote.get('expires_at'):
                    if datetime.fromisoformat(remote['expires_at'].replace('Z', '+00:00')).timestamp() <= client.now():
                        raise UpstreamError('账号已到期，请先在 Sub2API 处理有效期', 400)
                self.store.enroll(public_account(remote), options.pool)
                recovery = item.get('recovery')
                if recovery:
                    self.store.save_recovery(remote['id'], recovery)
                    self.store.update(remote['id'], relogin_error='', relogin_last_at=None, relogin_count=0, relogin_failures=0)
                else:
                    # JSON 原位覆盖或不可复用的验证码登录不继承旧账密恢复能力。
                    self.store.clear_recovery(remote['id'])
                rule = self.store.settings().rule
                reason = '推送入池，开始恢复观察' if account_guarded(self.store.account(remote['id']), rule) else '推送入池，开启调度'
                await self.scheduler.resume(client, self.store.account(remote['id']), rule, reason)
                self.store.update(remote['id'], post_import_batch_id=batch['id'], post_import_until=time.time() + POST_IMPORT_SECONDS, post_import_health={})
                completed_ids.append(remote['id'])
                item.update(status='done', message='已推送入池并开启调度')
                # 成功后清除暂存凭据，批次仍保留可审计的结果。
                item.pop('payload', None)
                item.pop('recovery', None)
                self.store.event('import', f"{item['name']} 已推送入池", remote['id'])
            except UpstreamError as error:
                old_status = item['status']
                item.update(status='uncertain' if error.uncertain and old_status == 'creating' else 'updating' if old_status == 'updating' else 'failed', message=error.message)
            self.store.save_batch(batch)
        # 整批可能耗时较长：本次成功账号统一从本次推送结束开始观察。
        until = time.time() + POST_IMPORT_SECONDS
        for account_id in completed_ids:
            current = self.store.account(account_id)
            values = {'post_import_until': until}
            if current['state'] == 'probation':
                values['probation_until'] = max(until, current.get('probation_until') or 0)
            self.store.update(account_id, **values)
            self.store.event('import_observation', '推送成功，开始 5 分钟入池观察，异常时自动重登', account_id)
        if completed_ids:
            self.scheduler.wakeup.set()
        return public_batch(batch, self.store)

    async def auto_relogin(self, account, recovery, account_login):
        from .models import LoginImport
        from .batch_health import authentication_failed, post_import_observing, observation_error, inspect_item
        from .scheduler import expiry_timestamp
        from .sub2api import Sub2API
        from . import updater

        connection = self.store.settings()
        account_id = account['id']
        options = ImportOptions(**recovery.get('options', {}))
        observing = post_import_observing(account)

        def current_account():
            settings = self.store.settings()
            current = self.store.account(account_id)
            if settings.base_url != connection.base_url or settings.admin_key != connection.admin_key:
                raise ReloginCancelled('登录期间连接配置变化，取消自动推送')
            if not settings.rule.enabled or updater.locked(self.store.directory) or (self.store.directory / 'upgrade-deploying').exists():
                raise ReloginCancelled('自动守护已关闭或正在升级，取消自动推送')
            if not current or current['pool'] != account['pool'] or (not observing and current['pool'] != 'third_party') or self.store.recovery(account_id) != recovery:
                raise ReloginCancelled('账号已移出号池或账密已更新，取消自动推送')
            if observing and (current.get('post_import_until') != account.get('post_import_until') or current.get('post_import_batch_id') != account.get('post_import_batch_id')):
                raise ReloginCancelled('入池观察已取消或账号已重新推送，取消自动推送')
            if current['state'] in {'manual', 'pausing', 'resuming'} or any(current[key] != account[key] for key in ('resume_at', 'epoch', 'watermark', 'cooldown_mode')):
                raise ReloginCancelled('账号调度状态发生变化，取消自动推送')
            return current

        def validate_remote(remote):
            if remote.get('platform') != 'openai' or remote.get('type') != 'oauth':
                raise UpstreamError('自动重登仅支持 GPT OAuth 账号')
            if not (observation_error(remote, time.time()) if observing else authentication_failed(remote)):
                raise ReloginCancelled('上游已不处于认证失效状态，取消自动推送')
            if remote.get('auto_pause_on_expired', True) and remote.get('expires_at') and expiry_timestamp(remote['expires_at']) <= time.time():
                raise ReloginCancelled('账号已到期，取消自动推送')
            if remote.get('schedulable') is False and account['state'] != 'cooldown' and not observing:
                raise ReloginCancelled('账号已停止调度，取消自动推送')
            email = (remote.get('credentials') or {}).get('email', '')
            if not email or email.casefold() != recovery['email'].casefold():
                raise UpstreamError('上游邮箱与保存账密不一致，取消自动推送')

        async with self.scheduler.lock:
            current_account()
            async with Sub2API(connection) as client:
                before = await client.account(account_id)
                validate_remote(before)
        credentials = before.get('credentials') or {}
        workspace = credentials.get('chatgpt_account_id') or credentials.get('account_id') or recovery.get('workspace_id', '')
        request = LoginImport(email=recovery['email'], password=recovery['password'],
                              totp_secret=recovery.get('totp_secret', ''), workspace_id=workspace,
                              provider=recovery.get('provider', 'session_studio'), options=options)
        # 登录不占用调度锁，其他账号巡检、手动暂停和移出仍可正常执行。
        result = await account_login.run(request)
        if not isinstance(result, dict) or not isinstance(result.get('payload'), (dict, list)):
            raise UpstreamError('自动重登需要额外验证，请手动登录')
        entries = normalize(result['payload'], options)
        if len(entries) != 1 or entries[0]['email'].casefold() != recovery['email'].casefold():
            raise UpstreamError('自动重登返回的账号不匹配，取消推送')
        fresh = entries[0]['payload']['credentials']
        if workspace and (fresh.get('chatgpt_account_id') or fresh.get('account_id')) != workspace:
            raise UpstreamError('自动重登工作空间不匹配，取消推送')
        async with self.scheduler.lock:
            current = current_account()
            async with Sub2API(connection) as client:
                remote = await client.account(account_id)
                validate_remote(remote)
                if remote.get('credentials') != before.get('credentials') or remote.get('schedulable') != before.get('schedulable'):
                    raise ReloginCancelled('登录期间上游凭据或调度开关变化，取消自动推送')
                # 只替换认证字段，不回写历史分组、优先级、模型映射或其它上号参数。
                auth_keys = ('access_token', 'refresh_token', 'id_token', 'expires_at', 'email', 'account_id',
                             'chatgpt_account_id', 'chatgpt_user_id', 'organization_id', 'client_id')
                updated_credentials = {**(remote.get('credentials') or {}), **{key: fresh[key] for key in auth_keys if key in fresh}}
                await client.request('PUT', f'accounts/{account_id}', json={'credentials': updated_credentials})
                await client.request('POST', f'accounts/{account_id}/clear-error')
                refreshed = await client.account(account_id)
                if refreshed.get('status') != 'active':
                    raise UpstreamError('新凭据已写入，但上游状态恢复未确认')
                # 生图冷却保留截止时间和分组；正常账号重新建立首字样本边界。
                if current['state'] == 'cooldown':
                    if current.get('cooldown_mode') == 'image':
                        await client.set_schedulable(account_id, True)
                        refreshed = await client.account(account_id)
                        if refreshed.get('schedulable') is not True:
                            raise UpstreamError('新凭据已写入，但生图调度尚未恢复')
                    self.store.update(account_id, remote=public_account(refreshed), error=None,
                                      sample=[], slow_count=0, breach_times=[], breach_sample='', last_breach_at=None, last_checked=None)
                else:
                    watermark = await client.watermark(account_id)
                    await client.set_schedulable(account_id, True)
                    refreshed = await client.account(account_id)
                    if refreshed.get('schedulable') is not True:
                        raise UpstreamError('新凭据已写入，但调度尚未恢复')
                    self.store.update(account_id, remote=public_account(refreshed))
                    self.scheduler.complete_resume(client, self.store.account(account_id), self.store.settings().rule, watermark, '凭据失效后自动重登，已重新入池')
                if observing:
                    health = inspect_item({'account_id': account_id}, refreshed, self.store.account(account_id), client.now())
                    self.store.update(account_id, post_import_health=health)
                self.store.update(account_id, relogin_error='', relogin_last_at=time.time(), relogin_failures=0)
                self.store.event('auto_relogin', '账号已自动重新登录并恢复调度，保留当前分组', account_id)
                self.store.inspection(account_id, self.store.account(account_id)['state'], 0, 0, 0, '自动重登成功', '新凭据已写入原账号，保留当前分组及冷却任务')
