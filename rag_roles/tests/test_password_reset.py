import re
import time
from werkzeug.security import check_password_hash
from test_access import app, client


def temporary_password(app):
    response=client(app,4).post('/users/2/reset-password',data={'csrf':'test'})
    assert response.status_code==200
    assert 'no-store' in response.headers['Cache-Control']
    return re.search(rb'value="([A-Za-z0-9_-]{24})" readonly',response.data)[1].decode()


def login(app,password):
    c=app.test_client(); c.get('/login')
    with c.session_transaction() as s: csrf=s['csrf']
    response=c.post('/login',data={'csrf':csrf,'login':'RAG_ACCOUNTANT','password':password})
    return c,response


def test_full_reset_flow_and_revocation(app):
    old=client(app,2)
    temporary=temporary_password(app)
    assert old.get('/documents').status_code==302
    assert login(app,'TestPassword123!')[1].status_code==200
    c,response=login(app,temporary)
    assert response.location=='/password'
    assert c.get('/documents').location=='/password'
    assert c.get('/documents/1/pdf').location=='/password'
    assert c.get('/api/session').status_code==403
    with c.session_transaction() as s: csrf=s['csrf']
    assert temporary not in str(dict(s))
    data={'csrf':csrf,'current_password':temporary,'password':'PermanentPassword456!','confirmation':'PermanentPassword456!'}
    response=c.post('/password',data=data)
    assert response.location=='/login'
    assert login(app,temporary)[1].status_code==200
    permanent,response=login(app,'PermanentPassword456!')
    assert response.location=='/'
    assert permanent.get('/documents').status_code==200
    with app.app_context():
        row=app.db().execute('SELECT * FROM users WHERE id=2').fetchone()
        assert not row['must_change_password'] and row['temporary_password_expires_at'] is None
        assert row['session_version']==2
        assert check_password_hash(row['password'],'PermanentPassword456!')
        audit=str([tuple(r) for r in app.db().execute('SELECT * FROM audit')])
        assert temporary not in audit and 'PermanentPassword456!' not in audit
        assert 'password_reset' in audit and 'password_changed' in audit


def test_expiration_and_second_reset(app):
    first=temporary_password(app)
    c,_=login(app,first)
    second=temporary_password(app)
    assert c.get('/password').location=='/login'
    assert login(app,first)[1].status_code==200
    c,_=login(app,second)
    with app.app_context():
        app.db().execute('UPDATE users SET temporary_password_expires_at=? WHERE id=2',(time.time()-1,))
        app.db().commit()
    assert c.get('/password').location=='/login'
    assert login(app,second)[1].status_code==200


def test_reset_permissions_csrf_and_self_reset(app):
    for uid in (1,2,3,5):
        assert client(app,uid).post('/users/2/reset-password',data={'csrf':'test'}).status_code==403
    admin=client(app,4)
    assert admin.post('/users/2/reset-password').status_code==400
    assert admin.post('/users/4/reset-password',data={'csrf':'test'}).status_code==400
    assert admin.post('/users/999/reset-password',data={'csrf':'test'}).status_code==404


def test_change_validation_and_regular_password_change(app):
    c=client(app,2)
    data={'csrf':'test','current_password':'wrong','password':'NewPassword123!','confirmation':'NewPassword123!'}
    assert c.post('/password',data=data).status_code==400
    data['current_password']='TestPassword123!'
    data['confirmation']='MismatchPassword!'
    assert c.post('/password',data=data).status_code==400
    data['password']=data['confirmation']='short'
    assert c.post('/password',data=data).status_code==400
    data['password']=data['confirmation']='NewPassword123!'
    old=client(app,2)
    assert c.post('/password',data=data).location=='/login'
    assert old.get('/documents').location=='/login'
