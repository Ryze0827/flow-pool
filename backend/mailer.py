import asyncio
import contextlib
import certifi
import smtplib
import ssl
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid


POOL_NAMES = {'priority': '高权重组', 'risk': '风控组', 'third_party': '三方账号组'}


class MailError(Exception):
    pass


def validate_mail(settings, recipients=True):
    if not settings.smtp_host or not settings.smtp_from_email:
        raise MailError('请填写 SMTP 地址和发件邮箱')
    if settings.smtp_username and not settings.smtp_password:
        raise MailError('请填写 SMTP 密码或邮箱授权码')
    if settings.smtp_username and not settings.smtp_use_tls:
        raise MailError('SMTP 密码认证必须启用 TLS')
    if recipients and not settings.recipients:
        raise MailError('请至少配置一个收件邮箱')


def smtp_send(settings, payload=None):
    validate_mail(settings, recipients=payload is not None)
    client = None
    try:
        # macOS 的 Python 可能没有配置系统 CA 路径；使用虚拟环境的
        # certifi 公共根证书，避免 smtp.resend.com 等合法证书被误判为不可信。
        context = ssl.create_default_context(cafile=certifi.where())
        if settings.smtp_use_tls and settings.smtp_port == 465:
            client = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=context)
        else:
            client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
            client.ehlo_or_helo_if_needed()
            if settings.smtp_use_tls:
                client.starttls(context=context)
                client.ehlo()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        if payload is not None:
            message = EmailMessage()
            message['From'] = formataddr((settings.smtp_from_name, settings.smtp_from_email))
            message['To'] = ', '.join(payload['recipients'])
            message['Subject'] = payload['subject']
            message['Date'] = formatdate(localtime=True)
            message['Message-ID'] = payload['message_id']
            message.set_content(payload['body'])
            refused = client.send_message(message, from_addr=settings.smtp_from_email, to_addrs=payload['recipients'])
            if refused:
                raise MailError(f'部分收件人被拒收（{len(refused)} 个），其余已提交；重发可能产生重复邮件')
    except smtplib.SMTPAuthenticationError:
        raise MailError('SMTP 认证失败，请检查用户名、密码或邮箱授权码') from None
    except smtplib.SMTPRecipientsRefused:
        raise MailError('所有收件人均被拒收，请检查收件邮箱和 SMTP 权限') from None
    except ssl.SSLError:
        raise MailError('TLS 连接失败，请检查证书、端口和 TLS 配置') from None
    except smtplib.SMTPNotSupportedError:
        raise MailError('SMTP 服务不支持所选 TLS 或认证方式') from None
    except (smtplib.SMTPException, OSError):
        raise MailError('SMTP 连接或发送失败，结果可能未确认；请检查邮箱及服务配置') from None
    finally:
        if client is not None:
            # DATA 已确认后，QUIT 的异常不能把成功发送标记为失败。
            with contextlib.suppress(Exception):
                client.quit()
            with contextlib.suppress(Exception):
                client.close()


