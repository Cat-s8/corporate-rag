"""Real BM25/FAISS tests with deterministic embeddings and a recording LLM."""
import json
import numpy as np
import pytest
from test_access import app, client
from rag_roles import backend
from app import rag_service, search_hybrid


def query(c, question='payment', **extra):
    return c.post('/api/search', json=dict(question=question, **extra), headers={'X-CSRF-Token':'test'})


@pytest.mark.parametrize('uid,expected',[(1,[1]),(2,[1,2]),(3,[1,2]),(4,[1,2]),(5,[1,2])])
def test_real_retrieval_scope(app, monkeypatch, uid, expected):
    observed={}
    original_bm25=search_hybrid.build_bm25
    original_faiss=search_hybrid.search_faiss
    def bm25(chunks):
        observed['ids']=[c['id'] for c in chunks]
        return original_bm25(chunks)
    def faiss(query, model, index, **kw):
        observed['vectors']=index.ntotal
        return original_faiss(query,model,index,**kw)
    monkeypatch.setattr(search_hybrid,'build_bm25',bm25)
    monkeypatch.setattr(search_hybrid,'search_faiss',faiss)
    response=query(client(app,uid))
    assert response.status_code==200
    assert sorted(observed['ids'])==expected
    assert observed['vectors']==len(expected)
    assert all('vector' not in s for s in response.json['sources'])


