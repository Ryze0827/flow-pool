import asyncio
import contextlib
import fcntl
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .importer import Importer, normalize, public_batch, recovery_from_note, recovery_material
from .batch_health import inspect_item
from .login import AccountLogin
from .models import AdminLogin, Enroll, ImportOptions, LoginImport, MailSettings, Preview, ReloginImport, Settings
from .mailer import Mailer, MailError, validate_mail
from .scheduler import Scheduler
from .store import Store
from .sub2api import Sub2API, UpstreamError, public_account
from .scheduler import expiry_timestamp
from . import updater
from .auth import SESSION_TTL_SECONDS

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('SCHEDULER_DATA_DIR', ROOT / 'data'))
store = None
scheduler = None
importer = None
account_login = None
mailer = None
POOL_NAMES = {'priority': '高权重组', 'risk': '风控组', 'third_party': '三方账号组'}


@asynccontextmanager
async def lifespan(app):
    global store, scheduler, importer, account_login, mailer
    DATA.mkdir(parents=True, exist_ok=True)
    lock_file = (DATA / 'worker.lock').open('w')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('已有调度进程使用此数据库；请勿启动多个 worker') from None
    store = Store(DATA)
    store.bootstrap_admin(os.environ.get('FLOWPOOL_ADMIN_USER', ''), os.environ.get('FLOWPOOL_ADMIN_PASSWORD', ''),
                          reset=os.environ.get('FLOWPOOL_ADMIN_RESET') == '1')
    mailer = Mailer(store)
    scheduler = Scheduler(store, mailer)
    importer = Importer(store, scheduler)
    account_login = AccountLogin()
    scheduler.relogin_handler = lambda account, recovery: importer.auto_relogin(account, recovery, account_login)
    scheduler.relogin_busy = lambda: account_login.lock.locked()
    async def after_deploy(worker):
        # 升级健康检查通过前不发送邮件、不改变远端账号，便于安全恢复数据库。
        while (DATA / 'upgrade-deploying').exists():
            await asyncio.sleep(1)
        await worker.run()
    task = asyncio.create_task(after_deploy(scheduler))
    mail_task = asyncio.create_task(after_deploy(mailer))
    try:
        yield
    finally:
        await scheduler.close()
        await account_login.close()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        mail_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await mail_task
        store.db.close()
        lock_file.close()


app = FastAPI(title='GPT 账号调控', lifespan=lifespan, docs_url=None, redoc_url=None)
allowed_hosts = [item.strip() for item in os.environ.get('FLOWPOOL_ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1],testserver').split(',') if item.strip()]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)


