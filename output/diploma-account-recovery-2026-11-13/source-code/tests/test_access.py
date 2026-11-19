import io
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rag_roles.server import create_app, ROLES
from werkzeug.security import generate_password_hash

@pytest.fixture
def app(tmp_path, monkeypatch):
    import hashlib
    import numpy as np
    from app import rag_service
    from rag_roles import backend
    class Embeddings:
        def encode(self, texts, **kwargs):
            result=[]
            for text in texts:
                vector=np.zeros(384,dtype='float32')
                for word in text.split()[1:]:
                    vector[int.from_bytes(hashlib.sha256(word.lower().encode()).digest()[:2],'little')%384]+=1
                vector/=max(float(np.linalg.norm(vector)),1.0)
                result.append(vector)
            return np.array(result,dtype='float32')
    monkeypatch.setattr(rag_service,'embedding_model',lambda:Embeddings())
    monkeypatch.setattr(backend,'generate',lambda q,s,t,**kw: ('\n'.join(c['text'] for c in s),s))
    a=create_app(tmp_path); a.config['TESTING']=True
    with a.app_context():
        db=a.db()
        for i,r in enumerate(ROLES,1):
            db.execute('INSERT INTO users(id,login,name,password,role) VALUES(?,?,?,?,?)',(i,r,r,generate_password_hash('TestPassword123!'),r))
        for i,(level,text) in enumerate([('general','payment public'),('restricted','payment SECRET')],1):
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?)',(i,'Document '+str(i),level,json.dumps(list(ROLES)),'Готов'))
            db.execute('INSERT INTO chunks VALUES(?,?,?,?)',(i,i,1,text))
        db.commit()
    return a

def client(app,uid):
    from rag_roles.sessions import open_session
    c=app.test_client()
    with app.app_context():
        user=app.db().execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        # Seed a completed session for permission tests. Authentication itself is
        # exercised through both HTTP stages in test_admin_auth.py.
        import time
        sid=open_session(app.db(),user,'127.0.0.1',admin_verified_at=time.time() if user['role']=='RAG_ADMIN' else None)
        version=user['session_version']
    with c.session_transaction() as s:
        s.update(uid=uid,csrf='test',sid=sid,session_version=version)
    return c

@pytest.mark.parametrize('uid,blocked',[(1,['users','settings','upload','index','audit','security','documents','system']),(2,['users','settings','upload','index','audit','security','system']),(3,['users','settings','upload','index','audit','security','system']),(5,['users','settings','upload','index','system'])])
def test_forbidden_direct_routes(app,uid,blocked):
    c=client(app,uid)
    for route in blocked: assert c.get('/'+route).status_code==403
    for route in set(blocked)&{'users','settings','upload','index'}: assert c.post('/'+route,data={'csrf':'test'}).status_code==403

def test_acl_before_retrieval_and_generation(app,monkeypatch):
    from rag_roles import backend
    seen={}
    def retrieve(q,chunks,k):
        seen['chunks']=chunks
        return [{'id':2,'text':'FORGED_SECRET'},{'id':1,'text':'FORGED_TEXT'}]
    def generate(q,sources,t,**kwargs):
        seen['sources']=sources
        return '|'.join(s['text'] for s in sources)
    monkeypatch.setattr(backend,'retrieve',retrieve); monkeypatch.setattr(backend,'generate',generate)
    c=client(app,1); r=c.post('/',data={'csrf':'test','question':'payment'})
    assert r.status_code==200
    assert [x['id'] for x in seen['chunks']]==[1]
    assert [x['id'] for x in seen['sources']]==[1]
    assert b'SECRET' not in r.data and b'FORGED' not in r.data
    assert c.get('/documents/2').status_code==404

def test_accountant_acl_access(app):
    c=client(app,2)
    assert b'SECRET' in c.post('/',data={'csrf':'test','question':'payment'}).data