def test_real_context_contains_only_authorized_text(app,monkeypatch):
    seen={}
    class LLM:
        def tokenize(self,text): return list(range(len(text)//4))
        def create_chat_completion(self,**kwargs):
            seen.update(kwargs)
            return {'choices':[{'message':{'content':'Ответ [SOURCES: 1]'}}]}
    monkeypatch.setattr(rag_service,'language_model',lambda:LLM())
    monkeypatch.setattr(backend,'generate',rag_service.generate)
    r=query(client(app,1))
    assert r.status_code==200
    context=seen['messages'][1]['content']
    assert 'payment public' in context and 'SECRET' not in context
    assert seen['temperature']==0.1
    assert [s['id'] for s in r.json['sources']]==[1]


def test_empty_scope_never_calls_retrieval_or_llm(app,monkeypatch):
    with app.app_context():
        app.db().execute("UPDATE documents SET roles='[]'"); app.db().commit()
    def fail(*a,**kw): raise AssertionError('Should not be called')
    monkeypatch.setattr(backend,'retrieve',fail); monkeypatch.setattr(backend,'generate',fail)
    r=query(client(app,1))
    assert r.status_code==200 and r.json['sources']==[]


def test_revocation_between_retrieval_and_generation(app,monkeypatch):
    real=backend.retrieve
    def retrieve(*args):
        result=real(*args)
        app.db().execute("UPDATE documents SET roles='[]'"); app.db().commit()
        return result
    def fail(*a,**kw): raise AssertionError('Must not generate after revocation')
    monkeypatch.setattr(backend,'retrieve',retrieve); monkeypatch.setattr(backend,'generate',fail)
    assert query(client(app,1)).status_code==409


def test_role_change_after_request_guard_never_uses_stale_role(app,monkeypatch):
    @app.before_request
    def change_role():
        app.db().execute("UPDATE users SET role='RAG_USER' WHERE id=4"); app.db().commit()
    def fail(*a,**kw): raise AssertionError('Must reject stale administrator before search')
    monkeypatch.setattr(backend,'retrieve',fail)
    assert query(client(app,4)).status_code==401  # Session revoked by the role change.


def test_revocation_during_model_loading(app,monkeypatch):
    def model():
        app.db().execute("UPDATE documents SET roles='[]'"); app.db().commit()
        return object()  # The validator must run before tokenize/completion.
    monkeypatch.setattr(rag_service,'language_model',model)
    monkeypatch.setattr(backend,'generate',rag_service.generate)
    assert query(client(app,1)).status_code==409


def test_revocation_during_generation_discards_answer(app,monkeypatch):
    def generate(*a,**kw):
        app.db().execute("UPDATE users SET active=0 WHERE id=1"); app.db().commit()
        return 'MUST_NOT_LEAK',[]
    monkeypatch.setattr(backend,'generate',generate)
    r=query(client(app,1))
    assert r.status_code==401 and b'MUST_NOT_LEAK' not in r.data


def test_api_auth_csrf_and_filters(app):
    assert query(app.test_client()).status_code==401
    c=client(app,1)
    assert c.post('/api/search',json={'question':'payment'}).status_code==400
    assert query(c,level='restricted').status_code==403
    assert query(client(app,2),level=[]).status_code==400
    r=query(client(app,2),level='general')
    assert r.status_code==200 and all(s['document_id']==1 for s in r.json['sources'])


def test_acl_route_only_admin_and_fail_closed(app):
    for uid in [1,2,3,5]:
        assert client(app,uid).post('/documents/1/acl',data={'csrf':'test','level':'general','roles':['RAG_USER']}).status_code==403
    admin=client(app,4)
    assert admin.post('/documents/1/acl',data={'csrf':'test','level':'general','roles':[]}).status_code==302
    assert query(client(app,1)).json['sources']==[]
    assert len(query(admin).json['sources'])==2


def test_publication_failure_rolls_back(app,monkeypatch):
    with app.app_context():
        app.db().execute("UPDATE documents SET status='Ожидает индексации'"); app.db().commit()
    def fail(*a): raise RuntimeError('Embedding failure')
    monkeypatch.setattr(backend,'prepare_index',fail)
    assert client(app,4).post('/index',data={'csrf':'test'}).status_code==503
    with app.app_context():
        assert app.db().execute("SELECT count(*) FROM documents WHERE status='Готов'").fetchone()[0]==0
    assert query(client(app,1)).json['sources']==[]


def test_draft_direct_url_and_published_vectors(app):
    with app.app_context():
        app.db().execute("UPDATE documents SET status='Ожидает индексации'"); app.db().commit()
    assert client(app,1).get('/documents/1').status_code==404
    assert client(app,4).post('/index',data={'csrf':'test'}).status_code==302
    with app.app_context():
        rows=app.db().execute('SELECT vector FROM chunk_details').fetchall()
        assert len(rows)==2 and all(len(r['vector'])==384*4 for r in rows)
    assert client(app,1).get('/documents/1').status_code==200


def test_invalid_role_and_malformed_acl(app):
    with app.app_context():
        app.db().execute("UPDATE documents SET roles='broken'")
        app.db().execute("UPDATE users SET role='INVALID' WHERE id=2"); app.db().commit()
    assert query(client(app,1)).json['sources']==[]
    assert query(client(app,2)).status_code==401


def test_import_idempotence_preserves_pages_and_existing_rows(app,tmp_path):
    import faiss
    from rag_roles.import_existing import import_corpus
    metadata=tmp_path/'metadata.json'; index_path=tmp_path/'test.index'
    metadata.write_text(json.dumps([dict(chunk_id=10,source='sample.pdf',text='sample text',
        pdf_page=8,document_page=4,section='Section',subsection='Subsection')]),encoding='utf-8')
    index=faiss.IndexFlatIP(384); index.add(np.ones((1,384),dtype='float32'))
    faiss.write_index(index,str(index_path))
    with app.app_context():
        assert import_corpus(app.db(),metadata,index_path)==1
        assert import_corpus(app.db(),metadata,index_path)==0
        assert app.db().execute('SELECT count(*) FROM documents').fetchone()[0]==3
        doc=app.db().execute("SELECT * FROM documents WHERE title='sample.pdf'").fetchone()
        assert doc['roles']=='[]' and doc['level']=='restricted'
        from rag_roles.storage import chunks_for
        chunk=chunks_for(app.db(),[doc])[0]
        assert chunk['pdf_page']==8 and chunk['document_page']==4 and chunk['section']=='Section'
        app.db().execute('DELETE FROM documents WHERE id=?',(doc['id'],)); app.db().commit()
        assert import_corpus(app.db(),metadata,index_path)==0  # Do not resurrect deleted documents.
