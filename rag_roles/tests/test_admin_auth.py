import concurrent.futures
import re
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

from test_access import app, client
from rag_roles import admin_auth, backend
from rag_roles.admin_access import main
from rag_roles.server import create_app
from rag_roles.sessions import open_session
from rag_roles.storage import migrate


def csrf(c):
    with c.session_transaction() as s:
        return s['csrf']


def stage_one(app, login='RAG_ADMIN', password='TestPassword123!', c=None):
    c = c or app.test_client()
    assert c.get('/admin/login').status_code == 200
    response = c.post('/admin/login', data={'csrf': csrf(c), 'login': login, 'password': password})
    assert response.location == '/admin/verify'
    with c.session_transaction() as s:
        assert 'uid' not in s and 'sid' not in s
        assert s['admin_pending']
    return c


def issue(app, login='RAG_ADMIN'):
    with app.app_context():
        return admin_auth.generate_code(app.db(), login)[0]


def verify(c, code):
    return c.post('/admin/verify', data={'csrf': csrf(c), 'code': code})


def test_normal_login_cannot_issue_admin_and_admin_page_rejects_normal(app):
    c = app.test_client(); c.get('/login')
    assert c.post('/login', data={'csrf': csrf(c), 'login': 'RAG_USER', 'password': 'TestPassword123!'}).location == '/'
    assert c.get('/api/history').status_code == 200
    for route, login in [('/login', 'RAG_ADMIN'), ('/admin/login', 'RAG_ACCOUNTANT')]:
        c = app.test_client(); c.get(route)
        assert c.post(route, data={'csrf': csrf(c), 'login': login, 'password': 'TestPassword123!'}).status_code == 200
        assert c.get('/api/history').status_code == 401
        with c.session_transaction() as s:
            assert not s.get('uid') and not s.get('admin_pending')


def test_pending_cannot_access_protected_routes_or_forge_second_factor(app):
    c = stage_one(app)
    for path in ['/', '/documents', '/documents/1', '/documents/1/pdf',
                 '/documents/1/sources/1', '/documents/1/sources/1/page.png',
                 '/users', '/users/history', '/settings', '/upload', '/index',
                 '/audit', '/audit/export', '/security', '/system', '/history', '/about', '/password']:
        assert c.get(path, headers={'X-Admin-Verified': '1'}).location == '/admin/verify', path
    for path in ['/api/history', '/api/session-state']:
        assert c.get(path + '?admin_verified=1').status_code == 401
    for path in ['/api/search', '/api/activity']:
        assert c.post(path, json={'question': 'payment', 'admin_verified': True}, headers={'X-CSRF-Token': csrf(c)}).status_code == 401
    assert c.post('/settings', data={'csrf': csrf(c), 'top_k': '20', 'temperature': '0.5'}).location == '/admin/verify'
    assert c.get('/api/session').json['user'] is None
    assert verify(c, '0000-0000-0000-0000').status_code == 400
    assert c.get('/api/session').json['user'] is None


def test_success_rotates_csrf_consumes_code_and_records_server_proof(app):
    c = stage_one(app); old_csrf = csrf(c); code = issue(app)
    response = verify(c, code.lower().replace('-', ' '))
    assert response.location == '/' and response.headers['Cache-Control'] == 'no-store'
    assert c.get('/users').status_code == 200
    assert csrf(c) != old_csrf
    with c.session_transaction() as s:
        assert 'admin_pending' not in s and code not in str(dict(s))
        sid = s['sid']
    with app.app_context():
        row = app.db().execute('SELECT * FROM user_sessions WHERE sid=?', (sid,)).fetchone()
        assert row['admin_verified_at'] is not None and row['authenticated_role'] == 'RAG_ADMIN'
        assert app.db().execute('SELECT used_at FROM admin_codes').fetchone()[0] is not None
        audit = str([tuple(r) for r in app.db().execute('SELECT * FROM audit')])
        assert 'admin_code_verified' in audit and code not in audit and sid not in audit
        assert 'TestPassword123!' not in audit
    assert verify(stage_one(app), code).status_code == 400


