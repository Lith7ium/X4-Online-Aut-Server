"""Opt-in dedicated-server policy: an empty world keeps running between players.

Synthetic lifecycle and fake-engine tests only; no X4 process is run.
"""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from authority.checkpoints import CheckpointStore
from authority.save_transfer import SaveOffers
from authority.session import SessionCoordinator
from authority.world import WorldCoordinator
from test_authority_restart_integration import (FakeEngine, FakeParticipants, confirm_load, fake_tick,
                                                file_service, run_step)


class RunWhenEmptySessionTests(unittest.TestCase):
    def setUp(self):
        self.session = SessionCoordinator('world-1', run_when_empty=True)

    def finish(self, operation, success=True):
        request = self.session.pending
        self.assertIsNotNone(request)
        self.assertEqual(request.operation, operation)
        self.session.complete(request.world_epoch, request.request_id, success)
        return request

    def ready(self, handle):
        baseline = self.session.baseline_id
        generation = self.session.begin_load(handle, baseline)
        self.session.mark_ready('world-1', handle, generation, baseline)

    def start_empty(self):
        self.finish('pause')
        self.finish('save')
        self.finish('resume')
        self.assertEqual((self.session.state, self.session.players, self.session.pending), ('running', (), None))

    def test_default_policy_still_leaves_an_empty_world_paused(self):
        session = SessionCoordinator('world-1')
        self.assertFalse(session.run_when_empty)
        request = session.pending
        session.complete(request.world_epoch, request.request_id, True)
        self.assertEqual((session.state, session.pending), ('paused', None))
        self.assertFalse(session.request_checkpoint())

    def test_policy_must_be_a_boolean(self):
        with self.assertRaises(ValueError):
            SessionCoordinator('world-1', run_when_empty=1)

    def test_empty_world_is_saved_before_it_runs(self):
        self.finish('pause')
        self.assertEqual(self.session.pending.operation, 'save')
        save = self.finish('save')
        resume = self.finish('resume')
        self.assertGreater(resume.request_id, save.request_id)
        self.assertEqual(self.session.state, 'running')
        self.assertIsNone(self.session.baseline_id)

    def test_join_pauses_saves_loads_and_last_departure_saves_then_resumes(self):
        self.start_empty()
        a = self.session.join('a')
        self.finish('pause')
        joined = self.finish('save')
        self.assertEqual(self.session.state, 'paused')
        self.assertIsNone(self.session.pending)
        self.ready(a)
        self.finish('resume')
        self.session.disconnect(a)
        self.assertTrue(self.session.save_required)
        self.finish('pause')
        departed = self.finish('save')
        self.assertGreater(departed.request_id, joined.request_id)
        self.finish('resume')
        self.assertEqual((self.session.state, self.session.players), ('running', ()))

    def test_a_loading_player_who_leaves_lets_the_saved_world_resume_without_another_save(self):
        self.start_empty()
        a = self.session.join('a')
        self.finish('pause')
        self.finish('save')
        self.session.begin_load(a, self.session.baseline_id)
        self.session.disconnect(a)
        self.assertFalse(self.session.save_required)
        self.finish('resume')
        self.assertEqual(self.session.state, 'running')

    def test_checkpoint_runs_only_from_a_settled_running_world(self):
        self.finish('pause')
        self.assertFalse(self.session.request_checkpoint())  # paused: may be serving a baseline
        self.finish('save')
        self.assertFalse(self.session.request_checkpoint())  # resume pending
        self.finish('resume')
        self.assertTrue(self.session.request_checkpoint())
        self.assertFalse(self.session.request_checkpoint())  # already scheduled
        self.finish('pause')
        self.finish('save')
        self.finish('resume')
        self.assertEqual(self.session.state, 'running')

    def test_checkpoint_with_ready_players_keeps_them_ready(self):
        self.start_empty()
        a = self.session.join('a')
        self.finish('pause')
        self.finish('save')
        self.ready(a)
        self.finish('resume')
        self.assertTrue(self.session.request_checkpoint())
        self.finish('pause')
        self.finish('save')
        self.assertEqual(self.session.ready_players, ((a, 'a'),))
        self.finish('resume')
        self.assertEqual(self.session.state, 'running')

    def test_shutdown_saves_and_stays_paused(self):
        self.start_empty()
        self.session.begin_shutdown()
        self.assertFalse(self.session.run_when_empty)
        self.finish('pause')
        self.finish('save')
        self.assertEqual((self.session.state, self.session.pending), ('paused', None))
        self.assertIsNotNone(self.session.baseline_id)

    def test_shutdown_during_resume_saves_after_the_resume_completes(self):
        self.finish('pause')
        self.finish('save')
        self.session.begin_shutdown()
        self.finish('resume')
        self.finish('pause')
        self.finish('save')
        self.assertEqual((self.session.state, self.session.pending), ('paused', None))

    def test_shutdown_before_an_undispatched_resume_saves_again_conservatively(self):
        self.finish('pause')
        self.finish('save')
        self.session.begin_shutdown()
        # The owner cancels the resume it never sent; the world still ends on a fresh save.
        self.session.cancel_unstarted_resume(engine_not_started=True)
        self.finish('save')
        self.assertEqual((self.session.state, self.session.pending), ('paused', None))

    def test_failed_save_faults_instead_of_running_unsaved(self):
        self.finish('pause')
        self.finish('save', success=False)
        self.assertEqual((self.session.state, self.session.pending), ('faulted', None))
        self.assertFalse(self.session.request_checkpoint())


