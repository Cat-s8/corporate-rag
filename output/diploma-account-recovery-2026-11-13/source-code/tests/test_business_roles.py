import json
import pytest
from test_access import app, client
from rag_roles.security import ROLES
from rag_roles.storage import migrate_business_roles


def test_exact_role_labels_and_forms(app):
    assert set(ROLES.values()) == {
        'Администратор', 'Специалист по ИБ', 'Бухгалтер',
        'Специалист по отчётности', 'Обычный пользователь',
    }
    for route in ['/users', '/upload', '/documents/1']:
        response = client(app, 4).get(route)
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert 'Бухгалтер' in html and 'Специалист по отчётности' in html
        assert 'RAG_CONTENT_MANAGER' not in html and 'RAG_RESTRICTED_USER' not in html


@pytest.mark.parametrize('uid,document_id,other_id', [(2,1,2), (3,2,1)])
def test_business_document_isolation(app, uid, document_id, other_id):
    with app.app_context():
        app.db().execute('UPDATE documents SET level=?, roles=? WHERE id=1',
                         ('restricted', json.dumps(['RAG_ACCOUNTANT'])))
        app.db().execute('UPDATE documents SET level=?, roles=? WHERE id=2',
                         ('restricted', json.dumps(['RAG_REPORTING_SPECIALIST'])))
        app.db().commit()
    c = client(app, uid)
    response = c.post('/api/search', json={'question':'payment'}, headers={'X-CSRF-Token':'test'})
    assert response.status_code == 200
    assert {source['document_id'] for source in response.json['sources']} == {document_id}
    assert c.get(f'/documents/{other_id}').status_code == 404
    assert c.get('/documents').status_code == 200
    assert c.post(f'/documents/{document_id}/delete', data={'csrf':'test'}).status_code == 403
    assert client(app,1).get(f'/documents/{document_id}').status_code == 404


def test_legacy_role_migration_is_atomic_and_idempotent(app):
    with app.app_context():
        db = app.db()
        original = [(r['id'],r['password']) for r in db.execute('SELECT id,password FROM users ORDER BY id')]
        db.execute("UPDATE users SET role='RAG_RESTRICTED_USER' WHERE id=2")
        db.execute("UPDATE users SET role='RAG_CONTENT_MANAGER' WHERE id=3")
        db.execute('UPDATE documents SET roles=? WHERE id=1', (json.dumps(['RAG_RESTRICTED_USER']),))
        db.execute('UPDATE documents SET roles=? WHERE id=2', (json.dumps(['RAG_CONTENT_MANAGER','RAG_SECURITY_AUDITOR']),))
        db.commit()
        migrate_business_roles(db)
        assert db.execute('SELECT role FROM users WHERE id=2').fetchone()[0] == 'RAG_ACCOUNTANT'
        assert db.execute('SELECT role FROM users WHERE id=3').fetchone()[0] == 'RAG_REPORTING_SPECIALIST'
        assert json.loads(db.execute('SELECT roles FROM documents WHERE id=1').fetchone()[0]) == ['RAG_ACCOUNTANT']
        assert json.loads(db.execute('SELECT roles FROM documents WHERE id=2').fetchone()[0]) == ['RAG_REPORTING_SPECIALIST','RAG_SECURITY_AUDITOR']
        assert original == [(r['id'],r['password']) for r in db.execute('SELECT id,password FROM users ORDER BY id')]
        revision = db.execute('SELECT value FROM corpus_revision').fetchone()[0]
        migrate_business_roles(db)
        assert db.execute('SELECT value FROM corpus_revision').fetchone()[0] == revision
        assert db.execute("SELECT count(*) FROM audit WHERE event='business_roles_migrated'").fetchone()[0] == 1


@pytest.mark.parametrize('role', ['RAG_RESTRICTED_USER','RAG_CONTENT_MANAGER'])
def test_legacy_roles_cannot_be_assigned(app, role):
    c = client(app,4)
    assert c.post('/users', data={'csrf':'test','action':'update','uid':2,'role':role,'active':'1'}).status_code == 400
    assert c.post('/documents/1/acl', data={'csrf':'test','level':'general','roles':[role]}).status_code == 400
