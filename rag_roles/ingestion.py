"""Retain the established structured parser, with an explicit generic-PDF fallback."""
from pathlib import Path


def parse_pdf(path, source_name):
    import pymupdf
    from app.ingest_pdf import process_pdf, split_long_text
    path = Path(path)
    with pymupdf.open(path) as document:
        if document.is_encrypted or not 1 <= len(document) <= 500:
            raise ValueError('PDF must be unencrypted and contain 1..500 pages')
        # The established treasury parser expects printed page numbers.
        structured = any('Стр.' in page.get_text() for page in document)
        fallback = []
        if not structured:
            for page_number, page in enumerate(document, 1):
                text = page.get_text(sort=True).strip()
                if text:
                    for part, piece in enumerate(split_long_text(text), 1):
                        fallback.append(dict(source=source_name, pdf_page=page_number,
                            document_page=None, chunk_part=part, text=piece,
                            section='', subsection='', point='', subpoint=''))
    chunks = process_pdf(path) if structured else fallback
    if not chunks:
        raise ValueError('No indexable text in PDF')
    for chunk in chunks:
        chunk['source'] = source_name
    return chunks
