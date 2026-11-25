"""Offline source/overlay checks for the isolated authority undock developer trial."""
import tempfile
import time
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

import authority_company_undock_trial as trial


class CompanyUndockTrialTests(unittest.TestCase):
    def test_one_stock_order_behind_identity_berth_idle_and_replay_guards(self):
        tree = ET.parse(trial.ROOT / 'tools/dev' / trial.CUE)
        order, = tree.iter('create_order')
        self.assertEqual(order.attrib, dict(name='$Order', id="'Undock'", object='$Ship', immediate='true'))
        ordered = list(tree.iter())
        for refusal in ("'berth_mismatch'", "'identity_mismatch'", "'duplicate_or_attempted'"):
            branch = tree.find(f".//set_value[@exact=\"{refusal}\"]/..")
            self.assertLess(ordered.index(branch), ordered.index(order))
        guard = next(branch for branch in tree.iter('do_elseif') if '$Ship.tradeorders.count' in branch.get('value'))
        self.assertIn('$Ship.orders.count', guard.get('value'))
        self.assertLess(ordered.index(guard), ordered.index(order))
        latch, = (action for action in tree.iter('set_value')
                   if action.get('name') == 'State.$AttemptedNonce')
        self.assertEqual(latch.get('exact'), '$Nonce')
        self.assertLess(ordered.index(latch), ordered.index(order))
        for bypass in ('undock', 'cancel_order', 'spawn_docked', 'teleport_player', 'remove_docking_request'):
            self.assertEqual(list(tree.iter(bypass)), [])

    def test_success_requires_stock_event_and_later_physical_release(self):
        tree = ET.parse(trial.ROOT / 'tools/dev' / trial.CUE)
        event = tree.find(".//cue[@name='Undocked']/conditions/check_value").get('value')
        for guard in ('event.object == $Ship', 'event.param == $Station', 'event.param3 == $Bay'):
            self.assertIn(guard, event)
        receipt = tree.find(".//cue[@name='Readback']/actions/do_if")
        for guard in ('$Witness', '$SameIdentity', '$SameBerth', 'not @$Order.exists',
                      'not @$Ship.dock.exists', 'not @$Ship.assigneddock.exists',
                      '$Bay.docked.count == 0', 'not @$Bay.assignedship.exists'):
            self.assertIn(guard, receipt.get('value'))
        ready = tree.find(".//cue[@name='Readback']/conditions/check_value")
        for guard in ('not @$Ship.parkedat.exists', 'not $Ship.isinternallystored'):
            self.assertIn(guard, ready.get('value'))
            self.assertIn(guard, receipt.get('value'))
        self.assertEqual(receipt.find('set_value').get('exact'), "'undocked'")

    def test_trace_records_individual_fields_even_if_bay_event_does_not_match(self):
        tree = ET.parse(trial.ROOT / 'tools/dev' / trial.CUE)
        trace = tree.find(".//cue[@name='UndockEventTrace']")
        self.assertEqual(trace.find('conditions/event_object_undocked').attrib, {'object': '$Ship'})
        self.assertNotIn('param3', trace.find('conditions/check_value').get('value'))
        event = trace.find('actions/debug_text').get('text')
        for field in ('event.param3', 'expected_bay', 'matches'):
            self.assertIn(field, event)
        values = ' '.join(row.get('text') for row in tree.findall(".//library[@name='TraceDetachment']/actions/debug_text"))
        for field in ('$Ship.dock', '$Ship.assigneddock', '$Ship.parkedat', '$Ship.isinternallystored',
                      '$Bay.docked.indexof.{$Ship}', '$Bay.assignedship', '$Ship.order.id',
                      '$Ship.order.state', '$Order.exists'):
            self.assertIn(field, values)

    def test_private_overlay_stages_and_rejects_reinstallation(self):
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary)
            extension = case / 'extensions/x4_lan_lab'
            (extension / 'ui').mkdir(parents=True)
            (extension / 'md').mkdir()
            bridge = extension / 'ui/authority_world_bridge_probe.lua'
            bridge.write_text(' local ok,reason=bridge:update();assert(ok,reason)\n')
            manifest = extension / 'ui.xml'
            manifest.write_text('    <file name="ui/authority_world_bridge_probe.lua" />\n')
            cue, lua, _, _ = trial.install(case, 'inspect', 'e' * 32, 'a' * 32)
            self.assertEqual(cue.name, trial.CUE)
            self.assertIn('company_undock_trial_tick', bridge.read_text())
            self.assertIn('507326418098681806', lua.read_text())
            self.assertLess(manifest.read_text().index(trial.DRIVER),
                            manifest.read_text().index(bridge.name))
            with self.assertRaises(FileExistsError):
                trial.install(case, 'inspect', 'e' * 32, 'a' * 32)

    def test_final_log_rejects_dispatch_without_readback(self):
        statuses = ('busy', 'berth_mismatch', 'idle', 'awaiting', 'undocked',
                    'duplicate_or_attempted', 'duplicate_or_attempted')
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / 'log'
            for rows in (statuses, tuple(status for status in statuses if status != 'undocked')):
                log.write_text(''.join(f'{trial.PREFIX}nonce|id|{status}\n' for status in rows)
                               + f'{trial.PREFIX}nonce|DONE\n')
                deadline = time.monotonic() + 1
                if rows == statuses:
                    self.assertTrue(trial.await_result(log, 'nonce', deadline, lambda: None)['undock_observed'])
                else:
                    with self.assertRaisesRegex(ValueError, 'Unexpected undock'):
                        trial.await_result(log, 'nonce', deadline, lambda: None)
