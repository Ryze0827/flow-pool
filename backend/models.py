import re
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator

from .model_catalog import OPENAI_MODELS

Pool = Literal['priority', 'risk', 'third_party']
UsageColumn = Literal['model', 'reasoning_effort', 'group']


class Rule(BaseModel):
    enabled: bool = False
    alert_only: bool = False
    guarded_pools: list[Pool] = Field(default_factory=lambda: ['risk'], max_length=3)
    replacement_pools: list[Pool] = Field(default_factory=list, max_length=3)
    sample_size: int = Field(10, ge=1, le=100)
    threshold_ms: int = Field(10000, ge=100, le=300000)
    slow_ratio: float = Field(0.5, ge=0, le=1)
    breach_window_seconds: int = Field(120, ge=10, le=86400)
    breach_count: int = Field(2, ge=1, le=100)
    breach_min_interval_seconds: int = Field(30, ge=1, le=3600)
    breach_fresh_ratio: float = Field(0.5, gt=0, le=1)
    cooldown_seconds: int = Field(1200, ge=10, le=86400)
    probation_seconds: int = Field(60, ge=10, le=3600)
    poll_seconds: int = Field(20, ge=5, le=300)


def account_guarded(account, rule):
    # 分组守护是前置条件；组内未单独设置的账号默认开启。
    if account.get('pool') not in rule.guarded_pools:
        return False
    value = account.get('guard_enabled')
    return bool(value) if value is not None else True


class GuardUpdate(BaseModel):
    enabled: bool


class MailSettings(BaseModel):
    enabled: bool = False
    smtp_host: str = Field('', max_length=253)
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_username: str = Field('', max_length=254)
    smtp_password: str = Field('', max_length=4096)
    smtp_from_email: str = Field('', max_length=254)
    smtp_from_name: str = Field('FlowPool 账号守护', max_length=120)
    smtp_use_tls: bool = True
    recipients: list[str] = Field(default_factory=list, max_length=20)
    notify_warning: bool = True
    notify_cooldown: bool = True
    notify_recovery: bool = True
    notify_rpm: bool = True
    warning_interval_seconds: int = Field(900, ge=60, le=86400)

    @field_validator('smtp_host', 'smtp_username', 'smtp_from_name')
    @classmethod
    def single_line(cls, value):
        if '\r' in value or '\n' in value or '\x00' in value:
            raise ValueError('不能包含换行或控制字符')
        return value.strip()

    @field_validator('smtp_host')
    @classmethod
    def smtp_host_valid(cls, value):
        if value and not re.fullmatch(r'[^\s/@?#]+', value):
            raise ValueError('请输入 SMTP 主机名或 IP，不包含协议和路径')
        return value

    @field_validator('smtp_from_email')
    @classmethod
    def email_valid(cls, value):
        value = value.strip()
        if value and not re.fullmatch(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', value):
            raise ValueError('请输入有效邮箱地址')
        return value

    @field_validator('recipients')
    @classmethod
    def recipients_valid(cls, values):
        result = []
        for value in values:
            value = cls.email_valid(value)
            if not value:
                raise ValueError('收件邮箱不能为空')
            if len(value) > 254:
                raise ValueError('收件邮箱过长')
            if value not in result:
                result.append(value)
        return result


class ModelMapping(BaseModel):
    source: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=200)

    @field_validator('source', 'target')
    @classmethod
    def model_valid(cls, value, info):
        value = value.strip()
        if not value or any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError('模型名称不能为空或包含空白字符')
        if '*' in value and (info.field_name == 'target' or value.count('*') != 1 or not value.endswith('*')):
            raise ValueError('请求模型仅支持末尾的一个 *，目标模型不支持通配符')
        return value


class ImportOptions(BaseModel):
    pool: Pool = 'third_party'
    name_template: str = Field('gpt-{email}', min_length=1, max_length=120)
    concurrency: int = Field(10, ge=1, le=1000)
    priority: int = Field(1, ge=0, le=10000)
    rate_multiplier: float = Field(1, ge=0, le=1000)
    load_factor: int = Field(500, ge=1, le=10000)
    group_ids: list[int] = Field(default_factory=list, max_length=200)
    proxy_id: int | None = Field(None, ge=1)
    notes: str = Field('', max_length=2000)
    auto_pause_on_expired: bool = True
    confirm_mixed_channel_risk: bool = False
    duplicate: Literal['skip', 'update'] = 'skip'
    model_whitelist: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=lambda: list(OPENAI_MODELS), max_length=200)
    model_mappings: list[ModelMapping] = Field(default_factory=list, max_length=200)

    @field_validator('model_whitelist')
    @classmethod
    def whitelist_valid(cls, values):
        # Sub2API 白名单使用同名映射，不允许将通配符作为上游模型名。
        models = [ModelMapping(source=value, target=value).target for value in values]
        if len(set(models)) != len(models):
            raise ValueError('白名单模型不能重复配置')
        return models

    @field_validator('model_mappings')
    @classmethod
    def mappings_valid(cls, values):
        if len({item.source for item in values}) != len(values):
            raise ValueError('请求模型不能重复配置')
        return values

    @field_validator('name_template')
    @classmethod
    def template_valid(cls, value):
        import string
        try:
            fields = list(string.Formatter().parse(value))
        except ValueError:
            raise ValueError('名称模板格式不正确')
        for _, name, spec, conversion in fields:
            if name is not None and (name not in {'email', 'id', 'index', 'workspace'} or spec or conversion):
                raise ValueError('仅支持 {email}、{id}、{index}、{workspace}')
        return value


