import json
from pathlib import Path
import tempfile
import unittest

from link_targets import link_key, resolve_link_targets, vault_link_targets
from problem_index import root_problem_records
from problem_candidates import signal_unresolved_target


class LinkIdentityTests(unittest.TestCase):
    def test_shared_resolution_contract(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/link_resolution_conformance.json').read_text())
        targets = resolve_link_targets(fixture['notes'])
        for case in fixture['cases']:
            self.assertEqual(targets.get(link_key(case['target'])), case['path'], case['target'])

    def test_nested_and_ambiguous_targets_never_invent_root_relations(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault/'folder').mkdir()
            (vault/'folder/B.md').write_text('Q\n***\nA')
            (vault/'B.md').write_text('---\naliases: [root alias]\n---\nQ\n***\nA')
            for name, target in [('Qualified', 'folder/B'), ('Ambiguous', 'B'), ('Alias', 'root alias.md#Scope')]:
                (vault/f'{name}.md').write_text(f'---\ncriticisms: "[[{target}]]"\n---\nQ\n***\nA')
            records = {r['name']: r for r in root_problem_records(vault)}
            self.assertEqual(records['B']['criticised_by'], ['Alias'])
            self.assertEqual(records['Qualified']['criticism_resolution'][0]['path'], 'folder/B')
            self.assertIsNone(records['Ambiguous']['criticism_resolution'][0]['path'])

    def test_missing_target_does_not_make_a_historical_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault/'A.md').write_text('[[remote/Note]] [[alias]]')
            (vault/'B.md').write_text('---\naliases:\n  - alias\n---\nText')
            hits = signal_unresolved_target(vault, 1)
            self.assertEqual([h['text'] for h in hits], ['remote/note'])
            self.assertEqual(hits[0]['signal'], 'unresolved-target')
            self.assertIn('cause unknown', hits[0]['also'])
            self.assertEqual(hits[0]['observed_vault'], str(vault.resolve()))

    def test_quoted_comma_is_not_two_aliases_or_two_criticisms(self):
        from note_chunks import criticism_targets
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault/'B.md').write_text('---\naliases: ["one, two"]\n---\nQ')
            targets = vault_link_targets(vault)
            self.assertEqual(targets.get('one, two'), 'B')
            self.assertNotIn('one', targets)
            self.assertNotIn('two', targets)
        self.assertEqual(criticism_targets('criticisms: ["[[one, two]]"]'), (['one, two'], None))
