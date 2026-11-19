"""Resolve original files from trusted document records; render without modifying PDFs."""
import json
import threading
from pathlib import Path

PDF_LOCK = threading.RLock()


def original_file(db, document_id, upload_root, imported_root):
    uploaded = db.execute('SELECT path FROM document_files WHERE document_id=?', (document_id,)).fetchone()
    if uploaded:
        candidate = Path(uploaded['path'])
        root = Path(upload_root).resolve()
        if not candidate.is_absolute():
            # Relative upload paths were historically based on the project directory.
            candidate = Path(__file__).resolve().parents[1] / candidate
    else:
        imported = db.execute('SELECT source FROM imported_sources WHERE document_id=?', (document_id,)).fetchone()
        if not imported:
            return None
        name = imported['source']
        if not name or '/' in name or '\\' in name or ':' in name:
            return None
        root = Path(imported_root).resolve()
        candidate = root / name
    try:
        resolved = candidate.resolve()
        if resolved.is_relative_to(root) and resolved.suffix.lower() == '.pdf' and resolved.is_file():
            return resolved
    except (OSError, ValueError):
        pass
    return None


def source_record(db, document_id, chunk_id):
    row = db.execute('''SELECT c.*, d.metadata FROM chunks c
        LEFT JOIN chunk_details d ON d.chunk_id=c.id
        WHERE c.document_id=? AND c.id=?''', (document_id, chunk_id)).fetchone()
    if row is None:
        return None
    try:
        metadata = json.loads(row['metadata']) if row['metadata'] else {}
        if not isinstance(metadata, dict):
            metadata = {}
    except (ValueError, TypeError):
        metadata = {}
    # Do not substitute the printed document page for the physical PDF page.
    physical = metadata.get('pdf_page')
    if type(physical) is not int or physical < 1:
        physical = None
    return dict(id=row['id'], text=row['text'], pdf_page=physical,
                document_page=metadata.get('document_page', row['page']),
                section=metadata.get('section', ''), point=metadata.get('point', ''))


def page_exists(path, physical_page):
    import pymupdf
    if path is None or physical_page is None:
        return False
    try:
        with PDF_LOCK, pymupdf.open(path) as document:
            return document.is_pdf and not document.is_encrypted and 1 <= physical_page <= len(document)
    except (OSError, ValueError, RuntimeError):
        return False


def render_page(path, physical_page):
    import pymupdf
    with PDF_LOCK, pymupdf.open(path) as document:
        if not document.is_pdf or document.is_encrypted or not 1 <= physical_page <= len(document):
            raise ValueError('Unavailable PDF page')
        page = document[physical_page - 1]
        scale = min(1.8, 2200 / max(page.rect.width, page.rect.height, 1))
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes('png')
