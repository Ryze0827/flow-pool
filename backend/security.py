"""Bound work before authentication, body parsing and file range handling."""
import math
import re
import time
from collections import OrderedDict, deque

from starlette.responses import JSONResponse


class RequestBudget:
    def __init__(self, per_client, total, seconds=60, max_clients=4096):
        self.per_client, self.total = per_client, total
        self.seconds, self.max_clients = seconds, max_clients
        self.clients = OrderedDict()
        self.requests = deque()

    def consume(self, client, now=None):
        now = time.monotonic() if now is None else now
        cutoff = now - self.seconds
        while self.requests and self.requests[0] <= cutoff:
            self.requests.popleft()
        while self.clients and next(iter(self.clients.values()))[-1] <= cutoff:
            self.clients.popitem(last=False)
        bucket = self.clients.get(client, deque())
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self.per_client:
            return max(1, math.ceil(bucket[0] + self.seconds - now))
        if len(self.requests) >= self.total:
            return max(1, math.ceil(self.requests[0] + self.seconds - now))
        if client not in self.clients and len(self.clients) >= self.max_clients:
            return self.seconds
        bucket.append(now)
        self.clients[client] = bucket
        self.clients.move_to_end(client)
        self.requests.append(now)
        return 0


class RequestSecurityMiddleware:
    def __init__(self, app):
        self.app = app
        # 密码登录与 2FA 共用预算，切换接口不能绕过限流。
        self.login_budget = RequestBudget(10, 120)
        self.lookup_budget = RequestBudget(120, 1200)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)

        async def reject(message, status, headers=None):
            response = JSONResponse({'detail': message}, status_code=status, headers={
                'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer', **(headers or {})})
            await response(scope, receive, send)

        ranges = [value for key, value in scope['headers'] if key.lower() == b'range']
        # Starlette 旧版本多段 Range 合并为 O(n²)，包括无需登录的静态资源。
        if ranges and (len(ranges) != 1 or len(ranges[0]) > 128 or
                       not re.fullmatch(rb'bytes=(?:[0-9]{1,20}-[0-9]{0,20}|-[0-9]{1,20})', ranges[0])):
            return await reject('仅支持单段字节范围请求', 400)
        path = scope['path'].rstrip('/')
        budget = None
        if path in {'/api/auth/login', '/api/auth/login/2fa'}:
            budget = self.login_budget
        elif path in {'/api/auth/status', '/api/auth/public-settings', '/api/auth/logout'} or path.startswith(('/api/invoice-user/', '/api/model-plaza-user/')):
            budget = self.lookup_budget
        if budget and scope['method'] != 'OPTIONS':
            # 仅使用 ASGI 提供的客户端地址，不自行信任 X-Forwarded-For。
            client = (scope.get('client') or ('unknown',))[0]
            retry = budget.consume(client)
            if retry:
                return await reject('请求过于频繁，请稍后重试', 429, {'Retry-After': str(retry)})
        await self.app(scope, receive, send)
