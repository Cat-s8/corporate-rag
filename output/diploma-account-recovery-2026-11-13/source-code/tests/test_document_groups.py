from test_access import app, client


def create_groups(app):
    admin=client(app,4)
    for name in ('Зарплата','Отчётность','Пустая'):
        assert admin.post('/document-groups',data={'csrf':'test','name':name}).status_code==302
    for did,gid in ((1,1),(2,2)):
        response=admin.post(f'/documents/{did}/group',data={'csrf':'test','group':str(gid)},headers={'Accept':'application/json'})
        assert response.status_code==200 and response.json=={'saved':True}


def test_group_filter_precedes_retrieval_and_generation(app,monkeypatch):
    create_groups(app)
    from rag_roles import backend
    seen=[]
    def retrieve(q,chunks,k):
        seen.extend(c['document_id'] for c in chunks)
        return [{'id':1},{'id':2}]
    monkeypatch.setattr(backend,'retrieve',retrieve)
    response=client(app,2).post('/api/search',json={'question':'payment','group':1},headers={'X-CSRF-Token':'test'})
    assert response.status_code==200
    assert seen==[1]
    assert [s['document_id'] for s in response.json['sources']]==[1]
    assert 'SECRET' not in response.json['answer']


def test_hidden_groups_and_ordinary_user_search(app):
    create_groups(app)
    c=client(app,1)
    assert 'Отчётность'.encode() not in c.get('/').data
    assert c.post('/',data={'csrf':'test','question':'payment','group':'2'}).status_code==404
    response=c.post('/',data={'csrf':'test','question':'payment','group':'1'})
    assert response.status_code==200 and b'payment public' in response.data


def test_management_filters_and_no_acl_change(app):
    with app.app_context():
        before=[tuple(r) for r in app.db().execute('SELECT id,level,roles,status FROM documents')]
    create_groups(app)
    admin=client(app,4)
    response=admin.get('/documents?group=1')
    assert b'/documents/1"' in response.data and b'/documents/2"' not in response.data
    assert admin.post('/documents/1/group',data={'csrf':'test','group':''}).status_code==302
    assert b'/documents/1"' in admin.get('/documents?group=ungrouped').data
    assert admin.post('/document-groups',data={'csrf':'test','name':'Зарплата'}).status_code==302
    assert admin.post('/documents/1/group',data={'csrf':'test','group':'999'}).status_code==404
    with app.app_context():
        assert before==[tuple(r) for r in app.db().execute('SELECT id,level,roles,status FROM documents')]
        assert app.db().execute('SELECT count(*) FROM document_groups').fetchone()[0]==3


def test_management_requires_admin_and_csrf(app):
    for uid in (1,2,3,5):
        c=client(app,uid)
        assert c.post('/document-groups',data={'csrf':'test','name':'Test'}).status_code==403
        assert c.post('/documents/1/group',data={'csrf':'test','group':''}).status_code==403
    assert client(app,4).post('/document-groups',data={'name':'Test'}).status_code==400


def test_move_during_search_invalidates_answer(app,monkeypatch):
    create_groups(app)
    from rag_roles import backend
    def retrieve(q,chunks,k):
        with app.app_context():
            app.db().execute('UPDATE document_group_members SET group_id=2 WHERE document_id=1')
            app.db().commit()
        return chunks
    monkeypatch.setattr(backend,'retrieve',retrieve)
    assert client(app,4).post('/',data={'csrf':'test','question':'payment','group':'1'}).status_code==409
