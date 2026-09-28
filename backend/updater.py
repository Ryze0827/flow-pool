import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path


class UpgradeError(Exception):
    pass


def git(root, *args):
    try:
        result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise UpgradeError('无法读取 Git 仓库，请检查 Git 安装与仓库目录') from None
    if result.returncode:
        raise UpgradeError('仓库尚未提交，或 Git 分支配置不完整')
    return result.stdout.strip()


def locked(data):
    path = data / 'upgrade.lock'
    if not path.exists():
        return False
    with path.open('a') as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False


def repo_info(root, data):
    info = {'branch': '', 'upstream': '', 'commit': '', 'ready': False, 'reason': ''}
    try:
        if data.resolve() != (root / 'data').resolve():
            raise UpgradeError('自定义数据目录暂不支持页面升级，请使用项目 data 目录部署')
        info['commit'] = git(root, 'rev-parse', '--verify', 'HEAD')
        info['branch'] = git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD')
        if git(root, 'status', '--porcelain', '--untracked-files=normal'):
            raise UpgradeError('存在未提交文件，请先提交并推送，或自行处理本地改动')
        info['upstream'] = git(root, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}')
        if git(root, 'config', f"branch.{info['branch']}.remote") != 'origin':
            raise UpgradeError('请将当前分支设置为跟踪 origin 的对应分支')
        ref = git(root, 'config', f"branch.{info['branch']}.merge")
        if not ref.startswith('refs/heads/'):
            raise UpgradeError('远程跟踪目标必须为分支')
        state = json.loads((data / 'service.json').read_text())
        if state.get('pid') != os.getpid() or state.get('instance') != os.environ.get('FLOWPOOL_INSTANCE_ID'):
            raise UpgradeError('请先通过当前仓库的 start.sh / restart.sh 启动服务')
        info['ready'] = True
    except UpgradeError as error:
        info['reason'] = str(error)
    except (OSError, ValueError):
        info['reason'] = '请先通过当前仓库的 start.sh / restart.sh 启动服务'
    return info


def status(root, data):
    running = locked(data)
    try:
        job = json.loads((data / 'upgrade.json').read_text())
    except (OSError, ValueError):
        job = None
    if job and job.get('status') == 'running' and not running:
        job = {**job, 'status': 'interrupted', 'message': '升级进程已中断，请检查本地升级记录后重试'}
    return {'repo': repo_info(root, data), 'running': running, 'job': job}


def start(root, data):
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (data / 'upgrade.lock').open('a')
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UpgradeError('已有升级任务正在执行') from None
        info = repo_info(root, data)
        if not info['ready']:
            raise UpgradeError(info['reason'])
        if (data / 'upgrade-deploying').exists():
            raise UpgradeError('上次部署尚未恢复，请先检查 data/upgrades 中的记录')
        job_id = uuid.uuid4().hex
        job_dir = data / 'upgrades' / job_id
        job_dir.mkdir(parents=True, mode=0o700)
        # 独立副本保证更新源文件、重启 Web 进程后任务仍能继续。
        runner = job_dir / 'upgrade.py'
        shutil.copy2(root / 'scripts' / 'upgrade.py', runner)
        shutil.copy2(root / 'scripts' / 'service.py', job_dir / 'service.py')
        job = {'id': job_id, 'status': 'running', 'phase': 'prepare', 'message': '正在准备升级',
               'old_commit': info['commit'], 'new_commit': '', 'started_at': time.time(), 'updated_at': time.time(),
               'steps': []}
        path = data / 'upgrade.json'
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(job, ensure_ascii=False))
        os.replace(temp, path)
        try:
            subprocess.Popen([sys.executable, str(runner), str(root), job_id, str(lock.fileno())],
                             cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             pass_fds=(lock.fileno(),), start_new_session=True)
        except OSError:
            job.update(status='failed', message='无法启动独立升级进程')
            path.write_text(json.dumps(job, ensure_ascii=False))
            raise UpgradeError(job['message']) from None
        return job
    finally:
        lock.close()
