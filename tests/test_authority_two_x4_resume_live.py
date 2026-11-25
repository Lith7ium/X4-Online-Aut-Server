"""Synthetic loopback checks for the diagnostic participant adapter, not X4."""
import hashlib
from pathlib import Path
import socket
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools/dev'))
import authority_two_x4_resume_live as experiment
from authority.client_seat_overlay import company_purchase_config
from authority.company_purchase_source import CompanyPurchaseSource


class ParticipantTest(unittest.TestCase):
    def setUp(self):
        self.participant = experiment.Participant()
        self.peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.peer.bind(('127.0.0.1', 0)); self.peer.settimeout(2)
        self.participant.epoch = 'a'*32
        self.participant.peer = self.peer.getsockname()
        self.participant.handle = 4

    def tearDown(self):
        self.peer.close(); self.participant.sock.close()

    def test_native_receipt_envelope_and_participant_set(self):
        def synthetic_peer():
            data, address = self.peer.recvfrom(769)
            command = data.decode('ascii').split('|')
            self.assertEqual(command[-1], 'resume')
            self.peer.sendto(('RECEIPT|'+'|'.join(command[1:])+'|confirmed').encode(), address)
        thread = threading.Thread(target=synthetic_peer)
        thread.start()
        self.assertTrue(self.participant.resume((4,)))
        thread.join(3); self.assertFalse(thread.is_alive())
        with self.assertRaises(ValueError): self.participant.pause((4, 5))

    def test_unexpected_receipt_fences_later_commands(self):
        def synthetic_peer():
            _, address = self.peer.recvfrom(769)
            self.peer.sendto(b'bad', address)
        thread = threading.Thread(target=synthetic_peer)
        thread.start()
        with self.assertRaises(RuntimeError): self.participant.pause((4,))
        thread.join(3)
        with self.assertRaises(RuntimeError): self.participant.resume((4,))

    def test_existing_output_is_never_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'client/case/extensions/x4_lan_lab/ui/config.lua'
            path.parent.mkdir(parents=True)
            path.write_text('original')
            with self.assertRaises(ValueError): experiment.run(SimpleNamespace(out=Path(directory)))
            self.assertEqual(path.read_text(), 'original')

    def write_owned_config(self, path, purchase_settings):
        path.write_text('return {authority_participant_lease="' + self.participant.lease
                        + '"' + purchase_settings + ',keep=1}\n', encoding='ascii')
        identity = path.stat()
        self.participant.config = path
        self.participant.config_identity = (identity.st_dev, identity.st_ino)

    def test_redacts_all_staged_purchase_options(self):
        for market, sales in ((False, False), (True, False), (True, True)):
            for undock in (False, True):
                with self.subTest(market=market, sales=sales, undock=undock):
                    self.check_staged_purchase_redaction(market, sales, undock)

    def check_staged_purchase_redaction(self, market, sales, undock):
        with tempfile.TemporaryDirectory() as directory:
            source = CompanyPurchaseSource(trade_view=market, sales=sales, undock=undock)
            try:
                purchase = company_purchase_config(source, 7354, {'epoch': 'a' * 32})
            finally:
                source.close()
            path = Path(directory) / 'config.lua'
            self.write_owned_config(path, purchase)
            self.participant.redact()
            self.assertEqual(path.read_text(), 'return {authority_participant_lease="redacted",keep=1}\n')

    def test_malformed_or_duplicate_purchase_settings_leave_config_untouched(self):
        base = ',authority_company_purchase={epoch="' + 'a' * 32 + '",index=42,port=3100,lease="' + 'b' * 32 + '"'
        undock_nonce, undock_request = ',undock_nonce="' + 'c' * 32 + '"', ',undock_request="' + 'd' * 32 + '"'
        invalid = {
            'false option': base + ',trade_view=false}',
            'duplicate option': base + ',trade_view=true,trade_view=true}',
            'sales without market': base + ',sales=true}',
            'false sales option': base + ',trade_view=true,sales=false}',
            'duplicate sales option': base + ',trade_view=true,sales=true,sales=true}',
            'sales after undock fields': base + ',trade_view=true' + undock_nonce + undock_request + ',sales=true}',
            'unpaired nonce': base + undock_nonce + '}',
            'reverse undock fields': base + undock_request + undock_nonce + '}',
            'view after undock fields': base + undock_nonce + undock_request + ',trade_view=true}',
            'duplicate table': base + ',trade_view=true}' + base + undock_nonce + undock_request + '}',
            'trailing token': base + '}junk',
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.lua'
            for reason, text in invalid.items():
                with self.subTest(reason=reason):
                    self.write_owned_config(path, text)
                    digest = hashlib.sha256(path.read_bytes()).digest()
                    with self.assertRaisesRegex(ValueError, '^unexpected Company purchase lease configuration$'):
                        self.participant.redact()
                    self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), digest)

    def test_redacts_fleet_lease_after_shutdown(self):
        fleet = ',authority_company_fleet={epoch="' + 'a' * 32 + '",port=3100,lease="' + 'b' * 32 + '"}'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.lua'
            for settings in (fleet, fleet[:-1] + ',version=2}'):
                self.write_owned_config(path, settings)
                self.participant.redact()
                self.assertEqual(path.read_text(), 'return {authority_participant_lease="redacted",keep=1}\n')
            self.write_owned_config(path, fleet + fleet)
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'unexpected Company fleet order lease configuration'):
                self.participant.redact()
            self.assertEqual(path.read_bytes(), before)

    def test_redaction_rejects_replaced_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.lua'
            path.write_text('original')
            self.participant.config = path
            self.participant.config_identity = (0, 0)
            with self.assertRaises(ValueError): self.participant.redact()
            self.assertEqual(path.read_text(), 'original')

if __name__ == '__main__': unittest.main()
