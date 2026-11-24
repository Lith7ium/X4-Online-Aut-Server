"""A synthetic complete recovery seed and rejection checks."""
import gzip
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools/dev'), str(ROOT / 'src')]
import authority_portable_seed as seed
from authority.checkpoints import CheckpointStore
from authority.registry_availability_evidence import verify_join_snapshot
from authority_recovered_registry_snapshot import receipt
from test_authority_registry_availability_evidence import RUN, build_xml


class PortableSeedTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'source'
        self.archive = self.root / 'seed.zip'
        store_path = self.source / 'checkpoints'
        store_path.mkdir(parents=True)
        self.store = CheckpointStore(store_path)
        md = self.source / seed.MD
        md.mkdir(parents=True)
        for name in seed.SCRIPTS:
            source = (ROOT / 'src/authority/md/checkpoint_marker.xml' if name == 'authority_checkpoint_marker.xml'
                      else ROOT / 'tools/dev' / name)
            shutil.copyfile(source, md / name)
        content = self.source / seed.CONTENT
        content.parent.mkdir(parents=True)
        content.write_bytes(b'<?xml version="1.0"?><content><extension id="x4_lan_lab" enabled="true" /></content>\n')
        marker_xml = (b'<script name="X4AuthorityCheckpointMarker"><cue name="MarkerState"><vars>'
                      b'<value name="$marker" type="string" value="11"/><value name="$loaded" type="integer"/>'
                      b'</vars></cue></script>')
        save = self.root / 'native-save.gz'
        save.write_bytes(gzip.compress(build_xml().replace(b'</md>', marker_xml + b'</md>')))
        self.store.publish(save, marker=RUN)
        self.checkpoint = self.store.publish(save, marker=RUN)
        self.store.confirm(self.checkpoint)
        evidence = verify_join_snapshot(self.checkpoint)
        self.result = {'supervisor_version': 1, 'success': True, 'final_world_state': 'paused',
                       'owned_processes_remaining': [], 'final_checkpoint_id': self.checkpoint.checkpoint_id,
                       'final_checkpoint_sha256': self.checkpoint.sha256, 'unneeded_private_data': 'omit',
                       'registry_snapshot': {'required': True, 'initial_inspect_verified': True,
                                             'final_receipt': receipt(self.checkpoint, evidence)}}
        (self.source / 'result.json').write_text(json.dumps(self.result))
        (self.source / 'private.log').write_text('omit')

    def test_format3_roundtrip_omits_extra_files_and_result_fields(self):
        seed.export(self.source, self.archive)
        self.assertEqual(self.archive.stat().st_mode & 0o777, 0o600)
        with ZipFile(self.archive) as package:
            self.assertEqual(len(package.namelist()), 11)
            self.assertNotIn(b'unneeded_private_data', package.read('result.json'))
            self.assertNotIn('private.log', package.namelist())
        destination = self.root / 'restored'
        restored = seed.restore(self.archive, destination)
        self.assertEqual(restored.checkpoint_id, self.checkpoint.checkpoint_id)
        self.assertEqual(restored.sha256, self.checkpoint.sha256)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
        self.assertEqual(len(list((destination / 'checkpoints').glob('checkpoint-*'))), 1)
        with self.assertRaises(FileExistsError):
            seed.restore(self.archive, destination)
        self.assertTrue(restored.path.is_file())

    def test_source_guard_rejects_changed_receipt(self):
        self.result['registry_snapshot']['final_receipt']['mapping_sha256'] = 'f' * 64
        (self.source / 'result.json').write_text(json.dumps(self.result))
        with self.assertRaisesRegex(ValueError, 'matching verified final registry receipt'):
            seed.export(self.source, self.archive)
        self.assertFalse(self.archive.exists())

    def test_archive_rejects_extra_entries_and_hash_mismatch(self):
        seed.export(self.source, self.archive)
        with ZipFile(self.archive) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        destination = self.root / 'restored'
        for injected in ({'../extra': b'bad'}, {seed.CONTENT: b'x' * len(files[seed.CONTENT])}):
            with self.subTest(injected=injected.keys()):
                bad = self.root / 'bad.zip'
                with ZipFile(bad, 'w') as archive:
                    for name, data in (files | injected).items():
                        archive.writestr(name, data)
                with self.assertRaises(ValueError):
                    seed.restore(bad, destination)
                self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main()
