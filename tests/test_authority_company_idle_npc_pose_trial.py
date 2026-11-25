"""Offline constraints on the private one-shot idle NPC pose trial."""
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import authority_company_idle_npc_pose_trial as trial


class IdleNpcPoseTrialTests(unittest.TestCase):
    def test_private_cue_requires_completed_release_and_exact_npc_binding(self):
        root_path = trial.overlay.ROOT
        original = (root_path / 'src/authority/md/company_ship_pose.xml').read_bytes()
        self.assertIn(b'PILOT_PRESENT', original)
        pose = ET.fromstring(trial.render_pose(original, '1' * 32))
        release = ET.fromstring(trial.render_undock(
            (root_path / 'tools/dev' / trial.undock.CUE).read_bytes()))
        recorded = release.find(".//cue[@name='Readback']/actions/do_if/set_value[@name='State.$PoseRelease']")
        self.assertIsNotNone(recorded)
        self.assertIsNone(release.find(".//cue[@name='Undocked']//set_value[@name='State.$PoseRelease']"))
        guards = '\n'.join(e.get('value', '') for e in pose.iter() if 'value' in e.attrib)
        for clause in ('$index != 7354', "$args.$frame != 'cluster_01_sector001_macro'",
                       'State.$PoseRelease.$ship != $ship', 'State.$PoseRelease.$captain.isbusy',
                       '$ship.assignedaipilot != ', '$captain.assignedcontrolled != $ship',
                       '$ship.orders.count != 0', '$ship.tradeorders.count != 0',
                       "$ship.defaultorder.id != 'Wait'", '$ship.order.state == orderstate.critical',
                       'player.controlled or player.occupiedship',
                       'not @State.$TrialWrite', '$args.$x le State.$TrialBefore.$x + 50.5'):
            self.assertIn(clause, guards)
        apply = pose.find("./cues/library[@name='ApplyPose']/actions/do_if")
        self.assertEqual([action.tag for action in apply], ['set_value', 'warp', 'set_value'])
        self.assertEqual(apply[0].get('name'), 'State.$TrialWrite')
        for mutation in ('teleport_player', 'assign_pilot', 'create_order', 'cancel_order'):
            self.assertEqual(list(pose.iter(mutation)), [])

    def test_one_write_with_two_distinct_position_reads(self):
        class Channel:
            epoch = 'a' * 32
            ship_codes = {trial.INDEX: trial.SHIP}
            ship_sectors = {trial.INDEX: trial.SECTOR}

            def __init__(self):
                self.actions = []

            def begin(self, action, sample, actor='0'):
                self.actions.append((action, tuple(sample[key] for key in 'xyz'), actor))

            def wait(self):
                action = self.actions[-1][0]
                position = (10, 20, 30) if action == 'inspect' else (60, 20, 30)
                return {'actor': '0x12', 'md': position, 'ui': position, 'code': trial.SHIP}

        channel = Channel()
        with patch.object(trial.time, 'monotonic', side_effect=(0, 1, 3)):
            proof = trial.verify_one_pose(channel, 'c' * 32, lambda: None)
        self.assertEqual([row[0] for row in channel.actions], ['inspect', 'apply', 'verify', 'verify'])
        self.assertEqual(channel.actions[1], ('apply', (60, 20, 30), '0x12'))
        self.assertEqual(proof['early_error_m'], 0)
        self.assertEqual(proof['late_error_m'], 0)
