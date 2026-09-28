"""Authentication security primitives extracted from the site's existing chain. MIT.
No account creation, mailbox service, operator proxy, or workspace administration.
"""
from __future__ import annotations
import base64,json,os,random,shutil,subprocess,time,uuid
from pathlib import Path
def log(*args):pass


SENTINEL_REQ_URL = 'https://sentinel.openai.com/backend-api/sentinel/req'

SENTINEL_REFERER = 'https://sentinel.openai.com/backend-api/sentinel/frame.html'

CHROME_PROFILES = [{'impersonate': 'chrome136', 'build': 7103, 'patch_range': (0, 200), 'sec_ch_ua': '"Google Chrome";v="136", "Chromium";v="136", "Not A(Brand";v="24"', 'major': 136}, {'impersonate': 'chrome131', 'build': 6778, 'patch_range': (0, 200), 'sec_ch_ua': '"Google Chrome";v="131", "Chromium";v="131", "Not A(Brand";v="24"', 'major': 131}, {'impersonate': 'chrome124', 'build': 6367, 'patch_range': (0, 200), 'sec_ch_ua': '"Google Chrome";v="124", "Chromium";v="124", "Not A(Brand";v="24"', 'major': 124}]

_TZ_LONGNAME = {'Asia/Tokyo': 'Japan Standard Time', 'America/Los_Angeles': 'Pacific Daylight Time', 'America/New_York': 'Eastern Daylight Time', 'America/Chicago': 'Central Daylight Time', 'Europe/London': 'Greenwich Mean Time', 'Europe/Berlin': 'Central European Summer Time', 'Europe/Paris': 'Central European Summer Time', 'Asia/Singapore': 'Singapore Standard Time', 'Asia/Hong_Kong': 'Hong Kong Standard Time', 'Asia/Seoul': 'Korean Standard Time'}

def _tz_date_string(tz_name: str) -> str:
    """生成与时区一致的 JS Date.toString() 风格串(本地时间+GMT偏移+时区名)。"""
    from datetime import datetime, timezone
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(tz_name))
        off = now.utcoffset()
        m = int(off.total_seconds() // 60)
        sign = '+' if m >= 0 else '-'
        off_str = f'GMT{sign}{abs(m) // 60:02d}{abs(m) % 60:02d}'
        longname = _TZ_LONGNAME.get(tz_name) or now.tzname() or tz_name
        return now.strftime(f'%a %b %d %Y %H:%M:%S {off_str} ({longname})')
    except Exception:
        return datetime.now(timezone.utc).strftime('%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)')

def random_chrome_fingerprint():
    profile = random.choice(CHROME_PROFILES)
    major = profile['major']
    build = profile['build']
    patch = random.randint(*profile['patch_range'])
    full_ver = f'{major}.0.{build}.{patch}'
    ua = f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_ver} Safari/537.36'
    return (profile['impersonate'], ua, profile['sec_ch_ua'])

