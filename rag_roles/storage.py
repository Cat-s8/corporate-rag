"""Additive schema: legacy rows and existing accounts remain intact."""
import json
from datetime import datetime, timezone
from pathlib import Path
import secrets
import sqlite3


def backup_before_admin_2fa(db):
    """SQLite online backup before the first security migration, including WAL.

    Fail closed if a backup of an existing populated database cannot be made.
    In-memory test databases have no persistent files to back up.
    """
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'security_migrations' in tables and db.execute(
            "SELECT 1 FROM security_migrations WHERE name='admin_2fa_v1'").fetchone():
        return
    if not any(table in tables and db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
               for table in ('users', 'documents', 'chunks', 'history', 'audit')):
        return
    filename = next((r[2] for r in db.execute('PRAGMA database_list') if r[1] == 'main'), '')
    if not filename:
        return
    if db.in_transaction:
        raise RuntimeError('Commit the current transaction before backing up and migrating the database')
    # The caller invokes this before starting a write transaction.
    directory = Path(filename).parent / 'backups'
    directory.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    destination = directory / f'roles-before-admin-2fa-{stamp}-{secrets.token_hex(4)}.sqlite3'
    with sqlite3.connect(destination) as backup:
        db.backup(backup)
        if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Pre-migration database backup failed its integrity check')