def test_acl_role_allowlist(app):
    with app.app_context():
        app.db().execute('UPDATE documents SET roles=? WHERE id=2',(json.dumps(['RAG_ADMIN']),)); app.db().commit()
    assert client(app,2).get('/documents/2').status_code==404
    assert client(app,4).get('/documents/2').status_code==200

def test_deactivation_and_role_changes_live(app):
    c=client(app,4)
    assert c.get('/users').status_code==200
    with app.app_context():
        app.db().execute("UPDATE users SET role='RAG_USER' WHERE id=4"); app.db().commit()
    assert c.get('/users').status_code==302  # A role change now revokes the session.
    with app.app_context():
        app.db().execute('UPDATE users SET active=0 WHERE id=4'); app.db().commit()
    assert c.get('/').status_code==302

def test_csrf(app):
    assert client(app,4).post('/settings',data={'top_k':4,'temperature':0.1}).status_code==400

def test_history_isolation(app):
    client(app,1).post('/',data={'csrf':'test','question':'private_question'})
    assert b'private_question' not in client(app,2).get('/history').data

def test_login_and_throttle(app):
    c=app.test_client(); c.get('/login')
    with c.session_transaction() as s: token=s['csrf']
    assert c.post('/login',data={'csrf':token,'login':'RAG_USER','password':'TestPassword123!'}).status_code==302
    assert c.get('/').status_code==200
    bad=app.test_client(); bad.get('/login')
    with bad.session_transaction() as s: token=s['csrf']
    for i in range(10): assert bad.post('/login',data={'csrf':token,'login':'x','password':'bad'}).status_code==200
    assert bad.post('/login',data={'csrf':token,'login':'x','password':'bad'}).status_code==429

def test_all_admin_pages_render(app):
    c=client(app,4)
    for path in ['/','/documents','/documents/1','/users','/upload','/index','/settings','/audit','/security','/system','/history','/about','/audit/export']:
        assert c.get(path).status_code==200,path

def test_user_creation_and_self_lock_protection(app):
    c=client(app,4)
    assert c.post('/users',data={'csrf':'test','action':'create','login':'newuser','name':'Test','password':'TestPassword123!','role':'RAG_USER'}).status_code==302
    assert c.post('/users',data={'csrf':'test','action':'update','uid':4,'role':'RAG_USER'}).status_code==400

def test_delete_removes_search_context(app):
    c=client(app,4)
    assert c.post('/documents/1/delete',data={'csrf':'test'}).status_code==302
    assert client(app,1).get('/documents/1').status_code==404
    with app.app_context(): assert app.db().execute('SELECT count(*) FROM chunks WHERE document_id=1').fetchone()[0]==0

def test_upload_invalid_file(app):
    c=client(app,4)
    r=c.post('/upload',data={'csrf':'test','title':'Bad','level':'general','roles':['RAG_REPORTING_SPECIALIST'],'file':(io.BytesIO(b'not pdf'),'bad.pdf')})
    assert r.status_code==302
    with app.app_context(): assert app.db().execute('SELECT count(*) FROM documents').fetchone()[0]==2

def test_pdf_upload_publish_search_end_to_end(app):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer=PdfWriter(); page=writer.add_blank_page(width=400,height=400)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 20 300 Td (uniqueupload payment) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    payload=io.BytesIO(); writer.write(payload); payload.seek(0)
    c=client(app,4)
    r=c.post('/upload',data={'csrf':'test','title':'Uploaded sample','level':'general','roles':['RAG_USER','RAG_REPORTING_SPECIALIST'],'file':(payload,'sample.pdf')})
    assert r.status_code==302
    u=client(app,1)
    assert b'uniqueupload payment' not in u.post('/',data={'csrf':'test','question':'uniqueupload'}).data
    assert c.post('/index',data={'csrf':'test'}).status_code==302
    assert b'uniqueupload payment' in u.post('/',data={'csrf':'test','question':'uniqueupload'}).data
