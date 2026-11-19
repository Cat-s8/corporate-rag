from app import rag_service


def test_warmup_uses_no_documents_and_limits_output(monkeypatch):
    calls = {}

    class Embeddings:
        def encode(self, texts, **kwargs):
            calls['texts'] = texts

    class Model:
        def create_chat_completion(self, **kwargs):
            calls['completion'] = kwargs

    monkeypatch.setattr(rag_service, 'embedding_model', lambda: Embeddings())
    monkeypatch.setattr(rag_service, 'language_model', lambda: Model())
    rag_service.warmup()
    assert calls['texts'] == ['query: проверка готовности']
    assert calls['completion']['max_tokens'] == 1
    assert 'Проверка готовности' in calls['completion']['messages'][-1]['content']