class RechargeTier(BaseModel):
    minimum: float = Field(ge=0, le=1e12, allow_inf_nan=False)
    rate: float = Field(gt=0, le=1000, allow_inf_nan=False)

    @field_validator('rate')
    @classmethod
    def rate_precision(cls, value):
        amount = Decimal(str(value))
        if amount != amount.quantize(Decimal('0.0001')):
            raise ValueError('倍率最多支持 4 位小数')
        return value


class RateRules(BaseModel):
    # None preserves the legacy named defaults until the administrator saves a selection.
    group_ids: list[Annotated[int, Field(strict=True, ge=1, le=9223372036854775807)]] | None = Field(None, min_length=1, max_length=200)
    tiers: list[RechargeTier] = Field(default_factory=lambda: [
        RechargeTier(minimum=50, rate=0.28), RechargeTier(minimum=100, rate=0.26),
        RechargeTier(minimum=200, rate=0.22), RechargeTier(minimum=500, rate=0.18),
    ], min_length=1, max_length=50)

    @field_validator('group_ids')
    @classmethod
    def groups_valid(cls, values):
        if values is not None:
            if len(values) != len(set(values)):
                raise ValueError('巡检分组不能重复')
            return sorted(values)
        return None

    @field_validator('tiers')
    @classmethod
    def tiers_valid(cls, values):
        if len({tier.minimum for tier in values}) != len(values):
            raise ValueError('充值门槛不能重复')
        return sorted(values, key=lambda tier: tier.minimum)


class PostgresSettings(BaseModel):
    host: str = Field('127.0.0.1', min_length=1, max_length=253)
    port: int = Field(5432, ge=1, le=65535)
    database: str = Field('sub2api', min_length=1, max_length=128)
    username: str = Field('', max_length=128)
    password: str = Field('', max_length=4096)
    sslmode: Literal['disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'] = 'prefer'
    connect_timeout: int = Field(5, ge=1, le=30)
    statement_timeout: int = Field(30, ge=1, le=300)

    @field_validator('host', 'database', 'username')
    @classmethod
    def pg_text_valid(cls, value, info):
        if any(ord(char) < 32 for char in value):
            raise ValueError('不能包含控制字符')
        value = value.strip()
        if not value and info.field_name in {'host', 'database'}:
            raise ValueError('不能为空')
        return value


class PostgresSettingsUpdate(PostgresSettings):
    clear_password: bool = False


class Settings(BaseModel):
    base_url: str = ''
    admin_key: str = Field('', max_length=4096)
    rule: Rule = Field(default_factory=Rule)
    import_options: ImportOptions = Field(default_factory=lambda: ImportOptions(model_mappings=[
        ModelMapping(source='gpt-5.6-luna', target='gpt-5.6-sol'),
        ModelMapping(source='gpt-5.6-terra', target='gpt-5.6-sol'),
        ModelMapping(source='gpt-6-luna', target='gpt-6-sol'),
    ]))
    usage_visible_columns: list[UsageColumn] = Field(default_factory=list, max_length=3)

    @field_validator('base_url')
    @classmethod
    def url_valid(cls, value):
        value = value.strip().rstrip('/')
        if not value:
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('请输入 http(s) 服务地址，不包含用户名、密码、查询参数')
        for suffix in ('/api/v1/admin', '/api/v1'):
            if value.endswith(suffix):
                value = value[:-len(suffix)]
        return value


class AdminLogin(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    password: SecretStr = Field(min_length=1, max_length=512)
    turnstile_token: str = Field('', max_length=4096)


class AdminLogin2FA(BaseModel):
    temp_token: str = Field(min_length=1, max_length=4096)
    totp_code: str = Field(pattern=r'^\d{6}$')


class RateInspectionRequest(BaseModel):
    emails: str = Field('', max_length=100000)


class RateCorrectionRequest(BaseModel):
    inspection_id: str = Field(min_length=1, max_length=64)
    row_ids: list[str] = Field(min_length=1, max_length=10000)



class Enroll(BaseModel):
    account_ids: list[int] = Field(min_length=1, max_length=500)
    pool: Pool


class Preview(BaseModel):
    payload: dict | list
    options: ImportOptions


class LoginCredentials(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    password: SecretStr = Field(min_length=1, max_length=512)
    totp_secret: SecretStr = Field(default_factory=lambda: SecretStr(''), max_length=1024)
    workspace_id: str = Field(default='', max_length=180, pattern=r'^[A-Za-z0-9_-]*$')


class ReloginImport(LoginCredentials):
    provider: Literal['local', 'session_studio'] = 'session_studio'


class SavedRelogin(BaseModel):
    model_config = {'extra': 'forbid'}
    use_saved: Literal[True]
    provider: Literal['local', 'session_studio'] | None = None


class LoginImport(ReloginImport):
    options: ImportOptions
    batch_id: str | None = Field(default=None, max_length=64)