@app.middleware('http')
async def local_security(request: Request, call_next):
    public_api = {'/api/health', '/api/auth/status', '/api/auth/login', '/api/auth/logout'}
    if request.url.path.startswith('/api/') and request.url.path not in public_api:
        if not store or not store.session(request.cookies.get('flowpool_session')):
            return JSONResponse({'detail': '请先登录'}, status_code=401)
    if request.url.path.startswith('/api/') and request.method != 'GET':
        origin = request.headers.get('origin')
        if request.headers.get('x-scheduler-request') != '1' or (origin and urlsplit(origin).netloc != request.headers.get('host')):
            return JSONResponse({'detail': '仅接受本机同源页面操作'}, status_code=403)
        if request.url.path != '/api/upgrade' and (updater.locked(DATA) or (DATA / 'upgrade-deploying').exists()):
            return JSONResponse({'detail': '系统升级中，请等待完成后再操作'}, status_code=409)
        if int(request.headers.get('content-length', '0')) > 8 * 1024 * 1024:
            return JSONResponse({'detail': 'JSON 文件不能超过 8 MB'}, status_code=413)
        body = await request.body()
        if len(body) > 8 * 1024 * 1024:
            return JSONResponse({'detail': 'JSON 文件不能超过 8 MB'}, status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(UpstreamError)
async def upstream_error(request, error):
    return JSONResponse({'detail': error.message}, status_code=error.status)


@app.exception_handler(MailError)
async def mail_error(request, error):
    return JSONResponse({'detail': str(error)}, status_code=400)


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # Pydantic 默认会回显 input，管理员 Key 和上传凭据不可出现在错误响应中。
    issues = ['.'.join(str(p) for p in e['loc'][1:]) + ': ' + e['msg'] for e in error.errors()]
    return JSONResponse({'detail': '；'.join(issues)}, status_code=422)


@app.get('/api/health')
async def health():
    return {'ok': True, 'service': 'gpt-account-scheduler', 'pid': os.getpid(), 'instance': os.environ.get('FLOWPOOL_INSTANCE_ID', '')}


@app.get('/api/auth/status')
async def auth_status(request: Request):
    session = store.session(request.cookies.get('flowpool_session'))
    return {'configured': store.admin_configured(), 'authenticated': bool(session),
            'username': session['username'] if session else ''}


@app.post('/api/auth/login')
async def auth_login(value: AdminLogin, request: Request, response: Response):
    if not store.admin_configured():
        raise HTTPException(503, '管理员尚未配置，请设置 FLOWPOOL_ADMIN_USER 和 FLOWPOOL_ADMIN_PASSWORD 后重启服务')
    if not store.authenticate_admin(value.username, value.password):
        raise HTTPException(401, '管理员账号或密码错误')
    token, expires_at = store.create_session(value.username, SESSION_TTL_SECONDS)
    forwarded_proto = request.headers.get('x-forwarded-proto', request.url.scheme)
    response.set_cookie('flowpool_session', token, max_age=SESSION_TTL_SECONDS, httponly=True,
                        secure=forwarded_proto == 'https', samesite='lax', path='/')
    return {'ok': True, 'username': value.username, 'expires_at': expires_at}


@app.post('/api/auth/logout')
async def auth_logout(request: Request, response: Response):
    store.delete_session(request.cookies.get('flowpool_session'))
    response.delete_cookie('flowpool_session', path='/')
    return {'ok': True}


@app.get('/api/upgrade')
async def upgrade_status():
    return await asyncio.to_thread(updater.status, ROOT, DATA)


@app.post('/api/upgrade', status_code=202)
async def upgrade_start():
    if account_login.lock.locked() or (scheduler.relogin_task and not scheduler.relogin_task.done()):
        raise HTTPException(409, '账号登录正在进行，请完成后再升级')
    try:
        return await asyncio.to_thread(updater.start, ROOT, DATA)
    except updater.UpgradeError as error:
        raise HTTPException(409, str(error)) from None


def resolve_mail_settings(value):
    current = store.mail_settings()
    if not value.smtp_password and value.smtp_host == current.smtp_host and value.smtp_username == current.smtp_username:
        value.smtp_password = current.smtp_password
    return value


@app.get('/api/mail/settings')
async def mail_settings_get():
    value = store.mail_settings()
    result = value.model_dump()
    result['smtp_password_configured'] = bool(value.smtp_password)
    result['smtp_password'] = ''
    return result


@app.put('/api/mail/settings')
async def mail_settings_put(value: MailSettings):
    async with mailer.lock:
        value = resolve_mail_settings(value)
        if value.enabled:
            validate_mail(value)
        store.save_mail_settings(value)
        for kind in ('warning', 'cooldown'):
            if not value.enabled or not getattr(value, 'notify_' + kind):
                store.db.execute("UPDATE mail_messages SET status='skipped', error='对应邮件通知已关闭' WHERE kind=? AND status='pending'", (kind,))
        store.db.commit()
        store.event('mail_settings', '已更新邮件通知配置')
    return await mail_settings_get()


@app.get('/api/mail/messages')
async def mail_messages():
    return store.mail_messages()


@app.post('/api/mail/test-connection')
async def mail_test_connection(value: MailSettings):
    return await mailer.test(resolve_mail_settings(value))


@app.post('/api/mail/test')
async def mail_test(value: MailSettings):
    return await mailer.test(resolve_mail_settings(value), send=True)


@app.get('/api/settings')
async def settings_get():
    value = store.settings()
    result = value.model_dump()
    result['key_configured'] = bool(value.admin_key)
    result['admin_key'] = ''
    return result


@app.put('/api/settings')
async def settings_put(value: Settings):
    async with scheduler.lock:
        current = store.settings()
        if current.base_url and current.base_url != value.base_url and (store.accounts() or store.batches()):
            raise HTTPException(409, '切换上游地址前，请移除本地托管账号并清空导入批次，防止跨服务账号 ID 混用')
        if current.base_url and value.base_url != current.base_url and not value.admin_key:
            raise HTTPException(400, '切换上游地址时请填写对应的新管理员 Key')
        if not value.admin_key:
            value.admin_key = current.admin_key
        if value.rule.enabled and (not value.base_url or not value.admin_key):
            raise HTTPException(400, '启用调度前请先设置地址和管理员 Key')
        guard_fields = ('enabled', 'alert_only', 'sample_size', 'threshold_ms', 'slow_ratio', 'breach_window_seconds', 'breach_count',
                        'breach_min_interval_seconds', 'breach_fresh_ratio')
        if any(getattr(current.rule, field) != getattr(value.rule, field) for field in guard_fields):
            # 新规则从新的计数周期开始，避免旧规则的告警被当作新规则的达限次数。
            for account in store.accounts():
                store.update(account['id'], breach_times=[], breach_sample='', last_breach_at=None)
        if not value.rule.enabled:
            for account in store.accounts():
                store.update(account['id'], sample=[], slow_count=0, last_checked=None)
        changed_pools = set(current.rule.guarded_pools) ^ set(value.rule.guarded_pools)
        for account in store.accounts():
            if account['pool'] in changed_pools:
                values = dict(breach_times=[], breach_sample='', last_breach_at=None, sample=[], slow_count=0, last_checked=None)
                if account['state'] in {'active', 'probation'}:
                    values['reason'] = '守护范围已更新，等待新一轮巡检' if account['pool'] in value.rule.guarded_pools else '该号池已取消首字延迟守护'
                    if account['pool'] not in value.rule.guarded_pools:
                        values.update(state='active', probation_until=None)
                store.update(account['id'], **values)
        store.save_settings(value)
        store.event('settings', '已更新连接配置 / 调度规则 / 上号默认参数')
    return await settings_get()


@app.post('/api/connection/test')
async def connection_test(value: Settings):
    if not value.admin_key:
        if value.base_url != store.settings().base_url:
            raise HTTPException(400, '测试新地址时请填写对应管理员 Key')
        value.admin_key = store.settings().admin_key
    async with Sub2API(value) as client:
        data = await client.metadata()
    return {'ok': True, **data}


@app.get('/api/metadata')
async def metadata():
    async with Sub2API(store.settings()) as client:
        return await client.metadata()


@app.get('/api/remote/accounts')
async def remote_accounts(search: str = '', group: str = '', account_type: str = 'oauth'):
    # 录入已有账号默认只展示 OAuth；保留 account_type 参数便于后续扩展其它认证类型。
    account_type = account_type.strip() or 'oauth'
    if account_type not in {'oauth', 'setup-token', 'apikey', 'upstream'}:
        raise HTTPException(400, '不支持的账号类型')
    filters = {'type': account_type}
    if search:
        filters['search'] = search
    if group:
        filters['group'] = group
    async with Sub2API(store.settings()) as client:
        accounts = await client.accounts(**filters)
    return [public_account(a) for a in accounts if a.get('type') == account_type]


@app.get('/api/dashboard')
async def dashboard():
    accounts = store.accounts()
    recovery_ids = store.recovery_ids()
    for account in accounts:
        account['auto_relogin_available'] = account['id'] in recovery_ids
        account['auto_relogin_running'] = scheduler.relogin_account_id == account['id']
    return {'accounts': accounts, 'events': store.events(), 'inspections': store.inspections(), 'batches': [public_batch(b) for b in store.batches()],
            'worker': {'last_tick': scheduler.last_tick, 'error': scheduler.last_error, 'running': scheduler.running},
            'configured': bool(store.settings().admin_key and store.settings().base_url), 'now': time.time()}


@app.get('/api/usage')
async def usage(page: int = 1, page_size: int = 50):
    page = max(1, min(page, 10000))
    page_size = max(10, min(page_size, 100))
    async with Sub2API(store.settings()) as client:
        return await client.usage(page=page, page_size=page_size)


@app.get('/api/usage/pools')
async def usage_pools(limit: int = 15):
    limit = max(1, min(limit, 50))
    pool_names = ('priority', 'risk', 'third_party')
    records = {pool: [] for pool in pool_names}
    pool_by_account = {int(account['id']): account['pool'] for account in store.accounts()}
    if not pool_by_account:
        return {'pools': {pool: {'items': [], 'total': 0, 'has_more': False} for pool in pool_names}}
    async with Sub2API(store.settings()) as client:
        for page in range(1, 21):
            result = await client.usage(page=page, page_size=100)
            items = result.get('items') or []
            for item in items:
                pool = pool_by_account.get(int(item['account_id'])) if item.get('account_id') is not None else None
                if pool in records and len(records[pool]) < limit:
                    records[pool].append(item)
            if all(len(records[pool]) >= limit for pool in pool_names) or not items or page >= result.get('pages', page):
                break
    return {'pools': {pool: {'items': records[pool], 'total': len(records[pool]), 'has_more': len(records[pool]) >= limit} for pool in pool_names}}


@app.get('/api/usage/ranking')
async def usage_ranking():
    """Rank every locally managed account by share of the newest 100 calls."""
    accounts = store.accounts()
    ranking = {int(account['id']): {
        'account_id': int(account['id']), 'account_name': account['name'],
        'pool': account['pool'], 'state': account['state'], 'share': 0,
        'last_created_at': None,
    } for account in accounts}
    if not ranking:
        return {'items': [], 'sample_size': 0}
    async with Sub2API(store.settings()) as client:
        result = await client.usage(page=1, page_size=100)
    items = result.get('items') or []
    total = len(items)
    seen = {}
    for item in items:
        account_id = item.get('account_id')
        if account_id is None or int(account_id) not in ranking:
            continue
        key = int(account_id)
        seen[key] = seen.get(key, 0) + 1
        if ranking[key]['last_created_at'] is None:
            ranking[key]['last_created_at'] = item.get('created_at')
    for account_id, count in seen.items():
        ranking[account_id]['share'] = round(count / total * 100, 1) if total else 0
    return {'items': sorted(ranking.values(), key=lambda item: (-item['share'], item['account_name'].casefold(), item['account_id'])), 'sample_size': total}


@app.post('/api/accounts/enroll')
async def enroll(value: Enroll):
    async with scheduler.lock:
        async with Sub2API(store.settings()) as client:
            # 全部验证后再保存，不更改已有上游分组或调度状态。
            remotes = [await client.account(account_id) for account_id in dict.fromkeys(value.account_ids)]
            conflicts = [
                f"{remote['name']} 已在{POOL_NAMES[store.account(remote['id'])['pool']]}"
                for remote in remotes
                if store.account(remote['id']) and store.account(remote['id'])['pool'] != value.pool
            ]
            if conflicts:
                raise HTTPException(409, f"账号不能同时进入多个号池：{'、'.join(conflicts)}，当前目标为{POOL_NAMES[value.pool]}")
            for remote in remotes:
                store.enroll(public_account(remote), value.pool)
                recovery = recovery_from_note(remote.get('notes'))
                if recovery:
                    store.save_recovery(remote['id'], recovery)
                    store.update(remote['id'], relogin_error='', relogin_last_at=None, relogin_count=0, relogin_failures=0)
                store.event('enroll', f"{remote['name']} 加入本地号池", remote['id'])
    return {'count': len(remotes)}


@app.post('/api/accounts/{account_id}/{action}')
async def account_action(account_id: int, action: str):
    if action not in {'pause', 'resume', 'remove'}:
        raise HTTPException(404, '未知操作')
    async with scheduler.lock:
        account = store.account(account_id)
        if not account:
            raise HTTPException(404, '账号未录入本地号池')
        if action == 'remove':
            # 持有调度锁时结束本地托管，后续巡检不会继续执行该账号的暂停或恢复。
            # 移出不需要调用上游接口，也不应先启用已停止调度的账号。
            store.remove(account_id)
            store.event('remove', '已移出本地号池并结束托管，上游账号及调度状态保留；不再执行本地冷却恢复任务', account_id)
        else:
            async with Sub2API(store.settings()) as client:
                remote = await client.account(account_id)
                rule = store.settings().rule
                if action == 'pause':
                    await scheduler.pause(client, account, rule, '手动暂停，需手动恢复', manual=True)
                else:
                    if remote.get('status') != 'active':
                        raise HTTPException(409, '请先在 Sub2API 将账号状态恢复为 active')
                    if remote.get('auto_pause_on_expired', True) and remote.get('expires_at'):
                        if expiry_timestamp(remote['expires_at']) <= client.now():
                            raise HTTPException(409, '账号已到期，请先在 Sub2API 处理有效期')
                    reason = '手动结束冷却，立即恢复调度' if account['resume_at'] is not None else '手动恢复调度'
                    if account['pool'] in rule.guarded_pools:
                        reason += '，进入观察期'
                    await scheduler.resume(client, account, rule, reason, restore_groups=account['pool'] == 'third_party')
    return {'ok': True}


@app.post('/api/scheduler/tick')
async def tick():
    await scheduler.tick()
    return {'ok': True}


@app.post('/api/import/login')
async def import_login(value: LoginImport):
    if value.batch_id:
        importer.login_batch(value.batch_id, value.options)
    connection = store.settings()
    async with Sub2API(connection) as client:
        await importer.validate_options(client, value.options)
    result = await account_login.run(value)
    if isinstance(result, JSONResponse):
        return result
    async with scheduler.lock:
        current = store.settings()
        if current.base_url != connection.base_url or current.admin_key != connection.admin_key:
            raise HTTPException(409, '登录期间连接配置发生变化，请确认目标服务后重新操作')
        # 登录成功后直接加密保存待确认批次，不把 OAuth 凭据返回浏览器。
        preview = Preview(payload=result['payload'], options=value.options)
        recovery = recovery_material(value, value.options)
        if value.batch_id:
            return importer.append_login_preview(value.batch_id, preview, recovery)
        return importer.create_preview(preview, recovery, source='login')


@app.post('/api/import/preview')
async def import_preview(value: Preview):
    async with scheduler.lock:
        async with Sub2API(store.settings()) as client:
            return await importer.preview(client, value)


@app.get('/api/import/batches')
async def import_batches(search: str = Query('', max_length=200), page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=50)):
    batches, total = store.batch_page(search, page, page_size)
    return {'items': [public_batch(batch) for batch in batches], 'total': total, 'page': page, 'page_size': page_size}


