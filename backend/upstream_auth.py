"""ErxinAI/Sub2API login contract with server-side, encrypted token storage."""
import asyncio
import logging
import time
from contextlib import suppress

import httpx
from fastapi import HTTPException

from .auth import SESSION_TTL_SECONDS, new_session_token, session_digest

logger = logging.getLogger(__name__)


class UpstreamAuth:
    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()
        store.db.execute('''CREATE TABLE IF NOT EXISTS upstream_sessions (
            token_hash TEXT PRIMARY KEY, payload TEXT NOT NULL, expires_at REAL NOT NULL
        )''')
        store.db.commit()

    async def request(self, method, path, *, token=None, body=None):
        base_url = self.store.settings().base_url
        if not base_url:
            raise HTTPException(503, '请在服务器配置 FLOWPOOL_SUB2API_URL，使用与 erxinai 相同的 Sub2API 后端')
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
                response = await client.request(method, base_url + '/api/v1/' + path,
                                                headers={'Authorization': 'Bearer ' + token} if token else {}, json=body)
        except httpx.RequestError:
            raise HTTPException(502, '无法连接 Sub2API 登录服务，请稍后重试') from None
        if not response.is_success:
            try:
                error_body = response.json()
                reason = error_body.get('reason') if isinstance(error_body, dict) else None
            except ValueError:
                reason = None
            known_reasons = {'TOKEN_EXPIRED', 'INVALID_TOKEN', 'TOKEN_REVOKED', 'USER_NOT_FOUND', 'USER_INACTIVE',
                             'USER_NOT_ACTIVE', 'REFRESH_TOKEN_INVALID', 'REFRESH_TOKEN_EXPIRED', 'REFRESH_TOKEN_REUSED',
                             'SESSION_BINDING_MISMATCH', 'SERVICE_UNAVAILABLE'}
            logger.warning('Sub2API auth request failed: endpoint=%s status=%s reason=%s',
                           path, response.status_code, reason if isinstance(reason, str) and reason in known_reasons else 'unrecognized')
            messages = {400: '登录参数或验证码无效，请重新验证', 401: '账号密码错误或登录已过期',
                        403: '登录被拒绝，请检查账号状态及人机校验', 429: '登录请求过于频繁，请稍后重试'}
            raise HTTPException(response.status_code if response.status_code in messages else 502,
                                messages.get(response.status_code, 'Sub2API 登录服务暂不可用'))
        try:
            result = response.json()
            if result.get('code') != 0 or not isinstance(result.get('data'), dict):
                raise ValueError()
            return result['data']
        except (ValueError, AttributeError):
            raise HTTPException(502, 'Sub2API 登录响应格式异常') from None

    @staticmethod
    def require_admin(user):
        if not user.get('id') or user.get('role') != 'admin' or user.get('status') != 'active':
            raise HTTPException(403, '仅允许正常状态的 Sub2API 管理员登录')

    async def login(self, body, two_factor=False):
        result = await self.request('POST', 'auth/login/2fa' if two_factor else 'auth/login', body=body)
        if result.get('requires_2fa') and not two_factor:
            return {key: result.get(key) for key in ('requires_2fa', 'temp_token', 'user_email_masked')}, None
        access = result.get('access_token')
        if not access:
            raise HTTPException(502, '登录响应缺少访问令牌')
        try:
            user = await self.request('GET', 'auth/me', token=access)
            self.require_admin(user)
        except HTTPException:
            if result.get('refresh_token'):
                with suppress(HTTPException):
                    await self.request('POST', 'auth/logout', body={'refresh_token': result['refresh_token']})
            raise
        now = time.time()
        token = new_session_token()
        payload = {'base_url': self.store.settings().base_url, 'access_token': access,
                   'refresh_token': result.get('refresh_token'), 'access_expires_at': now + result.get('expires_in', 3600),
                   'user': user}
        if not payload['refresh_token']:
            logger.warning('Admin login cannot auto-refresh: upstream returned access token only')
        self.store.db.execute('DELETE FROM upstream_sessions WHERE expires_at<=?', (now,))
        self.store.db.execute('INSERT INTO upstream_sessions VALUES (?, ?, ?)',
                              (session_digest(token), self.store.seal(payload), now + SESSION_TTL_SECONDS))
        self.store.db.commit()
        return {'ok': True, 'username': user.get('username') or user['email'], 'expires_at': now + SESSION_TTL_SECONDS}, token

    def delete(self, token):
        if token:
            self.store.db.execute('DELETE FROM upstream_sessions WHERE token_hash=?', (session_digest(token),))
            self.store.db.commit()

    def active(self, token):
        return bool(token and self.store.db.execute('SELECT 1 FROM upstream_sessions WHERE token_hash=? AND expires_at>?',
                                                   (session_digest(token), time.time())).fetchone())

    async def session(self, token):
        if not token:
            return None
        # Refresh tokens rotate; serialize reads/refresh/logout to avoid reusing one token.
        async with self.lock:
            row = self.store.db.execute('SELECT payload FROM upstream_sessions WHERE token_hash=? AND expires_at>?',
                                        (session_digest(token), time.time())).fetchone()
            if not row:
                return None
            payload = self.store.unseal(row['payload'])
            if payload['base_url'] != self.store.settings().base_url:
                logger.warning('Admin session invalidated: upstream base URL changed')
                self.delete(token)
                return None
            async def refresh():
                if not payload.get('refresh_token'):
                    raise HTTPException(401, '登录已过期，请重新登录')
                result = await self.request('POST', 'auth/refresh', body={'refresh_token': payload['refresh_token']})
                if not result.get('access_token'):
                    raise HTTPException(502, '刷新响应缺少访问令牌，请稍后重试')
                payload.update(access_token=result['access_token'], refresh_token=result.get('refresh_token') or payload['refresh_token'],
                               access_expires_at=time.time() + result.get('expires_in', 3600))
                # Persist rotated tokens before the next network call, including on transient /me failures.
                self.save(token, payload)
            try:
                refreshed = False
                stage = 'auth/refresh'
                # Sub2API can fall back to access-token-only login without expires_in.
                # Its actual validity must be checked by /me, not our guessed expiry.
                if payload.get('refresh_token') and payload['access_expires_at'] <= time.time() + 30:
                    await refresh()
                    refreshed = True
                try:
                    stage = 'auth/me'
                    user = await self.request('GET', 'auth/me', token=payload['access_token'])
                except HTTPException as error:
                    if error.status_code != 401 or refreshed:
                        raise
                    stage = 'auth/refresh'
                    await refresh()
                    stage = 'auth/me'
                    user = await self.request('GET', 'auth/me', token=payload['access_token'])
                stage = 'admin permissions'
                self.require_admin(user)
            except HTTPException as error:
                if error.status_code == 401 or (error.status_code in (400, 403) and stage != 'auth/me'):
                    logger.warning('Admin session invalidated: stage=%s status=%s', stage, error.status_code)
                    self.delete(token)
                    return None
                logger.warning('Admin authentication unavailable; session retained: stage=%s status=%s', stage, error.status_code)
                raise
            payload['user'] = user
            self.save(token, payload)
            return user

    def save(self, token, payload):
        self.store.db.execute('UPDATE upstream_sessions SET payload=?, expires_at=? WHERE token_hash=?',
                              (self.store.seal(payload), time.time() + SESSION_TTL_SECONDS, session_digest(token)))
        self.store.db.commit()

    async def logout(self, token):
        async with self.lock:
            row = self.store.db.execute('SELECT payload FROM upstream_sessions WHERE token_hash=?',
                                        (session_digest(token),)).fetchone() if token else None
            self.delete(token)
            if row:
                payload = self.store.unseal(row['payload'])
                if payload['base_url'] == self.store.settings().base_url and payload.get('refresh_token'):
                    with suppress(HTTPException):
                        await self.request('POST', 'auth/logout', body={'refresh_token': payload['refresh_token']})
