"""Generate an administrator login code locally; never exposed by the web app."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rag_roles.admin_auth import generate_code
from rag_roles.storage import migrate


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local administrator login codes (valid for 600 seconds).')
    commands = parser.add_subparsers(dest='command', required=True)
    generate = commands.add_parser('generate', help='Replace the unused code for an active administrator')
    generate.add_argument('--login', required=True)
    args = parser.parse_args(argv)
    data = Path(os.getenv('RAG_ROLES_DATA', Path(__file__).resolve().parent / 'data')).resolve()
    path = data / 'roles.sqlite3'
    if not path.is_file():
        parser.error('Application database not found. Check RAG_ROLES_DATA and initialize the application first.')
    db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        migrate(db)
        code, expires = generate_code(db, args.login)
    except ValueError as error:
        parser.error(str(error))
    finally:
        db.close()
    # The only output containing the secret. Do not redirect it into log files.
    print('Code: ' + code)
    print('Expires at (UTC): ' + datetime.fromtimestamp(expires, timezone.utc).isoformat(timespec='seconds'))
    print('Valid for 600 seconds from generation, once only. Previous unused codes are revoked.')


if __name__ == '__main__':
    main()