class Mailer:
    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()

    def enqueue(self, account, action, message, rule):
        kind = 'cooldown' if action in {'停止调度', '仅生图调度'} else 'warning' if action in {'告警', '暂缓停调', '停调未确认', '冷却切换未确认', '提前恢复调度', '恢复未确认'} else None
        if kind is None:
            return
        settings = self.store.mail_settings()
        if not settings.enabled:
            return
        if not getattr(settings, 'notify_' + kind):
            return
        validate_mail(settings)
        current = self.store.account(account['id'])
        if kind == 'warning':
            if not rule.enabled or current['pool'] not in rule.guarded_pools:
                return
            if action == '暂缓停调':
                now = time.time()
                count = sum(now - rule.breach_window_seconds < stamp <= now for stamp in current['breach_times'])
                if count < rule.breach_count:
                    return
        label = '冷却通知' if kind == 'cooldown' else '首字延迟告警'
        name = ' '.join(current['name'].splitlines())
        subject = label
        measures = {'告警': '告警，继续调度', '暂缓停调': '暂缓冷却，继续调度',
                    '停调未确认': '停止调度未确认，等待重试', '冷却切换未确认': '生图冷却未确认，等待重试',
                    '提前恢复调度': '替补不可用，已提前恢复当前账号', '恢复未确认': '替补不可用，当前账号恢复待重试'}
        measure = measures.get(action, action)
        if kind == 'cooldown':
            restore = datetime.fromtimestamp(current['resume_at']).astimezone().strftime('%H:%M')
            measure += f'，{restore} 恢复全部分组' if current.get('cooldown_mode') == 'image' else f'，{restore} 恢复调度'
        replacement_id = account.get('cooldown_replacement_id')
        replacement = self.store.account(replacement_id) if replacement_id else None
        replacement_text = f"{' '.join(replacement['name'].splitlines())}（#{replacement_id}）" if replacement else f'#{replacement_id}' if replacement_id else '否'
        body = f"账号：{name}（#{account['id']} · {POOL_NAMES[current['pool']]}）\n措施：{measure}\n拉起账号：{replacement_text}\n"
        payload = dict(subject=subject, body=body, recipients=settings.recipients, message_id=make_msgid())
        key = f"cooldown:{account['id']}:{current['resume_at']}" if kind == 'cooldown' else None
        self.store.queue_mail(kind, payload, account['id'], key, settings.warning_interval_seconds if kind == 'warning' else 0)

    async def run(self):
        # 重启前发送中的邮件可能已经送达，不自动重复发送。
        self.store.db.execute("UPDATE mail_messages SET status='failed', error='服务重启，发送结果未确认；请检查收件箱后再重发' WHERE status='sending'")
        self.store.db.commit()
        while True:
            try:
                await self.process_one()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.store.event('mail_error', '邮件任务执行异常，请检查邮件通知页面', level='error')
            await asyncio.sleep(2)

    async def process_one(self):
        async with self.lock:
            row = self.store.db.execute("SELECT * FROM mail_messages WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
            if not row:
                return
            if row['kind'] == 'test':
                self.store.update_mail(row['id'], 'failed', '测试发送被中断，请重新发送测试邮件')
                return
            settings = self.store.mail_settings()
            if not settings.enabled or not getattr(settings, 'notify_' + row['kind']):
                self.store.update_mail(row['id'], 'skipped', '对应邮件通知已关闭')
                return
            await self.deliver(row['id'], settings, self.store.unseal(row['payload']))

    async def deliver(self, message_id, settings, payload):
        self.store.update_mail(message_id, 'sending')
        try:
            validate_mail(settings)
            await asyncio.to_thread(smtp_send, settings, payload)
        except Exception as error:
            detail = str(error) if isinstance(error, MailError) else '邮件发送异常，请检查 SMTP 配置'
            self.store.update_mail(message_id, 'failed', detail)
        else:
            self.store.update_mail(message_id, 'sent', sent_at=time.time())

    async def test(self, settings, send=False):
        validate_mail(settings, recipients=send)
        async with self.lock:
            if not send:
                await asyncio.to_thread(smtp_send, settings)
                return {'ok': True, 'message': 'SMTP 连接和认证成功'}
            payload = dict(subject='邮件通知测试', body='这是一封 FlowPool 测试邮件，收到此邮件表示 SMTP 发送配置可用。', recipients=settings.recipients, message_id=make_msgid())
            message_id = self.store.queue_mail('test', payload)
            await self.deliver(message_id, settings, payload)
            row = self.store.db.execute('SELECT status, error FROM mail_messages WHERE id=?', (message_id,)).fetchone()
            if row['status'] != 'sent':
                raise MailError(row['error'])
            return {'ok': True, 'message': '测试邮件已提交 SMTP，请检查收件箱'}