def migrate_admin_2fa(db):
    with db:
        db.execute('BEGIN IMMEDIATE')
        columns = {r[1] for r in db.execute('PRAGMA table_info(user_sessions)')}
        if 'admin_verified_at' not in columns:
            db.execute('ALTER TABLE user_sessions ADD COLUMN admin_verified_at REAL')
        if 'authenticated_role' not in columns:
            db.execute('ALTER TABLE user_sessions ADD COLUMN authenticated_role TEXT')
            db.execute('''UPDATE user_sessions SET authenticated_role=
                (SELECT role FROM users WHERE id=user_sessions.user_id)''')
        db.execute('''CREATE TABLE IF NOT EXISTS admin_codes(
            id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),
            code_hash TEXT NOT NULL,version INTEGER NOT NULL,created_at REAL NOT NULL,
            expires_at REAL NOT NULL,used_at REAL,revoked_at REAL)''')
        db.execute('CREATE INDEX IF NOT EXISTS admin_codes_user ON admin_codes(user_id)')
        db.execute('''CREATE TABLE IF NOT EXISTS admin_login_attempts(
            token TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),
            version INTEGER NOT NULL,created_at REAL NOT NULL,expires_at REAL NOT NULL,
            failures INTEGER NOT NULL DEFAULT 0,completed_at REAL,revoked_at REAL,ip TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS admin_attempts_user ON admin_login_attempts(user_id)')
        db.execute('''CREATE TABLE IF NOT EXISTS admin_code_checks(
            user_id INTEGER NOT NULL REFERENCES users(id),at REAL NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS admin_checks_user_time ON admin_code_checks(user_id,at)')
        db.execute('CREATE TABLE IF NOT EXISTS security_migrations(name TEXT PRIMARY KEY,applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        # Also covers local account maintenance: promotion, reactivation, password
        # changes and version bumps cannot revive a previous code or login attempt.
        db.execute('''CREATE TRIGGER IF NOT EXISTS revoke_changed_auth
            AFTER UPDATE ON users WHEN
                OLD.role IS NOT NEW.role OR OLD.active IS NOT NEW.active OR
                OLD.password IS NOT NEW.password OR OLD.session_version IS NOT NEW.session_version OR
                OLD.deleted_at IS NOT NEW.deleted_at OR OLD.must_change_password IS NOT NEW.must_change_password OR
                OLD.temporary_password_expires_at IS NOT NEW.temporary_password_expires_at
            BEGIN
                DELETE FROM user_sessions WHERE user_id=NEW.id;
                INSERT INTO audit(actor,event,target,status,ip)
                    SELECT 'system','admin_code_revoked',CAST(NEW.id AS TEXT),'Успешно',''
                    WHERE EXISTS(SELECT 1 FROM admin_codes WHERE user_id=NEW.id AND used_at IS NULL AND revoked_at IS NULL);
                UPDATE admin_codes SET revoked_at=CAST(strftime('%s','now') AS REAL)
                    WHERE user_id=NEW.id AND used_at IS NULL AND revoked_at IS NULL;
                INSERT INTO audit(actor,event,target,status,ip)
                    SELECT 'system','admin_login_cancelled',CAST(NEW.id AS TEXT),'Успешно',''
                    WHERE EXISTS(SELECT 1 FROM admin_login_attempts WHERE user_id=NEW.id AND completed_at IS NULL AND revoked_at IS NULL);
                UPDATE admin_login_attempts SET revoked_at=CAST(strftime('%s','now') AS REAL)
                    WHERE user_id=NEW.id AND completed_at IS NULL AND revoked_at IS NULL;
                UPDATE users SET session_version=session_version+1
                    WHERE id=NEW.id AND session_version=OLD.session_version;
            END''')
        if not db.execute("SELECT 1 FROM security_migrations WHERE name='admin_2fa_v1'").fetchone():
            db.execute("UPDATE users SET session_version=session_version+1 WHERE role='RAG_ADMIN'")
            db.execute("DELETE FROM user_sessions WHERE user_id IN (SELECT id FROM users WHERE role='RAG_ADMIN')")
            db.execute("INSERT INTO security_migrations(name) VALUES('admin_2fa_v1')")
            db.execute('''INSERT INTO audit(actor,event,target,status,ip)
                VALUES('migration','admin_2fa_enabled','admin sessions revoked','Успешно','')''')


def migrate(db, *, backup=True):
    if backup:
        backup_before_admin_2fa(db)
    columns={row[1] for row in db.execute('PRAGMA table_info(users)')}
    for name,definition in (
        ('session_version','INTEGER NOT NULL DEFAULT 0'),
        ('must_change_password','INTEGER NOT NULL DEFAULT 0'),
        ('temporary_password_expires_at','REAL'),
        ('deleted_at','TEXT')):
        if name not in columns:
            db.execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')
    if 'ip' not in {row[1] for row in db.execute('PRAGMA table_info(audit)')}:
        db.execute("ALTER TABLE audit ADD COLUMN ip TEXT NOT NULL DEFAULT ''")
    db.executescript('''
    CREATE TABLE IF NOT EXISTS user_sessions(
        sid TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),
        version INTEGER NOT NULL,last_activity REAL NOT NULL,ip TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS user_sessions_user ON user_sessions(user_id);
    CREATE TABLE IF NOT EXISTS chunk_details(
        chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
        metadata TEXT NOT NULL, vector BLOB);
    CREATE TABLE IF NOT EXISTS imported_sources(
        source TEXT PRIMARY KEY, document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
        digest TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS document_files(
        document_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
        path TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS corpus_revision(id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS document_groups(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE);
    CREATE TABLE IF NOT EXISTS document_group_members(
        document_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
        group_id INTEGER NOT NULL REFERENCES document_groups(id) ON DELETE CASCADE);
    INSERT OR IGNORE INTO corpus_revision VALUES(1,0);
    ''')
    for table in ('users', 'documents', 'chunks', 'chunk_details', 'settings','document_groups','document_group_members'):
        for action in ('INSERT', 'UPDATE', 'DELETE'):
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS revision_{table}_{action}
                AFTER {action} ON {table} BEGIN
                UPDATE corpus_revision SET value=value+1 WHERE id=1; END''')
    db.commit()
    migrate_business_roles(db)
    migrate_admin_2fa(db)


def migrate_business_roles(db):
    """Rename legacy roles and matching ACL entries together, without broadening ACLs."""
    replacements = {
        'RAG_RESTRICTED_USER': 'RAG_ACCOUNTANT',
        'RAG_CONTENT_MANAGER': 'RAG_REPORTING_SPECIALIST',
    }
    with db:
        users = 0
        documents = 0
        for old, new in replacements.items():
            users += db.execute('UPDATE users SET role=? WHERE role=?', (new, old)).rowcount
        for row in db.execute('SELECT id, roles FROM documents').fetchall():
            try:
                roles = json.loads(row['roles'])
            except (ValueError, TypeError):
                continue  # Malformed ACLs remain denied by the policy.
            if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
                continue
            updated = list(dict.fromkeys(replacements.get(role, role) for role in roles))
            if updated != roles:
                db.execute('UPDATE documents SET roles=? WHERE id=?',
                           (json.dumps(updated, ensure_ascii=False), row['id']))
                documents += 1
        if users or documents:
            db.execute('INSERT INTO audit(actor,event,target,status) VALUES(?,?,?,?)',
                       ('migration', 'business_roles_migrated',
                        json.dumps({'users': users, 'documents': documents}), 'Успешно'))


def chunks_for(db, documents):
    """Fetch texts/vectors only for documents authorized by the caller."""
    result = []
    for doc in documents:
        for row in db.execute('''SELECT c.*, x.metadata, x.vector FROM chunks c
                LEFT JOIN chunk_details x ON x.chunk_id=c.id
                WHERE c.document_id=? ORDER BY c.id''', (doc['id'],)):
            extra = json.loads(row['metadata']) if row['metadata'] else {}
            extra.update(id=row['id'], chunk_id=row['id'], document_id=doc['id'],
                         title=doc['title'], text=row['text'], page=row['page'], vector=row['vector'])
            extra.setdefault('source', doc['title'])
            extra.setdefault('document_title', doc['title'])
            extra.setdefault('document_page', row['page'])
            extra.setdefault('pdf_page', row['page'])
            result.append(extra)
    # Stable ordering keeps BM25/FAISS tie-breaking independent of document-list UI order.
    return sorted(result, key=lambda chunk: chunk['id'])


def public_source(chunk):
    return {key: value for key, value in chunk.items() if key != 'vector'}
