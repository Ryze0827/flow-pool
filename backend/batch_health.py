import re
import time

from .sub2api import unavailable_reason


def authentication_failed(remote):
    error = str(remote.get('error_message') or '').casefold()
    return remote.get('status') == 'error' and bool(re.search(r'\b401\b|unauthorized|invalid_grant|token[_ ](?:expired|revoked|invalid)|(?:access|refresh)[_ ]token.*(?:expired|revoked|invalid)|authentication failed|凭据失效|登录失效', error))


def inspect_item(item, remote, local, now):
    result = {'checked_at': time.time(), 'status': 'unknown', 'message': '',
              'remote_status': remote.get('status') if remote else None,
              'schedulable': remote.get('schedulable') if remote else None,
              'local_pool': local['pool'] if local else None,
              'local_state': local['state'] if local else None}
    if not item.get('account_id'):
        result.update(status='not_imported', message='尚未关联上游账号，请查看导入结果或重新登录并推送')
    elif not remote:
        result.update(status='missing', message='上游账号已不存在，请核对 Sub2API')
    else:
        # 只返回分类说明，不把可能包含令牌的上游错误正文传给浏览器。
        if authentication_failed(remote):
            result.update(status='auth_expired', message='上游记录了 401 / 登录凭据失效，请重新登录并推送')
        elif remote.get('status') == 'error':
            result.update(status='error', message='上游账号错误，请核对原因；若为登录失效，可重新登录并推送')
        elif remote.get('status') != 'active':
            result.update(status='inactive', message='上游账号已停用')
        else:
            reason = unavailable_reason(remote, now)
            if reason:
                result.update(status='unavailable', message=reason)
            elif not local:
                result.update(status='not_enrolled', message='上游未见异常，但账号不在本地号池')
            else:
                result.update(status='healthy', message='上游状态正常、调度开启；未发起模型调用验证')
    return result
