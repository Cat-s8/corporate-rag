from test_access import app, client


def test_form_shows_effective_roles_without_level(app):
    response=client(app,4).get('/documents/2')
    assert b'name="level"' not in response.data
    assert b'value="RAG_USER" checked' not in response.data
    assert b'value="RAG_ACCOUNTANT" checked' in response.data
    assert b'value="RAG_ADMIN"' not in response.data
    assert b'name="level"' not in client(app,4).get('/upload').data


def test_simplified_access_grants_only_selected_roles(app):
    admin=client(app,4)
    response=admin.post('/documents/2/acl',data={'csrf':'test','access_mode':'roles','roles':['RAG_USER','RAG_REPORTING_SPECIALIST']})
    assert response.status_code==302
    assert client(app,1).get('/documents/2').status_code==200
    assert client(app,3).get('/documents/2').status_code==200
    assert client(app,2).get('/documents/2').status_code==404
    admin.post('/documents/2/acl',data={'csrf':'test','access_mode':'roles'})
    assert admin.get('/documents/2').status_code==200
    for uid in (1,2,3,5):
        assert client(app,uid).get('/documents/2').status_code==404


def test_simple_access_rejects_unknown_role_and_non_admin(app):
    data={'csrf':'test','access_mode':'roles','roles':['UNKNOWN']}
    assert client(app,4).post('/documents/1/acl',data=data).status_code==400
    data['roles']=['RAG_USER']
    assert client(app,2).post('/documents/1/acl',data=data).status_code==403
