"""Independent 30-second RPM sampling; browser reads never trigger mail or queries."""
import asyncio
import hashlib
import time
from collections import deque

from fastapi import HTTPException

from .analytics import Analytics
from .mailer import MailError
from .sub2api import UpstreamError

INTERVAL_SECONDS = 30


class PerformanceMonitor:
    def __init__(self, store, mailer):
        self.store = store
        self.mailer = mailer
        self.lock = asyncio.Lock()
        self.sample = None
        self.previous = None
        self.identity = None
        self.error = ''
        self.mail_error = ''
        self.next_check_at = None
        self.history = deque(maxlen=60)

    def connection_identity(self):
        settings = self.store.settings()
        # RPM always uses the same dashboard API, regardless of PG configuration.
        return hashlib.sha256(('snapshot-v2:' + settings.base_url + settings.admin_key).encode()).hexdigest()

    def status(self):
        mail = self.store.mail_settings()
        configured = bool(self.store.settings().base_url and self.store.settings().admin_key)
        stale = bool(self.sample and time.time() - self.sample['received_at'] > 65)
        return {'sample': self.sample, 'history': list(self.history), 'error': self.error,
                'mail_error': self.mail_error, 'configured': configured, 'stale': stale,
                'interval_seconds': INTERVAL_SECONDS, 'window_seconds': 300,
                'next_check_at': self.next_check_at, 'mail_enabled': mail.enabled and mail.notify_rpm}

    async def tick(self):
        async with self.lock:
            settings = self.store.settings()
            if not settings.base_url or not settings.admin_key:
                self.sample = self.previous = self.identity = None
                self.history.clear()
                self.error = self.mail_error = ''
                return
            try:
                identity = self.connection_identity()
                if self.identity != identity:
                    self.sample = self.previous = None
                    self.history.clear()
                    self.identity = identity
                current = await asyncio.wait_for(Analytics(self.store).rpm(), timeout=25)
                if identity != self.connection_identity():
                    raise UpstreamError('采样期间连接配置变化，下轮重新建立 RPM 基线')
                received = time.time()
                previous = self.previous
                if previous and current['sampled_at'] <= previous['sampled_at']:
                    # Snapshot endpoint caches for 30 seconds. A cached read is not a new sample.
                    self.error = ''
                    return
                comparable = previous is not None and 0 < current['sampled_at'] - previous['sampled_at'] <= 65
                before = previous['rpm'] if comparable else None
                growth = (current['rpm'] - before) / before * 100 if before else (0 if before == 0 and current['rpm'] == 0 else None)
                alert = before is not None and before > 0 and current['rpm'] > before * 2
                self.sample = {**current, 'received_at': received, 'previous_rpm': before,
                               'growth_percent': growth, 'alert': alert,
                               'baseline': 'ready' if before else 'zero' if before == 0 else 'initial'}
                self.previous = current
                self.history.append({'rpm': current['rpm'], 'sampled_at': current['sampled_at']})
                self.error = self.mail_error = ''
                if alert:
                    self.store.event('rpm_alert', f"RPM 从 {before} 增至 {current['rpm']}，增长 {growth:.1f}%（超过 100%）", level='warning')
                    try:
                        self.mailer.enqueue_rpm(self.sample, identity)
                    except MailError as error:
                        self.mail_error = str(error)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.previous = None
                self.error = error.detail if isinstance(error, HTTPException) else error.message if isinstance(error, UpstreamError) else 'RPM 采样失败或超时，下轮重新建立基线'
                # Retain the last successful sample with an explicit error; never publish a false zero.

    async def run(self):
        while True:
            started = time.monotonic()
            self.next_check_at = time.time() + INTERVAL_SECONDS
            await self.tick()
            await asyncio.sleep(max(0, INTERVAL_SECONDS - (time.monotonic() - started)))
