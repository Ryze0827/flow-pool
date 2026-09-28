"""OAuth/TOTP helpers adapted from Customer Recovery (MIT; see LICENSE)."""
import base64
import hashlib
import hmac
import json
import struct
import time
from urllib.parse import urlencode

AUTH = 'https://auth.openai.com'
CLIENT = 'app_EMoamEEZ73f0CkXaXp7hrann'
CALLBACK = 'http://localhost:1455/auth/callback'


class RecoveryError(Exception):
    pass


def totp(secret,at=None):
    secret=''.join(secret.split()).upper()
    if not secret:raise RecoveryError('Invalid TOTP secret')
    try:key=base64.b32decode(secret+'='*(-len(secret)%8))
    except ValueError:raise RecoveryError('Invalid TOTP secret') from None
    digest=hmac.new(key,struct.pack('>Q',int(time.time() if at is None else at)//30),hashlib.sha1).digest()
    offset=digest[-1]&15
    return str((struct.unpack('>I',digest[offset:offset+4])[0]&0x7fffffff)%1000000).zfill(6)



def claims(token):
    try:
        value=token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(value+'='*(-len(value)%4)))
    except (ValueError,IndexError,TypeError):raise RecoveryError('Invalid token document') from None



def auth_url(workspace_id,state,verifier):
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return AUTH+'/oauth/authorize?'+urlencode({
        'response_type':'code','client_id':CLIENT,'redirect_uri':CALLBACK,
        'scope':'openid profile email offline_access','code_challenge':challenge,
        'code_challenge_method':'S256','state':state,'prompt':'login',
        'id_token_add_organizations':'true','codex_cli_simplified_flow':'true',
        **({'allowed_workspace_id': workspace_id} if workspace_id else {})})



def workspace(item):
    return item.get('workspace_id', '')
