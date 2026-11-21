"""Bounded readers and record differences for caller-selected Anki backups.

Never opens a live collection. Archive members are read into a private temporary
file, then queried read-only. No scheduler, archive store or progress judgement.
"""
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import zipfile


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_backup(file, *, max_input_bytes=32*1024*1024,
                max_database_bytes=128*1024*1024, max_rows=500000):
    path = Path(file).expanduser().resolve(strict=True)
    if path.stat().st_size > max_input_bytes:
        raise ValueError('backup exceeds input byte limit')
    with path.open('rb') as stream:
        raw = stream.read(max_input_bytes + 1)
    if len(raw) > max_input_bytes:
        raise ValueError('backup exceeds input byte limit')
    member = None
    database = raw
    if not raw.startswith(b'SQLite format 3\0'):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                for name in ('collection.anki21b', 'collection.anki21', 'collection.anki2'):
                    matches = [i for i in archive.infolist() if i.filename == name]
                    if len(matches) > 1:
                        raise ValueError('duplicate collection members')
                    if not matches:
                        continue
                    member = name
                    if matches[0].file_size > max_database_bytes:
                        raise ValueError('collection member exceeds byte limit')
                    with archive.open(matches[0]) as stream:
                        database = stream.read(max_database_bytes + 1)
                    if len(database) > max_database_bytes:
                        raise ValueError('collection member exceeds byte limit')
                    break
                if member is None:
                    raise ValueError('archive has no supported collection member')
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise ValueError(f'unreadable backup archive: {exc}') from exc
        if member == 'collection.anki21b':
            try:
                import zstandard
            except ImportError as exc:
                raise ValueError('compressed Anki backups need requirements-anki.txt') from exc
            try:
                decoder = zstandard.ZstdDecompressor(
                    max_window_size=max(1024, max_database_bytes))
                with decoder.stream_reader(io.BytesIO(database)) as stream:
                    database = stream.read(max_database_bytes + 1)
            except zstandard.ZstdError as exc:
                raise ValueError(f'invalid compressed collection: {exc}') from exc
    if len(database) > max_database_bytes:
        raise ValueError('expanded collection exceeds byte limit')
    if not database.startswith(b'SQLite format 3\0'):
        raise ValueError('collection is not SQLite')

    required = {'notes': {'id', 'flds', 'mod'},
                'cards': {'id', 'nid', 'reps', 'lapses', 'ivl', 'queue', 'type', 'due'},
                'revlog': {'id', 'cid', 'ease', 'type'}}
    tables = {}
    with tempfile.TemporaryDirectory(prefix='perspirator-anki-') as tmp:
        local = Path(tmp) / 'collection.sqlite'
        local.write_bytes(database)
        connection = sqlite3.connect(local.as_uri() + '?mode=ro', uri=True)
        try:
            connection.execute('PRAGMA query_only=ON')
            connection.execute('PRAGMA trusted_schema=OFF')
            connection.row_factory = sqlite3.Row
            for table, columns in required.items():
                kind = connection.execute('SELECT type FROM sqlite_master WHERE name=?', (table,)).fetchone()
                if kind is None or kind[0] != 'table':
                    raise ValueError(f'unsupported collection schema: missing table {table}')
                actual = {r['name'] for r in connection.execute(f'PRAGMA table_info({table})')}
                if not columns <= actual:
                    raise ValueError(f'unsupported collection schema: {table}')
                rows = connection.execute(f'SELECT * FROM {table} ORDER BY id LIMIT ?', (max_rows + 1,)).fetchall()
                if len(rows) > max_rows:
                    raise ValueError(f'{table} exceeds row limit')
                if any(not isinstance(r['id'], int) for r in rows):
                    raise ValueError(f'invalid identities in {table}')
                tables[table] = {r['id']: dict(r) for r in rows}
                if len(tables[table]) != len(rows):
                    raise ValueError(f'duplicate identities in {table}')
        except sqlite3.Error as exc:
            raise ValueError(f'unreadable collection: {exc}') from exc
        finally:
            connection.close()
    tables['artifact'] = {'path': str(path), 'bytes': len(raw), 'sha256': digest(raw),
                          'member': member, 'database_sha256': digest(database)}
    return tables


