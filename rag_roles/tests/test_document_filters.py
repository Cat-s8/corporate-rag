from test_access import app, client


def test_role_filter_uses_actual_access(app):
    c=client(app,4)
    response=c.get('/documents?role=RAG_USER')
    assert b'/documents/1"' in response.data
    assert b'/documents/2"' not in response.data
    assert b'/documents/2"' in c.get('/documents?role=RAG_ACCOUNTANT').data


def test_combined_filters_and_reset(app):
    c=client(app,4)
    response=c.get('/documents',query_string={'q':'DOCUMENT 2','role':'RAG_ACCOUNTANT','status':'Готов'})
    assert b'/documents/2"' in response.data
    assert b'/documents/1"' not in response.data
    response=c.get('/documents',query_string={'q':'Document 2','status':'Ожидает индексации'})
    assert b'/documents/2"' not in response.data
    assert 'По выбранным фильтрам'.encode() in response.data
    assert b'/documents/1"' in c.get('/documents').data


def test_admin_only_filter_does_not_reveal_hidden_documents(app):
    with app.app_context():
        app.db().execute("UPDATE documents SET roles='[]' WHERE id=2")
        app.db().commit()
    response=client(app,4).get('/documents?role=admin_only')
    assert b'/documents/2"' in response.data
    assert b'/documents/1"' not in response.data
    for role in ('admin_only','RAG_ADMIN',''):
        assert b'/documents/2"' not in client(app,2).get('/documents',query_string={'role':role}).data


def test_invalid_filters(app):
    c=client(app,4)
    assert c.get('/documents?role=UNKNOWN').status_code==400
    assert c.get('/documents?status=UNKNOWN').status_code==400
