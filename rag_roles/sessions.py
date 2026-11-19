"""Server-side idle expiry. Polls and static assets do not prolong a session."""
import ipaddress
import secrets
import time


def idle_seconds(user):
    return 300 if user['role']=='RAG_ADMIN' else 900


def client_ip(request, trust_proxy=False):
    value=request.remote_addr or ''
    # Accept forwarded addresses only from an explicitly trusted local proxy.
    if trust_proxy and value in ('127.0.0.1','::1'):
        value=request.headers.get('X-Forwarded-For',value).split(',')[-1].strip()
    try: return str(ipaddress.ip_address(value))
    except ValueError: return request.remote_addr or ''


def open_session(db,user,ip,*,admin_verified_at=None,commit=True):
    sid=secrets.token_urlsafe(32)
    db.execute('''INSERT INTO user_sessions
        (sid,user_id,version,last_activity,ip,authenticated_role,admin_verified_at)
        VALUES(?,?,?,?,?,?,?)''',
        (sid,user['id'],user['session_version'],time.time(),ip,user['role'],admin_verified_at))
    if commit: db.commit()
    return sid


def valid_session(db,sid,user):
    if not sid or not user: return None
    row=db.execute('SELECT * FROM user_sessions WHERE sid=? AND user_id=?',(sid,user['id'])).fetchone()
    if not row or row['version']!=user['session_version']: return None
    if row['authenticated_role']!=user['role']: return None
    if user['role']=='RAG_ADMIN' and row['admin_verified_at'] is None: return None
    if time.time()-row['last_activity']>=idle_seconds(user): return None
    return row
