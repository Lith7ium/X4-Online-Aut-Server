"""Private cockpit trial cancellation through the ordinary observer callbacks."""
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from authority import company_motion_observer
from authority.company_motion_client import CompanyMotionClient
from authority.company_motion_observer import CompanyMotionObserver

EPOCH, GENERATION, NEXT_GENERATION = 'a' * 32, 'b' * 32, 'c' * 32
OWN_INDEX, TRIAL_INDEX = 1970, 7354
ACTOR, CODE, SECTOR = '0x123456789abcdef', 'WRN-302', 'sector_a'


class CockpitTrialEndTests(unittest.TestCase):
    def make(self, trial=TRIAL_INDEX):
        messages = []
        observer = CompanyMotionObserver((EPOCH, OWN_INDEX), lambda body: messages.append(body.split('|')) or True,
                                         trial_index=trial)
        observer.bind_views(lambda _: None)
        return observer, messages

    def view(self, observer, slot='cockpit', serial=1, *, clear=False, index=TRIAL_INDEX):
        binding = ['-', '0', '-', '-'] if clear else [SECTOR, str(index), ACTOR, CODE]
        observer.receive(['VIEW', slot, str(serial), EPOCH, *binding])

    def sample(self, sequence, *, generation=GENERATION, index=TRIAL_INDEX):
        return dict(version=3, epoch=EPOCH, index=index, sequence=sequence, generation=generation,
                    sector_macro=SECTOR, source_time=1, capture_time=sequence,
                    x=1, y=2, z=3, real_velocity={'x': 4, 'y': 0, 'z': 0})

    def seen(self, observer, remote):
        observer.receive(['SEEN', *remote[1:8], ACTOR, CODE, *remote[8:16]])

    def end(self, serial=1, generation=GENERATION):
        return ['END', 'cockpit', str(serial), EPOCH, str(TRIAL_INDEX), generation, SECTOR]

    def test_link_reset_ends_pending_trial_before_any_seen(self):
        observer, messages = self.make()
        self.view(observer)
        observer.snapshot(self.sample(1))
        remote, = messages
        owned, remote_socket = socket.socketpair()
        with owned, remote_socket:
            client = CompanyMotionClient.__new__(CompanyMotionClient)
            client._connection = owned
            client._visible = {TRIAL_INDEX: (SECTOR, GENERATION)}
            client._hidden = set()
            client._on_end = observer.end
            client._stats = dict(visibility_ends=0)
            client.close()
        self.assertEqual(messages, [remote, self.end()])
        observer.end(TRIAL_INDEX, GENERATION)
        self.seen(observer, remote)
        self.assertEqual(messages, [remote, self.end()])
        self.assertEqual(observer.stats()['samples_confirmed'], 0)

    def test_transport_loss_clears_pending_but_only_world_reset_sends_end(self):
        observer, messages = self.make()
        with (patch.object(company_motion_observer, 'sys', SimpleNamespace(platform='linux')),
              patch('authority.company_motion_observer.time.monotonic', return_value=100.0) as clock):
            self.view(observer)
            clock.side_effect = (100.25, 100.5, 100.75)
            observer.snapshot(self.sample(1))
        remote, = messages
        self.assertEqual(len(remote), 17)
        self.assertEqual(remote[-1], '100.25')
        observer.transport_lost(TRIAL_INDEX, GENERATION)
        with patch('authority.company_motion_observer.time.monotonic', return_value=101):
            self.seen(observer, remote)
        self.assertEqual(observer.stats()['samples_confirmed'], 0)
        self.assertEqual(messages, [remote])
        self.view(observer)
        observer.snapshot(self.sample(2))
        self.seen(observer, messages[-1])
        self.assertEqual(observer.stats()['samples_confirmed'], 1)
        observer.reset_world()
        self.assertEqual(messages[-1], self.end())

    def test_windows_trial_uses_the_native_qpc_clock_for_playback_only(self):
        observer, messages = self.make()
        self.view(observer)
        self.view(observer, slot='map')
        with (patch.object(company_motion_observer, 'sys', SimpleNamespace(platform='win32')),
              patch('authority.company_motion_observer.time.perf_counter', return_value=15.5)):
            observer.snapshot(self.sample(1))
        cockpit, map_view = messages
        self.assertEqual(cockpit[-1], '15.5')
        self.assertEqual(len(map_view), 16)
        for row in messages:
            self.seen(observer, row)
        self.assertEqual(observer.stats()['samples_confirmed'], 2)

    def test_generation_replacement_ends_old_pending_and_preserves_current_seen(self):
        observer, messages = self.make()
        self.view(observer)
        observer.snapshot(self.sample(1))
        old, = messages
        observer.snapshot(self.sample(1, generation=NEXT_GENERATION))
        self.assertEqual([row[0] for row in messages], ['REMOTE', 'END', 'REMOTE'])
        self.assertEqual(messages[1], self.end())
        observer.end(TRIAL_INDEX, GENERATION)
        self.seen(observer, old)
        self.assertEqual(observer.stats()['samples_confirmed'], 0)
        self.seen(observer, messages[-1])
        self.assertEqual(observer.stats()['last_confirmed']['generation'], NEXT_GENERATION)
        observer.end(TRIAL_INDEX, NEXT_GENERATION)
        self.assertEqual(messages[-1], self.end(generation=NEXT_GENERATION))

    def test_cockpit_clear_change_or_expiry_discards_unconfirmed_pose(self):
        for reason in ('clear', 'serial', 'ttl'):
            with self.subTest(reason=reason), patch('authority.company_motion_observer.time.monotonic') as clock:
                clock.return_value = 0
                observer, messages = self.make()
                self.view(observer)
                observer.snapshot(self.sample(1))
                self.seen(observer, messages[-1])
                observer.snapshot(self.sample(2))
                remote = messages[-1]
                previous = observer.stats()
                if reason == 'ttl':
                    clock.return_value = 3
                    observer.expire()
                    self.view(observer)  # A fresh heartbeat may re-open read-only observation.
                else:
                    self.view(observer, serial=2, clear=reason == 'clear')
                self.assertEqual(messages[-1], self.end())
                self.seen(observer, remote)
                self.assertEqual(observer.stats(), previous)

    def test_standard_observer_and_other_views_send_no_trial_frames(self):
        for trial, slot, index in ((None, 'cockpit', TRIAL_INDEX), (TRIAL_INDEX, 'map', TRIAL_INDEX),
                                   (TRIAL_INDEX, 'cockpit', 23)):
            with self.subTest(trial=trial, slot=slot, index=index):
                observer, messages = self.make(trial)
                self.view(observer, slot, index=index)
                observer.snapshot(self.sample(1, index=index))
                self.assertEqual(len(messages[-1]), 16)
                self.seen(observer, messages[-1])
                observer.end(index, GENERATION)
                self.view(observer, slot, serial=2, clear=True)
                self.assertEqual([row[0] for row in messages], ['REMOTE'])
                self.assertEqual(observer.stats()['samples_confirmed'], 1)


if __name__ == '__main__':
    unittest.main()
