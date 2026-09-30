from starlette.middleware.cors import CORSMiddleware


class InvoiceCORSMiddleware(CORSMiddleware):
    """Allow configured user sites without exposing admin APIs to CORS."""

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and scope['path'].startswith('/api/invoice-user/'):
            await super().__call__(scope, receive, send)
        else:
            await self.app(scope, receive, send)
