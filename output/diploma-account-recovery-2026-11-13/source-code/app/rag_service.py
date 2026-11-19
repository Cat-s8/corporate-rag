"""Existing BM25/FAISS/RRF on an authorized corpus, with no answer cache."""
import os
import logging
import threading
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMBEDDING_LOCK = threading.RLock()
GENERATION_LOCK = threading.RLock()


@lru_cache(maxsize=1)
def embedding_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(os.getenv('RAG_EMBEDDING_MODEL', 'intfloat/multilingual-e5-small'),
                               local_files_only=True, device='cpu')


@lru_cache(maxsize=1)
def language_model():
    from llama_cpp import Llama, llama_supports_gpu_offload
    path = Path(os.getenv('RAG_MODEL_PATH', str(ROOT/'models/Qwen3-8B-Q4_K_M.gguf')))
    if not path.is_absolute():
        path = ROOT/path
    layers = int(os.getenv('RAG_GPU_LAYERS', '-1'))
    logging.getLogger(__name__).warning('Qwen: GPU offload supported=%s, requested layers=%s',
                                       llama_supports_gpu_offload(), layers)
    return Llama(model_path=str(path), n_gpu_layers=layers,
                 n_ctx=4096, n_threads=int(os.getenv('RAG_THREADS', '6')), verbose=False)


def warmup():
    """Load shared models before serving; never use corporate documents for warmup."""
    from .chat_hybrid import build_messages
    with EMBEDDING_LOCK:
        embedding_model().encode(['query: проверка готовности'], normalize_embeddings=True)
    with GENERATION_LOCK:
        language_model().create_chat_completion(
            messages=build_messages('Проверка готовности', ''), max_tokens=1, temperature=0)


def encode_chunks(chunks):
    import numpy as np
    if not chunks:
        return np.empty((0, 384), dtype='float32')
    with EMBEDDING_LOCK:
        return embedding_model().encode(['passage: '+c['text'] for c in chunks],
                    normalize_embeddings=True, convert_to_numpy=True).astype('float32')


def retrieve(question, chunks, top_k):
    if not chunks:
        return []
    import faiss
    import numpy as np
    from .search_hybrid import build_bm25, hybrid_search
    vectors = []
    missing = [c for c in chunks if c.get('vector') is None]
    encoded = iter(encode_chunks(missing))
    for chunk in chunks:
        vector = (np.frombuffer(chunk['vector'], dtype='float32').copy()
                  if chunk.get('vector') is not None else next(encoded))
        if vector.shape != (384,) or not np.isfinite(vector).all():
            raise ValueError('Invalid embedding in authorized corpus')
        vectors.append(vector)
    index = faiss.IndexFlatIP(384)
    index.add(np.asarray(vectors, dtype='float32'))
    bm25 = build_bm25(chunks)
    with EMBEDDING_LOCK:
        hits = hybrid_search(question, embedding_model(), index, chunks, bm25, top_k=top_k)
    trusted = {c['chunk_id']: c for c in chunks}
    return [dict(trusted[h['chunk_id']], **{k: v for k, v in h.items()
                 if k in ('rrf_score','faiss_rank','faiss_score','bm25_rank','bm25_score')}) for h in hits]


def generate(question, sources, temperature, validate=None):
    if not sources:
        return 'В доступных документах информация не найдена.', []
    from .chat_hybrid import build_context, build_messages, generate_answer, select_sources
    with GENERATION_LOCK:
        llm = language_model()
        if validate:
            validate()
        selected = []
        for source in sources:
            candidate = selected + [source]
            context = build_context(candidate)
            prompt = '\n'.join(message['content'] for message in build_messages(question,context))
            # Include instructions; reserve chat-template tokens and 300 output tokens.
            if len(llm.tokenize(prompt.encode('utf-8'))) + 256 + 300 > 4096:
                break
            selected = candidate
        if not selected:
            return 'Запрос или фрагмент слишком длинный. Уточните вопрос.', []
        if validate:
            validate()
        answer, numbers, declared = generate_answer(llm, question, build_context(selected), temperature)
    if declared:
        used = [selected[n-1] for n in dict.fromkeys(numbers) if 1 <= n <= len(selected)]
    else:
        used = select_sources(selected, limit=2) or selected[:1]
    return answer, used
