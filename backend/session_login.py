import asyncio
import json
import re
import uuid

import httpx

from .login_worker import export_account, normalize_secret
from .recovery.customer_auth import LoginError
from .recovery.customer_recovery import RecoveryError
from .sub2api import UpstreamError

ENDPOINT = 'https://session.ameng2027.xyz/api/v1/relogin'
TIMEOUT_SECONDS = 1560
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
ERRORS = {
    'human_verification': '第三方登录服务仍遇到人机验证，请在官方页面完成验证后重试，或使用 JSON 导入',
    'captcha_required': '第三方登录服务需要人机验证，无法自动完成，请使用 JSON 导入',
    'risk_check': '第三方登录服务被上游安全校验拦截，请在官方页面确认账号状态',
    'password_invalid': '账号或密码不正确',
    'credentials_invalid': '账号或密码不正确',
    'mfa_code_invalid': '2FA 验证失败，请检查密钥或动态验证码',
    'mfa_code_expired': '2FA 验证码已过期，请使用密钥或新的验证码重试',
    'account_disabled': '账号已被停用或禁用',
    'account_deactivated': '账号已停用',
    'rate_limited': '第三方登录服务或上游已限流，请稍后手动重试',
    'queue_full': '第三方登录队列已满，请稍后手动重试',
    'service_busy': '第三方登录服务繁忙，请稍后手动重试',
    'queue_timeout': '第三方登录排队超时，请稍后手动重试',
    'task_timeout': '第三方登录任务超时，请稍后手动重试',
    'origin_mismatch': '第三方服务不接受当前来源的请求，请使用该网站导出 JSON 后导入',
}


def read_result(body, content_type):
    if 'application/x-ndjson' not in content_type:
        result = json.loads(body)
    else:
        result = None
        finished = False
        for line in body.splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict) or finished:
                raise ValueError('Invalid stream')
            if item.get('type') == 'result':
                result = item.get('payload')
                finished = True
    if not isinstance(result, dict):
        raise ValueError('Missing result')
    return result


async def run(value):
    try:
        return await asyncio.wait_for(login(value), timeout=TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise UpstreamError('第三方登录等待超时，未生成入池预览；请稍后手动重试', 504) from None


async def login(value):
    headers = {'X-Session-Studio-Relogin': '1', 'X-Session-Studio-Client': str(uuid.uuid4()),
               'Accept': 'application/x-ndjson, application/json'}
    secret = value.totp_secret.get_secret_value().strip()
    payload = {'action': 'start', 'email': value.email, 'password': value.password.get_secret_value(),
               'auth_mode': 'password_2fa' if secret else 'password', 'phone_binding_mode': 'manual'}
    if secret:
        if re.fullmatch(r'\d{6}', secret):
            payload['mfa_code'] = secret
        else:
            try:
                secret = normalize_secret(secret)
            except (LoginError, ValueError):
                raise UpstreamError('2FA 地址无效，请填写 TOTP 密钥或有效 otpauth:// 地址', 400) from None
            if not re.fullmatch(r'[A-Z2-7]{8,512}={0,6}', secret):
                raise UpstreamError('2FA 应为 Base32 密钥、otpauth:// 地址或六位动态验证码', 400)
            payload['mfa_secret'] = secret
    if value.workspace_id:
        payload['account_id'] = value.workspace_id
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=20), follow_redirects=False, headers=headers) as client:
            async with client.stream('POST', ENDPOINT, json=payload) as response:
                if response.status_code in {401, 403}:
                    raise UpstreamError('第三方网站拒绝 API 请求或要求身份 / 人机验证，请使用网站导出 JSON 后导入', 502)
                if response.status_code == 429:
                    raise UpstreamError(ERRORS['rate_limited'], 502)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise ValueError('Response too large')
                result = read_result(body, response.headers.get('content-type', ''))
                if not response.is_success or result.get('error') or result.get('status') in {'failed', 'error', 'blocked', 'denied'}:
                    error = result.get('error') if isinstance(result.get('error'), dict) else {}
                    code = error.get('code') or result.get('error_code')
                    message = ERRORS.get(code, '第三方登录未成功，可能需要额外验证或服务协议已变化；请在该网站检查后使用 JSON 导入') if isinstance(code, str) else '第三方登录返回异常结果，未生成入池预览'
                    raise UpstreamError(message, 502)
            if result.get('status') == 'challenge_required':
                challenge = result.get('challenge') or {}
                challenge_id = challenge.get('id') if isinstance(challenge, dict) else None
                if isinstance(challenge_id, str) and len(challenge_id) <= 512:
                    try:
                        async with client.stream('POST', ENDPOINT, json={'action': 'cancel', 'challenge_id': challenge_id}, timeout=10):
                            pass
                    except httpx.HTTPError:
                        pass
                raise UpstreamError('该账号需要额外邮箱 / 手机验证，自动流程已停止；请在该网站完成重登并导出 JSON 后导入', 409)
            credential = result.get('credential')
            if not isinstance(credential, dict) or not all(isinstance(credential.get(key), str) and credential[key] for key in ('access_token', 'refresh_token', 'id_token')):
                raise ValueError('Incomplete credentials')
            # 与本地登录共用邮箱、工作空间、有效期检查及 Sub2API JSON 格式。
            exported = export_account(value.email, credential, value.workspace_id)
            if isinstance(credential.get('client_id'), str) and credential['client_id']:
                exported['accounts'][0]['credentials']['client_id'] = credential['client_id']
            return {'payload': exported}
    except (asyncio.TimeoutError, httpx.TimeoutException):
        raise UpstreamError('第三方登录等待超时，未生成入池预览；请稍后手动重试', 504) from None
    except httpx.HTTPError:
        raise UpstreamError('无法连接第三方登录服务，请检查网络或稍后重试', 502) from None
    except LoginError as error:
        messages = {'account_mismatch': '返回凭据的邮箱与输入账号不一致', 'workspace_mismatch': '返回凭据的工作空间与所选空间不一致', 'expired_tokens': '返回凭据已过期'}
        raise UpstreamError(messages.get(error.code, '第三方返回凭据未通过校验') + '，未生成入池预览', 502) from None
    except (RecoveryError, ValueError, TypeError, AttributeError, KeyError, OverflowError):
        raise UpstreamError('第三方返回格式异常或凭据不完整，未生成入池预览', 502) from None
    finally:
        payload.clear()
