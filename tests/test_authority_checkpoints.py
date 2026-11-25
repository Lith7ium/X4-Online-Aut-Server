"""Synthetic, disposable-file tests; these do not run X4 or load an actual save."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from authority.checkpoints import CheckpointError, CheckpointStore


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store_dir = self.root / 'store'
        self.store_dir.mkdir()
        self.source = self.root / 'incoming.gz'
        self.store = CheckpointStore(self.store_dir)

    def write_save(self, text=b'<savegame><info seed="123"/></savegame>'):
        self.source.write_bytes(gzip.compress(text))

    def test_publish_restart_and_verify_previous_generation(self):
        self.write_save()
        first = self.store.publish(self.source)
        self.write_save(b'<savegame><info seed="456"/></savegame>')
        second = self.store.publish(self.source)
        restarted = CheckpointStore(self.store_dir)
        self.assertEqual(restarted.latest(), second)
        self.assertEqual(restarted.get(first.checkpoint_id), first)
        self.assertEqual(second.sha256, hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertNotEqual(first.sha256, second.sha256)

    def test_confirmed_pointer_survives_a_new_unconfirmed_publication(self):
        self.write_save()
        first = self.store.publish(self.source)
        self.store.confirm(first)
        self.write_save(b'<savegame><info seed="456"/></savegame>')
        second = self.store.publish(self.source)
        restarted = CheckpointStore(self.store_dir)
        self.assertEqual(restarted.latest(), second)
        self.assertEqual(restarted.latest_confirmed(), first)
        with self.assertRaises(CheckpointError):
            restarted.confirm(first)
        self.assertEqual(restarted.latest_confirmed(), first)
        restarted.confirm(second)
        self.assertEqual(CheckpointStore(self.store_dir).latest_confirmed(), second)

    def test_marked_confirmation_survives_restart_and_ignores_newer_unconfirmed(self):
        self.write_save()
        first = self.store.publish(self.source, marker='a' * 32)
        self.store.confirm(first)
        self.write_save(b'<savegame><newer/></savegame>')
        second = self.store.publish(self.source, marker='b' * 32)
        restarted = CheckpointStore(self.store_dir)
        self.assertEqual(restarted.latest(), second)
        self.assertEqual(restarted.latest_marked_confirmed(), first)
        self.assertEqual(restarted.latest_marked_confirmed().marker, 'a' * 32)
        restarted.confirm(second)
        self.assertEqual(CheckpointStore(self.store_dir).latest_marked_confirmed().marker, 'b' * 32)

    def test_marker_recovery_rejects_legacy_invalid_and_changed_manifest(self):
        self.write_save()
        legacy = self.store.publish(self.source)
        self.store.confirm(legacy)
        with self.assertRaises(CheckpointError):
            self.store.latest_marked_confirmed()
        for marker in ('A' * 32, 'a' * 31, '', 42):
            with self.subTest(marker=marker), self.assertRaises(CheckpointError):
                self.store.publish(self.source, marker=marker)
        checkpoint = self.store.publish(self.source, marker='a' * 32)
        self.store.confirm(checkpoint)
        manifest = checkpoint.path.parent / 'manifest.json'
        data = json.loads(manifest.read_bytes())
        data['marker'] = 'b' * 32
        manifest.write_text(json.dumps(data))
        with self.assertRaises(CheckpointError):
            CheckpointStore(self.store_dir).latest_marked_confirmed()

    def test_confirmation_replace_failure_keeps_previous_pointer(self):
        self.write_save()
        first = self.store.publish(self.source)
        self.store.confirm(first)
        self.write_save(b'<savegame><different/></savegame>')
        second = self.store.publish(self.source)
        with mock.patch('authority.checkpoints.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.store.confirm(second)
        self.assertEqual(CheckpointStore(self.store_dir).latest_confirmed(), first)
        self.assertEqual(self.store.latest(), second)
        self.assertFalse(list(self.store_dir.glob('.confirmed-*')))

    def test_confirmed_pointer_revalidates_and_never_falls_back(self):
        self.write_save()
        first = self.store.publish(self.source)
        with self.assertRaises(FileNotFoundError):
            self.store.latest_confirmed()
        self.store.confirm(first)
        self.write_save(b'<savegame><different/></savegame>')
        self.store.publish(self.source)
        first.path.write_bytes(b'corrupt')
        with self.assertRaises(CheckpointError):
            self.store.latest_confirmed()

    def test_invalid_gzip_or_xml_never_replaces_previous(self):
        self.write_save()
        previous = self.store.publish(self.source)
        valid = self.source.read_bytes()
        cases = [b'', b'not gzip', valid[:-4], valid[:-8] + b'badbytes',
                 gzip.compress(b'<savegame><unclosed></savegame>'),
                 gzip.compress(b'<wrong/>'),
                 gzip.compress(b'<!DOCTYPE savegame [<!ENTITY x "bad">]><savegame>&x;</savegame>')]
        for invalid in cases:
            with self.subTest(data=invalid[:20]):
                self.source.write_bytes(invalid)
                with self.assertRaises(CheckpointError):
                    self.store.publish(self.source)
                self.assertEqual(CheckpointStore(self.store_dir).latest(), previous)

    def test_failed_atomic_pointer_publish_keeps_previous_and_ignores_orphan(self):
        self.write_save()
        previous = self.store.publish(self.source)
        self.write_save(b'<savegame><different/></savegame>')
        with mock.patch('authority.checkpoints.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.store.publish(self.source)
        self.assertEqual(CheckpointStore(self.store_dir).latest(), previous)
        self.assertEqual(len(list(self.store_dir.glob('checkpoint-*'))), 2)
        self.assertFalse(list(self.store_dir.glob('.staging-*')))
        self.assertFalse(list(self.store_dir.glob('.current-*')))

    def test_publish_sync_failure_after_replace_can_leave_new_current_visible(self):
        self.write_save()
        previous = self.store.publish(self.source)
        self.store.confirm(previous)
        self.write_save(b'<savegame><different/></savegame>')
        from authority import checkpoints
        real_sync_dir = checkpoints._sync_dir
        calls = 0

        def fail_after_pointer_replace(path):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError('directory sync failed')
            real_sync_dir(path)

        with mock.patch.object(checkpoints, '_sync_dir', side_effect=fail_after_pointer_replace):
            with self.assertRaisesRegex(OSError, 'directory sync failed'):
                self.store.publish(self.source)
        self.assertEqual(calls, 3)
        current = self.store.latest()
        self.assertNotEqual(current, previous)
        self.assertEqual(current.sha256, hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(self.store.latest_confirmed(), previous)

    @unittest.skipUnless(hasattr(os, 'mkfifo') and getattr(os, 'O_NONBLOCK', 0),
                         'FIFO and nonblocking open required')
    def test_fifo_inputs_are_rejected_without_blocking(self):
        # Separate processes enforce a deadline even if a regression blocks in open().
        script = r'''
import gzip
import os
from pathlib import Path
import sys
import tempfile
from unittest import mock

sys.path.insert(0, sys.argv[1])
from authority.checkpoints import CheckpointError, CheckpointStore, _file_digest, _validate_save

case = sys.argv[2]
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    store_dir = root / 'store'
    store_dir.mkdir()
    source = root / 'incoming.gz'
    source.write_bytes(gzip.compress(b'<savegame/>'))
    store = CheckpointStore(store_dir)
    checkpoint = store.publish(source)
    store.confirm(checkpoint)
    targets = {
        'current': store_dir / 'CURRENT',
        'confirmed': store_dir / 'CONFIRMED',
        'manifest': checkpoint.path.parent / 'manifest.json',
        'digest': checkpoint.path,
        'validate': checkpoint.path,
        'source': source,
    }
    target = targets[case]
    target.unlink()
    os.mkfifo(target)
    try:
        if case == 'current':
            store.latest()
        elif case == 'confirmed':
            store.latest_confirmed()
        elif case == 'manifest':
            store.get(checkpoint.checkpoint_id)
        elif case == 'digest':
            _file_digest(target)
        elif case == 'validate':
            _validate_save(target)
        elif case == 'source':
            # Model replacement after the preliminary path type check.
            with mock.patch.object(Path, 'is_file', return_value=True):
                store.publish(source)
    except CheckpointError:
        pass
    else:
        raise AssertionError('FIFO was accepted')
'''
        source_dir = Path(__file__).resolve().parents[1] / 'src'
        for case in ('current', 'confirmed', 'manifest', 'digest', 'validate', 'source'):
            with self.subTest(case=case):
                result = subprocess.run(
                    [sys.executable, '-c', script, str(source_dir), case],
                    capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_deflate_preserves_current_and_confirmed_checkpoint(self):
        self.write_save()
        previous = self.store.publish(self.source)
        self.store.confirm(previous)
        current_pointer = (self.store_dir / 'CURRENT').read_bytes()
        confirmed_pointer = (self.store_dir / 'CONFIRMED').read_bytes()
        # Valid gzip header, followed by reserved DEFLATE block type 3.
        self.source.write_bytes(b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff' + b'\x07' + b'\x00' * 8)
        with self.assertRaisesRegex(CheckpointError, 'incomplete or invalid gzip/XML save'):
            self.store.publish(self.source)
        self.assertEqual((self.store_dir / 'CURRENT').read_bytes(), current_pointer)
        self.assertEqual((self.store_dir / 'CONFIRMED').read_bytes(), confirmed_pointer)
        self.assertEqual(self.store.latest(), previous)
        self.assertEqual(self.store.latest_confirmed(), previous)

    def test_tampering_with_content_manifest_or_pointer_fails_closed(self):
        self.write_save()
        checkpoint = self.store.publish(self.source)
        pointer = self.store_dir / 'CURRENT'
        manifest = checkpoint.path.parent / 'manifest.json'
        original_pointer = pointer.read_bytes()
        original_manifest = manifest.read_bytes()
        original_save = checkpoint.path.read_bytes()
        checkpoint.path.write_bytes(original_save[:-1] + bytes([original_save[-1] ^ 1]))
        with self.assertRaises(CheckpointError):
            self.store.latest()
        checkpoint.path.write_bytes(original_save)
        data = json.loads(original_manifest)
        data['size'] += 1
        manifest.write_text(json.dumps(data))
        with self.assertRaises(CheckpointError):
            self.store.latest()
        manifest.write_bytes(original_manifest)
        data = json.loads(original_pointer)
        data['id'] = '../outside'
        pointer.write_text(json.dumps(data))
        with self.assertRaises(CheckpointError):
            self.store.latest()
        pointer.write_bytes(original_pointer)
        self.assertEqual(self.store.latest(), checkpoint)

    def test_refuses_linked_source_inside_store_and_linked_checkpoint(self):
        self.write_save()
        checkpoint = self.store.publish(self.source)
        link = self.root / 'link.gz'
        try:
            link.symlink_to(self.source)
        except OSError as error:
            if getattr(error, 'winerror', None) != 1314:
                raise
            self.skipTest('creating symlinks needs Developer Mode or admin rights on Windows')
        with self.assertRaises(CheckpointError):
            self.store.publish(link)
        with self.assertRaises(CheckpointError):
            self.store.publish(checkpoint.path)
        save = checkpoint.path
        original = save.read_bytes()
        save.unlink()
        save.symlink_to(self.source)
        with self.assertRaises((CheckpointError, OSError)):
            self.store.latest()
        with mock.patch.object(os, 'O_NOFOLLOW', 0, create=True):
            with self.assertRaises(CheckpointError):
                self.store.latest()
        self.assertEqual(self.source.read_bytes(), original)

    def test_restart_revalidates_xml_even_when_hashes_agree(self):
        self.write_save()
        checkpoint = self.store.publish(self.source)
        invalid = gzip.compress(b'<savegame><broken></savegame>')
        checkpoint.path.write_bytes(invalid)
        manifest_path = checkpoint.path.parent / 'manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest.update(size=len(invalid), sha256=hashlib.sha256(invalid).hexdigest())
        metadata = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode('ascii')
        manifest_path.write_bytes(metadata)
        pointer_path = self.store_dir / 'CURRENT'
        pointer = json.loads(pointer_path.read_bytes())
        pointer['manifest_sha256'] = hashlib.sha256(metadata).hexdigest()
        pointer_path.write_text(json.dumps(pointer))
        with self.assertRaises(CheckpointError):
            CheckpointStore(self.store_dir).latest()


if __name__ == '__main__':
    unittest.main()
