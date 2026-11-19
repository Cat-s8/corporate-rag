import sqlite3
import time

import pytest

from test_access import app, client
from rag_roles import backend, sessions
from rag_roles.storage import migrate


def login_from(app, login='RAG_ACCOUNTANT', peer='192.0.2.10', forwarded=None):
    c = app.test_client()
    c.get('/login')
    with c.session_transaction() as session:
        csrf = session['csrf']
    response = c.post('/admin/login' if login=='RAG_ADMIN' else '/login', data={
        'csrf': csrf, 'login': login, 'password': 'TestPassword123!',
    }, environ_overrides={'REMOTE_ADDR': peer},
        headers={'X-Forwarded-For': forwarded} if forwarded else {})
    if login=='RAG_ADMIN' and response.location=='/admin/verify':
        from rag_roles.admin_auth import generate_code
        with app.app_context():
            code,_=generate_code(app.db(),login)
        with c.session_transaction() as session:
            csrf=session['csrf']
        response=c.post('/admin/verify',data={'csrf':csrf,'code':code},
                        environ_overrides={'REMOTE_ADDR':peer},
                        headers={'X-Forwarded-For':forwarded} if forwarded else {})
    return c, response


@pytest.mark.parametrize('uid,limit', [(4, 300), (2, 900)])
def test_idle_expiry_boundary_and_passive_requests(app, monkeypatch, uid, limit):
    now = [time.time()]
    monkeypatch.setattr(sessions.time, 'time', lambda: now[0])
    c = client(app, uid)
    cookie = c.get_cookie('session').value
    now[0] += limit - 1
    state = c.get('/api/session-state')
    assert state.status_code == 200
    assert state.json['remaining'] == pytest.approx(1)
    assert c.get('/api/session').status_code == 200
    assert c.get('/static/style.css').status_code == 200
    now[0] += 1
    assert c.post('/api/activity', data={'csrf': 'test'}).status_code == 401
    assert c.get('/documents/1').location == '/login'
    replay = app.test_client()
    replay.set_cookie('session', cookie)
    assert replay.get('/api/session-state').status_code == 401
    with app.app_context():
        assert app.db().execute('SELECT count(*) FROM user_sessions WHERE user_id=?', (uid,)).fetchone()[0] == 0
        assert app.db().execute("SELECT count(*) FROM audit WHERE event='session_expired'").fetchone()[0] == 1


