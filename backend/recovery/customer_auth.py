"""Order-owned password + TOTP -> original workspace OAuth credentials. MIT."""
import base64
import hmac
import json
import re
import secrets
import time
import uuid
from urllib.parse import parse_qs,unquote,urljoin,urlsplit

try:
    from . import customer_recovery as core,customer_auth_security as security
except ImportError:
    import customer_recovery as core,customer_auth_security as security


class LoginError(core.RecoveryError):
    def __init__(self,code):self.code=code;super().__init__(code)


def request_stage(url):
    path = urlsplit(url).path
    return {
        '/oauth/authorize': 'authorize', '/oauth/token': 'token_exchange',
        '/api/accounts/authorize/continue': 'identify', '/api/accounts/password/verify': 'password',
        '/api/accounts/mfa/issue_challenge': 'mfa_challenge', '/api/accounts/mfa/verify': 'mfa_verify',
        '/api/accounts/workspace/select': 'workspace', '/backend-api/sentinel/req': 'security_challenge',
    }.get(path, 'auth_redirect')


def available_workspaces(data, cookies):
    sources = [data, data.get('page') or {}, (data.get('page') or {}).get('payload') or {}]
    # OAuth 会话中的空间列表只用于选择；最终仍核对官方 token 的邮箱和空间。
    try:
        cookie = unquote(cookies.get('oai-client-auth-session') or '')
        for part in cookie.split('.')[:2]:
            try:
                value = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
                if isinstance(value, dict):
                    sources.append(value)
            except (ValueError, TypeError):
                pass
    except Exception:
        pass
    result = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for item in source.get('workspaces') or []:
            if not isinstance(item, dict):
                continue
            identity = item.get('id') or item.get('workspace_id')
            if isinstance(identity, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,180}', identity):
                result[identity] = {'id': identity, 'name': str(item.get('name') or item.get('title') or identity)[:180]}
    return list(result.values())


def next_url(data,fallback=''):
    for container in (data,data.get('page') or {}):
        nested=container.get('payload') or {}
        value=container.get('continue_url') or container.get('url') or nested.get('continue_url') or nested.get('url')
        if value:return str(value)
    return fallback


def mfa_factor(data,url):
    for obj in (data,data.get('page') or {},(data.get('page') or {}).get('payload') or {}):
        if obj.get('factor_id'):return str(obj['factor_id'])
        for key in ('factors','mfa_factors','mfa_challenge_factors'):
            for factor in obj.get(key) or []:
                if isinstance(factor,dict) and (factor.get('type') or factor.get('factor_type')) in ('totp','authenticator'):
                    return str(factor.get('id') or factor.get('factor_id') or '')
    match=re.search(r'/mfa-challenge/([^/?#]+)',url)
    return match[1] if match else ''


def is_mfa(data,url):
    page=data.get('page') or {};kind=page.get('type') or page.get('name') or data.get('type') or data.get('page_type')
    return 'mfa-challenge' in url or 'mfa_challenge' in url or kind in ('mfa_challenge','mfa-challenge')


class Transport:
    """Fixed TLS origins; no implicit proxy or cross-origin redirects."""
    def __init__(self):
        try:
            from curl_cffi import requests,CurlOpt
        except ImportError:raise LoginError('install_requirements_first') from None
        self.profile,self.ua,brand=security.random_chrome_fingerprint()
        self.session=requests.Session(impersonate=self.profile,trust_env=False,curl_options={CurlOpt.PROXY:''})
        self.session.headers.update({'User-Agent':self.ua,'Accept-Language':'en-US,en;q=0.9','sec-ch-ua':brand,
                                     'sec-ch-ua-mobile':'?0','sec-ch-ua-platform':'"Windows"'})
        self.cookies=self.session.cookies;self.until=time.monotonic()+180
        self.diagnostic = {'stage': 'initialize'}

    def request(self,method,url,**kwargs):
        self.diagnostic = {'stage': request_stage(url)}
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.netloc not in ('auth.openai.com','sentinel.openai.com'):
            raise LoginError('unexpected_auth_origin')
        left=self.until-time.monotonic()
        if left<=0:raise LoginError('login_deadline_exceeded')
        kwargs.pop('allow_redirects',None);timeout=min(float(kwargs.pop('timeout',18)),18,left)
        try:
            response = self.session.request(method,url,allow_redirects=False,timeout=timeout,**kwargs)
        except Exception as error:
            code = getattr(error, 'code', None)
            if isinstance(code, int):self.diagnostic['curl_code'] = code
            raise LoginError('auth_network_error') from None
        self.diagnostic['http_status'] = response.status_code
        self.diagnostic['challenge'] = response.headers.get('cf-mitigated') == 'challenge'
        if self.diagnostic['challenge']:
            raise LoginError('interactive_challenge_required')
        return response

    def get(self,url,**kwargs):return self.request('GET',url,**kwargs)
    def post(self,url,**kwargs):return self.request('POST',url,**kwargs)
    def close(self):self.session.close()


