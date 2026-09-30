"""Self-service invoices; billing remains in Sub2API, documents stay local."""
import asyncio
import hashlib
import json
import os
import sqlite3
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP

import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from .models import MailSettings
from .mailer import validate_mail
from .sub2api import Sub2API, UpstreamError


def money(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError()
        return result
    except Exception:
        raise HTTPException(502, '上游金额无效') from None


class FeeTier(BaseModel):
    minimum: Decimal = Field(ge=0, max_digits=16, decimal_places=2)
    fee: Decimal = Field(ge=0, max_digits=16, decimal_places=2)


class InvoiceRules(BaseModel):
    enabled: bool = False
    tiers: list[FeeTier] = Field(default_factory=lambda: [FeeTier(minimum=0, fee=0)], min_length=1, max_length=20)

    @model_validator(mode='after')
    def ordered(self):
        self.tiers.sort(key=lambda tier: tier.minimum)
        if self.tiers[0].minimum != 0 or len({tier.minimum for tier in self.tiers}) != len(self.tiers):
            raise ValueError('首档起点必须为 0，金额起点不能重复')
        return self


class QuoteRequest(BaseModel):
    order_ids: list[int] = Field(min_length=1, max_length=100)

    @field_validator('order_ids')
    @classmethod
    def valid_ids(cls, values):
        if any(value <= 0 for value in values) or len(set(values)) != len(values):
            raise ValueError('订单编号必须为正整数且不能重复')
        return sorted(values)


class InvoiceRequest(QuoteRequest):
    title: str = Field(min_length=1, max_length=200)
    tax_id: str = Field(min_length=1, max_length=32, pattern=r'^[A-Za-z0-9]+$')
    email: str = Field(min_length=3, max_length=254)
    quote_token: str = Field(min_length=1, max_length=50000)

    @field_validator('title', 'tax_id', 'email', mode='before')
    @classmethod
    def clean(cls, value):
        if not isinstance(value, str) or any(ord(char) < 32 for char in value):
            raise ValueError('不能包含控制字符')
        return value.strip()

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        return MailSettings.email_valid(value)


class ReviewRequest(BaseModel):
    action: str = Field(pattern=r'^(approve|reject|confirm_charged|confirm_not_charged)$')
    note: str = Field('', max_length=500)


class Invoices:
    def __init__(self, store, mailer):
        self.store, self.mailer = store, mailer
        self.lock = asyncio.Lock()
        self.directory = store.directory / 'invoices'
        self.directory.mkdir(mode=0o700, exist_ok=True)
        store.db.executescript('''
            CREATE TABLE IF NOT EXISTS invoice_rules (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS invoices (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, user_id INTEGER NOT NULL,
                status TEXT NOT NULL, charge_status TEXT NOT NULL DEFAULT 'none',
                payload TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', reviewer_id INTEGER,
                mail_id INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS invoices_user ON invoices(source, user_id, created_at);
            CREATE TABLE IF NOT EXISTS invoice_orders (
                source TEXT NOT NULL, order_id INTEGER NOT NULL, invoice_id TEXT NOT NULL,
                PRIMARY KEY(source, order_id)
            );
        ''')
        # A lost response must never trigger a new debit after restart.
        store.db.execute("UPDATE invoices SET charge_status='uncertain', note='扣费中服务中断，请核对 Sub2API 余额流水' WHERE charge_status='charging'")
        store.db.commit()
        row = store.db.execute('SELECT payload FROM invoice_rules WHERE id=1').fetchone()
        if row:
            payload = json.loads(row['payload'])
            if any('rate' in tier for tier in payload['tiers']):
                # Percentage values become a draft; an administrator must confirm fixed fees.
                value = InvoiceRules(enabled=False, tiers=[FeeTier(minimum=tier['minimum'],
                                     fee=Decimal(str(tier.get('fee', tier.get('rate', 0)))).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
                                     for tier in payload['tiers']])
                store.db.execute('UPDATE invoice_rules SET payload=? WHERE id=1', (value.model_dump_json(),))
                store.event('invoice_rules', '旧百分比规则已转为固定手续费草稿，自助发票已关闭，请核对金额后保存并重新开启')

    def source(self):
        return self.store.settings().base_url

    def rules(self):
        row = self.store.db.execute('SELECT payload FROM invoice_rules WHERE id=1').fetchone()
        return InvoiceRules.model_validate_json(row['payload']) if row else InvoiceRules()

    def save_rules(self, value):
        self.store.db.execute('INSERT OR REPLACE INTO invoice_rules VALUES (1, ?)', (value.model_dump_json(),))
        self.store.db.commit()
        self.store.event('invoice_rules', '已更新发票手续费规则')
        return value.model_dump(mode='json')

    async def user_request(self, token, path, params=None):
        if not self.source():
            raise HTTPException(503, '尚未配置 Sub2API')
        try:
            async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False) as client:
                response = await client.get(self.source() + '/api/v1/' + path,
                                            headers={'Authorization': 'Bearer ' + token}, params=params)
        except httpx.RequestError:
            raise HTTPException(502, '无法连接 Sub2API') from None
        if not response.is_success:
            raise HTTPException(response.status_code if response.status_code in (401, 403, 404, 429) else 502,
                                '登录已过期' if response.status_code == 401 else '无法读取用户或订单信息')
        try:
            result = response.json()
            if result['code'] != 0 or not isinstance(result['data'], dict):
                raise ValueError()
            return result['data']
        except (ValueError, KeyError, TypeError):
            raise HTTPException(502, 'Sub2API 响应格式无效') from None

    async def authenticate(self, request):
        scheme, _, token = request.headers.get('authorization', '').partition(' ')
        if scheme.lower() != 'bearer' or not token or len(token) > 16384:
            raise HTTPException(401, '请先登录')
        user = await self.user_request(token, 'auth/me')
        if not user.get('id') or user.get('status') != 'active':
            raise HTTPException(403, '账号不可用')
        request.state.invoice_user = user
        request.state.invoice_token = token

    def get(self, invoice_id, user_id=None):
        row = self.store.db.execute('SELECT * FROM invoices WHERE id=? AND source=?', (invoice_id, self.source())).fetchone()
        if not row or (user_id is not None and row['user_id'] != user_id):
            raise HTTPException(404, '申请不存在')
        result = dict(row)
        result.update(self.store.unseal(result.pop('payload')))
        mail = self.store.db.execute('SELECT status, error FROM mail_messages WHERE id=?', (result['mail_id'],)).fetchone()
        result.update(mail_status=mail['status'] if mail else None, mail_error=mail['error'] if mail else None)
        return result

    def listing(self, user_id=None, page=1):
        where, args = 'source=?', [self.source()]
        if user_id is not None:
            where += ' AND user_id=?'
            args.append(user_id)
        total = self.store.db.execute(f'SELECT COUNT(*) FROM invoices WHERE {where}', args).fetchone()[0]
        ids = self.store.db.execute(f'SELECT id FROM invoices WHERE {where} ORDER BY created_at DESC LIMIT 20 OFFSET ?', (*args, (page - 1) * 20)).fetchall()
        return {'items': [self.get(row['id'], user_id) for row in ids], 'total': total, 'page': page, 'pages': max(1, (total + 19) // 20)}

    def eligible(self, order):
        return (order.get('order_type') == 'balance' and order.get('status') == 'COMPLETED'
                and money(order.get('refund_amount', 0)) == 0 and not order.get('refund_requested_at'))

    def occupied(self, order_id):
        return bool(self.store.db.execute('SELECT 1 FROM invoice_orders WHERE source=? AND order_id=?', (self.source(), order_id)).fetchone())

    async def quote(self, user, token, value):
        rules = self.rules()
        if not rules.enabled:
            raise HTTPException(409, '自助发票尚未开启')
        orders = []
        for order_id in value.order_ids:
            order = await self.user_request(token, f'payment/orders/{order_id}')
            if not self.eligible(order) or self.occupied(order_id):
                raise HTTPException(409, f'订单 #{order_id} 不可申请或已申请开票')
            orders.append({'id': order_id, 'amount': str(money(order['amount']))})
        # Match the existing invoice page: invoice amount is credited USD balance.
        amount = sum((money(order['amount']) for order in orders), Decimal(0)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        tier = next(tier for tier in reversed(rules.tiers) if amount >= tier.minimum)
        fee = tier.fee.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        balance = money(user['balance'])
        snapshot = {'source': self.source(), 'user_id': user['id'], 'orders': orders,
                    'amount': str(amount), 'fee': str(fee), 'fee_mode': 'fixed', 'currency': 'USD',
                    'rules_version': hashlib.sha256(rules.model_dump_json().encode()).hexdigest()}
        return {**snapshot, 'balance': str(balance), 'sufficient': balance >= fee,
                'quote_token': self.store.seal({**snapshot, 'expires_at': time.time() + 900})}

    async def submit(self, user, token, value):
        async with self.lock:
            try:
                accepted = self.store.unseal(value.quote_token)
            except (InvalidToken, ValueError, TypeError):
                raise HTTPException(409, '报价无效，请刷新后提交') from None
            if accepted.get('expires_at', 0) < time.time() or accepted.get('user_id') != user['id'] or accepted.get('source') != self.source():
                raise HTTPException(409, '报价已过期，请刷新后提交')
            quote = await self.quote(user, token, value)
            if any(accepted.get(key) != quote[key] for key in ('orders', 'amount', 'fee', 'rules_version')):
                raise HTTPException(409, '订单或手续费已变化，请重新确认')
            # Read balance again immediately before insertion, rather than use browser data.
            latest = await self.user_request(token, 'auth/me')
            if money(latest['balance']) < money(quote['fee']):
                raise HTTPException(409, '余额不足，请充值后再提交')
            invoice_id, now = uuid.uuid4().hex, time.time()
            payload = {key: quote[key] for key in ('orders', 'amount', 'fee', 'fee_mode', 'currency')}
            payload.update(title=value.title, tax_id=value.tax_id, email=value.email, user_email=user.get('email', ''))
            try:
                with self.store.db:
                    self.store.db.execute('INSERT INTO invoices(id, source, user_id, status, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                                          (invoice_id, self.source(), user['id'], 'pending', self.store.seal(payload), now, now))
                    self.store.db.executemany('INSERT INTO invoice_orders VALUES (?, ?, ?)', [(self.source(), key, invoice_id) for key in value.order_ids])
            except sqlite3.IntegrityError:
                raise HTTPException(409, '订单已申请开票，请刷新列表') from None
            return self.get(invoice_id, user['id'])

    def update(self, invoice_id, **values):
        values['updated_at'] = time.time()
        with self.store.db:
            self.store.db.execute(f"UPDATE invoices SET {', '.join(key + '=?' for key in values)} WHERE id=?", (*values.values(), invoice_id))

    async def review(self, invoice_id, value, admin):
        async with self.lock:
            item = self.get(invoice_id)
            if item['status'] != 'pending':
                raise HTTPException(409, '申请已处理')
            if value.action.startswith('confirm_'):
                if item['charge_status'] not in ('uncertain', 'charging') or not value.note.strip():
                    raise HTTPException(409, '仅支持核对未确认扣费，且必须填写核对说明')
                charged = value.action == 'confirm_charged'
                self.update(invoice_id, charge_status='charged' if charged else 'none',
                            status='approved' if charged else 'pending', note=value.note.strip(), reviewer_id=admin['id'])
                self.store.event('invoice_review', f'申请 {invoice_id}：管理员核对扣费结果（{value.action}）')
                return self.get(invoice_id)
            if item['charge_status'] != 'none':
                raise HTTPException(409, '扣费结果待核对，请勿重复扣费或驳回')
            if value.action == 'reject':
                if not value.note.strip():
                    raise HTTPException(400, '请填写驳回原因')
                with self.store.db:
                    self.store.db.execute("UPDATE invoices SET status='rejected', note=?, reviewer_id=?, updated_at=? WHERE id=?", (value.note.strip(), admin['id'], time.time(), invoice_id))
                    self.store.db.execute('DELETE FROM invoice_orders WHERE invoice_id=?', (invoice_id,))
                return self.get(invoice_id)
            fee = money(item['fee'])
            async with Sub2API(self.store.settings()) as client:
                user = await client.request('GET', f"users/{item['user_id']}")
                if user.get('status') != 'active' or money(user['balance']) < fee:
                    self.update(invoice_id, note='账户不可用或余额不足，请补足余额后重新审核')
                    raise HTTPException(409, '账户不可用或余额不足，申请仍为待审核')
                for order in item['orders']:
                    latest = await client.request('GET', f"payment/orders/{order['id']}")
                    latest = latest.get('order', latest)
                    if latest.get('user_id') != item['user_id'] or not self.eligible(latest) or money(latest['amount']) != money(order['amount']):
                        raise HTTPException(409, '订单已变化，不能通过审核')
                if fee:
                    self.update(invoice_id, charge_status='charging', reviewer_id=admin['id'])
                    try:
                        await client.request('POST', f"users/{item['user_id']}/balance",
                                             headers={'Idempotency-Key': 'flowpool-invoice-' + invoice_id},
                                             json={'balance': float(fee), 'operation': 'subtract', 'notes': '发票手续费 ' + invoice_id})
                    except (UpstreamError, asyncio.CancelledError):
                        self.update(invoice_id, charge_status='uncertain', note='扣费结果未确认，请按申请编号核对 Sub2API 余额流水')
                        raise
                self.update(invoice_id, status='approved', charge_status='charged', note=value.note.strip(), reviewer_id=admin['id'])
            self.store.event('invoice_review', f'申请 {invoice_id}：审核通过，手续费 {fee} USD')
            return self.get(invoice_id)

    def path(self, invoice_id):
        item = self.get(invoice_id)
        path = self.directory / (item['id'] + '.pdf')
        if item['status'] != 'issued' or not path.is_file():
            raise HTTPException(404, '发票尚未上传')
        return path

    def enqueue(self, item, path):
        from email.utils import make_msgid
        validate = self.store.mail_settings()
        validate_mail(validate, recipients=False)
        payload = {'recipients': [item['email']], 'subject': '您的电子发票',
                   'body': f"您好，{item['title']}的电子发票已开具，详见附件。\n开票金额：{item['amount']} USD\n申请编号：{item['id']}\n",
                   'message_id': make_msgid(), 'attachment': str(path), 'filename': 'invoice-' + item['id'] + '.pdf'}
        cursor = self.store.db.execute('INSERT INTO mail_messages(kind, payload, created_at) VALUES (?, ?, ?)', ('invoice', self.store.seal(payload), time.time()))
        self.store.db.execute('UPDATE invoices SET mail_id=?, updated_at=? WHERE id=?', (cursor.lastrowid, time.time(), item['id']))

    async def upload(self, invoice_id, content):
        if len(content) > 5 * 1024 * 1024 or not content.startswith(b'%PDF-') or b'%%EOF' not in content[-4096:]:
            raise HTTPException(400, '请上传有效 PDF，文件不能超过 5 MB')
        async with self.lock:
            item = self.get(invoice_id)
            if item['status'] != 'approved':
                raise HTTPException(409, '仅支持为审核通过的申请上传发票')
            path = self.directory / (invoice_id + '.pdf')
            temporary = self.directory / (invoice_id + '.tmp')
            try:
                with temporary.open('wb') as output:
                    os.chmod(temporary, 0o600)
                    output.write(content)
                temporary.replace(path)
                with self.store.db:
                    self.enqueue(item, path)
                    self.store.db.execute("UPDATE invoices SET status='issued', updated_at=? WHERE id=?", (time.time(), invoice_id))
            except Exception:
                path.unlink(missing_ok=True)
                raise
            finally:
                temporary.unlink(missing_ok=True)
            return self.get(invoice_id)

    async def resend(self, invoice_id):
        async with self.lock, self.mailer.lock:
            item = self.get(invoice_id)
            if item['mail_status'] in ('pending', 'sending'):
                raise HTTPException(409, '邮件正在发送，请等待完成')
            with self.store.db:
                self.enqueue(item, self.path(invoice_id))
            return self.get(invoice_id)


def invoice_router(service):
    router = APIRouter()

    @router.get('/api/invoice-user/settings')
    async def user_settings():
        return {'enabled': service().rules().enabled}

    @router.get('/api/invoice-user/orders')
    async def orders(request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        instance = service()
        data = await instance.user_request(request.state.invoice_token, 'payment/orders/my', {'page': page, 'page_size': page_size, 'status': 'COMPLETED', 'order_type': 'balance'})
        data['items'] = [{**order, 'invoice_available': instance.eligible(order) and not instance.occupied(order['id'])} for order in data.get('items', [])]
        data['enabled'] = instance.rules().enabled
        return data

    @router.post('/api/invoice-user/quote')
    async def quote(request: Request, value: QuoteRequest):
        return await service().quote(request.state.invoice_user, request.state.invoice_token, value)

    @router.post('/api/invoice-user/requests')
    async def submit(request: Request, value: InvoiceRequest):
        return await service().submit(request.state.invoice_user, request.state.invoice_token, value)

    @router.get('/api/invoice-user/requests')
    async def mine(request: Request, page: int = Query(1, ge=1)):
        return service().listing(request.state.invoice_user['id'], page)

    @router.get('/api/invoice-user/requests/{invoice_id}/file')
    async def download_user(request: Request, invoice_id: str):
        service().get(invoice_id, request.state.invoice_user['id'])
        return FileResponse(service().path(invoice_id), media_type='application/pdf', filename='invoice.pdf')

    @router.get('/api/invoices/rules')
    async def rules():
        return service().rules().model_dump(mode='json')

    @router.put('/api/invoices/rules')
    async def save_rules(value: InvoiceRules):
        return service().save_rules(value)

    @router.get('/api/invoices')
    async def listing(page: int = Query(1, ge=1)):
        return service().listing(page=page)

    @router.post('/api/invoices/{invoice_id}/review')
    async def review(request: Request, invoice_id: str, value: ReviewRequest):
        return await service().review(invoice_id, value, request.state.admin)

    @router.put('/api/invoices/{invoice_id}/file')
    async def upload(request: Request, invoice_id: str):
        if request.headers.get('content-type', '').split(';')[0] != 'application/pdf':
            raise HTTPException(400, '仅支持 PDF 文件')
        return await service().upload(invoice_id, await request.body())

    @router.get('/api/invoices/{invoice_id}/file')
    async def download(invoice_id: str):
        return FileResponse(service().path(invoice_id), media_type='application/pdf', filename='invoice.pdf')

    @router.post('/api/invoices/{invoice_id}/resend')
    async def resend(invoice_id: str):
        return await service().resend(invoice_id)

    return router