def test_only_valid_activity_renews_session(app, monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(sessions.time, 'time', lambda: now[0])
    c = client(app, 4)
    now[0] += 240
    assert c.post('/api/activity').status_code == 400
    assert c.get('/api/session-state').json['remaining'] == pytest.approx(60)
    assert c.post('/api/activity', data={'csrf': 'test'}).json['remaining'] == 300
    now[0] += 299
    assert c.get('/api/session-state').status_code == 200
    assert c.get('/documents').status_code == 200
    assert c.get('/api/session-state').json['remaining'] == pytest.approx(300)


def test_generation_cannot_return_after_session_expires(app, monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(sessions.time, 'time', lambda: now[0])
    def generate(question, sources, temperature, **kwargs):
        now[0] += 300
        return 'ANSWER_MUST_NOT_ESCAPE', sources
    monkeypatch.setattr(backend, 'generate', generate)
    response = client(app, 4).post('/api/search', json={'question': 'payment'},
                                   headers={'X-CSRF-Token': 'test'})
    assert response.status_code == 401
    assert b'ANSWER_MUST_NOT_ESCAPE' not in response.data
    with app.app_context():
        assert app.db().execute('SELECT count(*) FROM history').fetchone()[0] == 0


def test_logout_revokes_copied_cookie(app):
    c = client(app, 2)
    copied = c.get_cookie('session').value
    assert c.post('/logout', data={'csrf': 'test'}).location == '/login'
    replay = app.test_client()
    replay.set_cookie('session', copied)
    assert replay.get('/api/history').status_code == 401


def test_cannot_assign_admin_role_via_create_or_update(app):
    c = client(app, 4)
    assert c.post('/users', data={
        'csrf': 'test', 'action': 'create', 'login': 'newadmin', 'name': 'New Admin',
        'password': 'TestPassword123!', 'role': 'RAG_ADMIN',
    }).status_code == 400
    assert c.post('/users', data={
        'csrf': 'test', 'action': 'update', 'uid': 2,
        'role': 'RAG_ADMIN', 'active': '1',
    }).status_code == 400
    assert b'<option value="RAG_ADMIN"' not in c.get('/users').data
    with app.app_context():
        assert app.db().execute("SELECT count(*) FROM users WHERE login='newadmin'").fetchone()[0] == 0
        assert app.db().execute('SELECT role FROM users WHERE id=2').fetchone()[0] == 'RAG_ACCOUNTANT'


def test_delete_preserves_history_and_revokes_every_access_path(app):
    old = client(app, 2)
    with app.app_context():
        app.db().execute('INSERT INTO history(user_id,question) VALUES(2,?)', ('RETAINED_QUESTION',))
        app.db().commit()
    admin = client(app, 4)
    assert admin.post('/users/2/delete', data={'csrf': 'test', 'confirm': 'yes'}).status_code == 302
    with app.app_context():
        db = app.db()
        deleted = db.execute('SELECT * FROM users WHERE id=2').fetchone()
        assert deleted['deleted_at'] and not deleted['active']
        assert not deleted['password']
        assert db.execute('SELECT count(*) FROM user_sessions WHERE user_id=2').fetchone()[0] == 0
        assert db.execute('SELECT question FROM history WHERE user_id=2').fetchone()[0] == 'RETAINED_QUESTION'
    assert old.get('/api/history').status_code == 401
    assert login_from(app)[1].status_code == 200
    assert b'/users/2/delete' not in admin.get('/users').data
    history = admin.get('/users/history?user_id=2')
    assert history.status_code == 200
    assert b'RETAINED_QUESTION' in history.data and b'RAG_ACCOUNTANT' in history.data
    assert 'удалён' in history.get_data(as_text=True).casefold()
    assert admin.post('/users/2/reset-password', data={'csrf': 'test'}).status_code == 404
    assert admin.post('/users', data={
        'csrf': 'test', 'action': 'update', 'uid': 2, 'role': 'RAG_USER', 'active': '1',
    }).status_code == 404
    assert admin.post('/users/2/delete', data={'csrf': 'test', 'confirm': 'yes'}).status_code == 404


def test_delete_requires_admin_csrf_confirmation_and_valid_target(app):
    for uid in (1, 2, 3, 5):
        assert client(app, uid).post('/users/2/delete', data={'csrf': 'test', 'confirm': 'yes'}).status_code == 403
    c = client(app, 4)
    assert c.post('/users/2/delete', data={'confirm': 'yes'}).status_code == 400
    assert c.post('/users/2/delete', data={'csrf': 'test'}).status_code == 400
    assert c.post('/users/4/delete', data={'csrf': 'test', 'confirm': 'yes'}).status_code == 400
    assert c.post('/users/999/delete', data={'csrf': 'test', 'confirm': 'yes'}).status_code == 404
    with app.app_context():
        assert app.db().execute('SELECT count(*) FROM users WHERE deleted_at IS NOT NULL').fetchone()[0] == 0


def test_admin_history_filter_and_other_roles_isolation(app):
    with app.app_context():
        app.db().executemany('INSERT INTO history(user_id,question) VALUES(?,?)',
                            [(1, 'ONLY_USER_ONE'), (2, 'ONLY_USER_TWO')])
        app.db().commit()
    admin = client(app, 4)
    all_rows = admin.get('/users/history')
    assert all_rows.status_code == 200
    assert b'ONLY_USER_ONE' in all_rows.data and b'ONLY_USER_TWO' in all_rows.data
    filtered = admin.get('/users/history?user_id=2')
    assert b'ONLY_USER_TWO' in filtered.data and b'ONLY_USER_ONE' not in filtered.data
    for uid in (1, 2, 3, 5):
        c = client(app, uid)
        assert c.get('/users/history?user_id=1').status_code == 403
        assert b'ONLY_USER_ONE' not in c.get('/history?user_id=1').data or uid == 1
        assert b'ONLY_USER_ONE' not in c.get('/api/history?user_id=1').data or uid == 1


def test_audit_session_ip_failed_login_proxy_and_csv(app):
    c, response = login_from(app, login='RAG_ADMIN', forwarded='203.0.113.99')
    assert response.status_code == 302
    with c.session_transaction() as session:
        csrf = session['csrf']
    assert c.post('/users/2/reset-password', data={'csrf': csrf},
                  environ_overrides={'REMOTE_ADDR': '192.0.2.20'}).status_code == 200
    assert c.post('/settings', data={'top_k': '4', 'temperature': '0.1'}).status_code == 400
    with app.app_context():
        rows = app.db().execute("SELECT event,ip FROM audit WHERE event IN ('login','password_reset','csrf_denied')").fetchall()
        assert len(rows) == 3
        assert all(row['ip'] == '192.0.2.10' for row in rows)
    for url in ('/audit', '/security', '/audit/export'):
        output = c.get(url)
        assert output.status_code == 200 and b'192.0.2.10' in output.data
    login_from(app, login='missing', peer='192.0.2.44')
    with app.app_context():
        assert app.db().execute("SELECT ip FROM audit WHERE event='login_failed' ORDER BY id DESC").fetchone()[0] == '192.0.2.44'
    app.config['TRUST_LOCAL_PROXY'] = True
    proxied, response = login_from(app, login='RAG_USER', peer='127.0.0.1',
                                  forwarded='203.0.113.99, 100.64.0.8')
    assert response.status_code == 302
    with app.app_context():
        assert app.db().execute("SELECT ip FROM audit WHERE event='login' ORDER BY id DESC").fetchone()[0] == '100.64.0.8'
    login_from(app, login='RAG_USER', peer='192.0.2.15', forwarded='100.64.0.9')
    with app.app_context():
        assert app.db().execute("SELECT ip FROM audit WHERE event='login' ORDER BY id DESC").fetchone()[0] == '192.0.2.15'


def test_security_migration_preserves_existing_accounts_and_logs():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE users(id INTEGER PRIMARY KEY,login TEXT,name TEXT,password TEXT,role TEXT,active INTEGER DEFAULT 1);
        CREATE TABLE documents(id INTEGER PRIMARY KEY,roles TEXT);
        CREATE TABLE chunks(id INTEGER PRIMARY KEY);
        CREATE TABLE settings(id INTEGER PRIMARY KEY);
        CREATE TABLE history(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),question TEXT);
        CREATE TABLE audit(id INTEGER PRIMARY KEY,at TEXT,actor TEXT,event TEXT,target TEXT,status TEXT);
        INSERT INTO users VALUES(1,'legacy','Legacy','UNCHANGED_HASH','RAG_ACCOUNTANT',1);
        INSERT INTO history VALUES(1,1,'RETAINED');
        INSERT INTO audit VALUES(1,'2026-01-01','legacy','search','','Успешно');
    ''')
    migrate(db)
    migrate(db)
    assert db.execute('SELECT password FROM users').fetchone()[0] == 'UNCHANGED_HASH'
    assert db.execute('SELECT deleted_at FROM users').fetchone()[0] is None
    assert db.execute('SELECT question FROM history').fetchone()[0] == 'RETAINED'
    assert db.execute('SELECT ip FROM audit').fetchone()[0] == ''
    assert db.execute("SELECT count(*) FROM audit WHERE event='search'").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM audit WHERE event='admin_2fa_enabled'").fetchone()[0] == 1
    db.close()
