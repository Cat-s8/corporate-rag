"""Idempotent local migration; preserves original JSON/FAISS and denies ACL by default."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def import_corpus(db, metadata_file, index_file):
    import faiss
    import numpy as np
    metadata_file, index_file = Path(metadata_file), Path(index_file)
    raw = metadata_file.read_bytes()
    chunks = json.loads(raw)
    index = faiss.read_index(str(index_file))
    if not isinstance(index, faiss.IndexFlatIP) or index.d != 384 or index.ntotal != len(chunks):
        raise ValueError('FAISS/metadata shape mismatch')
    if len({c['chunk_id'] for c in chunks}) != len(chunks):
        raise ValueError('Duplicate legacy chunk IDs')
    digest = hashlib.sha256(raw + index_file.read_bytes()).hexdigest()
    groups = {}
    for row, chunk in enumerate(chunks):
        groups.setdefault(chunk['source'], []).append((row, chunk))
    imported = 0
    with db:
        for source, rows in groups.items():
            prior = db.execute('SELECT digest FROM imported_sources WHERE source=?', (source,)).fetchone()
            if prior:
                if prior['digest'] != digest:
                    raise ValueError('Previously imported source changed; explicit version migration required')
                continue
            did = db.execute('INSERT INTO documents(title,level,roles,status) VALUES(?,?,?,?)',
                             (source, 'restricted', '[]', 'Готов')).lastrowid
            for row, chunk in rows:
                vector = index.reconstruct(row)
                if not np.isfinite(vector).all():
                    raise ValueError('Invalid vector')
                cid = db.execute('INSERT INTO chunks(document_id,page,text) VALUES(?,?,?)',
                    (did, chunk.get('document_page') or chunk.get('pdf_page'), chunk['text'])).lastrowid
                meta = {k:v for k,v in chunk.items() if k not in ('text','chunk_id')}
                meta['legacy_chunk_id'] = chunk['chunk_id']
                db.execute('INSERT INTO chunk_details VALUES(?,?,?)',
                    (cid, json.dumps(meta, ensure_ascii=False), vector.astype('float32').tobytes()))
                imported += 1
            db.execute('INSERT INTO imported_sources VALUES(?,?,?)', (source, did, digest))
        db.execute('INSERT INTO audit(actor,event,target,status) VALUES(?,?,?,?)',
                   ('migration','corpus_import',str(imported),'Успешно'))
    return imported


if __name__ == '__main__':
    from rag_roles.server import create_app
    root = Path(__file__).resolve().parents[1]
    application = create_app()
    with application.app_context():
        count = import_corpus(application.db(), root/'index/metadata_pdf.json', root/'index/faiss_pdf.index')
    print(f'Imported {count} chunks. ACL is empty; only administrators have access.')