@pytest.mark.parametrize('elapsed,accepted', [(599.999, True), (600, False), (601, False)])
def test_code_exact_expiry_from_generation_independent_of_pending(app, monkeypatch, elapsed, accepted):
    now = [time.time()]
    monkeypatch.setattr(admin_auth.time, 'time', lambda: now[0])
    code = issue(app); generated = now[0]
    now[0] += 100; c = stage_one(app)
    now[0] = generated + elapsed
    response = verify(c, code)
    assert response.status_code == (302 if accepted else 400)
    assert (c.get('/users').status_code == 200) == accepted


def test_pending_expiry_and_cancel_are_server_side(app, monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(admin_auth.time, 'time', lambda: now[0])
    c = stage_one(app); started = now[0]
    now[0] += 200; code = issue(app)
    now[0] = started + 600
    assert verify(c, code).location == '/admin/login'
    c = stage_one(app); copied = c.get_cookie('session').value
    assert c.post('/admin/cancel', data={'csrf': csrf(c)}).location == '/admin/login'
    replay = app.test_client(); replay.set_cookie('session', copied)
    assert verify(replay, issue(app)).location == '/admin/login'


def test_new_code_revokes_old_and_is_bound_to_account(app):
    with app.app_context():
        app.db().execute("INSERT INTO users(login,name,password,role) VALUES('otheradmin','Other',?,'RAG_ADMIN')",
                         (generate_password_hash('TestPassword123!'),)); app.db().commit()
    first = issue(app); second = issue(app); foreign = issue(app, 'otheradmin')
    c = stage_one(app)
    for invalid in [first, foreign, 'bad', 'a' * 1000]:
        assert verify(c, invalid).status_code == 400
    assert verify(c, second).location == '/'
    with app.app_context():
        rows = app.db().execute('SELECT * FROM admin_codes ORDER BY id').fetchall()
        assert rows[0]['revoked_at'] is not None
        assert rows[1]['used_at'] is not None and rows[2]['used_at'] is None
        assert all(row['code_hash'] not in (first, second, foreign) for row in rows)
        assert all(len(row['code_hash']) == 64 for row in rows)


@pytest.mark.parametrize('same_attempt', [False, True])
def test_concurrent_and_repeated_verification_only_creates_one_session(app, same_attempt):
    first = stage_one(app); second = app.test_client()
    if same_attempt:
        second.set_cookie('session', first.get_cookie('session').value)
    else:
        second = stage_one(app)
    code = issue(app); barrier = threading.Barrier(2)
    def submit(c):
        barrier.wait(timeout=10)
        response = verify(c, code)
        return response.location == '/' and c.get('/users').status_code == 200
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [first, second]))
    assert sum(results) == 1
    with app.app_context():
        assert app.db().execute('SELECT count(*) FROM user_sessions WHERE user_id=4').fetchone()[0] == 1
        assert app.db().execute("SELECT count(*) FROM audit WHERE event='admin_code_verified'").fetchone()[0] == 1


