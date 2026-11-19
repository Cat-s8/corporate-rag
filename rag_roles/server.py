import csv
import io
import json
import os
import re
import secrets
import sqlite3
import time
import sys
from datetime import timedelta
from functools import wraps
from pathlib import Path
from flask import Flask, abort, g, redirect, render_template, request, session, flash, Response, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rag_roles import backend
from rag_roles.security import ROLES, LEVELS, CLEARANCE, NAV, PERMISSIONS, allowed as document_allowed
from rag_roles.storage import migrate, chunks_for, public_source, backup_before_admin_2fa
from rag_roles.pdf_sources import original_file, source_record, page_exists, render_page
from rag_roles.sessions import idle_seconds, client_ip, open_session, valid_session
from rag_roles import admin_auth

ROOT = Path(__file__).resolve().parent

def create_app(data_dir=None):
    app = Flask(__name__)
    data = Path(data_dir or os.getenv('RAG_ROLES_DATA', ROOT/'data'))
    data.mkdir(parents=True, exist_ok=True)
    key = data/'session.key'
    if not key.exists():
        try:
            with key.open('x') as f: f.write(secrets.token_hex(32))
        except FileExistsError: pass
    app.config.update(SECRET_KEY=key.read_text(), MAX_CONTENT_LENGTH=20*1024*1024,
        TRUST_LOCAL_PROXY=os.getenv('RAG_TRUST_LOCAL_PROXY')=='1',
        PDF_UPLOAD_ROOT=data/'uploads', PDF_IMPORTED_ROOT=ROOT.parent/'data/raw_pdf',
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.getenv('RAG_HTTPS') == '1', PERMANENT_SESSION_LIFETIME=timedelta(hours=2))
    def db():
        if 'db' not in g:
            g.db=sqlite3.connect(data/'roles.sqlite3', timeout=30)
            g.db.row_factory=sqlite3.Row
            g.db.execute('PRAGMA foreign_keys=ON')
        return g.db
    @app.teardown_appcontext
    def close(error):
        if 'db' in g: g.db.close()
    with app.app_context():
        backup_before_admin_2fa(db())
        db().executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,login TEXT UNIQUE NOT NULL,name TEXT NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL,active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY,title TEXT NOT NULL,level TEXT NOT NULL,roles TEXT NOT NULL,status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY,document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,page INTEGER,text TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),at TEXT DEFAULT CURRENT_TIMESTAMP,question TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at TEXT DEFAULT CURRENT_TIMESTAMP,actor TEXT,event TEXT,target TEXT,status TEXT);
        CREATE TABLE IF NOT EXISTS attempts(ip TEXT,at REAL);
        CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1),top_k INTEGER,temperature REAL);
        INSERT OR IGNORE INTO settings VALUES(1,5,0.1);
        ''')
        migrate(db(),backup=False)
    def audit(event,target='',status='Успешно'):
        actor=g.user['login'] if getattr(g,'user',None) else 'anonymous'
        db().execute('INSERT INTO audit(actor,event,target,status,ip) VALUES(?,?,?,?,?)',(actor,event,str(target),status,audit_ip())); db().commit()
    def audit_ip():
        row=getattr(g,'auth_session',None)
        return row['ip'] if row else client_ip(request,app.config['TRUST_LOCAL_PROXY'])
    def clear_auth():
        if session.get('admin_pending'):
            with db(): admin_auth.cancel_pending(db(),session['admin_pending'])
        if session.get('sid'):
            db().execute('DELETE FROM user_sessions WHERE sid=?',(session['sid'],)); db().commit()
        session.clear(); g.user=None; g.auth_session=None
        session['csrf']=secrets.token_urlsafe(32)
    def allowed(doc):
        return document_allowed(g.user, doc)
    def visible_docs():
        return [d for d in db().execute('SELECT * FROM documents ORDER BY id DESC') if allowed(d)]
    def gate(permission):
        def deco(fn):
            @wraps(fn)
            def run(*a,**kw):
                if permission not in PERMISSIONS.get(g.user['role'], set()):
                    audit('access_denied',permission,'Запрещено'); abort(403)
                return fn(*a,**kw)
            return run
        return deco
    @app.before_request
    def guard():
        g.user=db().execute('SELECT * FROM users WHERE id=? AND active=1 AND deleted_at IS NULL',(session.get('uid',-1),)).fetchone()
        g.auth_session=db().execute('SELECT * FROM user_sessions WHERE sid=? AND user_id=?',
                                    (session.get('sid'),session.get('uid',-1))).fetchone()
        if g.user and g.auth_session and time.time()-g.auth_session['last_activity']>=idle_seconds(g.user):
            audit('session_expired',g.user['id'])
            clear_auth()
        if session.get('uid') is not None and (not g.user or g.user['role'] not in ROLES or
                not valid_session(db(),session.get('sid'),g.user) or
                session.get('session_version',0)!=g.user['session_version'] or
                (g.user['must_change_password'] and (g.user['temporary_password_expires_at'] or 0)<=time.time())):
            clear_auth()
        if 'csrf' not in session: session['csrf']=secrets.token_urlsafe(32)
        if g.user and g.user['role'] not in ROLES:
            g.user=None
        if request.endpoint not in {'login','admin_login','admin_verify','admin_cancel','static','api_session'} and not g.user:
            if request.path.startswith('/api/'):
                abort(401)
            return redirect('/admin/verify' if session.get('admin_pending') else '/login')
        if request.method=='POST' and not secrets.compare_digest(session['csrf'],request.headers.get('X-CSRF-Token',request.form.get('csrf',''))):
            audit('csrf_denied',request.path,'Запрещено'); abort(400)
        if g.user and g.user['must_change_password'] and request.endpoint not in {'change_password','logout','static','session_activity','session_state'}:
            if request.path.startswith('/api/'): abort(403)
            return redirect('/password')
        if g.user and request.endpoint not in {'static','api_session','session_state','session_activity'}:
            db().execute('UPDATE user_sessions SET last_activity=? WHERE sid=?',(time.time(),session['sid'])); db().commit()
    @app.after_request
    def headers(r):
        r.headers['Cache-Control']='no-store'
        r.headers['X-Content-Type-Options']='nosniff'
        r.headers['X-Frame-Options']='DENY'
        r.headers['Content-Security-Policy']="default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return r
    @app.context_processor
    def context():
        return dict(roles=ROLES,levels=LEVELS,accessible_roles=accessible_roles,groups=available_groups(),document_group=document_group,user=getattr(g,'user',None),idle_limit=idle_seconds(g.user) if g.user else 0,csrf=session.get('csrf'),nav=[(k,v) for k,v in NAV if g.user and k in PERMISSIONS[g.user['role']]])

    def document_group(did):
        row=db().execute('SELECT group_id FROM document_group_members WHERE document_id=?',(did,)).fetchone()
        return row[0] if row else None

    def available_groups():
        if not getattr(g,'user',None): return []
        groups=db().execute('SELECT * FROM document_groups ORDER BY name').fetchall()
        if g.user['role']=='RAG_ADMIN': return groups
        ids={document_group(d['id']) for d in visible_docs() if d['status']=='Готов'}
        return [group for group in groups if group['id'] in ids]

    def group_filter(value):
        if value in ('',None): return ''
        if value=='ungrouped': return value
        if isinstance(value,bool): abort(400)
        try: gid=int(value)
        except (TypeError,ValueError): abort(400)
        if str(gid)!=str(value): abort(400)
        if gid not in {group['id'] for group in available_groups()}: abort(404)
        return str(gid)

    def in_group(doc,value):
        return not value or document_group(doc['id'])==(None if value=='ungrouped' else int(value))

    @app.post('/document-groups')
    @gate('users')
    def create_group():
        name=request.form.get('name','').strip()
        if not 1<=len(name)<=100: abort(400)
        try:
            db().execute('INSERT INTO document_groups(name) VALUES(?)',(name,)); db().commit()
        except sqlite3.IntegrityError:
            db().rollback(); flash('Группа с таким названием уже существует.'); return redirect('/documents')
        audit('group_create',name); flash('Группа создана. Теперь можно перенести в неё документы.')
        return redirect('/documents')

    @app.post('/documents/<int:did>/group')
    @gate('users')
    def move_document(did):
        if not db().execute('SELECT id FROM documents WHERE id=?',(did,)).fetchone(): abort(404)
        group=group_filter(request.form.get('group',''))
        with db():
            db().execute('DELETE FROM document_group_members WHERE document_id=?',(did,))
            if group and group!='ungrouped':
                db().execute('INSERT INTO document_group_members VALUES(?,?)',(did,int(group)))
        audit('document_group_move',f'{did}:{group or "ungrouped"}')
        if request.accept_mimetypes.best=='application/json':
            return jsonify(saved=True)
        flash('Группа документа изменена.')
        return redirect('/documents')

    def accessible_roles(doc):
        return [role for role in ROLES if document_allowed({'role':role,'active':1},doc)]

    def submitted_access():
        roles=request.form.getlist('roles')
        if any(role not in ROLES for role in roles): abort(400)
        if request.form.get('access_mode')=='roles':
            roles=sorted(set(roles)|{'RAG_ADMIN'})
            return ('general' if 'RAG_USER' in roles else 'restricted'),roles
        return request.form.get('level'),roles
    def password_user(admin=False):
        ip=client_ip(request,app.config['TRUST_LOCAL_PROXY']); cutoff=time.time()-300
        db().execute('DELETE FROM attempts WHERE at<?',(cutoff,)); db().commit()
        if db().execute('SELECT count(*) FROM attempts WHERE ip=?',(ip,)).fetchone()[0]>=10: abort(429)
        u=db().execute('SELECT * FROM users WHERE login=? AND active=1 AND deleted_at IS NULL',(request.form.get('login',''),)).fetchone()
        password=request.form.get('password','')
        if (u and u['role'] in ROLES and (u['role']=='RAG_ADMIN')==admin and len(password)<=256
                and (not u['must_change_password'] or (u['temporary_password_expires_at'] or 0)>time.time())
                and check_password_hash(u['password'],password)):
            return u,ip
        db().execute('INSERT INTO attempts VALUES(?,?)',(ip,time.time())); db().commit()
        audit('admin_login_failed' if admin else 'login_failed',status='Запрещено')
        flash('Неверный логин или пароль либо учётная запись отключена.')
        return None,ip

    def finish_login(u,sid):
        session.clear()
        session.update(uid=u['id'],sid=sid,session_version=u['session_version'],csrf=secrets.token_urlsafe(32))
        session.permanent=True
        g.user=u; g.auth_session=valid_session(db(),sid,u)
        return redirect('/password' if u['must_change_password'] else '/')

    @app.route('/login',methods=['GET','POST'])
    def login():
        if g.user: return redirect('/')
        if request.method=='POST':
            u,ip=password_user()
            if u:
                clear_auth()
                response=finish_login(u,open_session(db(),u,ip))
                audit('login'); return response
        return render_template('login.html')

    @app.route('/admin/login',methods=['GET','POST'])
    def admin_login():
        if g.user: return redirect('/')
        if request.method=='POST':
            u,ip=password_user(admin=True)
            if u:
                clear_auth()
                token=admin_auth.start_pending(db(),u,ip)
                session['csrf']=secrets.token_urlsafe(32)
                if not token:
                    flash('Учётная запись изменилась. Повторите вход.'); return redirect('/admin/login')
                session['admin_pending']=token
                return redirect('/admin/verify')
        return render_template('admin_login.html')

    @app.route('/admin/verify',methods=['GET','POST'])
    def admin_verify():
        if g.user: return redirect('/')
        token=session.get('admin_pending')
        if request.method=='POST':
            status,u,sid=admin_auth.verify_code(db(),token,request.form.get('code',''),client_ip(request,app.config['TRUST_LOCAL_PROXY']))
            if status=='ok': return finish_login(u,sid)
            if status=='invalid':
                flash('Код недействителен. Проверьте ввод и срок действия.')
                return render_template('admin_verify.html'),400
            if status=='expired': audit('admin_code_failed',status='Запрещено')
            clear_auth()
            if status=='limited':
                flash('Слишком много проверок для этой учётной записи. Подождите до 10 минут перед новым входом.')
                return render_template('admin_login.html'),429
            flash('Попытка входа закрыта или истекла. Введите логин и пароль заново.')
            return redirect('/admin/login')
        if not admin_auth.pending_user(db(),token):
            clear_auth()
            flash('Начните вход администратора с логина и пароля.')
            return redirect('/admin/login')
        return render_template('admin_verify.html')

    @app.post('/admin/cancel')
    def admin_cancel():
        if g.user: return redirect('/')
        clear_auth()
        flash('Попытка входа отменена.')
        return redirect('/admin/login')
    @app.post('/logout')
    def logout():
        audit('logout'); clear_auth(); return redirect('/login')

    @app.get('/api/session-state')
    def session_state():
        row=valid_session(db(),session.get('sid'),g.user)
        if not row: abort(401)
        return jsonify(remaining=max(0,row['last_activity']+idle_seconds(g.user)-time.time()))

    @app.post('/api/activity')
    def session_activity():
        if not valid_session(db(),session.get('sid'),g.user): abort(401)
        db().execute('UPDATE user_sessions SET last_activity=? WHERE sid=?',(time.time(),session['sid'])); db().commit()
        return jsonify(remaining=idle_seconds(g.user))

    @app.route('/password',methods=['GET','POST'])
    def change_password():
        if request.method=='POST':
            current=request.form.get('current_password','')
            password=request.form.get('password','')
            confirmation=request.form.get('confirmation','')
            if not check_password_hash(g.user['password'],current):
                audit('password_change_failed',g.user['id'],'Ошибка')
                flash('Текущий пароль неверен.'); return render_template('password.html',page='password'),400
            if not 12<=len(password)<=256 or password!=confirmation or check_password_hash(g.user['password'],password):
                flash('Введите новый пароль от 12 до 256 символов, отличный от текущего, и повторите его без изменений.')
                return render_template('password.html',page='password'),400
            hashed=generate_password_hash(password)
            with db():
                db().execute('BEGIN IMMEDIATE')
                fresh=db().execute('SELECT * FROM users WHERE id=? AND active=1',(g.user['id'],)).fetchone()
                if not fresh or fresh['session_version']!=g.user['session_version']: abort(409)
                if fresh['must_change_password'] and (fresh['temporary_password_expires_at'] or 0)<=time.time(): abort(401)
                db().execute('UPDATE users SET password=?,must_change_password=0,temporary_password_expires_at=NULL,session_version=session_version+1 WHERE id=?',(hashed,g.user['id']))
                db().execute('DELETE FROM user_sessions WHERE user_id=?',(g.user['id'],))
                db().execute('INSERT INTO audit(actor,event,target,status,ip) VALUES(?,?,?,?,?)',(g.user['login'],'password_changed',str(g.user['id']),'Успешно',audit_ip()))
            session.clear()
            flash('Пароль изменён. Войдите с новым паролем.')
            return redirect('/admin/login' if g.user['role']=='RAG_ADMIN' else '/login')
        return render_template('password.html',page='password')

    @app.post('/users/<int:uid>/reset-password')
    @gate('users')
    def reset_password(uid):
        if uid==g.user['id']: abort(400)
        temporary=secrets.token_urlsafe(18)
        hashed=generate_password_hash(temporary)
        with db():
            db().execute('BEGIN IMMEDIATE')
            fresh=db().execute('SELECT * FROM users WHERE id=? AND active=1',(g.user['id'],)).fetchone()
            if not fresh or fresh['role']!='RAG_ADMIN' or fresh['session_version']!=g.user['session_version']: abort(403)
            target=db().execute('SELECT id,login,name FROM users WHERE id=? AND active=1',(uid,)).fetchone()
            if not target: abort(404)
            db().execute('UPDATE users SET password=?,must_change_password=1,temporary_password_expires_at=?,session_version=session_version+1 WHERE id=?',(hashed,time.time()+1800,uid))
            db().execute('DELETE FROM user_sessions WHERE user_id=?',(uid,))
            db().execute('INSERT INTO audit(actor,event,target,status,ip) VALUES(?,?,?,?,?)',(g.user['login'],'password_reset',str(uid),'Успешно',audit_ip()))
        return render_template('password_reset.html',page='users',target=target,temporary=temporary)

    def revision():
        return db().execute('SELECT value FROM corpus_revision WHERE id=1').fetchone()[0]

    def require_current(expected):
        fresh=db().execute('SELECT * FROM users WHERE id=? AND active=1',(session.get('uid',-1),)).fetchone()
        if not fresh or fresh['role'] not in ROLES:
            abort(401)
        if fresh['session_version']!=session.get('session_version',0) or fresh['must_change_password']:
            abort(401)
        if not valid_session(db(),session.get('sid'),fresh): abort(401)
        if not g.user or fresh['role'] != g.user['role'] or fresh['id'] != g.user['id']:
            abort(409)
        g.user=fresh
        if revision()!=expected:
            abort(409)

    def process_question(q, selected='', group=''):
        try:
            return run_question(q, selected, group)
        except HTTPException:
            raise
        except Exception:
            app.logger.exception('RAG request failed')
            audit('search_failed',status='Ошибка')
            abort(503)

    def run_question(q, selected='', group=''):
        if not isinstance(q,str) or not 2<=len(q.strip())<=2000: abort(400)
        q=q.strip()
        if not isinstance(selected,str) or (selected and selected not in LEVELS): abort(400)
        if selected and g.user['role']=='RAG_USER': abort(403)
        expected=revision()
        group=group_filter(group)
        docs=[d for d in visible_docs() if d['status']=='Готов' and (not selected or d['level']==selected) and in_group(d,group)]
        chunks=chunks_for(db(),docs)
        settings=db().execute('SELECT * FROM settings').fetchone()
        require_current(expected)
        hits=backend.retrieve(q,chunks,settings['top_k']) if chunks else []
        trusted={c['id']:c for c in chunks}
        sources=[]; seen=set()
        for hit in hits:
            cid=hit.get('id')
            if cid in trusted and cid not in seen:
                source=dict(trusted[cid])
                for field in ('rrf_score','faiss_rank','faiss_score','bm25_rank','bm25_score'):
                    if field in hit: source[field]=hit[field]
                sources.append(source); seen.add(cid)
        sources=sources[:settings['top_k']]
        with backend.GENERATION_LOCK:
            require_current(expected)
            if sources:
                generated=backend.generate(q,sources,settings['temperature'],validate=lambda:require_current(expected))
                if isinstance(generated,tuple):
                    answer, used=generated
                    selected_ids={s['id'] for s in used}
                    sources=[s for s in sources if s['id'] in selected_ids]
                else:
                    answer=generated
            else:
                answer='В доступных документах информация не найдена.'
            require_current(expected)
        db().execute('INSERT INTO history(user_id,question) VALUES(?,?)',(g.user['id'],q))
        db().commit(); audit('search',','.join(str(s['id']) for s in sources))
        return answer,[public_source(s) for s in sources],expected

    @app.get('/api/session')
    def api_session():
        if not g.user: return jsonify(csrf=session['csrf'],user=None)
        expected=revision()
        docs=[d for d in visible_docs() if d['status']=='Готов']
        count=sum(db().execute('SELECT count(*) FROM chunks WHERE document_id=?',(d['id'],)).fetchone()[0] for d in docs)
        require_current(expected)
        return jsonify(csrf=session['csrf'],user={k:g.user[k] for k in ('id','login','name','role')},
                       role_name=ROLES[g.user['role']],revision=expected,chunk_count=count,
                       documents=[dict(id=d['id'],title=d['title'],level=d['level']) for d in docs],
                       nav=[(k,v) for k,v in NAV if k in PERMISSIONS[g.user['role']]])

    @app.post('/api/search')
    @gate('search')
    def api_search():
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict): abort(400)
        answer,sources,version=process_question(payload.get('question',''),payload.get('level',''),payload.get('group',''))
        return jsonify(answer=answer,sources=sources,revision=version)

    @app.get('/api/history')
    @gate('history')
    def api_history():
        return jsonify(rows=[dict(r) for r in db().execute('SELECT at,question FROM history WHERE user_id=? ORDER BY id DESC LIMIT 200',(g.user['id'],))])
    @app.route('/',methods=['GET','POST'])
    @gate('search')
    def search():
        answer=None; sources=[]; q=''; selected=request.form.get('level','')
        search_group=group_filter(request.form.get('group',''))
        if request.method=='POST':
            q=request.form.get('question','').strip()
            answer,sources,_=process_question(q,selected,search_group)
        return render_template('page.html',page='search',answer=answer,sources=sources,question=q,selected=selected,search_group=search_group)
    @app.get('/history')
    @gate('history')
    def history():
        rows=db().execute('SELECT at,question FROM history WHERE user_id=? ORDER BY id DESC LIMIT 200',(g.user['id'],)).fetchall()
        return render_template('page.html',page='history',rows=rows)
    @app.get('/documents')
    @gate('documents')
    def documents():
        query=request.args.get('q','').strip()
        role=request.args.get('role','')
        status=request.args.get('status','')
        document_section=group_filter(request.args.get('group',''))
        if role and role not in ROLES and role!='admin_only': abort(400)
        if status and status not in ('Готов','Ожидает индексации'): abort(400)
        docs=visible_docs()
        total=len(docs)
        rows=[d for d in docs if (not query or query.casefold() in d['title'].casefold())
              and in_group(d,document_section)
              and (not status or d['status']==status)
              and (not role or (accessible_roles(d)==['RAG_ADMIN'] if role=='admin_only'
                                else role in accessible_roles(d)))]
        return render_template('page.html',page='documents',rows=rows,
                               document_query=query,document_role=role,document_status=status,document_total=total,document_section=document_section)
    @app.get('/documents/<int:did>')
    def document(did):
        d=readable_document(did)
        audit('document_view',did)
        return render_template('page.html',page='document',doc=d,
            pdf_available=pdf_path(did) is not None,
            rows=db().execute('SELECT * FROM chunks WHERE document_id=? ORDER BY page,id',(did,)))

    def readable_document(did):
        d=db().execute('SELECT * FROM documents WHERE id=?',(did,)).fetchone()
        if not d or not allowed(d) or (d['status']!='Готов' and g.user['role']!='RAG_ADMIN'):
            audit('document_denied',did,'Запрещено'); abort(404)
        return d

    def pdf_path(did):
        return original_file(db(),did,app.config['PDF_UPLOAD_ROOT'],app.config['PDF_IMPORTED_ROOT'])

    @app.get('/documents/<int:did>/pdf')
    def original_pdf(did):
        expected=revision()
        readable_document(did)
        path=pdf_path(did)
        if path is None: abort(404)
        require_current(expected)
        audit('original_pdf_view',did)
        return send_file(path,mimetype='application/pdf',as_attachment=False,
                         download_name=path.name,conditional=True,etag=False,max_age=0)

    @app.get('/documents/<int:did>/sources/<int:cid>')
    def source_view(did,cid):
        expected=revision()
        doc=readable_document(did)
        source=source_record(db(),did,cid)
        if source is None: abort(404)
        path=pdf_path(did)
        preview=page_exists(path,source['pdf_page'])
        require_current(expected)
        audit('source_view',cid)
        return render_template('source.html',page='documents',doc=doc,source=source,
                               pdf_available=path is not None,preview_available=preview)

    @app.get('/documents/<int:did>/sources/<int:cid>/page.png')
    def source_image(did,cid):
        expected=revision()
        readable_document(did)
        source=source_record(db(),did,cid)
        if source is None: abort(404)
        path=pdf_path(did)
        if not page_exists(path,source['pdf_page']): abort(404)
        try:
            png=render_page(path,source['pdf_page'])
        except (OSError,ValueError,RuntimeError):
            abort(404)
        require_current(expected)
        audit('source_page_view',cid)
        return Response(png,mimetype='image/png')

    @app.post('/documents/<int:did>/acl')
    @gate('users')
    def update_acl(did):
        level,roles=submitted_access()
        if level not in LEVELS or any(r not in ROLES for r in roles): abort(400)
        if not db().execute('SELECT id FROM documents WHERE id=?',(did,)).fetchone(): abort(404)
        db().execute('UPDATE documents SET level=?,roles=? WHERE id=?',(level,json.dumps(sorted(set(roles))),did))
        db().commit(); audit('document_acl',did)
        flash('Права документа обновлены.'); return redirect('/documents')
    @app.post('/documents/<int:did>/delete')
    @gate('upload')
    def delete(did):
        d=db().execute('SELECT * FROM documents WHERE id=?',(did,)).fetchone()
        if not d or not allowed(d): abort(404)
        db().execute('DELETE FROM documents WHERE id=?',(did,)); db().commit(); audit('document_delete',did)
        flash('Документ и его фрагменты удалены.'); return redirect('/documents')
    @app.route('/upload',methods=['GET','POST'])
    @gate('upload')
    def upload():
        if request.method=='POST':
            f=request.files.get('file'); title=request.form.get('title','').strip()
            level,roles=submitted_access()
            if not f or not f.filename.lower().endswith('.pdf') or not 1<=len(title)<=200 or level not in LEVELS or not roles or any(r not in ROLES for r in roles): abort(400)
            from rag_roles.ingestion import parse_pdf
            uploads=data/'uploads'; uploads.mkdir(exist_ok=True)
            stored=uploads/(secrets.token_hex(16)+'.pdf')
            try:
                f.save(stored)
                chunks=parse_pdf(stored,Path(f.filename.replace('\\','/')).name)
            except Exception:
                flash('Не удалось прочитать PDF. Нужен текстовый PDF без пароля, до 500 страниц. Для сканов сначала выполните OCR.'); return redirect('/upload')
            with db():
                did=db().execute('INSERT INTO documents(title,level,roles,status) VALUES(?,?,?,?)',(title,level,json.dumps(roles),'Ожидает индексации')).lastrowid
                db().execute('INSERT INTO document_files VALUES(?,?)',(did,str(stored)))
                for chunk in chunks:
                    cid=db().execute('INSERT INTO chunks(document_id,page,text) VALUES(?,?,?)',
                        (did,chunk.get('document_page') or chunk['pdf_page'],chunk['text'])).lastrowid
                    meta={k:v for k,v in chunk.items() if k not in ('text','chunk_id')}
                    db().execute('INSERT INTO chunk_details VALUES(?,?,NULL)',(cid,json.dumps(meta,ensure_ascii=False)))
            audit('document_upload',did); flash('PDF разобран. Откройте «Индексация», чтобы включить документ в поиск.'); return redirect('/index')
        return render_template('page.html',page='upload')
    @app.route('/index',methods=['GET','POST'])
    @gate('index')
    def index():
        if request.method=='POST':
            expected=revision()
            docs=[d for d in visible_docs() if d['status']!='Готов']
            chunks=chunks_for(db(),docs)
            try:
                vectors=backend.prepare_index(chunks)
                if set(vectors)!={c['id'] for c in chunks if c.get('vector') is None}:
                    raise ValueError('Incomplete embedding batch')
            except Exception:
                app.logger.exception('Index preparation failed')
                audit('index_failed',status='Ошибка'); abort(503)
            # Publish vectors and statuses in one transaction, retaining the old corpus on failure.
            with db():
                db().execute('BEGIN IMMEDIATE')
                require_current(expected)
                for chunk in chunks:
                    if chunk['id'] in vectors:
                        db().execute('''INSERT INTO chunk_details(chunk_id,metadata,vector) VALUES(?,?,?)
                            ON CONFLICT(chunk_id) DO UPDATE SET vector=excluded.vector''',
                            (chunk['id'],json.dumps({k:v for k,v in chunk.items() if k!='vector'},ensure_ascii=False),vectors[chunk['id']]))
                for d in docs: db().execute("UPDATE documents SET status='Готов' WHERE id=?",(d['id'],))
            audit('index_publish'); flash('Эмбеддинги сохранены. Документы опубликованы для BM25 и FAISS.'); return redirect('/index')
        return render_template('page.html',page='index',rows=visible_docs())
    @app.route('/users',methods=['GET','POST'])
    @gate('users')
    def users():
        if request.method=='POST':
            action=request.form.get('action')
            if action=='create':
                login=request.form.get('login',''); name=request.form.get('name','').strip(); password=request.form.get('password',''); role=request.form.get('role')
                if not re.fullmatch(r'[a-zA-Z0-9_.-]{3,40}',login) or not 1<=len(name)<=120 or not 12<=len(password)<=256 or role not in ROLES or role=='RAG_ADMIN': abort(400)
                try:
                    db().execute('INSERT INTO users(login,name,password,role) VALUES(?,?,?,?)',(login,name,generate_password_hash(password),role)); db().commit()
                except sqlite3.IntegrityError:
                    flash('Такой логин уже существует.'); return redirect('/users')
                audit('user_create',login)
            elif action=='update':
                uid=request.form.get('uid',type=int); role=request.form.get('role'); active=int(request.form.get('active')=='1')
                if uid==g.user['id'] or role=='RAG_ADMIN': abort(400)
                with db():
                    db().execute('BEGIN IMMEDIATE')
                    target=db().execute('SELECT id,role FROM users WHERE id=? AND deleted_at IS NULL',(uid,)).fetchone()
                    if not target: abort(404)
                    if role=='' and target['role']=='RAG_ADMIN': role=target['role']
                    elif role not in ROLES: abort(400)
                    db().execute('UPDATE users SET role=?,active=?,session_version=session_version+1 WHERE id=?',(role,active,uid))
                    db().execute('DELETE FROM user_sessions WHERE user_id=?',(uid,))
                audit('user_update',uid)
            else: abort(400)
            return redirect('/users')
        return render_template('page.html',page='users',rows=db().execute('SELECT id,login,name,role,active FROM users WHERE deleted_at IS NULL ORDER BY id'))

    @app.post('/users/<int:uid>/delete')
    @gate('users')
    def delete_user(uid):
        if uid==g.user['id'] or request.form.get('confirm')!='yes': abort(400)
        with db():
            db().execute('BEGIN IMMEDIATE')
            target=db().execute('SELECT id FROM users WHERE id=? AND deleted_at IS NULL',(uid,)).fetchone()
            if not target: abort(404)
            db().execute("UPDATE users SET deleted_at=CURRENT_TIMESTAMP,active=0,password='',must_change_password=0,temporary_password_expires_at=NULL,session_version=session_version+1 WHERE id=?",(uid,))
            db().execute('DELETE FROM user_sessions WHERE user_id=?',(uid,))
            db().execute('INSERT INTO audit(actor,event,target,status,ip) VALUES(?,?,?,?,?)',(g.user['login'],'user_deleted',str(uid),'Успешно',audit_ip()))
        flash('Пользователь удалён. История запросов сохранена.')
        return redirect('/users')

    @app.get('/users/history')
    @gate('users')
    def users_history():
        selected=request.args.get('user_id','')
        if selected and (not selected.isascii() or not selected.isdecimal() or len(selected)>18): abort(400)
        rows=db().execute('''SELECT h.at,h.question,u.id AS uid,u.login,u.name,u.deleted_at
            FROM history h JOIN users u ON u.id=h.user_id
            WHERE (?='' OR u.id=?) ORDER BY h.id DESC LIMIT 500''',(selected,selected)).fetchall()
        people=db().execute('SELECT id,login,name,deleted_at FROM users ORDER BY name').fetchall()
        return render_template('users_history.html',page='users',rows=rows,people=people,selected=selected)
    @app.route('/settings',methods=['GET','POST'])
    @gate('settings')
    def settings():
        if request.method=='POST':
            try:
                k=int(request.form['top_k']); t=float(request.form['temperature'])
                if not (1<=k<=20 and 0<=t<=1): abort(400)
            except (ValueError,KeyError): abort(400)
            db().execute('UPDATE settings SET top_k=?,temperature=? WHERE id=1',(k,t)); db().commit(); audit('settings_update'); flash('Настройки сохранены.'); return redirect('/settings')
        return render_template('page.html',page='settings',settings=db().execute('SELECT * FROM settings').fetchone())
    def audit_rows(security=False):
        args=[]; clauses=['1=1']
        for field,expr in [('start','at>=?'),('end','at<?'),('actor','actor=?'),('event','event=?')]:
            value=request.args.get(field,'')
            if value:
                if field=='end': value+=' 23:59:59.999'
                clauses.append(expr); args.append(value)
        if security: clauses.append("status IN ('Запрещено','Ошибка')")
        return db().execute('SELECT at,actor,event,target,status,ip FROM audit WHERE '+' AND '.join(clauses)+' ORDER BY id DESC LIMIT 2000',args).fetchall()
    @app.get('/audit')
    @gate('audit')
    def audit_page(): return render_template('page.html',page='audit',rows=audit_rows())
    @app.get('/security')
    @gate('security')
    def security(): return render_template('page.html',page='security',rows=audit_rows(True))
    @app.get('/audit/export')
    @gate('audit')
    def export():
        out=io.StringIO(); w=csv.writer(out); w.writerow(['Дата UTC','Пользователь','Событие','Объект','Статус','IP сессии'])
        for row in audit_rows(): w.writerow(["'"+str(v) if str(v).startswith(('=','+','-','@','\t','\r')) else v for v in row])
        audit('audit_export'); return Response('\ufeff'+out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=audit.csv'})
    @app.get('/system')
    @gate('system')
    def system():
        counts={t:db().execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['users','documents','chunks']}
        return render_template('page.html',page='system',counts=counts)
    @app.get('/about')
    def about(): return render_template('page.html',page='about')
    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(409)
    @app.errorhandler(503)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    @app.errorhandler(429)
    def error(e):
        if request.path.startswith('/api/'):
            return jsonify(error={401:'Требуется вход',403:'Доступ запрещён',409:'Права или корпус изменились. Повторите запрос.',503:'RAG недоступен. Проверьте локальные модели и журнал сервера.'}.get(e.code,'Запрос отклонён')),e.code
        return render_template('error.html',code=e.code),e.code
    app.db=db
    return app

if __name__=='__main__':
    import argparse
    import getpass
    parser=argparse.ArgumentParser(); parser.add_argument('--init-admin',action='store_true'); parser.add_argument('--port',type=int,default=8502)
    args=parser.parse_args(); app=create_app()
    if args.init_admin:
        pw=getpass.getpass('Administrator password (12+ characters): ')
        if len(pw)<12: raise SystemExit('Password too short')
        with app.app_context():
            try:
                app.db().execute('INSERT INTO users(login,name,password,role) VALUES(?,?,?,?)',('admin','Администратор',generate_password_hash(pw),'RAG_ADMIN')); app.db().commit()
            except sqlite3.IntegrityError: raise SystemExit('admin already exists; unchanged')
        print('Created admin')
    else:
        from waitress import serve
        if os.getenv('RAG_PRELOAD', '1') == '1':
            print('Loading and warming up local models. Please wait...', flush=True)
            try:
                from app.rag_service import warmup
                warmup()
            except Exception:
                app.logger.exception('Model warmup failed; UI will start so configuration can be checked')
        print(f'Open http://127.0.0.1:{args.port}',flush=True)
        proxy_options=({'trusted_proxy':'127.0.0.1','trusted_proxy_count':1,
                        'trusted_proxy_headers':{'x-forwarded-for'}} if app.config['TRUST_LOCAL_PROXY'] else {})
        serve(app,host='127.0.0.1',port=args.port,threads=4,**proxy_options)
