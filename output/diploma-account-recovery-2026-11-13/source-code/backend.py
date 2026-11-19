"""Existing RAG adapter; inputs must be authorized database records."""
from app.rag_service import retrieve, generate, encode_chunks, GENERATION_LOCK


def prepare_index(chunks):
    import faiss
    import numpy as np
    from app.search_hybrid import build_bm25
    if not chunks:
        return {}
    missing = [chunk for chunk in chunks if chunk.get('vector') is None]
    encoded = encode_chunks(missing)
    if encoded.shape != (len(missing), 384) or not np.isfinite(encoded).all():
        raise ValueError('Invalid embedding batch')
    new = {chunk['id']: vector.astype('float32').tobytes()
           for chunk, vector in zip(missing, encoded)}
    vectors = [np.frombuffer(new.get(c['id'], c.get('vector')), dtype='float32') for c in chunks]
    if any(v.shape != (384,) or not np.isfinite(v).all() for v in vectors):
        raise ValueError('Invalid persisted vector')
    index = faiss.IndexFlatIP(384)
    index.add(np.asarray(vectors, dtype='float32'))
    build_bm25(chunks)
    return new
