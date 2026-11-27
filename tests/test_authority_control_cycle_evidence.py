import gzip
from pathlib import Path
import tempfile
import unittest
from decimal import Decimal
from unittest import mock

from tools.dev import authority_control_cycle_evidence as evidence
from authority.checkpoints import CheckpointStore


def xml(name='cycle-one', time='1.25', prefix='', suffix=''):
    return (f'<savegame>{prefix}<info><save name="{name}"/>'
            f'<game time="{time}"/></info>{suffix}</savegame>').encode()


class CycleEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'store').mkdir()
        self.store = CheckpointStore(root / 'store')
        self.source = root / 'source.gz'

    def publish(self, data):
        self.source.write_bytes(gzip.compress(data))
        return self.store.publish(self.source)

    def check(self, checkpoint, name='cycle-one', previous=None):
        return evidence.read_cycle_evidence(self.store, checkpoint.checkpoint_id, name,
                                            previous_game_time=previous)

    def test_real_store_revalidates_and_reads_full_metadata(self):
        checkpoint = self.publish(xml(time='12.00000000000000000001', suffix='<world><ship/></world>'))
        result = self.check(checkpoint, previous='12.00000000000000000000')
        self.assertEqual(result.game_time, Decimal('12.00000000000000000001'))
        self.assertEqual(result.sha256, checkpoint.sha256)
        self.assertEqual(result.checkpoint_id, checkpoint.checkpoint_id)
        self.assertEqual(result.size, checkpoint.size)
        self.assertEqual(result.save_name, 'cycle-one')
        with self.assertRaises(evidence.EvidenceError):
            self.check(checkpoint, previous='12.00000000000000000001')
        with self.assertRaises(evidence.EvidenceError):
            self.check(checkpoint, 'wrong')

    def test_missing_duplicate_and_wrong_paths_fail(self):
        cases = [
            b'<savegame/>',
            b'<savegame><info><save name="cycle-one"/></info></savegame>',
            b'<savegame><info><game time="1"/></info></savegame>',
            b'<savegame><info><save name="cycle-one"/><save name="cycle-one"/><game time="1"/></info></savegame>',
            b'<savegame><info><save name="cycle-one"/><game time="1"/><game time="1"/></info></savegame>',
            xml(suffix='<info/>'),
            b'<savegame><else><info><save name="cycle-one"/><game time="1"/></info></else></savegame>',
            b'<savegame><info><save/><game time="1"/></info></savegame>',
        ]
        for data in cases:
            with self.subTest(data=data):
                checkpoint = self.publish(data)
                with self.assertRaises(evidence.EvidenceError):
                    self.check(checkpoint)

    def test_late_duplicate_metadata_fails(self):
        world = '<world>' + 'x' * (evidence.CHUNK * 2) + '</world>'
        leading_metadata = xml()
        for tag, data in (
            ('info', xml(suffix=world + '<info/>')),
            ('save', leading_metadata.replace(b'</info>', world.encode() + b'<save name="cycle-one"/></info>')),
            ('game', leading_metadata.replace(b'</info>', world.encode() + b'<game time="1.25"/></info>')),
        ):
            with self.subTest(tag=tag):
                checkpoint = self.publish(data)
                with self.assertRaisesRegex(evidence.EvidenceError, 'duplicate save identity metadata'):
                    self.check(checkpoint)

    def test_invalid_and_nonadvancing_times_fail(self):
        for value in ('', '-1', 'NaN', 'Infinity', '1e99999999999999999999999999999', ' 1', '1x'):
            with self.subTest(time=value):
                checkpoint = self.publish(xml(time=value))
                with self.assertRaises(evidence.EvidenceError):
                    self.check(checkpoint)
        checkpoint = self.publish(xml(time='0'))
        self.assertEqual(self.check(checkpoint).game_time, 0)
        for previous in ('NaN', '-1', True, '0', '1'):
            with self.subTest(previous=previous), self.assertRaises(evidence.EvidenceError):
                self.check(checkpoint, previous=previous)

    def test_rechecks_digest_after_get_and_rejects_doctype(self):
        checkpoint = self.publish(xml())
        class Changed:
            def get(_, checkpoint_id):
                valid = self.store.get(checkpoint_id)
                valid.path.write_bytes(gzip.compress(xml(time='2')))
                return valid
        with self.assertRaises(evidence.EvidenceError):
            evidence.read_cycle_evidence(Changed(), checkpoint.checkpoint_id, 'cycle-one')
        checkpoint = self.publish(xml())
        checkpoint.path.write_bytes(gzip.compress(b'<!DOCTYPE savegame><savegame/>'))
        with self.assertRaises(evidence.EvidenceError):
            self.check(checkpoint)
        # Bypass store validation only to verify this parser rejects DOCTYPE itself.
        data = gzip.compress(b'<!DOCTYPE savegame><savegame/>')
        checkpoint.path.write_bytes(data)
        import hashlib
        fake = type('Checkpoint', (), dict(path=checkpoint.path, sha256=hashlib.sha256(data).hexdigest(),
                                             size=len(data), checkpoint_id=checkpoint.checkpoint_id))()
        store = type('Store', (), {'get': lambda _, _id: fake})()
        with self.assertRaises(evidence.EvidenceError):
            evidence.read_cycle_evidence(store, checkpoint.checkpoint_id, 'cycle-one')

    def test_invalid_gzip_xml_and_oversize_token_fail(self):
        checkpoint = self.publish(xml())
        # A bad CRC is rejected even if get() is replaced by a faulty test double.
        bad = gzip.compress(xml())[:-4] + b'bad!'
        checkpoint.path.write_bytes(bad)
        import hashlib
        fake = type('Checkpoint', (), dict(path=checkpoint.path, sha256=hashlib.sha256(bad).hexdigest(),
                                             size=len(bad), checkpoint_id=checkpoint.checkpoint_id))()
        store = type('Store', (), {'get': lambda _, _id: fake})()
        with self.assertRaises(evidence.EvidenceError):
            evidence.read_cycle_evidence(store, checkpoint.checkpoint_id, 'cycle-one')
        data = xml(suffix='<tag value="' + 'x' * (evidence.CHUNK * 3) + '"/>')
        checkpoint = self.publish(data)
        with mock.patch.object(evidence, 'MAX_TOKEN', evidence.CHUNK), self.assertRaises(evidence.EvidenceError):
            self.check(checkpoint)

    def test_bounded_decompression_and_depth(self):
        checkpoint = self.publish(xml(suffix='<large>' + ('x' * 1000) + '</large>'))
        with mock.patch.object(evidence, 'MAX_XML', 256), self.assertRaises(evidence.EvidenceError):
            self.check(checkpoint)
        checkpoint = self.publish(xml(suffix='<a><a><a/></a></a>'))
        with mock.patch.object(evidence, 'MAX_DEPTH', 3), self.assertRaises(evidence.EvidenceError):
            self.check(checkpoint)


if __name__ == '__main__':
    unittest.main()
