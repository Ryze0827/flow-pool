"""FlowPool-owned display settings; pricing and visibility stay with Sub2API."""
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool


class PlazaSettings(BaseModel):
    model_config = {'extra': 'forbid'}
    enabled: StrictBool = False
    description: str = Field(default='', max_length=20000)
    hidden_group_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(default_factory=list, max_length=1000)


def plaza_router(store, invoices):
    router = APIRouter()

    def settings():
        row = store().db.execute('SELECT payload FROM model_plaza_settings WHERE id=1').fetchone()
        return PlazaSettings.model_validate_json(row['payload']) if row else PlazaSettings()

    @router.get('/api/model-plaza/settings')
    async def get_settings():
        return settings().model_dump()

    @router.put('/api/model-plaza/settings')
    async def save_settings(value: PlazaSettings):
        value.hidden_group_ids = sorted(set(value.hidden_group_ids))
        store().db.execute('INSERT OR REPLACE INTO model_plaza_settings VALUES (1, ?)', (value.model_dump_json(),))
        store().db.commit()
        store().event('model_plaza_settings', '已更新模型广场展示配置')
        return value.model_dump()

    @router.get('/api/model-plaza-user/catalog')
    async def catalog(request: Request):
        config = settings()
        if not config.enabled:
            return {'enabled': False, 'description': '', 'groups': []}
        try:
            result = await invoices().user_request(request.state.invoice_token, 'model-plaza')
        except HTTPException as error:
            if error.status_code == 404:
                raise HTTPException(503, '上游模型广场未启用或版本不支持，请检查 Sub2API 模型广场开关') from None
            raise
        if not isinstance(result.get('groups'), list):
            raise HTTPException(502, '模型广场响应格式无效')
        return {'enabled': True, 'description': config.description or result.get('description', ''),
                'groups': [group for group in result['groups'] if group.get('id') not in config.hidden_group_ids]}

    return router