def response_data(response):
    if response.status_code!=200:
        try:
            error=response.json().get('error') or {};code=error.get('code','') if isinstance(error,dict) else ''
        except (ValueError,AttributeError):code=''
        known={'invalid_password','invalid_otp','invalid_totp','account_deactivated','account_disabled','unsupported_country_region_territory'}
        raise LoginError(code if code in known else 'auth_http_'+str(int(response.status_code)))
    try:
        value=response.json()
        if not isinstance(value,dict):raise ValueError()
        return value
    except ValueError:raise LoginError('unexpected_auth_response') from None


def login(item,password,totp_secret,transport_factory=Transport):
    if not isinstance(password,str) or not password or len(password)>512:raise LoginError('password_required')
    if not isinstance(totp_secret,str):raise LoginError('totp_secret_required')
    try:
        if totp_secret:core.totp(totp_secret)
    except (ValueError,core.RecoveryError):raise LoginError('invalid_totp_secret') from None
    workspace=core.workspace(item);state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64)
    client=transport_factory();data={};selected=False;password_sent=False;mfa_sent=False
    try:
        current=core.auth_url(workspace,state,verifier)
        # The callback is parsed locally and never requested as a network URL.
        def follow(url):
            for _ in range(12):
                parsed=urlsplit(url)
                if parsed.scheme=='http' and parsed.netloc in ('localhost:1455','127.0.0.1:1455') and parsed.path=='/auth/callback':return url
                response=client.get(url,headers={'Accept':'text/html'})
                if response.status_code in (301,302,303,307,308):
                    url=urljoin(url,response.headers.get('Location',''));continue
                if response.status_code!=200:response_data(response)
                return str(response.url or url)
            raise LoginError('too_many_auth_redirects')
        current=follow(current)
        try:device=client.cookies.get('oai-did') or str(uuid.uuid4())
        except Exception:device=str(uuid.uuid4())
        sentinel=security.SentinelSolver(device,client.ua,{'tz':'UTC','lang':'en-US','langs':'en-US,en'})
        def post(path,body,flow=None):
            headers={'Accept':'application/json','Content-Type':'application/json','Origin':core.AUTH,
                     'Referer':current if current.startswith(core.AUTH+'/') else core.AUTH+'/log-in','User-Agent':client.ua}
            if flow:
                token=sentinel.build_token(client,flow,client.profile,'')
                if not token:raise LoginError('security_verification_unavailable')
                headers['openai-sentinel-token']=token
            return response_data(client.post(core.AUTH+path,json=body() if callable(body) else body,headers=headers))
        for _ in range(14):
            parsed=urlsplit(current);query=parse_qs(parsed.query)
            if parsed.scheme=='http' and parsed.netloc in ('localhost:1455','127.0.0.1:1455') and parsed.path=='/auth/callback':
                if len(query.get('state',[]))!=1 or not hmac.compare_digest(query['state'][0],state):raise LoginError('oauth_state_mismatch')
                if len(query.get('code',[]))!=1:raise LoginError('oauth_code_missing')
                tokens=response_data(client.post(core.AUTH+'/oauth/token',data={'grant_type':'authorization_code',
                    'client_id':core.CLIENT,'redirect_uri':core.CALLBACK,'code':query['code'][0],'code_verifier':verifier}))
                if not tokens.get('access_token') or not tokens.get('refresh_token'):raise LoginError('incomplete_tokens')
                tokens['_selected_workspace'] = workspace
                return tokens
            if is_mfa(data,current):
                if mfa_sent:raise LoginError('mfa_not_advanced')
                if not totp_secret:raise LoginError('totp_secret_required')
                factor=mfa_factor(data,current)
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,180}',factor):raise LoginError('mfa_factor_missing')
                post('/api/accounts/mfa/issue_challenge',{'id':factor,'type':'totp','force_fresh_challenge':False})
                data=post('/api/accounts/mfa/verify',lambda:{'id':factor,'type':'totp','code':core.totp(totp_secret)},'password_verify')
                mfa_sent=True;current=next_url(data,current);continue
            if 'log-in/password' in current:
                if password_sent:raise LoginError('password_not_advanced')
                data=post('/api/accounts/password/verify',{'password':password},'password_verify')
                password_sent=True;current=next_url(data,current);continue
            if 'email-verification' in current:raise LoginError('additional_email_verification_required')
            if 'add-phone' in current or 'about-you' in current:raise LoginError('additional_verification_required')
            if 'workspace' in current or 'consent' in current:
                if selected:raise LoginError('workspace_not_advanced')
                if not workspace:
                    choices = available_workspaces(data, client.cookies)
                    if len(choices) == 1:
                        workspace = choices[0]['id']
                    else:
                        error = LoginError('workspace_selection_required')
                        error.workspaces = choices
                        raise error
                data=post('/api/accounts/workspace/select',{'workspace_id':workspace})
                selected=True;current=follow(urljoin(core.AUTH,next_url(data)));continue
            if 'oauth2/auth' in current or 'oauth/authorize' in current:
                current=follow(urljoin(core.AUTH,current));continue
            if password_sent or mfa_sent:raise LoginError('unexpected_login_step')
            data=post('/api/accounts/authorize/continue',{'username':{'value':item['email'],'kind':'email'}},'authorize_continue')
            current=next_url(data,current)
        raise LoginError('login_step_limit')
    except LoginError as error:
        error.diagnostic = getattr(client, 'diagnostic', {})
        raise
    finally:client.close()
