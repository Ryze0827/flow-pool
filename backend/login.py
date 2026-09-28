import asyncio
import contextlib
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

from fastapi.responses import JSONResponse

from .sub2api import UpstreamError

logger = logging.getLogger('uvicorn.error')
STAGES = {'initialize': '初始化', 'authorize': '授权入口', 'auth_redirect': '登录跳转',
          'identify': '账号识别', 'password': '密码校验', 'mfa_challenge': '2FA 挑战',
          'mfa_verify': '2FA 校验', 'workspace': '工作空间选择', 'token_exchange': '令牌交换',
          'security_challenge': '认证挑战'}

ERRORS = {
    'interactive_challenge_required': '官方认证入口要求 Cloudflare 交互验证，自动登录无法继续；请通过官方浏览器登录完成验证，或使用已有 JSON 导入',
    'auth_http_403': '官方认证服务拒绝请求（HTTP 403），请检查当前网络或通过官方浏览器登录',
    'auth_http_429': '官方认证服务限流（HTTP 429），请稍后再试',
    'invalid_password': '账号或密码不正确',
    'invalid_otp': '2FA 验证失败，请检查密钥和本机时间',
    'invalid_totp': '2FA 验证失败，请检查密钥和本机时间',
    'invalid_totp_secret': '2FA 应填写 Base32 密钥或 otpauth:// 地址，不是六位动态验证码',
    'totp_secret_required': '该账号需要 2FA，请填写验证器密钥',
    'account_deactivated': '账号已停用',
    'account_disabled': '账号已禁用',
    'unsupported_country_region_territory': '当前网络所在地区不支持登录',
    'additional_email_verification_required': '需要额外的邮箱验证，请在官方页面完成验证后再试',
    'additional_verification_required': '需要额外的账号验证，请在官方页面完成后再试',
    'workspace_selection_required': '请选择工作空间后重新登录；若未返回列表，请填写工作空间 ID',
    'workspace_mismatch': '返回凭据的工作空间与所选空间不一致，未生成入池预览',
    'account_mismatch': '返回凭据的邮箱与输入账号不一致，未生成入池预览',
    'expired_tokens': '返回凭据已过期，未生成入池预览',
    'incomplete_tokens': '官方认证未返回完整的访问令牌和刷新令牌',
    'auth_network_error': '连接官方认证服务失败，请检查本机网络',
    'login_deadline_exceeded': '登录超时，请检查网络后手动重试',
    'install_requirements_first': '缺少登录依赖，请执行 ./restart.sh 安装',
    'security_verification_unavailable': '认证挑战未完成，请检查网络和 Node.js 22+ 环境，或使用 JSON 导入',
}


class AccountLogin:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.process = None
        self.remote_task = None

    async def close(self):
        if self.remote_task:
            self.remote_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.remote_task
        if self.process and self.process.returncode is None:
            self.process.kill()
            await self.process.wait()

    async def run(self, value):
        if self.lock.locked():
            raise UpstreamError('已有账号正在登录，请等待完成后再试', 409)
        async with self.lock:
            if value.provider == 'session_studio':
                from .session_login import run
                attempt = uuid.uuid4().hex[:12]
                started = time.monotonic()
                logger.info('account_login provider=session_studio attempt=%s started', attempt)
                self.remote_task = asyncio.create_task(run(value))
                try:
                    result = await self.remote_task
                    logger.info('account_login provider=session_studio attempt=%s completed elapsed=%.2fs', attempt, time.monotonic() - started)
                    return result
                except UpstreamError as error:
                    # 第三方适配器只返回固定错误文案，不记录远端正文和凭据。
                    logger.warning('account_login provider=session_studio attempt=%s error=%s elapsed=%.2fs', attempt, error.message, time.monotonic() - started)
                    raise
                finally:
                    self.remote_task = None
            attempt = uuid.uuid4().hex[:12]
            started = time.monotonic()
            logger.info('account_login attempt=%s started', attempt)
            payload = {'email': value.email, 'password': value.password.get_secret_value(),
                       'totp_secret': value.totp_secret.get_secret_value(), 'workspace_id': value.workspace_id}
            env = {key: item for key, item in os.environ.items() if key.upper() in {'PATH', 'HOME', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
            try:
                self.process = await asyncio.create_subprocess_exec(
                    sys.executable, '-m', 'backend.login_worker', cwd=Path(__file__).resolve().parent.parent,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env)
                output, _ = await asyncio.wait_for(self.process.communicate(json.dumps(payload).encode()), timeout=210)
                if self.process.returncode != 0:
                    logger.warning('account_login attempt=%s error=worker_exit returncode=%s', attempt, self.process.returncode)
                    raise UpstreamError('登录进程未完成，请检查运行环境', 502)
                result = json.loads(output)
                if result.get('error'):
                    code = result['error']
                    diagnostic = result.get('diagnostic') or {}
                    stage = diagnostic.get('stage')
                    stage = stage if stage in STAGES else 'initialize'
                    status = diagnostic.get('http_status')
                    status = status if isinstance(status, int) else None
                    curl_code = diagnostic.get('curl_code')
                    curl_code = curl_code if isinstance(curl_code, int) else None
                    # 仅记录固定阶段、枚举错误码和数字状态，不记录账号/URL/响应正文或凭据。
                    safe_code = code if isinstance(code, str) and code.isascii() and code.replace('_', '').isalnum() and len(code) <= 80 else 'unknown_error'
                    logger.warning('account_login attempt=%s stage=%s error=%s http_status=%s curl_code=%s challenge=%s elapsed=%.2fs',
                                   attempt, stage, safe_code, status, curl_code, diagnostic.get('challenge') is True, time.monotonic() - started)
                    message = ERRORS.get(code, '官方登录流程未完成，可能需要额外验证或认证协议已变化；可使用 JSON 导入')
                    message += f'（阶段：{STAGES[stage]}，诊断编号：{attempt}）'
                    if code == 'workspace_selection_required':
                        return JSONResponse({'detail': message, 'workspaces': result.get('workspaces', [])}, status_code=409)
                    raise UpstreamError(message, 502)
                logger.info('account_login attempt=%s completed elapsed=%.2fs', attempt, time.monotonic() - started)
                return result
            except asyncio.TimeoutError:
                logger.warning('account_login attempt=%s error=worker_timeout elapsed=%.2fs', attempt, time.monotonic() - started)
                raise UpstreamError(ERRORS['login_deadline_exceeded'], 504) from None
            except (OSError, ValueError):
                logger.warning('account_login attempt=%s error=worker_response elapsed=%.2fs', attempt, time.monotonic() - started)
                raise UpstreamError('登录进程响应异常，请检查运行环境', 502) from None
            finally:
                payload.clear()
                await self.close()
                self.process = None
