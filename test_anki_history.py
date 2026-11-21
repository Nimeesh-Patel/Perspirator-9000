import hashlib
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from anki_history import read_backup, compare_backups
from anki_query import capture_time, historical_note


class BackupComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def database(self, name, *, title='Old', text='A', review=1000, extra=False):
        path = self.root / name
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('CREATE TABLE notes(id integer primary key,flds text,mod integer);'
                'CREATE TABLE cards(id integer primary key,nid integer,reps integer,lapses integer,ivl integer,queue integer,type integer,due integer);'
                'CREATE TABLE revlog(id integer primary key,cid integer,ease integer,type integer);')
            front = f'<div class="interest-source"><a href="obsidian://open?vault=Vault&amp;file={title}">{title}</a></div>Q'
            db.execute('INSERT INTO notes VALUES(1,?,2)', (front+'\x1f'+text,))
            db.execute('INSERT INTO cards VALUES(10,1,2,0,4,2,2,42)')
            db.execute('INSERT INTO revlog VALUES(?,10,3,1)', (review,))
            if extra:
                db.execute('INSERT INTO notes VALUES(2,?,3)', ('Other\x1fA',))
                db.execute('INSERT INTO revlog VALUES(2000,10,1,1)')
        return path

    def compare(self, a, b, **kwargs):
        return compare_backups(a, b, before_at='1970-01-01T00:00:01+00:00',
            after_at='1970-01-01T00:00:03+00:00', decode_note=historical_note,
            parse_time=capture_time, **kwargs)

    def test_rename_and_rendering_change_are_distinct_from_text_change(self):
        a = self.database('a.sqlite')
        b = self.database('b.sqlite', title='New', text='A<div class="interest-criticisms">Title</div>', extra=True)
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (a,b)]
        result = self.compare(a,b,limit=1)
        change = result['records']['changed_note_fields']['shown'][0]
        self.assertTrue(change['source_reference_changed'])
        self.assertFalse(change['text_changed'])
        self.assertEqual(change['note_id'], 1)
        event = result['records']['added_review_entries']['shown'][0]
        self.assertEqual((event['note_id'],event['ease'],event['type']), (1,1,1))
        self.assertTrue(event['inside_selected_interval'])
        self.assertEqual(result['records']['notes_added']['total'], 1)
        self.assertFalse(result['history_continuity_warning'])
        self.assertEqual(hashes, [hashlib.sha256(p.read_bytes()).hexdigest() for p in (a,b)])

    def test_missing_and_changed_history_is_exposed_with_exact_omissions(self):
        a = self.database('a.sqlite', extra=True)
        b = self.database('b.sqlite', text='Changed', review=2000)
        result = self.compare(a,b,limit=1)
        self.assertTrue(result['history_continuity_warning'])
        self.assertEqual(result['records']['notes_absent_after']['shown'][0]['note_id'], 2)
        self.assertTrue(result['records']['changed_note_fields']['shown'][0]['text_changed'])
        with closing(sqlite3.connect(b)) as db, db:
            db.execute("INSERT INTO notes VALUES(3,'New',4)")
            db.execute("INSERT INTO notes VALUES(4,'Other',4)")
        group = self.compare(a,b,limit=1)['records']['notes_added']
        self.assertEqual((group['total'],len(group['shown']),group['omitted']), (2,1,1))

    def test_modern_archive_uses_real_collection_instead_of_placeholder(self):
        try:
            import zstandard
        except ImportError:
            self.skipTest('optional compressed-backup dependency unavailable')
        # Large enough to require a real compression window, as phone backups
        # do; tiny SQLite fixtures alone miss an accidentally undersized bound.
        db = self.database('a.sqlite', text='A' * 300000)
        package = self.root/'modern.colpkg'
        with zipfile.ZipFile(package,'w') as archive:
            archive.writestr('collection.anki21b',zstandard.ZstdCompressor().compress(db.read_bytes()))
            archive.writestr('collection.anki2',b'placeholder')
            archive.writestr('../outside',b'never extracted')
        observed = read_backup(package)
        self.assertEqual(observed['artifact']['member'], 'collection.anki21b')
        self.assertEqual(len(observed['notes']),1)
        self.assertFalse((self.root.parent/'outside').exists())
        with self.assertRaisesRegex(ValueError,'byte limit'):
            read_backup(package,max_database_bytes=100)

    def test_legacy_archive_bounds_schema_and_invalid_inputs(self):
        db = self.database('a.sqlite')
        package = self.root/'old.colpkg'
        with zipfile.ZipFile(package,'w') as archive:
            archive.writestr('collection.anki2',db.read_bytes())
        self.assertEqual(len(read_backup(package)['cards']),1)
        with self.assertRaisesRegex(ValueError,'input byte limit'):
            read_backup(package,max_input_bytes=10)
        with self.assertRaisesRegex(ValueError,'row limit'):
            read_backup(self.database('many.sqlite',extra=True),max_rows=1)
        with self.assertRaisesRegex(ValueError,'output byte limit'):
            self.compare(db,db,max_bytes=100)
        with self.assertRaisesRegex(ValueError,'positive integers'):
            self.compare(db,db,limit=0)
        bad = self.root/'bad.sqlite'
        with closing(sqlite3.connect(bad)) as con, con:
            con.execute('create table unrelated(id integer)')
        with self.assertRaisesRegex(ValueError,'schema'):
            read_backup(bad)
        bad.write_bytes(b'not an archive')
        with self.assertRaisesRegex(ValueError,'archive'):
            read_backup(bad)

    def test_dates_and_cli_do_not_silently_guess_an_interval(self):
        db = self.database('a.sqlite')
        with self.assertRaisesRegex(ValueError,'timezone'):
            compare_backups(db,db,before_at='2026-11-20',after_at='2026-11-21',
                decode_note=historical_note,parse_time=capture_time)
        with self.assertRaisesRegex(ValueError,'later'):
            compare_backups(db,db,before_at='2026-11-21T00:00:00Z',after_at='2026-11-20T00:00:00Z',
                decode_note=historical_note,parse_time=capture_time)