def compare_backups(before, after, *, before_at, after_at, decode_note,
                    parse_time, limit=25, max_bytes=256*1024,
                    max_input_bytes=32*1024*1024, max_database_bytes=128*1024*1024,
                    max_rows=500000, include_fields=False):
    limits = (limit, max_bytes, max_input_bytes, max_database_bytes, max_rows)
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 1 for v in limits):
        raise ValueError('comparison limits must be positive integers')
    start, finish = parse_time(before_at), parse_time(after_at)
    if finish <= start:
        raise ValueError('after-at must be later than before-at')
    options = dict(max_input_bytes=max_input_bytes, max_database_bytes=max_database_bytes, max_rows=max_rows)
    old, new = read_backup(before, **options), read_backup(after, **options)
    records, totals = {}, {}

    def collect(name, items):
        items = iter(items)
        shown, total = [], 0
        for item in items:
            total += 1
            if len(shown) < limit:
                shown.append(item)
        records[name] = {'total': total, 'shown': shown, 'omitted': total-len(shown)}
        totals[name] = total

    def note_record(row):
        if not isinstance(row['flds'], str):
            raise ValueError('note fields must be text')
        decoded = decode_note(row['flds'])
        return {'note_id': row['id'], **decoded,
                'fields_sha256': digest(row['flds'].encode()),
                **({'fields': row['flds'].split('\x1f')} if include_fields else {})}

    def note_changes():
        for key in sorted(old['notes'].keys() & new['notes'].keys()):
            left, right = old['notes'][key], new['notes'][key]
            if left['flds'] == right['flds']:
                continue
            a, b = note_record(left), note_record(right)
            yield {'note_id': key, 'before': a, 'after': b,
                   'source_reference_changed': a['source'] != b['source'],
                   'text_changed': a['content_sha256'] != b['content_sha256']}

    collect('changed_note_fields', note_changes())
    for name, left, right in [('notes_added', new, old), ('notes_absent_after', old, new)]:
        collect(name, (note_record(left['notes'][k])
                       for k in sorted(left['notes'].keys() - right['notes'].keys())))

    def events():
        for key in sorted(new['revlog'].keys() - old['revlog'].keys()):
            row = new['revlog'][key]
            card = new['cards'].get(row['cid'], {})
            note = new['notes'].get(card.get('nid'))
            yield {**row, 'note_id': card.get('nid'),
                   'source': decode_note(note['flds'])['source'] if note else None,
                   'inside_selected_interval': start.timestamp()*1000 < key <= finish.timestamp()*1000}

    collect('added_review_entries', events())
    collect('removed_review_entries', (old['revlog'][k] for k in sorted(old['revlog'].keys()-new['revlog'].keys())))
    collect('changed_review_entries', ({'before': old['revlog'][k], 'after': new['revlog'][k]}
        for k in sorted(old['revlog'].keys() & new['revlog'].keys()) if old['revlog'][k] != new['revlog'][k]))
    collect('cards_added', (new['cards'][k] for k in sorted(new['cards'].keys()-old['cards'].keys())))
    collect('cards_absent_after', (old['cards'][k] for k in sorted(old['cards'].keys()-new['cards'].keys())))
    collect('changed_card_state', ({'before': old['cards'][k], 'after': new['cards'][k]}
        for k in sorted(old['cards'].keys() & new['cards'].keys()) if old['cards'][k] != new['cards'][k]))

    result = {'provider': 'anki-backup', 'capability': 'record-comparison', 'status': 'complete',
        'scope': {'before': old['artifact'], 'after': new['artifact'],
                  'before_at': start.isoformat(), 'after_at': finish.isoformat(),
                  'timestamp_source': 'caller supplied; not verified capture times',
                  'collection_identity': 'not independently verified; identities compared as supplied',
                  'tables': ['notes', 'cards', 'revlog'], 'record_limit_per_group': limit},
        'limitations': [
            'No live phone, vault delivery, traversal, authorship or research progress is established.',
            'New review rows may include imports or non-answer events; type and ease are retained.',
            'Absence from the second backup is not proof of a deliberate deletion.',
            'Text comparison strips known Interest source/relation sections; it is a retrieval aid, not a semantic judgement.',
            'Historical source URLs are reported without asserting a current vault match.'],
        'counts_before': {k: len(old[k]) for k in ('notes', 'cards', 'revlog')},
        'counts_after': {k: len(new[k]) for k in ('notes', 'cards', 'revlog')},
        'records': records,
        'history_continuity_warning': bool(totals['removed_review_entries'] or totals['changed_review_entries'])}
    if len(json.dumps(result, ensure_ascii=False).encode()) > max_bytes:
        raise ValueError('comparison exceeds output byte limit; reduce --limit or omit --include-fields')
    return result
