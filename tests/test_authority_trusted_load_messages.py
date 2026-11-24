"""Offline marked-offer/claim codec checks; not an authenticated transport."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from authority.experimental_client_load import HonestClientLoadClaim
from authority.load_messages import Offer, Receipt
from authority.trusted_load_messages import (decode_bound_offer, decode_load_claim,
    encode_bound_offer, encode_load_claim)
from authority.trusted_marker_load_session import MarkerBoundOffer

BASE = dict(world_epoch='world1', connection_handle=1, baseline_id=2, load_generation=3,
            attempt_id='a'*32, checkpoint_id='checkpoint-'+'b'*32, sha256='c'*64, size=4096)


class TrustedLoadMessagesTest(unittest.TestCase):
    def setUp(self):
        self.bound = MarkerBoundOffer(Offer(**BASE, name='j1.xml.gz'), 'd'*32)
        self.claim = HonestClientLoadClaim(Receipt(**BASE), 'd'*32, True, True)

    def test_motion_claim_uses_only_distinct_opt_in_envelope_kind(self):
        old = encode_load_claim(self.claim)
        motion = encode_load_claim(self.claim, ship_motion=True)
        self.assertEqual(motion, old.replace(b'"marked_claim"', b'"marked_motion_claim"'))
        self.assertEqual(decode_load_claim(motion, ship_motion=True), self.claim)
        with self.assertRaises(ValueError):
            decode_load_claim(motion)
        with self.assertRaises(ValueError):
            decode_load_claim(old, ship_motion=True)

    def test_round_trip(self):
        self.assertEqual(decode_bound_offer(encode_bound_offer(self.bound)), self.bound)
        self.assertEqual(decode_load_claim(encode_load_claim(self.claim)), self.claim)

    def test_malformed_envelopes_and_nested_payload_rejected(self):
        for value, encode, decode in ((self.bound, encode_bound_offer, decode_bound_offer),
                                      (self.claim, encode_load_claim, decode_load_claim)):
            original = json.loads(encode(value))
            cases = [dict(original, marker='x'*32), dict(original, version=True),
                     dict(original, kind='other'), dict(original, extra=1)]
            for field in original:
                missing = dict(original); del missing[field]; cases.append(missing)
            bad_nested = dict(original); bad_nested['payload'] = dict(original['payload'], size=True)
            cases.append(bad_nested)
            for obj in cases:
                with self.subTest(obj=obj), self.assertRaises(ValueError):
                    decode(json.dumps(obj).encode())
            raw = encode(value)
            for raw_bad in (raw[:-1]+b',"kind":"duplicate"}', b'\xff', b'{}', b' '*4097,
                            raw.replace(b'"size":4096', b'"size":NaN')):
                with self.subTest(raw=raw_bad[:60]), self.assertRaises(ValueError): decode(raw_bad)
        raw = encode_load_claim(self.claim)
        for key in ('load_event_observed', 'paused_observed'):
            obj = json.loads(raw); obj[key] = 1
            with self.assertRaises(ValueError): decode_load_claim(json.dumps(obj).encode())

    def test_reject_wrong_type_and_unconfirmed_claim(self):
        with self.assertRaises(ValueError): encode_bound_offer(self.claim)
        with self.assertRaises(ValueError): encode_load_claim(self.bound)
        with self.assertRaises(ValueError):
            encode_load_claim(HonestClientLoadClaim(self.claim.receipt, 'd'*32, True, False))
        with self.assertRaises(ValueError): decode_bound_offer(encode_load_claim(self.claim))
        with self.assertRaises(ValueError): decode_load_claim(encode_bound_offer(self.bound))


if __name__ == '__main__': unittest.main()
