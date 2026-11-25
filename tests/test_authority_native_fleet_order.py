"""Offline checks of the owned transport and one-shot native dispatch contract."""

import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from authority_native_fleet_order import NativeFleetOrderProvider


ROOT = Path(__file__).resolve().parents[1]
BINDING = {'registry_epoch': 'a' * 32}
ORDER = {'order_id': 'b' * 32, 'ship_index': 7354, 'ship_code': 'WRN-302',
         'sector': 'cluster_01_sector001_macro', 'x': -123456.25, 'y': 0, 'z': 82.125}


class NativeFleetOrderTests(unittest.TestCase):
    def setUp(self):
        self.now, self.owned = 1, True
        self.provider = NativeFleetOrderProvider(clock=lambda: self.now, timeout=5)
        self.addCleanup(self.provider.close)
        image = Path('/proc/self/exe').stat()
        ticks = int(Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19])
        self.provider.bind(dict(pid=os.getpid(), start_ticks=ticks, exe_dev=image.st_dev, exe_ino=image.st_ino),
                           lambda: self.owned)
        self.peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.peer.close)
        self.peer.connect(self.provider._socket.getsockname())
        self.peer.settimeout(1)
        self.peer.send(f'FLEET_HELLO|{self.provider._lease}'.encode())

    def begin(self, order=ORDER):
        self.assertIsNone(self.provider.begin(BINDING, order))
        expected = [
            'FLEET_ORDER', self.provider._lease, order['order_id'], BINDING['registry_epoch'],
            '7354', 'WRN-302', ORDER['sector'], '-123456.25', '0', '82.125', str(self.now + 5),
        ]
        if 'queue_mode' in order:
            expected.append(order['queue_mode'])
        self.assertEqual(self.peer.recv(512).decode().split('|'), expected)

    def reply(self, status='accepted', witness=('MoveWait', '0x101', '0x102'), **other):
        lease = other.get('lease', self.provider._lease)
        order_id = other.get('order_id', ORDER['order_id'])
        index = other.get('index', str(ORDER['ship_index']))
        queue = (other['queue_mode'],) if 'queue_mode' in other else ()
        packet = '|'.join(('FLEET_RESULT', lease, order_id, BINDING['registry_epoch'], index,
                           ORDER['ship_code'], ORDER['sector'], status, *witness, *queue)).encode()
        other.get('peer', self.peer).send(packet)

    def assert_no_dispatch(self):
        self.peer.setblocking(False)
        with self.assertRaises(BlockingIOError):
            self.peer.recv(512)

    def test_one_dispatch_and_exact_authoritative_receipt(self):
        self.begin()
        self.assertIsNone(self.provider.begin(BINDING, ORDER))
        with self.assertRaisesRegex(ValueError, 'ID reused'):
            self.provider.begin(BINDING, dict(ORDER, x=9))
        with self.assertRaisesRegex(RuntimeError, 'already active'):
            self.provider.begin(BINDING, dict(ORDER, order_id='c' * 32))
        self.assert_no_dispatch()

        self.reply(lease='f' * 32)
        self.reply(order_id='c' * 32)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other:
            other.connect(self.provider._socket.getsockname())
            self.reply(peer=other)
        self.assertIsNone(self.provider.poll())
        self.reply()
        self.assertEqual(self.provider.poll(), dict(ORDER, outcome='accepted'))
        self.assertEqual(self.provider.begin(BINDING, ORDER), dict(ORDER, outcome='accepted'))
        self.reply()
        self.assertIsNone(self.provider.poll())
        self.assert_no_dispatch()

    def test_no_native_refusal_becomes_acceptance(self):
        self.begin()
        self.reply('captain_required', ('-', '0', '0'))
        expected = dict(ORDER, outcome='refused', reason='captain_required')
        self.assertEqual(self.provider.poll(), expected)
        self.assertEqual(self.provider.begin(BINDING, ORDER), expected)
        self.assert_no_dispatch()

    def test_mismatched_ship_witness_is_unknown(self):
        self.begin()
        self.reply(index='1124')
        self.assertEqual(self.provider.poll(), dict(ORDER, outcome='unknown'))

    def test_deadline_fences_late_receipt_without_resubmission(self):
        self.begin()
        self.now += 5
        self.reply()
        expected = dict(ORDER, outcome='unknown')
        self.assertEqual(self.provider.poll(), expected)
        self.assertEqual(self.provider.begin(BINDING, ORDER), expected)
        another = dict(ORDER, order_id='c' * 32)
        self.assertEqual(self.provider.begin(BINDING, another), dict(another, outcome='refused', reason='provider_limit'))
        self.assertIsNone(self.provider.poll())
        self.assert_no_dispatch()

    def test_ownership_change_invalidates_post_send_receipt(self):
        self.begin()
        self.owned = False
        self.reply()
        self.assertEqual(self.provider.poll(), dict(ORDER, outcome='unknown'))

    def test_explicit_queue_modes_round_trip_and_bind_duplicate_id(self):
        for order_id, mode in ((ORDER['order_id'], 'append'), ('c' * 32, 'replace')):
            with self.subTest(mode=mode):
                order = dict(ORDER, order_id=order_id, queue_mode=mode)
                self.begin(order)
                with self.assertRaisesRegex(ValueError, 'ID reused'):
                    self.provider.begin(BINDING, dict(ORDER, order_id=order_id))
                self.reply(order_id=order_id, queue_mode=mode)
                self.assertEqual(self.provider.poll(), dict(order, outcome='accepted'))
                self.assertEqual(self.provider.begin(BINDING, order), dict(order, outcome='accepted'))
                self.assert_no_dispatch()

    def test_wrong_queue_witness_fences_without_dispatching_a_second_order(self):
        order = dict(ORDER, queue_mode='replace')
        self.begin(order)
        self.reply(queue_mode='append')
        self.assertEqual(self.provider.poll(), dict(order, outcome='unknown'))
        self.assertEqual(self.provider.begin(BINDING, order), dict(order, outcome='unknown'))
        another = dict(order, order_id='c' * 32)
        self.assertEqual(self.provider.begin(BINDING, another),
                         dict(another, outcome='refused', reason='provider_limit'))
        self.assert_no_dispatch()

    def test_installation_binds_the_md_and_driver_to_this_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            case = out / 'case'
            lab = case / 'extensions/x4_lan_lab'
            (lab / 'ui').mkdir(parents=True)
            (lab / 'md').mkdir()
            bridge = lab / 'ui/authority_world_bridge_probe.lua'
            bridge.write_text('local registry_checkpoint\n local ok,reason=bridge:update();assert(ok,reason)\n')
            manifest = lab / 'ui.xml'
            manifest.write_text('    <file name="ui/authority_world_bridge_probe.lua" />\n')
            cue, driver, _, _ = self.provider.install(case)
            self.assertIn(self.provider._lease, bridge.read_text())
            self.assertLess(manifest.read_text().index(driver.name), manifest.read_text().index(bridge.name))
            xml = ET.parse(cue).getroot()
            command = xml.find("./cues/cue[@name='MoveWait']")
            self.assertIn(self.provider._lease, command.find('actions/do_if').get('value'))
            self.assertIsNone(command.find('delay'))
            self.assertIsNone(command.find('cues'))
            request = xml.find("./cues/library[@name='CheckRequest']/actions")
            self.assertEqual(request.find("set_value[@name='$attempt_key']").get('exact'),
                             "'$fleet_%s'.[$args.$order_id]")
            journal_entry = 'State.$attempted.{$attempt_key}'
            journal_readback = f'@{journal_entry} == true'
            duplicate = request.find(f"do_if[@value='{journal_readback}']")
            self.assertEqual(duplicate.find("set_value[@name='$status']").get('exact'), "'unknown'")
            issue = xml.find("./cues/library[@name='IssueMoveWait']/actions")
            self.assertEqual((issue[1].get('name'), issue[1].get('exact')), (journal_entry, 'true'))
            self.assertEqual(issue.find('do_if').get('value'), journal_readback)
            writes = list(xml.iter('create_order'))
            self.assertEqual(len(writes), 1)
            self.assertEqual(writes[0].attrib, {'object': '$ship', 'id': "'MoveWait'", 'name': '$order'})
            self.assertEqual(self.provider.archive_cue(case, out), out / cue.name)
            self.assertFalse(cue.exists())

    def test_md_replaces_only_preexisting_cancellable_handles_after_new_order_readback(self):
        path = ROOT / 'tools/dev/authority_native_fleet_order.xml'
        cues = ET.parse(path).getroot().find('cues')
        request = cues.find("library[@name='CheckRequest']")
        self.assertTrue(any("$queue_mode == '-'" in guard.get('value') and '$ship.orders.count != 0' in guard.get('value')
                            for guard in request.iter('do_elseif')))
        issue = cues.find("library[@name='IssueMoveWait']/actions/do_if")
        tags = [action.tag for action in issue]
        self.assertLess(tags.index('do_if'), tags.index('create_order'))
        self.assertEqual(issue.find('./do_if/set_value').get('exact'), '$ship.orders.clone')
        snapshot = issue.findall('do_if')[1].find('do_for_each')
        self.assertEqual(snapshot.get('in'), '$expected_queue')
        self.assertIn('$prior.state != orderstate.critical and $prior.canplayercancel', snapshot.find('do_if').get('value'))
        self.assertEqual(snapshot.find('do_if/append_to_list').get('name'), '$replaceable')
        self.assertLess(tags.index('create_order'), tags.index('include_actions'))
        self.assertEqual(issue.find('include_actions').get('ref'), 'CheckMoveWait')
        replace = issue.find("do_if/do_if[@value=\"$queue_verified and $queue_mode == 'replace'\"]")
        self.assertEqual([element.get('ref') for element in replace], ['ReplacePriorOrders', 'CheckMoveWait'])
        retirement = cues.find("library[@name='ReplacePriorOrders']/actions/do_for_each")
        self.assertEqual(retirement.get('in'), '$replaceable')
        self.assertEqual(retirement.get('reverse'), 'true')
        self.assertIn('$prior == $order', retirement.find('do_if').get('value'))
        eligible = retirement.findall('do_if')[1]
        self.assertIn('$prior.state != orderstate.critical and $prior.canplayercancel', eligible.get('value'))
        self.assertEqual(eligible.find('cancel_order').attrib, {'order': '$prior', 'keepinloop': 'false'})
        self.assertEqual(len(list(cues.iter('cancel_order'))), 1)
        self.assertFalse(list(cues.iter('cancel_all_orders')))

    @unittest.skipUnless(shutil.which('luajit'), 'requires LuaJIT')
    def test_native_event_is_never_repeated(self):
        process = subprocess.run(['luajit', 'tests/authority_native_fleet_order.lua'], cwd=ROOT,
                                 check=True, timeout=5)
        self.assertEqual(process.returncode, 0)