@app.post('/api/import/{batch_id}/check')
async def import_check(batch_id: str):
    original = store.batch(batch_id)
    if not original:
        raise HTTPException(404, '导入批次不存在')
    connection = store.settings()
    # 获取完整最新列表，管理员鉴权 / 网络失败时整轮失败，不能误判为账号 401。
    async with Sub2API(connection) as client:
        remotes = {account['id']: account for account in await client.accounts()}
        remote_now = client.now()
    async with scheduler.lock:
        current = store.settings()
        if current.base_url != connection.base_url or current.admin_key != connection.admin_key:
            raise HTTPException(409, '检查期间连接配置发生变化，请重新检查')
        batch = store.batch(batch_id)
        if not batch:
            raise HTTPException(404, '导入批次已删除')
        snapshots = {item['index']: {key: value for key, value in item.items() if key != 'health'} for item in original['items']}
        for item in batch['items']:
            if {key: value for key, value in item.items() if key != 'health'} != snapshots.get(item['index']):
                continue
            account_id = item.get('account_id')
            item['health'] = inspect_item(item, remotes.get(account_id), store.account(account_id), remote_now)
        store.save_batch(batch)
        return public_batch(batch)


@app.post('/api/import/{batch_id}/items/{item_index}/relogin')
async def import_relogin(batch_id: str, item_index: int, value: ReloginImport):
    async with scheduler.lock:
        original = store.batch(batch_id)
        if not original:
            raise HTTPException(404, '导入批次不存在')
        item = next((item for item in original['items'] if item['index'] == item_index), None)
        if not item:
            raise HTTPException(404, '批次账号不存在')
        options = ImportOptions(**original['options'])
        connection = store.settings()
        async with Sub2API(connection) as client:
            await importer.validate_options(client, options)
            target = await importer.relogin_target(client, item, value.email, options.pool)
        credentials = (target or item.get('payload') or {}).get('credentials', {})
        workspace = credentials.get('chatgpt_account_id') or credentials.get('account_id') or ''
        if value.workspace_id and workspace and value.workspace_id != workspace:
            raise HTTPException(409, '工作空间与原账号不一致，不能覆盖原账号')
    request = LoginImport(**{**value.model_dump(), 'workspace_id': workspace or value.workspace_id}, options=options)
    result = await account_login.run(request)
    if isinstance(result, JSONResponse):
        return result
    async with scheduler.lock:
        current = store.settings()
        if current.base_url != connection.base_url or current.admin_key != connection.admin_key:
            raise HTTPException(409, '登录期间连接配置发生变化，未推送账号，请重新操作')
        batch = store.batch(batch_id)
        current_item = next((entry for entry in batch['items'] if entry['index'] == item_index), None) if batch else None
        if not batch or not current_item or {key: value for key, value in current_item.items() if key != 'health'} != {key: value for key, value in item.items() if key != 'health'} or batch['options'] != original['options']:
            raise HTTPException(409, '登录期间批次已删除或账号记录发生变化，未推送，请重新操作')
        async with Sub2API(current) as client:
            await importer.validate_options(client, options)
            latest_target = await importer.relogin_target(client, item, value.email, options.pool)
            if (latest_target or {}).get('id') != (target or {}).get('id'):
                raise HTTPException(409, '登录期间上游目标账号发生变化，未推送，请重新操作')
            latest_credentials = (latest_target or {}).get('credentials', credentials)
            latest_workspace = latest_credentials.get('chatgpt_account_id') or latest_credentials.get('account_id') or ''
            if latest_workspace != workspace:
                raise HTTPException(409, '登录期间上游账号工作空间发生变化，未推送，请重新操作')
            entries = normalize(result['payload'], options, start_index=item_index)
            if len(entries) != 1 or entries[0]['email'].casefold() != value.email.casefold():
                raise HTTPException(409, '登录结果与所选账号不一致，未推送')
            updated = entries[0]
            updated.update(name=item['name'], account_id=target['id'] if target else None,
                           status='updating' if target else 'pending', relogin=True)
            updated['payload']['name'] = item['name']
            updated['payload']['credentials'] = {**latest_credentials, **updated['payload']['credentials']}
            updated['recovery'] = recovery_material(value, options)
            updated['source'] = 'login'
            batch['items'] = [updated if entry['index'] == item_index else entry for entry in batch['items']]
            # 账密随批次加密暂存，成功入池后移到账号恢复记录；推送失败可复用原导入重试。
            store.save_batch(batch)
            result = await importer.commit(client, batch, item_index=item_index)
        if next(entry for entry in result['items'] if entry['index'] == item_index)['status'] == 'done':
            current.rule.enabled = True
            store.save_settings(current)
        return result