def test_five_errors_close_attempt_and_account_limit_survives_new_cookies_and_code(app, monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(admin_auth.time, 'time', lambda: now[0])
    for _ in range(2):
        c = stage_one(app); cookie = c.get_cookie('session').value
        for _ in range(4):
            assert verify(c, 'WRONG').status_code == 400
        assert verify(c, 'WRONG').location == '/admin/login'
        replay = app.test_client(); replay.set_cookie('session', cookie)
        assert verify(replay, issue(app)).location == '/admin/login'
    code = issue(app); c = stage_one(app)
    assert verify(c, code).status_code == 429
    # The database limit survives another app instance and a changed source IP.
    with app.app_context():
        directory = Path(app.db().execute('PRAGMA database_list').fetchone()[2]).parent
    restarted = create_app(directory); restarted.config['TESTING'] = True
    c = stage_one(restarted)
    assert c.post('/admin/verify', data={'csrf': csrf(c), 'code': code},
                  environ_overrides={'REMOTE_ADDR': '192.0.2.99'}).status_code == 429
    now[0] += 600
    c = stage_one(app)
    assert verify(c, issue(app)).location == '/'


@pytest.mark.parametrize('change', ["active=0", "role='RAG_ACCOUNTANT'", 'session_version=session_version+1',
                                   "password='changed'", 'must_change_password=1', "deleted_at='2026-10-09'"])
def test_account_changes_revoke_pending_codes_and_full_sessions(app, change):
    full = client(app, 4); pending = stage_one(app); code = issue(app)
    with app.app_context():
        db = app.db(); db.execute('UPDATE users SET ' + change + ' WHERE id=4'); db.commit()
        assert db.execute('SELECT count(*) FROM user_sessions WHERE user_id=4').fetchone()[0] == 0
        assert db.execute('SELECT revoked_at FROM admin_codes').fetchone()[0] is not None
        assert db.execute('SELECT revoked_at FROM admin_login_attempts').fetchone()[0] is not None
    assert verify(pending, code).location == '/admin/login'
    assert full.get('/api/history').status_code == 401


def test_promotion_and_legacy_sessions_cannot_skip_second_stage(app):
    old = client(app, 2)
    with app.app_context():
        db = app.db(); db.execute("UPDATE users SET role='RAG_ADMIN' WHERE id=2"); db.commit()
        user = db.execute('SELECT * FROM users WHERE id=2').fetchone()
        # Even a correctly signed cookie plus a server session without second
        # factor proof must fail. Client-side claims cannot upgrade that record.
        sid = open_session(db, user, '127.0.0.1')
        version = user['session_version']
    assert old.get('/api/history').status_code == 401
    forged = app.test_client()
    with forged.session_transaction() as s:
        s.update(uid=2, sid=sid, session_version=version, csrf='test', admin_verified=True)
    assert forged.get('/users').location == '/login'
    assert forged.get('/api/history').status_code == 401
    c = stage_one(app, 'RAG_ACCOUNTANT')
    assert verify(c, issue(app, 'RAG_ACCOUNTANT')).location == '/'


def test_admin_temporary_password_requires_both_stages_then_only_password_change(app):
    with app.app_context():
        app.db().execute("INSERT INTO users(login,name,password,role) VALUES('resetadmin','Other',?,'RAG_ADMIN')",
                         (generate_password_hash('OldPassword123!'),)); app.db().commit()
        uid = app.db().execute("SELECT id FROM users WHERE login='resetadmin'").fetchone()[0]
    pending = stage_one(app, 'resetadmin', 'OldPassword123!'); old_code = issue(app, 'resetadmin')
    response = client(app, 4).post(f'/users/{uid}/reset-password', data={'csrf': 'test'})
    temporary = re.search(rb'value="([A-Za-z0-9_-]{24})" readonly', response.data)[1].decode()
    assert verify(pending, old_code).location == '/admin/login'
    c = stage_one(app, 'resetadmin', temporary)
    assert c.get('/password').location == '/admin/verify'
    assert verify(c, issue(app, 'resetadmin')).location == '/password'
    assert c.get('/users').location == '/password'
    assert c.get('/api/session').status_code == 403
    unused = issue(app, 'resetadmin')
    response = c.post('/password', data={'csrf': csrf(c), 'current_password': temporary,
                      'password': 'PermanentPassword456!', 'confirmation': 'PermanentPassword456!'})
    assert response.location == '/admin/login'
    c = stage_one(app, 'resetadmin', 'PermanentPassword456!')
    assert verify(c, unused).status_code == 400
    assert verify(c, issue(app, 'resetadmin')).location == '/'


def test_auth_forms_csrf_no_store_and_missing_stage_one(app):
    c = app.test_client()
    for path in ['/login', '/admin/login', '/admin/verify']:
        assert c.get(path).headers['Cache-Control'] == 'no-store'
    for path in ['/login', '/admin/login', '/admin/verify', '/admin/cancel']:
        assert c.post(path, data={'code': issue(app)}).status_code == 400
    assert verify(c, issue(app)).location == '/admin/login'
    c = stage_one(app)
    for path in ['/admin/verify', '/admin/cancel']:
        assert c.post(path, data={'csrf': 'forged', 'code': issue(app)}).status_code == 400
    assert verify(c, issue(app)).location == '/'
    assert not any('generate' in rule.rule for rule in app.url_map.iter_rules())


def test_completed_session_is_not_limited_to_code_lifetime(app, monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(admin_auth.time, 'time', lambda: now[0])
    c = stage_one(app); assert verify(c, issue(app)).location == '/'
    for _ in range(4):
        now[0] += 240
        assert c.post('/api/activity', data={'csrf': csrf(c)}).status_code == 200
    assert c.get('/users').status_code == 200  # 16 minutes, no new code.
    now[0] += 300
    assert c.get('/api/history').status_code == 401  # Existing idle policy remains.


def test_revocation_during_generation_discards_admin_answer(app, monkeypatch):
    c = stage_one(app); assert verify(c, issue(app)).location == '/'
    def generate(*args, **kwargs):
        app.db().execute('UPDATE users SET session_version=session_version+1 WHERE id=4'); app.db().commit()
        return 'MUST_NOT_ESCAPE', []
    monkeypatch.setattr(backend, 'generate', generate)
    response = c.post('/api/search', json={'question': 'payment'}, headers={'X-CSRF-Token': csrf(c)})
    assert response.status_code == 401 and b'MUST_NOT_ESCAPE' not in response.data


def test_cli_uses_configured_store_prints_once_and_never_accepts_secret_argument(app, monkeypatch, capsys):
    with app.app_context():
        directory = Path(app.db().execute('PRAGMA database_list').fetchone()[2]).parent
    monkeypatch.setenv('RAG_ROLES_DATA', str(directory))
    main(['generate', '--login', 'RAG_ADMIN'])
    output = capsys.readouterr().out
    code = re.search(r'Code: ([A-Z0-9-]+)', output)[1]
    assert output.count(code) == 1 and 'Expires at (UTC)' in output
    assert verify(stage_one(app), code).location == '/'
    with pytest.raises(SystemExit):
        main(['generate', '--login', 'RAG_ADMIN', '--code', 'not-accepted'])
    for login in ['missing', 'RAG_ACCOUNTANT']:
        with pytest.raises(SystemExit): main(['generate', '--login', login])
    with app.app_context():
        app.db().execute('UPDATE users SET active=0 WHERE id=4'); app.db().commit()
    with pytest.raises(SystemExit): main(['generate', '--login', 'RAG_ADMIN'])


def test_migration_backs_up_and_preserves_data_revoking_only_legacy_admin_sessions(tmp_path):
    path = tmp_path / 'roles.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
            CREATE TABLE users(id INTEGER PRIMARY KEY,login TEXT UNIQUE,name TEXT,password TEXT,role TEXT,active INTEGER DEFAULT 1,session_version INTEGER DEFAULT 0);
            CREATE TABLE documents(id INTEGER PRIMARY KEY,title TEXT,level TEXT,roles TEXT,status TEXT);
            CREATE TABLE chunks(id INTEGER PRIMARY KEY,document_id INTEGER,page INTEGER,text TEXT);
            CREATE TABLE chunk_details(chunk_id INTEGER PRIMARY KEY,metadata TEXT,vector BLOB);
            CREATE TABLE settings(id INTEGER PRIMARY KEY,top_k INTEGER,temperature REAL);
            CREATE TABLE history(id INTEGER PRIMARY KEY,user_id INTEGER,at TEXT,question TEXT);
            CREATE TABLE audit(id INTEGER PRIMARY KEY,at TEXT,actor TEXT,event TEXT,target TEXT,status TEXT);
            CREATE TABLE user_sessions(sid TEXT PRIMARY KEY,user_id INTEGER,version INTEGER,last_activity REAL,ip TEXT);
            INSERT INTO users VALUES(1,'admin','Admin','ADMIN_HASH','RAG_ADMIN',1,7);
            INSERT INTO users VALUES(2,'user','User','USER_HASH','RAG_USER',1,3);
            INSERT INTO documents VALUES(1,'Preserved','general','["RAG_USER"]','Готов');
            INSERT INTO chunks VALUES(1,1,1,'RETAINED_TEXT');
            INSERT INTO chunk_details VALUES(1,'{"pdf_page":1}',X'00010203');
            INSERT INTO history VALUES(1,1,'2026-10-09','RETAINED_QUESTION');
            INSERT INTO user_sessions VALUES('old-admin',1,7,123,'127.0.0.1');
            INSERT INTO user_sessions VALUES('old-user',2,3,123,'127.0.0.1');
        ''')
        db.row_factory = sqlite3.Row
        migrate(db)
        assert list(tmp_path.glob('backups/roles-before-admin-2fa-*.sqlite3'))
        snapshot = list(tmp_path.glob('backups/*.sqlite3'))[0]
        with sqlite3.connect(snapshot) as backup:
            assert backup.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert backup.execute('SELECT count(*) FROM user_sessions').fetchone()[0] == 2
            assert backup.execute('SELECT session_version FROM users WHERE id=1').fetchone()[0] == 7
        assert db.execute('SELECT password FROM users WHERE id=1').fetchone()[0] == 'ADMIN_HASH'
        assert db.execute('SELECT session_version FROM users WHERE id=1').fetchone()[0] == 8
        assert db.execute('SELECT session_version FROM users WHERE id=2').fetchone()[0] == 3
        assert db.execute('SELECT sid FROM user_sessions').fetchone()[0] == 'old-user'
        assert db.execute('SELECT roles FROM documents').fetchone()[0] == '["RAG_USER"]'
        assert db.execute('SELECT text FROM chunks').fetchone()[0] == 'RETAINED_TEXT'
        assert tuple(db.execute('SELECT metadata,vector FROM chunk_details').fetchone()) == ('{"pdf_page":1}', b'\x00\x01\x02\x03')
        assert db.execute('SELECT question FROM history').fetchone()[0] == 'RETAINED_QUESTION'
        user = db.execute('SELECT * FROM users WHERE id=1').fetchone()
        sid = open_session(db, user, '127.0.0.1', admin_verified_at=time.time())
        migrate(db)
        assert db.execute('SELECT 1 FROM user_sessions WHERE sid=?', (sid,)).fetchone()
        assert db.execute('SELECT session_version FROM users WHERE id=1').fetchone()[0] == 8
        assert db.execute("SELECT count(*) FROM security_migrations WHERE name='admin_2fa_v1'").fetchone()[0] == 1
        assert len(list(tmp_path.glob('backups/*.sqlite3'))) == 1


def test_failed_backup_prevents_migration(app, monkeypatch):
    from rag_roles import storage
    with app.app_context():
        db = app.db()
        db.execute("DELETE FROM security_migrations WHERE name='admin_2fa_v1'"); db.commit()
        before = [tuple(r) for r in db.execute('SELECT id,password,role,session_version FROM users ORDER BY id')]
        def unavailable(*args, **kwargs):
            raise OSError('Backup destination unavailable')
        monkeypatch.setattr(storage.sqlite3, 'connect', unavailable)
        with pytest.raises(OSError, match='Backup destination unavailable'):
            migrate(db)
        assert [tuple(r) for r in db.execute('SELECT id,password,role,session_version FROM users ORDER BY id')] == before
        assert not db.execute("SELECT 1 FROM security_migrations WHERE name='admin_2fa_v1'").fetchone()