class RunWhenEmptyWorldTests(unittest.TestCase):
    def test_world_runs_empty_admits_a_player_and_saves_each_transition(self):
        with tempfile.TemporaryDirectory() as temporary, file_service() as service:
            root = Path(temporary)
            (root / 'store').mkdir()
            store = CheckpointStore(root / 'store')
            engine = FakeEngine(root / 'save.xml.gz', tick=1)
            world = WorldCoordinator('world-1', engine, store, SaveOffers(service, store),
                                     participants=FakeParticipants(), run_when_empty=True)
            run_step(self, world, 'pause')
            run_step(self, world, 'save')
            run_step(self, world, 'resume')
            self.assertFalse(engine.paused)

            engine.tick = 2  # the empty world advanced
            self.assertTrue(world.session.request_checkpoint())
            run_step(self, world, 'pause')
            run_step(self, world, 'save')
            self.assertEqual(fake_tick(store.latest_marked_confirmed().path.read_bytes()), 2)
            run_step(self, world, 'resume')

            engine.tick = 3
            handle = world.join('player')
            run_step(self, world, 'pause')
            run_step(self, world, 'save')
            offer = world.offer_load(handle)
            confirm_load(world, offer)
            run_step(self, world, 'resume')

            engine.tick = 4
            world.disconnect(handle)
            run_step(self, world, 'pause')
            run_step(self, world, 'save')
            self.assertEqual(fake_tick(store.latest_marked_confirmed().path.read_bytes()), 4)
            run_step(self, world, 'resume')
            self.assertFalse(engine.paused)

            engine.tick = 5
            world.session.begin_shutdown()
            run_step(self, world, 'pause')
            run_step(self, world, 'save')
            self.assertIsNone(world.step())
            self.assertTrue(engine.paused)
            self.assertEqual(fake_tick(store.latest_marked_confirmed().path.read_bytes()), 5)

    def test_default_world_never_resumes_while_empty(self):
        with tempfile.TemporaryDirectory() as temporary, file_service() as service:
            root = Path(temporary)
            (root / 'store').mkdir()
            store = CheckpointStore(root / 'store')
            engine = FakeEngine(root / 'save.xml.gz', tick=1)
            world = WorldCoordinator('world-1', engine, store, SaveOffers(service, store))
            run_step(self, world, 'pause')
            self.assertIsNone(world.step())
            self.assertTrue(engine.paused)


if __name__ == '__main__':
    unittest.main()
