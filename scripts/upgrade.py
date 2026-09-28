#!/usr/bin/env python3
"""由本机管理员页面启动的独立升级任务，不接受自定义命令或下载地址。"""
import fcntl
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path


class UpgradeFailure(Exception):
    pass


class Upgrade:
    def __init__(self, root, job_id):
        self.root = Path(root).resolve()
        self.data = self.root / 'data'
        self.directory = self.data / 'upgrades' / job_id
        self.candidate = self.directory / 'candidate'
        self.backup = self.directory / 'backup'
        self.state_path = self.data / 'upgrade.json'
        self.state = json.loads(self.state_path.read_text())
        self.service = json.loads((self.data / 'service.json').read_text())
        self.env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_SSH_COMMAND': 'ssh -oBatchMode=yes',
                    'FLOWPOOL_SERVICE_ROOT': str(self.root), 'PORT': str(self.service['port'])}
        self.env.pop('SCHEDULER_DATA_DIR', None)
        self.env.pop('FLOWPOOL_INSTANCE_ID', None)
        self.base_python = getattr(sys, '_base_executable', sys.executable)
        self.old_commit = self.state['old_commit']
        self.new_commit = ''
        self.switched_code = False
        self.switched_env = False
        self.switched_dist = False
        self.stopped = False

    def report(self, phase, message, status='running'):
        self.state.update(phase=phase, message=message, status=status, updated_at=time.time())
        self.state['new_commit'] = self.new_commit
        self.state['steps'].append({'phase': phase, 'message': message, 'time': time.time()})
        temp = self.state_path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state, ensure_ascii=False))
        os.replace(temp, self.state_path)
        (self.directory / 'result.json').write_text(json.dumps(self.state, ensure_ascii=False))

    def command(self, args, cwd=None, timeout=120, env=None):
        # 工具输出可能含仓库凭据 / 环境变量，只保留失败阶段与退出码。
        try:
            process = subprocess.Popen(args, cwd=cwd or self.root, env=env or self.env,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       start_new_session=True)
            try:
                output, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise UpgradeFailure(f'{self.state["message"]}：执行超时') from None
        except OSError:
            raise UpgradeFailure(f'{self.state["message"]}：无法启动所需工具') from None
        if process.returncode:
            raise UpgradeFailure(f'{self.state["message"]}：命令退出码 {process.returncode}')
        return output

    def git(self, *args):
        return self.command(['git', *args]).decode().strip()

    def health(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.service['port']}/api/health", timeout=2) as response:
                return json.load(response)
        except (OSError, ValueError):
            return {}

    def managed_health(self):
        try:
            service = json.loads((self.data / 'service.json').read_text())
            result = self.health()
            return (result.get('service') == 'gpt-account-scheduler' and result.get('instance') == service.get('instance')
                    and result.get('pid') == service.get('pid') and bool(service.get('instance')))
        except (OSError, ValueError):
            return False

    def service_command(self, action):
        self.command([self.base_python, str(self.directory / 'service.py'), action], timeout=60)

    def validate_checkout(self):
        if self.git('rev-parse', 'HEAD') != self.old_commit or self.git('status', '--porcelain', '--untracked-files=normal'):
            raise UpgradeFailure('准备期间仓库发生变化，升级已取消，请处理本地改动后重试')
        if (self.data / 'upgrade-deploying').exists():
            raise UpgradeFailure('检测到未完成的部署标记，请先恢复上次部署')
        if not self.managed_health() or self.health().get('instance') != self.service.get('instance'):
            raise UpgradeFailure('运行中的服务已变化，未执行部署')

    def prepare(self):
        self.report('fetch', '正在拉取远程代码')
        self.validate_checkout()
        branch = self.git('symbolic-ref', '--quiet', '--short', 'HEAD')
        if self.git('config', f'branch.{branch}.remote') != 'origin':
            raise UpgradeFailure('当前分支未跟踪 origin')
        ref = self.git('config', f'branch.{branch}.merge')
        if not ref.startswith('refs/heads/'):
            raise UpgradeFailure('远程跟踪分支无效')
        try:
            self.command(['git', 'fetch', '--no-tags', 'origin', ref], timeout=180)
        except UpgradeFailure:
            raise UpgradeFailure('拉取失败，请检查部署机网络、origin 地址及 Git 访问权限') from None
        self.new_commit = self.git('rev-parse', 'FETCH_HEAD')
        if self.old_commit == self.new_commit:
            self.report('done', '当前已是最新版本，无需重启', 'unchanged')
            return False
        # 与 git pull --ff-only 相同的合并约束，锁定已构建的提交后再部署。
        try:
            self.command(['git', 'merge-base', '--is-ancestor', self.old_commit, self.new_commit])
        except UpgradeFailure:
            raise UpgradeFailure('本地分支与远程无法快进合并，请先处理分叉或本地未推送提交') from None
        protected = self.git('ls-tree', '-r', '--name-only', self.new_commit, '--', 'data', '.venv', 'frontend/dist')
        if any(path != 'data/.gitkeep' for path in protected.splitlines()):
            raise UpgradeFailure('远程提交包含运行数据、虚拟环境或构建产物，已停止升级')
        self.report('build', '正在安装依赖并构建新版本，当前服务继续运行')
        self.candidate.mkdir()
        archive = self.command(['git', 'archive', self.new_commit], timeout=120)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            # 不允许归档路径越界或写入特殊文件。
            for member in source.getmembers():
                path = self.candidate / member.name
                if not path.resolve().is_relative_to(self.candidate) or not (member.isfile() or member.isdir()):
                    raise UpgradeFailure('远程代码归档包含不支持的路径或文件类型')
            source.extractall(self.candidate, **({'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}))
        for path in ('backend/main.py', 'scripts/setup.sh', 'scripts/service.py', 'frontend/package-lock.json'):
            if not (self.candidate / path).is_file():
                raise UpgradeFailure(f'远程版本缺少必要文件：{path}')
        self.command([self.base_python, '-m', 'venv', str(self.candidate / '.venv')], timeout=120)
        candidate_env = {**self.env, 'PATH': str(Path(self.base_python).parent) + os.pathsep + self.env.get('PATH', '')}
        candidate_env.pop('FLOWPOOL_SERVICE_ROOT', None)
        self.command(['bash', 'scripts/setup.sh'], cwd=self.candidate, timeout=1800, env=candidate_env)
        self.command([str(self.candidate / '.venv/bin/python'), '-m', 'pip', 'check'], timeout=60)
        self.report('verify', '正在验证新版本启动和页面资源')
        # 空白临时数据，验证过程不会调用真实账号或发送邮件。
        smoke = """import os, tempfile
from fastapi.testclient import TestClient
with tempfile.TemporaryDirectory() as data:
    os.environ['SCHEDULER_DATA_DIR'] = data
    from backend.main import app
    with TestClient(app) as client:
        assert client.get('/api/health').json()['service'] == 'gpt-account-scheduler'
        assert client.get('/api/settings').status_code == 200
        assert client.get('/api/upgrade').status_code == 200
        assert 'instance' in client.get('/api/health').json()
        assert client.get('/import').status_code == 200
        assert 'image/svg+xml' in client.get('/favicon.svg').headers['content-type']
"""
        self.command([str(self.candidate / '.venv/bin/python'), '-c', smoke], cwd=self.candidate, timeout=90, env=candidate_env)
        return True

    def deploy(self):
        self.validate_checkout()
        self.report('deploy', '构建已通过，正在备份并切换版本')
        (self.data / 'upgrade-deploying').write_text(self.state['id'])
        self.service_command('stop')
        self.stopped = True
        self.backup.mkdir()
        # 停机后备份，回退时不会丢失构建期间产生的账号状态。
        database = self.data / 'scheduler.db'
        if database.exists():
            with sqlite3.connect(database) as source, sqlite3.connect(self.backup / 'scheduler.db') as target:
                source.backup(target)
        if (self.data / 'secret.key').exists():
            shutil.copy2(self.data / 'secret.key', self.backup / 'secret.key')
        self.command(['git', 'merge', '--ff-only', '--no-edit', self.new_commit])
        self.switched_code = True
        os.replace(self.root / '.venv', self.backup / 'venv')
        self.switched_env = True
        (self.root / '.venv').symlink_to(self.candidate / '.venv', target_is_directory=True)
        if (self.root / 'frontend/dist').exists():
            os.replace(self.root / 'frontend/dist', self.backup / 'dist')
        self.switched_dist = True
        shutil.copytree(self.candidate / 'frontend/dist', self.root / 'frontend/dist')
        self.report('restart', '正在重启并检查新版本')
        self.service_command('start')
        if not self.managed_health():
            raise UpgradeFailure('新版本健康检查失败')
        (self.data / 'upgrade-deploying').unlink()
        self.report('done', '升级完成，新版本已运行', 'success')

    def rollback(self):
        self.report('rollback', '新版本部署失败，正在恢复旧版本')
        if (self.data / 'service.json').exists():
            self.service_command('stop')
        if self.switched_code:
            if self.git('rev-parse', 'HEAD') != self.new_commit or self.git('status', '--porcelain', '--untracked-files=normal'):
                raise UpgradeFailure('部署后仓库出现额外改动，未覆盖，请人工恢复')
            self.command(['git', 'reset', '--hard', self.old_commit])
        if self.switched_env:
            path = self.root / '.venv'
            if path.is_symlink():
                path.unlink()
            elif path.exists():
                raise UpgradeFailure('虚拟环境被外部修改，未覆盖，请人工恢复')
            os.replace(self.backup / 'venv', path)
        if self.switched_dist:
            shutil.rmtree(self.root / 'frontend/dist', ignore_errors=True)
            if (self.backup / 'dist').exists():
                os.replace(self.backup / 'dist', self.root / 'frontend/dist')
        if (self.backup / 'scheduler.db').exists():
            for suffix in ('-wal', '-shm'):
                (self.data / ('scheduler.db' + suffix)).unlink(missing_ok=True)
            shutil.copy2(self.backup / 'scheduler.db', self.data / 'scheduler.db')
        if (self.backup / 'secret.key').exists():
            shutil.copy2(self.backup / 'secret.key', self.data / 'secret.key')
        self.service_command('start')
        if not self.managed_health():
            raise UpgradeFailure('旧版本启动尚未确认，请检查 data/service.log')
        (self.data / 'upgrade-deploying').unlink(missing_ok=True)

    def run(self):
        try:
            if self.prepare():
                self.deploy()
        except Exception as error:
            reason = str(error) if isinstance(error, UpgradeFailure) else '升级任务异常，请检查本机环境和磁盘空间'
            if self.stopped:
                try:
                    self.rollback()
                except Exception:
                    self.report('failed', reason + '；自动恢复未完成，请检查 data/upgrades 中的备份和 data/service.log', 'recovery_failed')
                else:
                    self.report('failed', reason + '；已恢复旧版本', 'rolled_back')
            else:
                # 停止失败时不启用第二个服务，也不强制终止原进程。
                if self.managed_health():
                    (self.data / 'upgrade-deploying').unlink(missing_ok=True)
                self.report('failed', reason + '；未切换版本', 'failed')


if __name__ == '__main__':
    root, job_id, lock_fd = sys.argv[1:]
    with os.fdopen(int(lock_fd), 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        Upgrade(root, job_id).run()
