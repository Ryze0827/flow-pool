import asyncio
import json
import math
import time
import contextlib

from .models import account_guarded
from .sub2api import Sub2API, UpstreamError, image_only_account, public_account
from .batch_health import authentication_failed, post_import_observing, observation_error, inspect_item
from .analytics import Analytics
from .importer import ReloginCancelled
from . import updater


def expiry_timestamp(value):
    from datetime import datetime
    if isinstance(value, (int, float)):
        return float(value)
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()


class Scheduler:
    def __init__(self, store, mailer=None):
        self.store = store
        self.mailer = mailer
        self.lock = asyncio.Lock()
        self.wakeup = asyncio.Event()
        self.last_tick = None
        self.last_error = None
        self.running = False
        self.relogin_handler = None
        self.relogin_busy = lambda: False
        self.relogin_task = None
        self.relogin_account_id = None

    async def close(self):
        if self.relogin_task and not self.relogin_task.done():
            self.relogin_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.relogin_task

    def recovery_notice(self, account, message, action):
        self.store.update(account['id'], error=message)
        if account.get('error') != message:
            self.store.event('auto_relogin', message, account['id'], 'warning')
            self.store.inspection(account['id'], account['state'], 0, 0, 0, action, message)

    async def recover_account(self, account, recovery):
        try:
            await self.relogin_handler(account, recovery)
        except asyncio.CancelledError:
            raise
        except ReloginCancelled as error:
            current = self.store.account(account['id'])
            if current and self.store.recovery(account['id']) == recovery:
                self.store.update(account['id'], relogin_last_at=time.time())
                self.store.inspection(account['id'], current['state'], 0, 0, 0, '取消自动重登', error.message)
        except Exception as error:
            # 登录适配器及管理员 API 的异常文案已脱敏，未知异常不回显内容。
            detail = error.message if isinstance(error, UpstreamError) else '自动重登失败，请检查登录环境'
            current = self.store.account(account['id'])
            if current and self.store.recovery(account['id']) == recovery:
                failures = current.get('relogin_failures', 0) + 1
                action = '放弃自动重登' if failures > 3 else '自动重登失败'
                detail = f'已失败 {failures} 次，' + ('已放弃自动重登；' if failures > 3 else '等待重试；') + detail
                self.store.update(account['id'], relogin_failures=failures, relogin_error=detail, relogin_last_at=time.time(), error=detail)
                self.store.event('auto_relogin_error', detail, account['id'], 'error')
                self.store.inspection(account['id'], current['state'], 0, 0, 0, action, detail)
        finally:
            self.relogin_account_id = None

    async def run(self):
        while True:
            self.wakeup.clear()
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.last_error = '调度周期执行异常，请检查本地日志'
                self.store.event('worker_error', self.last_error, level='error')
            interval = self.store.settings().rule.poll_seconds
            if any(post_import_observing(account) for account in self.store.accounts()):
                interval = min(interval, 20)
            try:
                await asyncio.wait_for(self.wakeup.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def tick(self):
        async with self.lock:
            settings = self.store.settings()
            if not settings.base_url or not settings.admin_key:
                return
            self.running = True
            try:
                async with Sub2API(settings) as client:
                    for account in self.store.accounts():
                        try:
                            await self.check(client, account, settings.rule)
                        except UpstreamError as error:
                            if account.get('error') != error.message:
                                self.store.event('upstream_error', error.message, account['id'], 'error')
                            self.store.update(account['id'], error=error.message)
                self.last_error = None
                self.last_tick = time.time()
            finally:
                self.running = False

    async def check(self, client, account, rule):
        account_id = account['id']
        remote = await client.account(account_id)
        # 远端状态刷新不等于首字延迟巡检。只有下面真正进入样本采集时，
        # 才更新 last_checked，避免关闭守护或手动停调的账号显示“仍在巡检”。
        self.store.update(account_id, remote=public_account(remote), name=remote['name'], error=None)
        if post_import_observing(account):
            health = inspect_item({'account_id': account_id}, remote, self.store.account(account_id), client.now())
            self.store.update(account_id, post_import_health=health)
        image_only = image_only_account(remote)
        if image_only:
            # 同步生图没有首字指标，清除旧告警，但保留冷却和恢复任务。
            values = dict(sample=[], slow_count=0, breach_times=[], breach_sample='', last_breach_at=None, last_checked=None)
            if account['state'] == 'probation':
                values.update(state='active', probation_until=None)
            self.store.update(account_id, **values)
            account = {**account, **values}
        # 非 active、到期自动暂停账号不被本系统强行复活。
        expired = False
        if remote.get('expires_at') and remote.get('auto_pause_on_expired', True):
            expired = expiry_timestamp(remote['expires_at']) <= client.now()
        observing = post_import_observing(account)
        observed_error = observing and account['state'] in {'active', 'probation'} and observation_error(remote, client.now())
        if (observed_error or (not observing and authentication_failed(remote) and account.get('pool') == 'third_party')) and not expired:
            label = '入池观察异常' if observing else '凭据失效'
            recovery = self.store.recovery(account_id)
            if not recovery:
                self.recovery_notice(account, f'{label} · 未保存账密，请手动重登', '跳过自动重登')
            elif self.relogin_account_id == account_id:
                self.store.update(account_id, error=f'{label} · 自动重登中')
            elif account.get('relogin_failures', 0) > 3:
                self.store.update(account_id, error=account.get('relogin_error') or '重登失败超过 3 次，已放弃自动重登')
            elif not rule.enabled or account['state'] in {'manual', 'pausing', 'resuming'} or (remote.get('schedulable') is False and account['state'] != 'cooldown' and not observed_error):
                self.recovery_notice(account, '凭据失效 · 守护关闭或已暂停，暂不重登', '跳过自动重登')
            elif time.time() - (account.get('relogin_last_at') or 0) < (60 if observing else 300):
                self.store.update(account_id, error=account.get('relogin_error') or '凭据失效 · 等待重试')
            elif (self.relogin_task and not self.relogin_task.done()) or self.relogin_busy() or updater.locked(self.store.directory) or (self.store.directory / 'upgrade-deploying').exists():
                self.store.update(account_id, error='凭据失效 · 等待登录通道')
            elif self.relogin_handler:
                self.store.update(account_id, relogin_last_at=time.time(), relogin_error='', relogin_count=account.get('relogin_count', 0) + 1)
                self.recovery_notice(account, f'{label} · 自动重登中', '自动重登')
                self.relogin_account_id = account_id
                self.relogin_task = asyncio.create_task(self.recover_account(self.store.account(account_id), recovery))
            else:
                self.store.update(account_id, error='凭据失效 · 登录服务未就绪')
            return
        if remote.get('status') != 'active' or expired:
            self.store.update(account_id, error='上游账号非 active 或已到期，等待上游状态恢复')
            return
        state = account['state']
        if state == 'pausing' and account.get('cooldown_mode') == 'image' and account['resume_at'] is not None:
            # 分组切换意图已落库；响应丢失 / 重启后继续完成同一次操作。
            await self.finish_image_pause(client, account, rule)
            return
        if state == 'pausing':
            if account['resume_at'] is None:
                await self.finish_pause(client, account, rule)
                return
            if remote.get('schedulable') is False:
                # 上一轮停调响应丢失时，以实时上游状态确认结果，不重复发送停调。
                self.complete_pause(self.store.account(account_id), rule, '已从上游确认停止调度')
                await self.ensure_cooldown_coverage(client, self.store.account(account_id), rule)
                return
            # 自动停调重试必须重新采样、检查当前窗口，不能沿用过期的停调意图。
            state = 'probation' if account['probation_until'] is not None else 'active'
            self.store.update(account_id, state=state, resume_at=None)
            account = self.store.account(account_id)
        if state == 'resuming':
            await self.finish_resume(client, account, rule)
            return
        if state == 'cooldown' and image_only and account.get('cooldown_mode') != 'image':
            # 已退避到生图的旧记录可能没有模式标记，且不一定属于三方号池。
            # 补齐恢复责任，保留原截止时间；可调度是生图冷却的正常状态。
            self.store.update(account_id, cooldown_mode='image')
            account = self.store.account(account_id)
        if state == 'cooldown' and (account.get('cooldown_mode') == 'image' or account['pool'] == 'third_party'):
            if time.time() >= account['resume_at']:
                # 旧版三方冷却也按新规则恢复所有正常 GPT 分组。
                self.store.update(account_id, cooldown_mode='image')
                await self.resume(client, self.store.account(account_id), rule)
            elif account.get('cooldown_mode') != 'image':
                # 迁移已有停调冷却，保留原截止时间；此时只是重新提供生图能力。
                await client.cooldown_groups(image_only=True)
                # 已计次的旧冷却只切换模式；确认时补回，切换重试不多计一次。
                self.store.update(account_id, state='pausing', cooldown_mode='image',
                                  cooldown_count=max(0, account.get('cooldown_count', 0) - 1))
                await self.finish_image_pause(client, self.store.account(account_id), rule, migrated=True)
            # 生图可调度属于冷却本身，不视为外部恢复，也不重复采样 / 拉起替补。
            return
        if remote.get('schedulable') is True and (state in {'manual', 'cooldown'} or account['remote'].get('schedulable') is False):
            if account.get('cooldown_mode') == 'image' or account['pool'] == 'third_party':
                await self.resume(client, account, rule, '检测到上游手动启用调度', restore_groups=True)
                return
            # 外部恢复只对齐本地状态，不再次写入上游调度开关。
            monitored = account_guarded(account, rule) and not image_only
            try:
                watermark = await Analytics(self.store, client).watermark(account_id) if monitored else account['watermark']
            except UpstreamError:
                # 保留恢复前的快照，下轮继续建立基线，避免失败后混入旧样本。
                self.store.update(account_id, remote=account['remote'])
                raise
            reason = '检测到上游已启用调度，已同步本地状态' + ('，进入新的恢复观察期' if monitored else '')
            self.complete_resume(client, self.store.account(account_id), rule, watermark, reason)
            return
        # 已有自动冷却始终处理完毕，取消守护也不能让账号一直停调。
        if state == 'cooldown':
            if time.time() >= account['resume_at']:
                await self.resume(client, account, rule)
            return
        if state == 'manual':
            return
        if image_only:
            return
        if observing and state == 'active':
            # 非守护号池也有推送后的健康观察，不把观察期转成新的自动冷却。
            return
        if not account_guarded(account, rule):
            return
        if not remote.get('schedulable'):
            self.store.update(account_id, error='上游已停止调度，本系统不会自动恢复外部暂停')
            return
        if not rule.enabled:
            return
        if image_only_account(account['remote']):
            # 生图限制解除后重新建立边界，不把生图时期或切换前在途调用拿来评估。
            self.store.update(account_id, epoch=client.now() + 1, state='probation',
                              probation_until=time.time() + rule.probation_seconds,
                              reason='已恢复非生图分组，重新观察首字延迟，仅统计切换后的调用')
            return
        self.store.update(account_id, last_checked=time.time())
        samples = await Analytics(self.store, client).samples(account_id, rule.sample_size, account['epoch'], account['watermark'])
        slow = sum(item['first_token_ms'] > rule.threshold_ms for item in samples)
        sample_signature = [(item.get('id'), item.get('first_token_ms')) for item in samples]
        previous_signature = [(item.get('id'), item.get('first_token_ms')) for item in account.get('sample', [])]
        sample_changed = sample_signature != previous_signature
        self.store.update(account_id, sample=samples, slow_count=slow)
        if not samples:
            message = '最近 5 分钟内无有效调用，清除告警，等待新调用'
            self.store.update(account_id, breach_times=[], breach_sample='', last_breach_at=None, reason=message)
            self.record_inspection(account, state, samples, slow, '继续观察' if state == 'probation' else '保持调度', message, rule=rule)
            return
        if state == 'probation':
            if time.time() < max(account['probation_until'] or 0, account.get('post_import_until') or 0):
                if sample_changed:
                    self.record_inspection(account, state, samples, slow, '继续观察', '恢复观察期内，暂不执行自动暂停', rule=rule)
                return
            if slow / len(samples) >= rule.slow_ratio:
                await self.check_breaches(client, account, rule, samples, slow, f'恢复观察达限：{slow}/{len(samples)} 条首字延迟超限')
            else:
                self.record_inspection(account, state, samples, slow, '保持调度', f'恢复观察通过：{slow}/{len(samples)} 条首字延迟超限', rule=rule)
                # 保留 epoch 和 watermark，观察通过后也永不混入上一轮记录。
                self.store.update(account_id, state='active', probation_until=None, reason='恢复观察通过')
                self.store.event('healthy', f'恢复观察通过：{slow}/{len(samples)} 条超限', account_id)
        elif len(samples) < rule.sample_size:
            if sample_changed:
                self.record_inspection(account, state, samples, slow, '继续调度', f'最近 5 分钟内有效样本不足：{len(samples)}/{rule.sample_size} 条', rule=rule)
        elif slow / len(samples) >= rule.slow_ratio:
            await self.check_breaches(client, account, rule, samples, slow, f'最近 {len(samples)} 次调用中 {slow} 次首字延迟超限')
        elif sample_changed:
            self.record_inspection(account, state, samples, slow, '保持调度', f'最近 {len(samples)} 次调用中 {slow} 次首字延迟超限', rule=rule)
        # 连续冷却次数仅作记录，用恢复后的足量有效调用确认正常后清零。
        if len(samples) >= rule.sample_size and slow / len(samples) < rule.slow_ratio:
            self.store.update(account_id, cooldown_count=0)

    def window_breaches(self, account, rule):
        now = time.time()
        return [stamp for stamp in account['breach_times'] if now - rule.breach_window_seconds < stamp <= now]

    async def check_breaches(self, client, account, rule, samples, slow, reason):
        current = self.store.account(account['id'])
        times = self.window_breaches(current, rule)
        signature = json.dumps(sorted((item['id'], item['first_token_ms']) for item in samples))
        previous_ids = {item[0] for item in json.loads(current['breach_sample'] or '[]')}
        # 以真正计次时的样本为基线；未满足确认条件的巡检不能刷新基线。
        # ID 水位排除旧样本过期、重排或重新出现导致的重复计次。
        fresh_count = sum(item_id > max(previous_ids, default=0) for item_id in {item['id'] for item in samples})
        fresh_required = math.ceil(rule.sample_size * rule.breach_fresh_ratio)
        last_breach_at = current['last_breach_at']
        wait_seconds = max(0, math.ceil(rule.breach_min_interval_seconds - (time.time() - last_breach_at))) if last_breach_at is not None else 0
        first = not previous_ids
        confirmed = first or (not wait_seconds and fresh_count >= fresh_required)
        if confirmed:
            now = time.time()
            times.append(now)
            self.store.update(account['id'], breach_times=times, breach_sample=signature, last_breach_at=now)
            confirmation = '本轮首次达限，已计次' if first else f'距上次计次至少 {rule.breach_min_interval_seconds} 秒，新样本 {fresh_count}/{fresh_required} 条，已计次'
        else:
            self.store.update(account['id'], breach_times=times)
            confirmation = f'本次未新增计次：新样本 {fresh_count}/{fresh_required} 条'
            confirmation += f'，计次间隔还需 {wait_seconds} 秒' if wait_seconds else '，计次间隔已满足'
        message = f'{reason}；最近 {rule.breach_window_seconds} 秒达限 {len(times)}/{rule.breach_count} 次'
        message += f'；{confirmation}'
        if rule.alert_only:
            message += '，仅告警模式，继续调度'
            values = {'reason': message}
            if account['state'] == 'probation' and account.get('probation_until') is not None and time.time() >= account['probation_until']:
                values.update(state='active', probation_until=None)
            self.store.update(account['id'], **values)
            self.record_inspection(account, account['state'], samples, slow, '告警', message, rule=rule)
            return
        if len(times) < rule.breach_count:
            message += '，仅告警，继续调度'
            self.store.update(account['id'], reason=message)
            self.record_inspection(account, account['state'], samples, slow, '告警', message, rule=rule)
            return
        await self.pause(client, self.store.account(account['id']), rule, message)

    def record_inspection(self, account, state, samples, slow, action, message, rule, notify=True):
        ratio = slow / len(samples) if samples else 0
        count = len(self.window_breaches(self.store.account(account['id']), rule))
        self.store.inspection(account['id'], state, len(samples), slow, ratio, action, message,
                              count, rule.breach_count, rule.breach_window_seconds)
        if notify:
            self.notify(self.store.account(account['id']), action, message, rule)

    def notify(self, account, action, message, rule):
        if self.mailer is not None:
            try:
                self.mailer.enqueue(account, action, message, rule)
            except Exception:
                self.store.event('mail_error', '通知入队失败，请检查邮件配置；调度继续执行', account['id'], 'error')

    async def replacement_candidate(self, client, candidate):
        try:
            remote = await client.account(candidate['id'])
        except UpstreamError:
            return None, '状态查询失败'
        self.store.update(candidate['id'], remote=public_account(remote), name=remote['name'])
        if remote.get('status') != 'active':
            return None, '上游状态非 active'
        if remote.get('rate_limit_reset_at'):
            try:
                if expiry_timestamp(remote['rate_limit_reset_at']) > client.now():
                    return None, '限流中'
            except (ValueError, TypeError, OverflowError):
                return None, '无法解析限流状态'
        if candidate['state'] in {'pausing', 'cooldown', 'resuming'}:
            return None, '本地暂停或切换中'
        if not any(group.get('name', '').strip().casefold() == 'pro号池' for group in remote.get('groups', []) or []):
            return None, '未加入 Pro号池'
        return remote, ''

    async def priority_ready(self, client, account, rule):
        candidates = sorted((item for item in self.store.accounts()
                             if item['id'] != account['id'] and item['pool'] in {'priority', 'third_party'}),
                            key=lambda item: item['id'])
        reasons = []
        available = {'priority': [], 'third_party': []}
        # 先检查所有高权重候选，确认已有账号调度后就不再开启新替补。
        for pool in ('priority', 'third_party'):
            for candidate in candidates:
                if candidate['pool'] != pool:
                    continue
                remote, reason = await self.replacement_candidate(client, candidate)
                if reason:
                    reasons.append(f"#{candidate['id']} {reason}")
                    continue
                if pool == 'priority' and remote.get('schedulable') is True:
                    return [candidate['id']], f"Pro号池高权重账号 #{candidate['id']} 正在调度，无需新开启替补", None
                available[pool].append((candidate, remote))
        # 没有正在调度的高权重账号时，三方优先，高权重兜底；不选择风控账号。
        for pool in ('third_party', 'priority'):
            pool_name = '三方账号组' if pool == 'third_party' else '高权重组'
            for candidate, remote in available[pool]:
                if remote.get('schedulable') is True:
                    return [candidate['id']], f"Pro号池账号 #{candidate['id']}（{pool_name}）已在调度，无需重复启用", None
            for candidate, remote in available[pool]:
                try:
                    await client.set_schedulable(candidate['id'], True)
                except UpstreamError:
                    reasons.append(f"#{candidate['id']} 启用调度失败")
                    continue
                remote['schedulable'] = True
                self.store.update(candidate['id'], remote=public_account(remote), error=None)
                self.store.update(account['id'], cooldown_replacement_id=candidate['id'])
                return [candidate['id']], f"冷却前已开启 Pro号池替补账号 #{candidate['id']}（{pool_name}）调度", candidate['id']
        detail = 'Pro号池的三方账号组、高权重组均无可用替补，不允许冷却'
        if reasons:
            detail += '；' + '；'.join(reasons)
        return None, detail, None

    async def ensure_cooldown_coverage(self, client, account, rule, replacement_id=None, ready_ids=None):
        if ready_ids is None:
            # 停调响应丢失后的恢复路径，仅在确认进入冷却时检查一次。
            ready_ids, detail, enabled_id = await self.priority_ready(client, account, rule)
        else:
            enabled_id = None
            candidate = self.store.account(ready_ids[0])
            remote, reason = await self.replacement_candidate(client, candidate)
            if remote and remote.get('schedulable') is True:
                detail = f"冷却后已复核保障账号 #{candidate['id']} 仍在调度"
            else:
                ready_ids = None
                detail = f"保障账号 #{candidate['id']} 不可用：{reason or '调度已关闭'}"
        if ready_ids is not None:
            enabled_id = enabled_id or replacement_id
            if enabled_id:
                detail += f'；本次冷却新开启调度账号 #{enabled_id}'
                self.store.event('replacement', f'账号 #{account["id"]} 本次冷却新开启调度账号 #{enabled_id}', account['id'], 'info')
            current_reason = account.get('reason') or '达限累计次数达到停调条件'
            if detail not in current_reason:
                self.store.update(account['id'], reason=f'{current_reason}；{detail}')
            self.record_inspection(account, 'cooldown', account['sample'], account['slow_count'],
                                   '继续冷却', detail, rule=rule)
            current = self.store.account(account['id'])
            self.notify(current, '仅生图调度' if current.get('cooldown_mode') == 'image' else '停止调度', detail, rule)
            return
        reason = f'保障可用性，提前结束冷却：{detail}'
        try:
            await self.resume(client, account, rule, reason)
        except UpstreamError as error:
            self.record_inspection(account, account['state'], account['sample'], account['slow_count'],
                                   '恢复未确认', f'{reason}；{error.message}', rule=rule, notify=False)
            self.notify(account, '恢复未确认', reason, rule)
            raise
        self.record_inspection(account, self.store.account(account['id'])['state'], account['sample'], account['slow_count'],
                               '提前恢复调度', reason, rule=rule, notify=False)

    async def pause(self, client, account, rule, reason, manual=False):
        if manual:
            self.store.update(account['id'], post_import_until=None)
        # 先落库意图，再调用幂等开关。进程中断时下一轮可完成未提交状态。
        self.store.update(account['id'], state='pausing', reason=reason, cooldown_replacement_id=None,
                          resume_at=None if manual else time.time() + rule.cooldown_seconds)
        await self.finish_pause(client, self.store.account(account['id']), rule)

    async def finish_pause(self, client, account, rule):
        automatic = account['resume_at'] is not None
        detail = ''
        if automatic:
            if not account_guarded(account, rule) or not rule.enabled or rule.alert_only or len(self.window_breaches(account, rule)) < rule.breach_count:
                state = 'active' if rule.alert_only else ('probation' if account['probation_until'] is not None else 'active')
                reason = '仅告警模式，继续调度' if rule.alert_only else '当前窗口未满足停调条件，继续巡检'
                self.store.update(account['id'], state=state, resume_at=None, probation_until=None if rule.alert_only else account['probation_until'], reason=reason)
                self.record_inspection(account, state, account['sample'], account['slow_count'],
                                       '告警' if rule.alert_only else '暂缓停调', reason, rule=rule)
                return
            ready_ids, detail, replacement_id = await self.priority_ready(client, account, rule)
            if ready_ids is None:
                message = f"{account['reason']}；{detail}，未执行停止调度，下轮重试"
                self.store.update(account['id'], state='probation' if account['probation_until'] is not None else 'active', resume_at=None, error=detail, reason=message)
                self.record_inspection(account, account['state'], account['sample'], account['slow_count'], '暂缓停调', message, rule=rule)
                return
            # 替补接口可能耗时较长，发送停调前再次确认次数没有滑出窗口。
            if len(self.window_breaches(account, rule)) < rule.breach_count:
                state = 'probation' if account['probation_until'] is not None else 'active'
                self.store.update(account['id'], state=state, resume_at=None, reason='达限次数已滑出窗口，仅告警')
                self.record_inspection(account, state, account['sample'], account['slow_count'],
                                       '告警', '达限次数已滑出窗口，未执行停止调度', rule=rule)
                return
            if account['pool'] == 'third_party':
                # 确认目标分组存在后落库切换意图，重启或请求超时不会丢失恢复责任。
                await client.cooldown_groups(image_only=True)
                self.store.update(account['id'], cooldown_mode='image')
                await self.finish_image_pause(client, self.store.account(account['id']), rule, ready_ids, replacement_id, detail)
                return
        try:
            await client.set_schedulable(account['id'], False)
        except UpstreamError as error:
            if automatic:
                self.record_inspection(account, account['state'], account['sample'], account['slow_count'],
                                       '停调未确认', f'{detail}；{error.message}，下轮重新检查并重试', rule=rule)
            raise
        self.complete_pause(account, rule, detail)
        if automatic:
            # 只复核本次选定的保障账号，不在冷却期间重复拉起替补。
            await self.ensure_cooldown_coverage(client, self.store.account(account['id']), rule, replacement_id, ready_ids)

    async def finish_image_pause(self, client, account, rule, ready_ids=None, replacement_id=None, detail='', migrated=False):
        if time.time() >= account['resume_at']:
            await self.resume(client, account, rule)
            return
        try:
            groups = await client.cooldown_groups(image_only=True)
            remote = await client.set_groups(account['id'], groups)
            if remote.get('status') != 'active':
                raise UpstreamError('上游账号非 active，生图冷却切换等待状态恢复')
            if remote.get('schedulable') is not True:
                await client.set_schedulable(account['id'], True)
            remote = await client.account(account['id'])
            if remote.get('schedulable') is not True or set(remote.get('group_ids') or []) != {group['id'] for group in groups}:
                raise UpstreamError('生图分组 / 调度状态尚未确认，下轮重试')
        except UpstreamError as error:
            self.record_inspection(account, 'pausing', account['sample'], account['slow_count'],
                                   '冷却切换未确认', error.message, rule=rule)
            raise
        reason = f"{account['reason']}；保持可调度，所属分组仅「生图」#{groups[0]['id']}；冷却结束恢复全部正常 GPT 分组"
        if detail:
            reason += f'；{detail}'
        self.store.update(account['id'], state='cooldown', remote=public_account(remote), reason=reason, error=None,
                          cooldown_count=account.get('cooldown_count', 0) + 1)
        current = self.store.account(account['id'])
        self.store.event('image_cooldown', reason, account['id'], 'warning')
        self.record_inspection(current, 'cooldown', account['sample'], account['slow_count'], '仅生图调度', reason, rule=rule, notify=migrated)
        if not migrated:
            await self.ensure_cooldown_coverage(client, current, rule, replacement_id, ready_ids)

    def complete_pause(self, account, rule, detail):
        automatic = account['resume_at'] is not None
        state = 'manual' if not automatic else 'cooldown'
        self.store.update(account['id'], state=state, remote={**account['remote'], 'schedulable': False}, error=None,
                          cooldown_count=account.get('cooldown_count', 0) + int(automatic),
                          resume_at=None if state == 'manual' else time.time() + rule.cooldown_seconds)
        self.store.event('pause', account['reason'] or '停止调度', account['id'], 'warning')
        if automatic:
            self.record_inspection(account, state, account['sample'], account['slow_count'],
                                   '停止调度', f"{account['reason']}；{detail}；已停止调度", rule=rule, notify=False)

    async def resume(self, client, account, rule, reason=None, restore_groups=False):
        if account['resume_at'] is not None and not account.get('cooldown_mode') and (account['pool'] == 'third_party' or image_only_account(account['remote'])):
            self.store.update(account['id'], cooldown_mode='image')
            account = self.store.account(account['id'])
        if reason is None:
            reason = '冷却结束，恢复调度' + ('并进入观察期' if account_guarded(account, rule) else '')
        if restore_groups or account.get('restore_all_groups') or account.get('cooldown_mode') == 'image':
            reason += '；恢复全部正常 GPT 分组'
        self.store.update(account['id'], state='resuming', reason=reason,
                          restore_all_groups=bool(restore_groups or account.get('restore_all_groups')))
        await self.finish_resume(client, self.store.account(account['id']), rule)

    async def finish_resume(self, client, account, rule):
        if account['resume_at'] is not None and not account.get('cooldown_mode') and (account['pool'] == 'third_party' or image_only_account(account['remote'])):
            self.store.update(account['id'], cooldown_mode='image')
            account = self.store.account(account['id'])
        restore_groups = bool(account.get('restore_all_groups') or account.get('cooldown_mode') == 'image')
        if restore_groups:
            groups = await client.cooldown_groups()
            remote = await client.set_groups(account['id'], groups)
            if remote.get('status') != 'active':
                raise UpstreamError('上游账号非 active，等待状态恢复后继续恢复分组调度')
            self.store.update(account['id'], remote=public_account(remote))
            account = self.store.account(account['id'])
        watermark = await Analytics(self.store, client).watermark(account['id'])
        await client.set_schedulable(account['id'], True)
        remote = await client.account(account['id'])
        if remote.get('status') != 'active' or remote.get('schedulable') is not True or (restore_groups and set(remote.get('group_ids') or []) != {group['id'] for group in groups}):
            raise UpstreamError('恢复后的分组 / 调度状态尚未确认，下轮重试')
        self.store.update(account['id'], remote=public_account(remote))
        account = self.store.account(account['id'])
        reason = account['reason']
        if restore_groups:
            reason += '；已绑定：' + '、'.join(f"{group['name']} #{group['id']}" for group in groups)
        self.complete_resume(client, account, rule, watermark, reason)
        if restore_groups:
            self.record_inspection(self.store.account(account['id']), self.store.account(account['id'])['state'], [], 0,
                                   '恢复全部分组', reason, rule=rule)

    def complete_resume(self, client, account, rule, watermark, reason):
        # HTTP Date 只有秒精度，额外留 1 秒边界，宁可少计边界请求也不计入旧调用。
        epoch = client.now() + 1
        monitored = account_guarded(account, rule) and not image_only_account(account['remote'])
        self.store.update(account['id'], state='probation' if monitored else 'active', epoch=epoch, watermark=watermark,
                          probation_until=max(time.time() + rule.probation_seconds, account.get('post_import_until') or 0) if monitored else None, resume_at=None,
                          sample=[], slow_count=0, breach_times=[], breach_sample='', last_breach_at=None, last_checked=None,
                          error=None, reason=reason, cooldown_mode='', restore_all_groups=False, cooldown_replacement_id=None,
                          remote={**account['remote'], 'schedulable': True})
        self.store.event('resume', reason or ('恢复调度，进入观察期' if monitored else '恢复调度'), account['id'])
        if account['resume_at'] is not None:
            self.notify(account, '冷却恢复', reason or '冷却结束', rule)
