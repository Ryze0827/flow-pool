"""Bounded, disposable login worker. Credentials arrive on stdin, never argv."""
import json
import sys
import time
from urllib.parse import parse_qs, urlsplit

from .recovery.customer_auth import LoginError, login
from .recovery.customer_recovery import RecoveryError, claims


def normalize_secret(value):
    value = value.strip()
    if value.startswith('otpauth://'):
        parsed = urlsplit(value)
        if parsed.netloc != 'totp':
            raise LoginError('invalid_totp_secret')
        value = (parse_qs(parsed.query).get('secret') or [''])[0]
    return ''.join(value.split()).upper()


def export_account(email, tokens, workspace_id=''):
    access = tokens.get('access_token')
    refresh = tokens.get('refresh_token')
    if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
        raise LoginError('incomplete_tokens')
    access_claims = claims(access)
    identity = claims(tokens.get('id_token') or access)
    if not isinstance(access_claims, dict) or not isinstance(identity, dict):
        raise LoginError('unexpected_auth_response')
    auth = access_claims.get('https://api.openai.com/auth') or {}
    identity_auth = identity.get('https://api.openai.com/auth') or {}
    profile = identity.get('https://api.openai.com/profile') or {}
    actual_email = identity.get('email') or profile.get('email') or ''
    team = auth.get('chatgpt_account_id') or identity_auth.get('chatgpt_account_id')
    expected_workspace = workspace_id or tokens.get('_selected_workspace')
    if actual_email.lower() != email.lower():
        raise LoginError('account_mismatch')
    if not team or (expected_workspace and team != expected_workspace):
        raise LoginError('workspace_mismatch')
    expires_at = int(access_claims.get('exp') or time.time() + int(tokens.get('expires_in', 3600)))
    if expires_at <= time.time():
        raise LoginError('expired_tokens')
    credentials = {key: tokens[key] for key in ('access_token', 'refresh_token', 'id_token', 'expires_in') if key in tokens}
    credentials.update(email=email, chatgpt_account_id=team, expires_at=expires_at)
    for key in ('chatgpt_user_id', 'organization_id', 'chatgpt_plan_type'):
        value = auth.get(key) or identity_auth.get(key)
        if value:
            credentials['plan_type' if key == 'chatgpt_plan_type' else key] = value
    return {'accounts': [{'name': email, 'email': email, 'platform': 'openai', 'type': 'oauth', 'credentials': credentials}]}


def main():
    try:
        value = json.load(sys.stdin)
        email = value['email']
        workspace_id = value.get('workspace_id', '')
        tokens = login({'email': email, 'workspace_id': workspace_id}, value['password'], normalize_secret(value.get('totp_secret', '')))
        result = {'payload': export_account(email, tokens, workspace_id)}
    except LoginError as error:
        result = {'error': error.code, 'workspaces': getattr(error, 'workspaces', []), 'diagnostic': getattr(error, 'diagnostic', {})}
    except RecoveryError:
        result = {'error': 'unexpected_auth_response'}
    except Exception:
        # 不输出异常原文 / traceback，避免凭据或上游响应进入服务日志。
        result = {'error': 'login_failed'}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
