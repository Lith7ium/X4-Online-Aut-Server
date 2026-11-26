"""Saved-marker XML evidence tests; no native X4 launch or load."""
import gzip
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from authority import marker_evidence
from authority.checkpoints import Checkpoint
from authority.marker_evidence import MarkerEvidenceError, verify_saved_marker

MARKER = 'a' * 32
STRING_GROUP = '<refs type="string"><ref id="42" string="' + MARKER + '"/></refs>'
CUE = ('<cue name="MarkerState"><vars><value name="$marker" type="string" value="42"/>'
       '<value name="$loaded" type="integer"/></vars></cue>')


def document(string_group=STRING_GROUP, cue=CUE):
    return ('<savegame><script>' + string_group + '<refs type="list"><ref id="42"/></refs>'
            '</script><md><script name="X4AuthorityCheckpointMarker">' + cue + '</script></md></savegame>')


class MarkerEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'save.xml.gz'

    def check(self, xml, expected=MARKER):
        self.path.write_bytes(gzip.compress(xml.encode()))
        data = self.path.read_bytes()
        cp = Checkpoint('checkpoint-' + 'b' * 32, self.path, hashlib.sha256(data).hexdigest(), len(data))
        return verify_saved_marker(cp, expected, script_name='X4AuthorityCheckpointMarker', cue_name='MarkerState')

    def test_exact_path_and_typed_reference(self):
        self.assertTrue(self.check(document()))
        with self.assertRaises(MarkerEvidenceError):
            self.check(document(), 'b' * 32)

    def test_missing_or_ambiguous_components_are_rejected(self):
        cases = [
            document(string_group=''),
            document(string_group=STRING_GROUP + STRING_GROUP),
            document(string_group=STRING_GROUP.replace('</refs>', '<ref id="42" string="' + MARKER + '"/></refs>')),
            document(string_group=STRING_GROUP.replace('type="string"', 'type="list"')),
            document(string_group=STRING_GROUP.replace('id="42"', 'id="41"')),
            document(cue=''), document(cue=CUE + CUE),
            document(cue=CUE.replace('type="string"', 'type="integer"')),
            document(cue=CUE.replace('value="42"', 'value="fortytwo"')),
            document(cue=CUE.replace('</vars>', '<value name="$marker" type="string" value="42"/></vars>')),
            document(cue=CUE.replace('</vars>', '</vars><vars/>')),
            document(cue=CUE.replace('<value name="$loaded" type="integer"/>', '')),
            document(cue=CUE.replace('name="$loaded" type="integer"', 'name="$loaded" type="integer" value="1"')),
            document(cue=CUE.replace('name="$loaded" type="integer"', 'name="$loaded" type="string"')),
            document().replace('</md>', '<script name="X4AuthorityCheckpointMarker"/></md>'),
        ]
        for xml in cases:
            with self.subTest(xml=xml), self.assertRaises(MarkerEvidenceError):
                self.check(xml)

    def test_streams_opaque_unsorted_ids_in_either_order(self):
        strings = (
            '<refs type="string"><ref id="90" string="unrelated"/><ref id="42" string="wrong"/>'
            '<ref id="90" string="duplicate unrelated"/><ref id="00042" string="' + MARKER + '"/></refs>'
        )
        cue = CUE.replace('value="42"', 'value="00042"')
        refs_last = (
            '<savegame><md><script name="X4AuthorityCheckpointMarker">' + cue + '</script></md>'
            '<script>' + strings + '</script></savegame>'
        )
        for xml in (document(strings, cue), refs_last):
            with self.subTest(xml=xml), patch.object(marker_evidence, '_parse', wraps=marker_evidence._parse) as parse:
                self.assertTrue(self.check(xml))
                self.assertEqual(parse.call_count, 1)

    def test_reference_cap_switches_to_the_same_exact_lookup(self):
        extra = '<ref id="91"/><ref id="17"/><ref id="17"/><ref id="' + '1' * 33 + '"/>'
        for cap, duplicate, status, passes in ((3, False, True, 1), (2, False, True, 2),
                                                (3, True, False, 1), (2, True, False, 2)):
            strings = STRING_GROUP.replace('</refs>', extra + ('<ref id="42"/>' if duplicate else '') + '</refs>')
            with (self.subTest(cap=cap, duplicate=duplicate),
                  patch.object(marker_evidence, '_MAX_REFERENCE_IDS', cap),
                  patch.object(marker_evidence, '_parse', wraps=marker_evidence._parse) as parse):
                if status:
                    self.assertTrue(self.check(document(strings)))
                else:
                    with self.assertRaisesRegex(MarkerEvidenceError, 'mismatched marker string'):
                        self.check(document(strings))
                self.assertEqual(parse.call_count, passes)

    def test_hash_and_malformed_xml_are_rejected(self):
        with self.assertRaises(MarkerEvidenceError):
            self.check(document()[:-5])
        self.path.write_bytes(gzip.compress(document().encode()))
        cp = Checkpoint('checkpoint-' + 'b' * 32, self.path, 'c' * 64, self.path.stat().st_size)
        with self.assertRaises(MarkerEvidenceError):
            verify_saved_marker(cp, MARKER, script_name='X4AuthorityCheckpointMarker', cue_name='MarkerState')


if __name__ == '__main__':
    unittest.main()
