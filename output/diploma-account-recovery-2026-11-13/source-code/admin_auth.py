"""Local administrator codes and server-side, single-use login challenges.

All verification decisions and session creation share a SQLite write transaction.
Only the local CLI calls generate_code; no HTTP route issues codes.
"""
import hashlib
import secrets
import time

from rag_roles.sessions import open_session

TTL = 60000
MAX_FAILURES = 5
ACCOUNT_CHECKS = 10
ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'


def audit(db, user, event, ip='', status='Успешно', reason=''):
    db.execute('INSERT INTO audit(actor,event,target,status,ip) VALUES(?,?,?,?,?)',
               (user['login'], event, str(user['id']) + (':' + reason if reason else ''), status, ip))


def eligible(user, now):
    return bool(user and user['active'] and user['deleted_at'] is None
                and user['role'] == 'RAG_ADMIN'
                and (not user['must_change_password']
                     or (user['temporary_password_expires_at'] or 0) > now))


def code_hash(code):
    # Sixteen independent base32 characters provide 80 random bits. Formatting
    # is not secret. SHA-256 is sufficient for this high-entropy, short-lived key.
    normalized = code.strip().upper().replace('-', '').replace(' ', '')
    if len(normalized) != 16 or any(c not in ALPHABET for c in normalized):
        return None
    return hashlib.sha256(normalized.encode('ascii')).hexdigest()


def generate_code(db, login):
    with db:
        db.execute('BEGIN IMMEDIATE')
        now = time.time()
        user = db.execute('SELECT * FROM users WHERE login=?', (login,)).fetchone()
        if not eligible(user, now):
            raise ValueError('Active administrator with a valid password is required.')
        raw = ''.join(secrets.choice(ALPHABET) for _ in range(16))
        changed = db.execute('''UPDATE admin_codes SET revoked_at=?
            WHERE user_id=? AND used_at IS NULL AND revoked_at IS NULL''', (now, user['id'])).rowcount
        if changed:
            audit(db, user, 'admin_code_revoked', reason='replaced')
        db.execute('''INSERT INTO admin_codes(user_id,code_hash,version,created_at,expires_at)
            VALUES(?,?,?,?,?)''', (user['id'], code_hash(raw), user['session_version'], now, now + TTL))
        audit(db, user, 'admin_code_generated')
    return '-'.join(raw[i:i+4] for i in range(0, 16, 4)), now + TTL


def cancel_pending(db, token, reason='cancelled'):
    row = db.execute('''SELECT a.*,u.login FROM admin_login_attempts a
        JOIN users u ON u.id=a.user_id WHERE token=?''', (token,)).fetchone()
    if row and row['revoked_at'] is None and row['completed_at'] is None:
        changed = db.execute('''UPDATE admin_login_attempts SET revoked_at=? WHERE token=?
            AND revoked_at IS NULL AND completed_at IS NULL''', (time.time(), token)).rowcount
        if changed:
            audit(db, {'id': row['user_id'], 'login': row['login']},
                  'admin_login_cancelled', row['ip'], reason=reason)


def start_pending(db, user, ip):
    with db:
        db.execute('BEGIN IMMEDIATE')
        now = time.time()
        fresh = db.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()
        if (not eligible(fresh, now) or fresh['session_version'] != user['session_version']
                or fresh['password'] != user['password']):
            return None
        token = secrets.token_urlsafe(32)
        db.execute('''INSERT INTO admin_login_attempts(token,user_id,version,created_at,expires_at,ip)
            VALUES(?,?,?,?,?,?)''', (token, user['id'], user['session_version'], now, now + TTL, ip))
        audit(db, user, 'admin_password_verified', ip)
    return token


def pending_user(db, token, now=None):
    attempt = db.execute('SELECT * FROM admin_login_attempts WHERE token=?', (token,)).fetchone()
    now = time.time() if now is None else now
    if (not attempt or attempt['revoked_at'] is not None or attempt['completed_at'] is not None
            or attempt['expires_at'] <= now or attempt['failures'] >= MAX_FAILURES):
        return None
    user = db.execute('SELECT * FROM users WHERE id=?', (attempt['user_id'],)).fetchone()
    if not eligible(user, now) or user['session_version'] != attempt['version']:
        return None
    return user


def verify_code(db, token, code, ip):
    """Return (status, user, sid); never put codes or challenge IDs in audit."""
    with db:
        db.execute('BEGIN IMMEDIATE')
        now = time.time()
        user = pending_user(db, token, now)
        if not user:
            cancel_pending(db, token, 'expired_or_changed')
            return 'expired', None, None
        db.execute('DELETE FROM admin_code_checks WHERE at<=?', (now - TTL,))
        count = db.execute('SELECT count(*) FROM admin_code_checks WHERE user_id=?', (user['id'],)).fetchone()[0]
        if count >= ACCOUNT_CHECKS:
            audit(db, user, 'admin_code_failed', ip, 'Запрещено', 'rate_limit')
            cancel_pending(db, token, 'rate_limit')
            return 'limited', None, None
        db.execute('INSERT INTO admin_code_checks(user_id,at) VALUES(?,?)', (user['id'], now))
        row = db.execute('''SELECT * FROM admin_codes WHERE user_id=? AND version=?
            AND created_at<=? AND expires_at>? AND used_at IS NULL AND revoked_at IS NULL
            ORDER BY id DESC LIMIT 1''', (user['id'], user['session_version'], now, now)).fetchone()
        digest = code_hash(code) if len(code) <= 64 else None
        if not row or not digest or not secrets.compare_digest(row['code_hash'], digest):
            db.execute('UPDATE admin_login_attempts SET failures=failures+1 WHERE token=?', (token,))
            audit(db, user, 'admin_code_failed', ip, 'Запрещено', 'invalid_code')
            failures = db.execute('SELECT failures FROM admin_login_attempts WHERE token=?', (token,)).fetchone()[0]
            if failures >= MAX_FAILURES:
                cancel_pending(db, token, 'attempt_limit')
                return 'closed', None, None
            return 'invalid', None, None
        # BEGIN IMMEDIATE serializes this check, consumption, and session creation
        # across Waitress threads and processes; a losing request cannot log in.
        updated = db.execute('''UPDATE admin_codes SET used_at=? WHERE id=?
            AND used_at IS NULL AND revoked_at IS NULL AND expires_at>?''', (now, row['id'], now)).rowcount
        if updated != 1:
            return 'invalid', None, None
        db.execute('UPDATE admin_login_attempts SET completed_at=? WHERE token=?', (now, token))
        attempt = db.execute('SELECT ip FROM admin_login_attempts WHERE token=?', (token,)).fetchone()
        sid = open_session(db, user, attempt['ip'], admin_verified_at=now, commit=False)
        audit(db, user, 'admin_code_verified', ip)
        audit(db, user, 'login', attempt['ip'])
        return 'ok', user, sid