class SentinelSolver:
    """纯协议 Sentinel solver: sentinel/req → PoW → 组装 token"""
    MAX_ATTEMPTS = 500000

    def __init__(self, device_id: str, user_agent: str, geo: dict=None):
        self.device_id = device_id
        self.user_agent = user_agent
        self.geo = geo or {}
        self.sid = str(uuid.uuid4())
        self.screen = random.choice(['1920x1080', '1536x864', '2560x1440', '1366x768', '1440x900', '1280x720'])
        self.hw_concurrency = random.choice([4, 8, 12, 16])

    @staticmethod
    def _fnv1a_32(text: str) -> str:
        h = 2166136261
        for ch in text:
            h ^= ord(ch)
            h = h * 16777619 & 4294967295
        h ^= h >> 16
        h = h * 2246822507 & 4294967295
        h ^= h >> 13
        h = h * 3266489909 & 4294967295
        h ^= h >> 16
        return format(h & 4294967295, '08x')

    def _get_config(self) -> list:
        date_str = _tz_date_string(self.geo.get('tz', 'America/Los_Angeles'))
        lang = self.geo.get('lang', 'en-US')
        langs = self.geo.get('langs', 'en-US,en')
        perf_now = random.uniform(1000, 50000)
        time_origin = time.time() * 1000 - perf_now
        nav_prop = random.choice(['vendorSub', 'productSub', 'vendor', 'maxTouchPoints', 'hardwareConcurrency', 'cookieEnabled', 'credentials', 'mediaDevices', 'permissions', 'locks'])
        return [self.screen, date_str, 4294967296, random.random(), self.user_agent, 'https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js', None, None, lang, langs, random.random(), f'{nav_prop}−undefined', random.choice(['location', 'implementation', 'URL', 'documentURI', 'compatMode']), random.choice(['Object', 'Function', 'Array', 'Number', 'parseFloat', 'undefined']), perf_now, self.sid, '', self.hw_concurrency, time_origin, 0, 0, 0, 0, 0, 0]

    def _b64_encode(self, data) -> str:
        raw = json.dumps(data, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        return base64.b64encode(raw).decode('ascii')

    def generate_requirements_token(self) -> str:
        config = self._get_config()
        config[3] = 1
        config[9] = round(random.uniform(5, 50))
        return 'gAAAAAC' + self._b64_encode(config)

    def solve_pow(self, seed: str, difficulty: str='0') -> str:
        start = time.time()
        config = self._get_config()
        for nonce in range(self.MAX_ATTEMPTS):
            config[3] = nonce
            config[9] = round((time.time() - start) * 1000)
            encoded = self._b64_encode(config)
            digest = self._fnv1a_32(seed + encoded)
            if digest[:len(difficulty)] <= difficulty:
                return 'gAAAAAB' + encoded + '~S'
        return 'gAAAAAB' + self._b64_encode(str(None))

    def build_token(self, session, flow: str, impersonate: str, proxy: str='') -> str:
        """纯协议: sentinel/req → PoW → 组装 token（无浏览器）"""
        p_token = self.generate_requirements_token()
        req_body = {'p': p_token, 'id': self.device_id, 'flow': flow}
        headers = {'Content-Type': 'text/plain;charset=UTF-8', 'Accept': '*/*', 'Referer': SENTINEL_REFERER, 'Origin': 'https://sentinel.openai.com', 'User-Agent': self.user_agent}
        try:
            resp = session.post(SENTINEL_REQ_URL, data=json.dumps(req_body), headers=headers, timeout=20, impersonate=impersonate)
            if resp.status_code != 200:
                log('WARN', f'sentinel/req 失败: {resp.status_code}')
                return ''
            challenge = resp.json()
        except Exception as e:
            log('WARN', f'sentinel/req 异常: {e}')
            return ''
        c_value = str(challenge.get('token') or '').strip()
        if not c_value:
            log('WARN', 'sentinel challenge 无 token')
            return ''
        pow_data = challenge.get('proofofwork') or {}
        if pow_data.get('required') and pow_data.get('seed'):
            p_value = self.solve_pow(pow_data['seed'], pow_data.get('difficulty', '0'))
        else:
            p_value = self.generate_requirements_token()
        t_value = None
        turnstile_data = challenge.get('turnstile') or {}
        dx = turnstile_data.get('dx', '')
        if dx:
            t_value = self._execute_turnstile_vm(dx, p_token)
            if not t_value:
                return ''
        if not t_value:
            log('WARN', 'Turnstile VM 未产出有效 t')
        return json.dumps({'p': p_value, 't': t_value, 'c': c_value, 'id': self.device_id, 'flow': flow}, separators=(',', ':'))

    def _execute_turnstile_vm(self, dx_b64, p_token):
        script = Path(__file__).with_name('customer_auth_vm.js')
        payload = json.dumps({'id': 'local', 'dx_b64': dx_b64, 'req_token': p_token, 'ua': self.user_agent, 'device_id': self.device_id}) + '\n'
        env = {k: v for k, v in os.environ.items() if k.upper() in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'HOME')}
        try:
            node = shutil.which('node')
            candidates = [node] if node else []
            candidates += [str(path) for version in ('v22*', 'v24*') for path in sorted((Path.home() / '.nvm/versions/node').glob(version + '/bin/node'), reverse=True)]
            node = next((candidate for candidate in candidates if int(subprocess.check_output([candidate, '-p', 'process.versions.node.split(".")[0]'], timeout=3, env=env).strip()) >= 22), None)
            if not node:
                return ''
            result = subprocess.run([node, '--experimental-permission', '--no-experimental-global-navigator', '--allow-fs-read=' + str(script), str(script), '--worker'], input=payload, capture_output=True, text=True, encoding='utf-8', timeout=18, env=env)
            lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
            return next((str(line.get('result', '')) for line in lines if line.get('id') == 'local'), '')
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return ''
