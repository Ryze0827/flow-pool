import json
import os
import sqlite3
import time
from pathlib import Path

from cryptography.fernet import Fernet

from .models import MailSettings, PostgresSettings, RateRules, Settings
from .sub2api import public_account


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        key = self.directory / 'secret.key'
        if not key.exists():
            with key.open('xb') as file:
                file.write(Fernet.generate_key())
        os.chmod(key, 0o600)
        self.cipher = Fernet(key.read_bytes())
        self.db = sqlite3.connect(self.directory / 'scheduler.db')
        os.chmod(self.directory / 'scheduler.db', 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS postgres_settings (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS rate_rules (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mail_settings (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mail_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER, kind TEXT NOT NULL,
                event_key TEXT UNIQUE, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                error TEXT, created_at REAL NOT NULL, sent_at REAL
            );
            CREATE INDEX IF NOT EXISTS mail_pending ON mail_messages(status, id);
            CREATE INDEX IF NOT EXISTS mail_warning ON mail_messages(account_id, kind, created_at);
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, pool TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'active', remote TEXT NOT NULL DEFAULT '{}',
                resume_at REAL, epoch REAL, watermark INTEGER NOT NULL DEFAULT 0,
                probation_until REAL, sample TEXT NOT NULL DEFAULT '[]',
                slow_count INTEGER NOT NULL DEFAULT 0, last_checked REAL, error TEXT,
                reason TEXT, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER,
                level TEXT NOT NULL, action TEXT NOT NULL, message TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inspections (
                id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL,
                state TEXT NOT NULL, sample_size INTEGER NOT NULL, slow_count INTEGER NOT NULL,
                slow_ratio REAL NOT NULL, action TEXT NOT NULL, message TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account_recovery (
                account_id INTEGER PRIMARY KEY, payload TEXT NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_credentials (
                id INTEGER PRIMARY KEY CHECK (id=1), username TEXT NOT NULL,
                password_hash TEXT NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY, username TEXT NOT NULL,
                created_at REAL NOT NULL, expires_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS auth_sessions_expiry ON auth_sessions(expires_at);
        ''')
        # 兼容已有数据库，达限计数独立持久化，不依赖巡检日志保留数量。
        for table, columns in {
            'accounts': {'guard_enabled': 'INTEGER', 'breach_times': "TEXT NOT NULL DEFAULT '[]'", 'breach_sample': "TEXT NOT NULL DEFAULT ''", 'last_breach_at': 'REAL', 'cooldown_mode': "TEXT NOT NULL DEFAULT ''", 'restore_all_groups': 'INTEGER NOT NULL DEFAULT 0', 'relogin_last_at': 'REAL', 'relogin_error': "TEXT NOT NULL DEFAULT ''", 'relogin_count': 'INTEGER NOT NULL DEFAULT 0'},
            'inspections': {'breach_count': 'INTEGER', 'breach_limit': 'INTEGER', 'breach_window_seconds': 'INTEGER'},
        }.items():
            existing = {row['name'] for row in self.db.execute(f'PRAGMA table_info({table})')}
            for name, definition in columns.items():
                if name not in existing:
                    self.db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
                    if table == 'accounts' and name == 'last_breach_at':
                        # 旧版本未保存真正计次的样本基线，新规则不能沿用旧累计次数。
                        self.db.execute("UPDATE accounts SET breach_times='[]', breach_sample=''")
        existing = {row['name'] for row in self.db.execute('PRAGMA table_info(accounts)')}
        for name, definition in {'cooldown_count': 'INTEGER NOT NULL DEFAULT 0', 'cooldown_replacement_id': 'INTEGER', 'relogin_failures': 'INTEGER NOT NULL DEFAULT 0', 'post_import_until': 'REAL', 'post_import_batch_id': "TEXT NOT NULL DEFAULT ''", 'post_import_health': "TEXT NOT NULL DEFAULT '{}'"}.items():
            if name not in existing:
                self.db.execute(f'ALTER TABLE accounts ADD COLUMN {name} {definition}')
                if name == 'cooldown_count':
                    # 已有冷却保留截止时间，计作首次；之后再次冷却使用第二档。
                    self.db.execute("UPDATE accounts SET cooldown_count=1 WHERE resume_at IS NOT NULL AND state IN ('cooldown', 'resuming')")
        self.db.commit()

    def seal(self, value):
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()

    def unseal(self, value):
        return json.loads(self.cipher.decrypt(value.encode()))

    def settings(self):
        row = self.db.execute('SELECT payload FROM settings WHERE id=1').fetchone()
        value = self.unseal(row['payload']) if row else {}
        if os.environ.get('FLOWPOOL_SUB2API_URL'):
            value['base_url'] = os.environ['FLOWPOOL_SUB2API_URL']
        return Settings(**value)

    def save_settings(self, settings):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES (1, ?)', (self.seal(settings.model_dump()),))
        self.db.commit()

    def rate_rules(self):
        row = self.db.execute('SELECT payload FROM rate_rules WHERE id=1').fetchone()
        return RateRules(**self.unseal(row['payload'])) if row else RateRules()

    def save_rate_rules(self, value):
        self.db.execute('INSERT OR REPLACE INTO rate_rules VALUES (1, ?)', (self.seal(value.model_dump()),))
        self.db.commit()

    def postgres_source(self):
        if self.db.execute('SELECT 1 FROM postgres_settings WHERE id=1').fetchone():
            return 'saved'
        return 'environment' if os.environ.get('FLOWPOOL_SUB2API_PG_DSN') else 'default'

    def postgres_settings(self):
        row = self.db.execute('SELECT payload FROM postgres_settings WHERE id=1').fetchone()
        if row:
            return PostgresSettings(**self.unseal(row['payload']))
        dsn = os.environ.get('FLOWPOOL_SUB2API_PG_DSN', '')
        if dsn:
            import psycopg
            from psycopg.conninfo import conninfo_to_dict
            from fastapi import HTTPException
            try:
                values = conninfo_to_dict(dsn)
                mapping = {'host': 'host', 'port': 'port', 'dbname': 'database', 'user': 'username',
                           'password': 'password', 'sslmode': 'sslmode', 'connect_timeout': 'connect_timeout'}
                return PostgresSettings(**{mapping[key]: value for key, value in values.items() if key in mapping})
            except (ValueError, psycopg.Error):
                raise HTTPException(503, 'PostgreSQL 环境变量配置无效，请检查 FLOWPOOL_SUB2API_PG_DSN') from None
        return PostgresSettings()

    def save_postgres_settings(self, value):
        self.db.execute('INSERT OR REPLACE INTO postgres_settings VALUES (1, ?)', (self.seal(value.model_dump()),))
        self.db.commit()

    def mail_settings(self):
        row = self.db.execute('SELECT payload FROM mail_settings WHERE id=1').fetchone()
        return MailSettings(**self.unseal(row['payload'])) if row else MailSettings()

    def save_mail_settings(self, settings):
        self.db.execute('INSERT OR REPLACE INTO mail_settings VALUES (1, ?)', (self.seal(settings.model_dump()),))
        self.db.commit()

    def queue_mail(self, kind, payload, account_id=None, event_key=None, interval=0):
        if interval and self.db.execute('''SELECT 1 FROM mail_messages WHERE account_id=? AND kind=?
                AND status!='skipped' AND created_at>? LIMIT 1''', (account_id, kind, time.time() - interval)).fetchone():
            return None
        cursor = self.db.execute('''INSERT OR IGNORE INTO mail_messages(account_id, kind, event_key, payload, created_at)
            VALUES (?, ?, ?, ?, ?)''', (account_id, kind, event_key, self.seal(payload), time.time()))
        self.db.commit()
        return cursor.lastrowid if cursor.rowcount else None

    def mail_messages(self, limit=100):
        result = []
        for row in self.db.execute('SELECT * FROM mail_messages ORDER BY id DESC LIMIT ?', (limit,)):
            item = dict(row)
            item.update(self.unseal(item.pop('payload')))
            item.pop('event_key')
            result.append(item)
        return result

    def update_mail(self, message_id, status, error=None, sent_at=None):
        self.db.execute('UPDATE mail_messages SET status=?, error=?, sent_at=? WHERE id=?', (status, error, sent_at, message_id))
        self.db.execute("DELETE FROM mail_messages WHERE id < (SELECT COALESCE(MAX(id), 0)-10000 FROM mail_messages) AND status NOT IN ('pending','sending')")
        self.db.commit()

    def accounts(self):
        return [self.decode(row) for row in self.db.execute('''SELECT * FROM accounts
            ORDER BY julianday(json_extract(remote, '$.created_at')) DESC, id DESC''')]

    def account(self, account_id):
        row = self.db.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone()
        return self.decode(row) if row else None

    def decode(self, row):
        result = dict(row)
        for key in ('remote', 'sample', 'breach_times', 'post_import_health'):
            result[key] = json.loads(result[key])
        result['remote'] = public_account(result['remote'])
        return result

    def enroll(self, remote, pool):
        remote = public_account(remote)
        self.db.execute('''INSERT INTO accounts(id, name, pool, remote, updated_at) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, pool=excluded.pool, remote=excluded.remote,
            updated_at=excluded.updated_at''', (remote['id'], remote['name'], pool, json.dumps(remote), time.time()))
        self.db.commit()

    def update(self, account_id, **values):
        allowed = {'name', 'pool', 'state', 'remote', 'resume_at', 'epoch', 'watermark', 'probation_until', 'sample', 'slow_count', 'last_checked', 'error', 'reason', 'breach_times', 'breach_sample', 'last_breach_at', 'cooldown_mode', 'restore_all_groups', 'relogin_last_at', 'relogin_error', 'relogin_count'}
        allowed.update({'guard_enabled', 'cooldown_count', 'cooldown_replacement_id', 'relogin_failures'})
        allowed.update({'post_import_until', 'post_import_batch_id', 'post_import_health'})
        if not values.keys() <= allowed:
            raise ValueError('Invalid account fields')
        values['updated_at'] = time.time()
        if 'remote' in values:
            values['remote'] = public_account(values['remote'])
        for key in ('remote', 'sample', 'breach_times', 'post_import_health'):
            if key in values:
                values[key] = json.dumps(values[key])
        self.db.execute(f"UPDATE accounts SET {', '.join(key + '=?' for key in values)} WHERE id=?", (*values.values(), account_id))
        self.db.commit()

    def set_guard(self, account_id, enabled):
        account = self.account(account_id)
        values = dict(guard_enabled=enabled, breach_times=[], breach_sample='', last_breach_at=None,
                      sample=[], slow_count=0, last_checked=None)
        if account['state'] in {'active', 'probation'}:
            values['reason'] = '账号风控已开启，等待新一轮巡检' if enabled else '账号风控已关闭'
            if not enabled:
                values.update(state='active', probation_until=None)
        self.update(account_id, **values)

    def remove(self, account_id):
        self.db.execute('DELETE FROM accounts WHERE id=?', (account_id,))
        self.db.execute('DELETE FROM account_recovery WHERE account_id=?', (account_id,))
        self.db.commit()

    def save_recovery(self, account_id, payload):
        self.db.execute('INSERT OR REPLACE INTO account_recovery(account_id, payload, updated_at) VALUES (?, ?, ?)',
                        (account_id, self.seal(payload), time.time()))
        self.db.commit()

    def recovery(self, account_id):
        row = self.db.execute('SELECT payload FROM account_recovery WHERE account_id=?', (account_id,)).fetchone()
        return self.unseal(row['payload']) if row else None

    def clear_recovery(self, account_id):
        self.db.execute('DELETE FROM account_recovery WHERE account_id=?', (account_id,))
        self.db.commit()

    def recovery_ids(self):
        return {row['account_id'] for row in self.db.execute('SELECT account_id FROM account_recovery')}

    def event(self, action, message, account_id=None, level='info'):
        self.db.execute('INSERT INTO events(account_id, level, action, message, created_at) VALUES (?, ?, ?, ?, ?)', (account_id, level, action, message, time.time()))
        self.db.execute('DELETE FROM events WHERE id < (SELECT COALESCE(MAX(id), 0)-10000 FROM events)')
        self.db.commit()

    def events(self, limit=100):
        return [dict(row) for row in self.db.execute('SELECT * FROM events ORDER BY id DESC LIMIT ?', (limit,))]

    def inspection(self, account_id, state, sample_size, slow_count, slow_ratio, action, message,
                   breach_count=None, breach_limit=None, breach_window_seconds=None):
        self.db.execute('''INSERT INTO inspections(account_id, state, sample_size, slow_count, slow_ratio, action, message, created_at,
            breach_count, breach_limit, breach_window_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (account_id, state, sample_size, slow_count, slow_ratio, action, message, time.time(),
             breach_count, breach_limit, breach_window_seconds))
        self.db.execute('DELETE FROM inspections WHERE id < (SELECT COALESCE(MAX(id), 0)-10000 FROM inspections)')
        self.db.commit()

    def inspections(self, limit=100):
        return [dict(row) for row in self.db.execute('SELECT * FROM inspections ORDER BY id DESC LIMIT ?', (limit,))]

    def save_batch(self, batch):
        self.db.execute('INSERT OR REPLACE INTO batches VALUES (?, ?, ?)', (batch['id'], self.seal(batch), batch['created_at']))
        self.db.commit()

    def batch(self, batch_id):
        row = self.db.execute('SELECT payload FROM batches WHERE id=?', (batch_id,)).fetchone()
        return self.unseal(row['payload']) if row else None

    def batches(self):
        return [self.unseal(row['payload']) for row in self.db.execute('SELECT payload FROM batches ORDER BY created_at DESC LIMIT 20')]

    def batch_page(self, search, page, page_size):
        query = search.strip().casefold()
        items = []
        total = 0
        for row in self.db.execute('SELECT payload FROM batches ORDER BY created_at DESC, id DESC'):
            batch = self.unseal(row['payload'])
            if query and not any(query in f"{item.get('name', '')} {item.get('email', '')} {item.get('account_id') or ''}".casefold() for item in batch['items']):
                continue
            if (page - 1) * page_size <= total < page * page_size:
                items.append(batch)
            total += 1
        return items, total