@app.post('/api/import/{batch_id}/commit')
async def import_commit(batch_id: str):
    async with scheduler.lock:
        batch = store.batch(batch_id)
        if not batch:
            raise HTTPException(404, '导入批次不存在')
        async with Sub2API(store.settings()) as client:
            result = await importer.commit(client, batch)
        # 推送入池即开启自动监控，不要求用户再到规则页启用。
        if any(item['status'] == 'done' for item in result['items']):
            settings = store.settings()
            settings.rule.enabled = True
            store.save_settings(settings)
    return result


@app.delete('/api/import/{batch_id}')
async def import_discard(batch_id: str):
    async with scheduler.lock:
        store.db.execute('DELETE FROM batches WHERE id=?', (batch_id,))
        store.db.commit()
    return {'ok': True}


DIST = ROOT / 'frontend' / 'dist'
if DIST.exists():
    app.mount('/assets', StaticFiles(directory=DIST / 'assets'), name='assets')


@app.get('/favicon.svg')
async def favicon():
    return FileResponse(DIST / 'favicon.svg', media_type='image/svg+xml')


@app.get('/{path:path}')
async def frontend(path: str):
    if path.startswith('api/'):
        raise HTTPException(404, '接口不存在')
    if not (DIST / 'index.html').exists():
        return JSONResponse({'detail': '请先执行 ./start.sh 构建前端'}, status_code=503)
    return FileResponse(DIST / 'index.html')
