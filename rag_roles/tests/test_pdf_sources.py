import json

import pymupdf
import pytest

from test_access import app, client
from rag_roles.pdf_sources import render_page


@pytest.fixture
def pdf(app):
    root = app.config['PDF_UPLOAD_ROOT']
    root.mkdir()
    path = root / 'original.pdf'
    with pymupdf.open() as document:
        for text in ['FIRST PAGE', 'SECOND PAGE: payment public']:
            page = document.new_page()
            page.insert_text((72, 72), text)
        document.save(path)
    with app.app_context():
        db = app.db()
        for did in (1, 2):
            db.execute('INSERT INTO document_files VALUES(?,?)', (did, str(path)))
            db.execute('INSERT INTO chunk_details VALUES(?,?,NULL)',
                       (did, json.dumps({'pdf_page': 2, 'document_page': 9})))
        db.commit()
    return path


def test_source_opens_physical_page_and_inline_pdf(app, pdf):
    c = client(app, 1)
    response = c.post('/', data={'csrf': 'test', 'question': 'payment'})
    assert b'/documents/1/sources/1' in response.data
    response = c.get('/documents/1/sources/1')
    assert response.status_code == 200
    assert b'/documents/1/pdf#page=2' in response.data
    assert b'/documents/1/sources/1/page.png' in response.data
    response = c.get('/documents/1/sources/1/page.png')
    assert response.mimetype == 'image/png'
    assert response.data == render_page(pdf, 2)
    assert response.data != render_page(pdf, 1)
    response = c.get('/documents/1/pdf')
    assert response.data == pdf.read_bytes()
    assert response.mimetype == 'application/pdf'
    assert response.headers['Content-Disposition'].startswith('inline;')
    assert 'no-store' in response.headers['Cache-Control']
    response = c.get('/documents/1/pdf', headers={'Range': 'bytes=0-4'})
    assert response.status_code == 206
    assert response.data == b'%PDF-'


@pytest.mark.parametrize('suffix', ['pdf', 'sources/2', 'sources/2/page.png'])
def test_pdf_access_checked_on_every_request(app, pdf, suffix):
    url = '/documents/2/' + suffix
    assert app.test_client().get(url).status_code == 302
    assert client(app, 1).get(url).status_code == 404
    c = client(app, 2)
    assert c.get(url).status_code == 200
    with app.app_context():
        app.db().execute('UPDATE documents SET roles=? WHERE id=2', ('["RAG_ADMIN"]',))
        app.db().commit()
    assert c.get(url).status_code == 404
    assert client(app, 4).get(url).status_code == 200


def test_wrong_chunk_and_unpublished_document(app, pdf):
    c = client(app, 2)
    for suffix in ['sources/2', 'sources/2/page.png', 'sources/999']:
        assert c.get('/documents/1/' + suffix).status_code == 404
    with app.app_context():
        app.db().execute("UPDATE documents SET status='Ожидает индексации' WHERE id=1")
        app.db().commit()
    for suffix in ['pdf', 'sources/1', 'sources/1/page.png']:
        assert c.get('/documents/1/' + suffix).status_code == 404


def test_missing_file_and_untrusted_path(app, pdf, tmp_path):
    outside = tmp_path / 'outside.pdf'
    outside.write_bytes(pdf.read_bytes())
    with app.app_context():
        app.db().execute('UPDATE document_files SET path=? WHERE document_id=1', (str(outside),))
        app.db().commit()
    c = client(app, 1)
    assert c.get('/documents/1/pdf').status_code == 404
    assert c.get('/documents/1/sources/1/page.png').status_code == 404
    response = c.get('/documents/1/sources/1')
    assert response.status_code == 200
    assert 'Оригинальный PDF не найден'.encode() in response.data


@pytest.mark.parametrize('physical', [None, 999, True, '2'])
def test_no_invented_page_number(app, pdf, physical):
    with app.app_context():
        app.db().execute('UPDATE chunk_details SET metadata=? WHERE chunk_id=1',
                         (json.dumps({'pdf_page': physical, 'document_page': 1}),))
        app.db().commit()
    c = client(app, 1)
    assert c.get('/documents/1/sources/1/page.png').status_code == 404
    response = c.get('/documents/1/sources/1')
    assert response.status_code == 200
    assert b'<img class="pdf-page-image"' not in response.data


def test_imported_original_and_path_traversal(app, pdf):
    app.config['PDF_IMPORTED_ROOT'] = pdf.parent
    with app.app_context():
        db = app.db()
        db.execute('DELETE FROM document_files WHERE document_id=1')
        db.execute('INSERT INTO imported_sources VALUES(?,?,?)', (pdf.name, 1, 'test'))
        db.commit()
    c = client(app, 1)
    assert c.get('/documents/1/pdf').data == pdf.read_bytes()
    with app.app_context():
        app.db().execute("UPDATE imported_sources SET source='../original.pdf' WHERE document_id=1")
        app.db().commit()
    assert c.get('/documents/1/pdf').status_code == 404
