"""WorldCoordinator over actual loopback UDP to LuaJIT controller doubles; no X4."""
import os
import gzip
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools/dev'))
from authority.checkpoints import CheckpointStore
from authority.local_control_bridge import LocalControlBridge
from authority.marker_evidence import MarkerEvidenceError
from authority.save_transfer import SaveOffers
from authority.world import WorldCoordinator
from lockstep.launcher import joinfiles
from authority_control_cycle_evidence import read_cycle_evidence
import authority_world_bridge_live as live

@unittest.skipUnless(shutil.which('cc') and shutil.which('luajit') and shutil.which('gzip'), 'LuaJIT, gzip and C compiler required')
class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name); self.profile=root/'45841'; (self.profile/'save').mkdir(parents=True)
        d=root/'store'; d.mkdir(); self.store=CheckpointStore(d)
        lib=root/'net.so'
        subprocess.run(['cc','-std=c11','-shared','-fPIC',str(ROOT/'src/native/net.c'),'-ldl','-o',str(lib)],check=True)
        self.sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.sock.bind(('127.0.0.1',0));self.sock.settimeout(3)
        self.addCleanup(self.sock.close)
        self.lease='a'*32;self.epoch='b'*32
        self.marker_required = self._testMethodName in ('test_marked_save_waits_for_md_receipt_and_is_durable',
                                                        'test_wrong_saved_marker_is_never_confirmed')
        peer_mode = ('badmarker' if self._testMethodName == 'test_wrong_saved_marker_is_never_confirmed'
                     else 'marked' if self.marker_required else 'legacy')
        self.proc=subprocess.Popen(['luajit',str(ROOT/'tests/authority_bridge_peer.lua'),str(self.sock.getsockname()[1]),
            str(self.profile/'save/save_009.xml.gz'),self.lease,self.epoch,str(lib),
            peer_mode],cwd=ROOT,stderr=subprocess.PIPE)
        self.addCleanup(self.stop)
        data,peer=self.sock.recvfrom(768)
        self.assertEqual(data,f'HELLO|{self.lease}|{self.epoch}'.encode())
        self.bridge=LocalControlBridge(enabled=True,sock=self.sock,peer=peer,lease=self.lease,world_epoch=self.epoch,
            profile=self.profile,store=self.store,verify_checkpoint=read_cycle_evidence,timeout=3,
            marker_required=self.marker_required)
        self.service=joinfiles.FileService('c'*32,slots=(1,2)).start();self.addCleanup(self.service.close)
        self.world=WorldCoordinator(self.epoch,self.bridge,self.store,SaveOffers(self.service,self.store),
                                    participants=live.FakeParticipants())

    def stop(self):
        self.proc.terminate();self.proc.wait(timeout=3);self.proc.stderr.close()

    def confirm(self,offer):
        self.world.mark_loaded(offer.world_epoch,offer.connection_handle,offer.baseline_id,offer.load_generation,
            offer.save.checkpoint_id,offer.save.sha256,trusted_confirmation=True)

    def test_join_and_last_departure_cross_real_socket_and_lua(self):
        lifecycle=live.execute_world(self.world,self.store)
        cycles=lifecycle['fake_client_loads']
        self.assertNotEqual(cycles[0]['checkpoint_id'],cycles[1]['checkpoint_id'])
        self.assertNotEqual(cycles[0]['sha256'],cycles[1]['sha256'])
        self.assertTrue(all('FAKE' in cycle['load_confirmation'] for cycle in cycles))
        self.assertNotIn(lifecycle['last_departure_checkpoint']['checkpoint_id'],
                         {cycle['checkpoint_id'] for cycle in cycles})
        self.assertEqual(lifecycle['final_state'],'paused')
        self.assertEqual(self.world.session.state,'paused')

    def test_invalid_missing_and_duplicate_marker_receipts_fail_closed(self):
        subprocess.run(['luajit',str(ROOT/'tests/authority_bridge_marker_failures.lua')],
                       cwd=ROOT,check=True)

    def test_marked_save_waits_for_md_receipt_and_is_durable(self):
        lifecycle=live.execute_world(self.world,self.store,expect_markers=True)
        checkpoints=[self.store.get(c['checkpoint_id']) for c in lifecycle['fake_client_loads']]
        checkpoints.append(self.store.latest_marked_confirmed())
        self.assertEqual(len({c.marker for c in checkpoints}),3)
        for checkpoint in checkpoints:
            self.assertRegex(checkpoint.marker, r'^[0-9a-f]{32}$')
            with gzip.open(checkpoint.path, 'rt') as source:
                self.assertIn('string="'+checkpoint.marker+'"', source.read())
        self.assertIsNone(self.bridge.pending_marker)

    def test_verification_failure_faults_and_never_authorizes_resume(self):
        self.world.join('a');self.world.step()
        self.bridge._verify=mock.Mock(side_effect=ValueError('bad identity'))
        with self.assertRaisesRegex(ValueError,'bad identity'):self.world.step()
        self.assertEqual(self.world.session.state,'faulted')
        self.assertTrue(self.store.latest().path.is_file())
        with self.assertRaises(RuntimeError):self.bridge.resume()

    def test_wrong_saved_marker_is_never_confirmed(self):
        self.world.join('a'); self.world.step()
        with self.assertRaises(MarkerEvidenceError):
            self.world.step()
        self.assertEqual(self.world.session.state, 'faulted')
        self.assertTrue(self.store.latest().path.is_file())
        with self.assertRaises(FileNotFoundError):
            self.store.latest_confirmed()
        with self.assertRaises(RuntimeError):
            self.bridge.resume()

    def test_timeout_and_wrong_receipt_poison_adapter(self):
        for wrong in (False, True):
            with self.subTest(wrong=wrong), socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as server, \
                    socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as client:
                server.bind(('127.0.0.1',0));server.settimeout(1)
                client.bind(('127.0.0.1',0))
                adapter=LocalControlBridge(enabled=True,sock=client,peer=server.getsockname(),lease=self.lease,
                    world_epoch=self.epoch,profile=self.profile,store=self.store,
                    verify_checkpoint=read_cycle_evidence,timeout=.1)
                received=[]
                def remote():
                    data,peer=server.recvfrom(768);received.append(data)
                    if wrong:
                        server.sendto(f'RECEIPT|{self.lease}|{self.epoch}|1|resume|confirmed'.encode(),peer)
                worker=threading.Thread(target=remote);worker.start()
                with self.assertRaises(RuntimeError if wrong else TimeoutError): adapter.pause()
                worker.join(timeout=2);self.assertFalse(worker.is_alive());self.assertEqual(len(received),1)
                with self.assertRaises(RuntimeError):adapter.pause()

    def test_save_only_timeout_leaves_other_operations_unchanged(self):
        options = dict(enabled=True, sock=self.sock, peer=('127.0.0.1', 12345),
                       lease=self.lease, world_epoch=self.epoch, profile=self.profile,
                       store=self.store, verify_checkpoint=read_cycle_evidence)
        with self.assertRaises(ValueError):
            LocalControlBridge(**options, save_timeout=601)
        adapter = LocalControlBridge(**options, save_timeout=480)
        with mock.patch.object(adapter, '_exchange') as exchange:
            adapter.pause()
            adapter.resume()
            adapter.save()
        self.assertEqual([call.kwargs['timeout'] for call in exchange.call_args_list],
                         [None, None, 480])

    def test_disposable_overlay_installs_ui_probe_in_order(self):
        case=Path(self.temp.name)/'case'; lab=case/'extensions/x4_lan_lab'
        for folder in ('ui','md','native'):(lab/folder).mkdir(parents=True,exist_ok=True)
        (lab/'ui/config.lua').write_text('old')
        (lab/'ui/native_gate_snapshot.lua').write_text('old')
        (lab/'md/native_gate_battle.xml').write_text('<md/>')
        live.install_probe(case,12345,self.lease,self.epoch)
        ui=(lab/'ui.xml').read_text(); config=(lab/'ui/config.lua').read_text()
        self.assertLess(ui.index('ui/authority_control_state.lua'),ui.index('ui/local_control_bridge.lua'))
        self.assertLess(ui.index('ui/local_control_bridge.lua'),ui.index('ui/authority_world_bridge_probe.lua'))
        self.assertIn('authority_world_bridge=true',config)
        self.assertNotIn('authority_reuse_probe',ui)
        self.assertEqual((lab/'ui/local_control_bridge.lua').read_bytes(),
                         (ROOT/'src/authority/lua/local_control_bridge.lua').read_bytes())

    def test_disposable_overlay_can_opt_into_marker(self):
        case=Path(self.temp.name)/'marked-case';lab=case/'extensions/x4_lan_lab'
        for folder in ('ui','md','native'):(lab/folder).mkdir(parents=True,exist_ok=True)
        (lab/'ui/config.lua').write_text('old')
        (lab/'ui/native_gate_snapshot.lua').write_text('old')
        (lab/'md/native_gate_battle.xml').write_text('<md/>')
        live.install_probe(case,12345,self.lease,self.epoch,marker_required=True)
        self.assertIn('authority_marker_required=true', (lab/'ui/config.lua').read_text())
        self.assertEqual((lab/'md/authority_checkpoint_marker.xml').read_bytes(),
                         (ROOT/'src/authority/md/checkpoint_marker.xml').read_bytes())

    def test_disabled_and_occupied_slot_refuse(self):
        args=dict(sock=self.sock,peer=('127.0.0.1',1234),lease=self.lease,world_epoch=self.epoch,
                  profile=self.profile,store=self.store,verify_checkpoint=read_cycle_evidence)
        with self.assertRaises(ValueError):LocalControlBridge(enabled=False,**args)
        (self.profile/'save/save_009.xml.gz').write_bytes(b'occupied')
        with self.assertRaises(ValueError):LocalControlBridge(enabled=True,**args)
