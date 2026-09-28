#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path


def load_env_file(path):
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


load_env_file(Path(os.environ.get('FLOWPOOL_SERVICE_ROOT', Path(__file__).resolve().parent.parent)) / '.env')

ROOT = Path(os.environ.get('FLOWPOOL_SERVICE_ROOT', Path(__file__).resolve().parent.parent)).resolve()
DATA = ROOT / 'data'
PID_FILE = DATA / 'service.json'
PORT = int(os.environ.get('PORT', '8765'))


def health(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
            return json.load(response)
    except Exception:
        return {}


def healthy(port):
    return health(port).get('service') == 'gpt-account-scheduler'


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def current():
    if not PID_FILE.exists():
        return None
    try:
        state = json.loads(PID_FILE.read_text())
        # 端口健康接口是服务身份的可靠信号；某些环境禁止读取 ps。
        result = health(state.get('port', PORT))
        if result.get('service') != 'gpt-account-scheduler':
            return None
        if state.get('instance') and (result.get('instance') != state['instance'] or result.get('pid') != state['pid']):
            return None
        return state
    except (KeyError, TypeError, ValueError, OSError):
        return None


def start():
    state = current()
    if state:
        print(f"已运行：http://127.0.0.1:{state['port']}")
        return
    DATA.mkdir(exist_ok=True, mode=0o700)
    if healthy(PORT):
        raise SystemExit(f'端口 {PORT} 已有调度服务运行，请先停止对应服务。')
    if PID_FILE.exists():
        pending = json.loads(PID_FILE.read_text())
        if alive(pending['pid']):
            raise SystemExit('原服务仍在运行但健康检查未通过，请先检查日志。')
    instance = uuid.uuid4().hex
    env = {**os.environ, 'FLOWPOOL_INSTANCE_ID': instance}
    with (DATA / 'service.log').open('ab', buffering=0) as log:
        process = subprocess.Popen([str(ROOT / '.venv/bin/python'), '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', str(PORT), '--no-access-log'], cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=True)
    PID_FILE.write_text(json.dumps({'pid': process.pid, 'port': PORT, 'instance': instance}))
    for _ in range(50):
        if process.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            raise SystemExit('启动失败，请查看 data/service.log')
        result = health(PORT)
        if result.get('instance') == instance and result.get('pid') == process.pid:
            print(f'启动成功：http://127.0.0.1:{PORT}\n日志：{DATA / "service.log"}')
            return
        time.sleep(.2)
    raise SystemExit('服务仍在启动，请查看 data/service.log；可执行 ./status.sh 查询。')


def stop():
    state = current()
    if not state:
        if PID_FILE.exists():
            pending = json.loads(PID_FILE.read_text())
            if alive(pending['pid']):
                raise SystemExit('服务身份或健康状态无法确认，未停止进程，请检查日志。')
        PID_FILE.unlink(missing_ok=True)
        print('服务未运行')
        return
    os.kill(state['pid'], signal.SIGTERM)
    for _ in range(150):
        if not alive(state['pid']):
            PID_FILE.unlink(missing_ok=True)
            print('服务已停止。冷却状态已保存，重新启动后继续处理。')
            return
        time.sleep(.2)
    raise SystemExit('服务仍在完成当前请求，请稍后再次检查。未强制杀死进程。')


if __name__ == '__main__':
    command = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if command == 'start':
        start()
    elif command == 'stop':
        stop()
    elif command == 'restart':
        stop()
        start()
    elif command == 'status':
        state = current()
        print(f"运行中 · PID {state['pid']} · http://127.0.0.1:{state['port']} · {'健康' if healthy(state['port']) else '暂未就绪'}" if state else '服务未运行')
    else:
        raise SystemExit('用法: service.py start|stop|restart|status')
