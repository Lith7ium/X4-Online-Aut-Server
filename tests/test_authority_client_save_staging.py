"""Offline file and short-write checks; no X4 or Windows execution."""
import errno
import hashlib
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from authority.client_save_staging import _write_all, copy_offered_save
import authority.client_save_staging as staging
from authority.load_messages import Offer


class ClientSaveStagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = b'a small synthetic save'
        self.source = self.root / 'download'
        self.source.write_bytes(self.data)
        self.offer = Offer(world_epoch='epoch', connection_handle=1, baseline_id=1, load_generation=1,
                           attempt_id='a' * 32, checkpoint_id='checkpoint-' + 'b' * 32,
                           sha256=hashlib.sha256(self.data).hexdigest(), size=len(self.data), name='j1.xml.gz')

    def test_short_writes_are_completed(self):
        class PartialWriter:
            def __init__(self): self.data = bytearray()
            def write(self, data):
                count = min(3, len(data))
                self.data.extend(data[:count])
                return count
        target = PartialWriter()
        pumps = []
        _write_all(target, self.data, lambda: pumps.append(True))
        self.assertEqual(bytes(target.data), self.data)
        self.assertGreater(len(pumps), 2)

    def test_zero_write_fails(self):
        class StalledWriter:
            def write(self, data): return 0
        with self.assertRaises(OSError):
            _write_all(StalledWriter(), self.data, lambda: None)

    def test_invalid_slot_and_existing_symlink_are_untouched(self):
        with self.assertRaises(ValueError):
            copy_offered_save(self.source, self.root / 'other.xml.gz', self.offer, pump=lambda: None)
        previous = self.root / 'previous'
        previous.write_bytes(b'old data')
        target = self.root / 'save_009.xml.gz'
        try:
            target.symlink_to(previous)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links unavailable')
        with self.assertRaises(FileExistsError):
            copy_offered_save(self.source, target, self.offer, pump=lambda: None)
        self.assertEqual(previous.read_bytes(), b'old data')
        self.assertTrue(target.is_symlink())

    def test_oversized_source_stays_bounded_and_is_retained(self):
        self.source.write_bytes(self.data + b'extra')
        target = self.root / 'save_009.xml.gz'
        with self.assertRaises(ValueError):
            copy_offered_save(self.source, target, self.offer, pump=lambda: None)
        self.assertFalse(target.exists())
        self.assertEqual(self.source.read_bytes(), self.data + b'extra')

    def test_dangling_target_symlink_is_not_followed_or_replaced(self):
        missing = self.root / 'missing'
        target = self.root / 'save_009.xml.gz'
        try:
            target.symlink_to(missing)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links unavailable')
        with self.assertRaises(FileExistsError):
            copy_offered_save(self.source, target, self.offer, pump=lambda: None)
        self.assertTrue(target.is_symlink())
        self.assertFalse(missing.exists())
        self.assertEqual(self.source.read_bytes(), self.data)

    def test_streams_more_than_one_chunk(self):
        data = b'x' * (1024 * 1024 + 31)
        self.source.write_bytes(data)
        offer = replace(self.offer, size=len(data), sha256=hashlib.sha256(data).hexdigest())
        target = self.root / 'save_009.xml.gz'
        copy_offered_save(self.source, target, offer, pump=lambda: None)
        self.assertEqual(target.read_bytes(), data)

    def test_changed_source_and_nonregular_source_are_rejected(self):
        target = self.root / 'save_009.xml.gz'
        real_write = staging._write_all
        def change_source(outgoing, block, pump):
            real_write(outgoing, block, pump)
            with self.source.open('ab') as source:
                source.write(b'changed')
        with patch.object(staging, '_write_all', side_effect=change_source):
            with self.assertRaises(ValueError):
                copy_offered_save(self.source, target, self.offer, pump=lambda: None)
        self.assertFalse(target.exists())
        other_target = self.root / 'save_010.xml.gz'
        with self.assertRaises(ValueError):
            copy_offered_save(self.root, other_target, self.offer, pump=lambda: None)
        self.assertFalse(other_target.exists())

    def test_io_failure_leaves_no_slot_or_temp(self):
        for operation in ('_write_all', 'fsync'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'save_009.xml.gz'
                owner = staging if operation == '_write_all' else staging.os
                with patch.object(owner, operation, side_effect=OSError('synthetic I/O failure')):
                    with self.assertRaises(OSError):
                        copy_offered_save(self.source, target, self.offer, pump=lambda: None)
                self.assertEqual(list(Path(directory).iterdir()), [])
                self.assertEqual(self.source.read_bytes(), self.data)


    def test_slot_is_absent_during_copy_and_unrelated_temp_is_untouched(self):
        target = self.root / 'save_009.xml.gz'
        unrelated = self.root / '.save_009.xml.gz.unrelated.part'
        unrelated.write_bytes(b'other attempt')
        real_write = staging._write_all

        def observe_write(outgoing, block, pump):
            real_write(outgoing, block, pump)
            self.assertFalse(target.exists())

        with patch.object(staging, '_write_all', side_effect=observe_write):
            copy_offered_save(self.source, target, self.offer, pump=lambda: None)
        self.assertEqual(target.read_bytes(), self.data)
        self.assertEqual(unrelated.read_bytes(), b'other attempt')
        self.assertCountEqual(self.root.iterdir(), [self.source, target, unrelated])

    def test_cancellation_immediately_before_publication_leaves_no_slot(self):
        target = self.root / 'save_009.xml.gz'
        real_copy = staging._copy_verified
        copy_completed = False

        def mark_completed(*args):
            nonlocal copy_completed
            real_copy(*args)
            copy_completed = True

        def pump():
            if copy_completed:
                raise TimeoutError('cancelled before publication')

        with patch.object(staging, '_copy_verified', side_effect=mark_completed):
            with self.assertRaises(TimeoutError):
                copy_offered_save(self.source, target, self.offer, pump=pump)
        self.assertEqual(list(self.root.iterdir()), [self.source])

    def test_concurrent_winner_is_preserved_on_publication_and_fallback(self):
        real_write = staging._write_all
        for link_unavailable in (False, True):
            with self.subTest(link_unavailable=link_unavailable):
                target = self.root / ('save_010.xml.gz' if link_unavailable else 'save_009.xml.gz')

                def create_winner(outgoing, block, pump):
                    real_write(outgoing, block, pump)
                    with target.open('xb') as existing:
                        existing.write(b'winner')

                with patch.object(staging, '_write_all', side_effect=create_winner):
                    with self.assertRaises(FileExistsError):
                        if link_unavailable:
                            with patch.object(staging, '_WINDOWS', False), patch.object(
                                    staging.os, 'link', side_effect=OSError(errno.EOPNOTSUPP, 'unsupported')):
                                copy_offered_save(self.source, target, self.offer, pump=lambda: None)
                        else:
                            copy_offered_save(self.source, target, self.offer, pump=lambda: None)
                self.assertEqual(target.read_bytes(), b'winner')
                self.assertEqual(list(self.root.glob('*.part')), [])

    def test_unsupported_links_use_fenced_exclusive_copy(self):
        target = self.root / 'save_009.xml.gz'
        failed = self.root / 'save_010.xml.gz'
        real_write = staging._write_all

        def fail_only_in_visible_slot(outgoing, block, pump):
            if failed.exists():
                raise OSError('fallback write error')
            real_write(outgoing, block, pump)

        with patch.object(staging, '_WINDOWS', False), patch.object(
                staging.os, 'link', side_effect=OSError(errno.EOPNOTSUPP, 'unsupported')):
            copy_offered_save(self.source, target, self.offer, pump=lambda: None)
            with patch.object(staging, '_write_all', side_effect=fail_only_in_visible_slot):
                with self.assertRaisesRegex(OSError, 'fallback write error'):
                    copy_offered_save(self.source, failed, self.offer, pump=lambda: None)
        self.assertEqual(target.read_bytes(), self.data)
        self.assertEqual(failed.read_bytes(), b'')
        self.assertCountEqual(self.root.iterdir(), [self.source, target, failed])


if __name__ == '__main__': unittest.main()
