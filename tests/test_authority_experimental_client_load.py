"""Synthetic callback assertions and disposable files; no X4 or transport."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from authority.experimental_client_load import ExperimentalClientLoad
from authority.load_messages import Offer, decode_receipt, encode_receipt


class ClientLoadTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.path = self.root / 'save_009.xml.gz'
        self.path.write_bytes(b'synthetic checkpoint bytes')
        self.attempt = 'a' * 32; self.marker = 'b' * 32
        self.offer = Offer(world_epoch='epoch-1', connection_handle=1, baseline_id=2, load_generation=3,
                           attempt_id=self.attempt, checkpoint_id='checkpoint-'+'c'*32,
                           sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
                           size=self.path.stat().st_size, name='j1.xml.gz')
        self.client = ExperimentalClientLoad(enabled=True, offer=self.offer, expected_marker=self.marker)

    def requested(self):
        self.client.verify_staged(self.path)
        self.client.observe_load_requested(self.attempt)

    def loaded(self):
        self.requested(); self.client.observe_loaded_marker(self.attempt, self.marker)

    def test_complete_claim_is_self_report_and_single_use(self):
        self.loaded(); self.client.observe_pause(self.attempt, True)
        claim = self.client.make_claim(self.attempt)
        self.assertEqual(decode_receipt(encode_receipt(claim.receipt)), claim.receipt)
        self.assertEqual(claim.receipt.attempt_id, self.attempt)
        self.assertEqual(claim.receipt.sha256, self.offer.sha256)
        self.assertEqual(claim.marker, self.marker)
        self.assertIs(claim.load_event_observed, True); self.assertIs(claim.paused_observed, True)
        with self.assertRaises(ValueError): self.client.make_claim(self.attempt)

    def test_requires_ordered_attempt_bound_events(self):
        with self.assertRaises(ValueError): self.client.observe_loaded_marker(self.attempt, self.marker)
        with self.assertRaises(ValueError): self.client.verify_staged(self.path)
        client = ExperimentalClientLoad(enabled=True, offer=self.offer, expected_marker=self.marker)
        client.verify_staged(self.path)
        with self.assertRaises(ValueError): client.observe_load_requested('d' * 32)
        with self.assertRaises(ValueError): client.observe_load_requested(self.attempt)

    def test_wrong_marker_or_pause_are_terminal(self):
        self.requested()
        with self.assertRaises(ValueError): self.client.observe_loaded_marker(self.attempt, 'd' * 32)
        with self.assertRaises(ValueError): self.client.observe_loaded_marker(self.attempt, self.marker)
        client = ExperimentalClientLoad(enabled=True, offer=self.offer, expected_marker=self.marker)
        client.verify_staged(self.path); client.observe_load_requested(self.attempt)
        client.observe_loaded_marker(self.attempt, self.marker)
        with self.assertRaises(ValueError): client.observe_pause(self.attempt, 1)
        with self.assertRaises(ValueError): client.make_claim(self.attempt)

    def test_mismatched_and_changed_checkpoint_refused(self):
        self.path.write_bytes(b'not the offered bytes')
        with self.assertRaises(ValueError): self.client.verify_staged(self.path)
        self.path.write_bytes(b'synthetic checkpoint bytes')
        client = ExperimentalClientLoad(enabled=True, offer=self.offer, expected_marker=self.marker)
        client.verify_staged(self.path); client.observe_load_requested(self.attempt)
        client.observe_loaded_marker(self.attempt, self.marker); client.observe_pause(self.attempt, True)
        self.path.write_bytes(b'synthetic checkpoint BYTES')
        with self.assertRaises(ValueError): client.make_claim(self.attempt)
        with self.assertRaises(ValueError): client.make_claim(self.attempt)

    def test_replaced_file_and_symlink_refused(self):
        self.loaded(); self.client.observe_pause(self.attempt, True)
        replacement = self.root / 'replacement'; replacement.write_bytes(self.path.read_bytes())
        replacement.replace(self.path)
        with self.assertRaises(ValueError): self.client.make_claim(self.attempt)
        link = self.root / 'link'
        try:
            link.symlink_to(self.path)
        except OSError as error:
            if getattr(error, 'winerror', None) != 1314:
                raise
            self.skipTest('creating symlinks needs Developer Mode or admin rights on Windows')
        client = ExperimentalClientLoad(enabled=True, offer=self.offer, expected_marker=self.marker)
        with self.assertRaises(ValueError): client.verify_staged(link)

    def test_close_fences_and_disabled_is_refused(self):
        with self.assertRaises(ValueError):
            ExperimentalClientLoad(enabled=False, offer=self.offer, expected_marker=self.marker)
        with self.assertRaises(ValueError):
            ExperimentalClientLoad(enabled=True, offer=self.offer, expected_marker='unknown')
        self.loaded(); self.client.close()
        with self.assertRaises(ValueError): self.client.observe_pause(self.attempt, True)


if __name__ == '__main__': unittest.main()
