"""Serial job ACK/REARM -> motion registration using real TCP/UDP and Lua gate."""
from pathlib import Path
import select
import socket
import sys
import threading
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
from authority.experimental_tcp_job_delivery import ExperimentalTCPJobDelivery
from authority.experimental_tcp_motion import ExperimentalTCPMotion
from authority.experimental_source_job_bridge import ExperimentalSourceJobBridge
from authority.experimental_source_motion_ipc import ExperimentalSourceMotionIPC
from authority.experimental_job_motion_scheduler import ExperimentalJobMotionScheduler
import test_authority_experimental_source_job_bridge as fixture

class Scheduler(unittest.TestCase):
 def test_reentrant_direct_job_access_faults_shared_session(self):
  case=fixture.SourceBridgeTest('test_correlated_finished_after_rearm');case.setUp();self.addCleanup(case.doCleanups)
  a=case.base.case.connect();case.base.case.offer_and_claim(a);case.base.client(a,1);case.base.case.host.tick(.1)
  delivery=ExperimentalTCPJobDelivery(case.base.case.host,enabled=True,timeout=1)
  sockets=[]
  for _ in range(4):
   s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('127.0.0.1',0));sockets.append(s);self.addCleanup(s.close)
  ji,jo,mi,mo=sockets;ji.connect(jo.getsockname());jo.connect(ji.getsockname());mi.connect(mo.getsockname());mo.connect(mi.getsockname())
  job=ExperimentalSourceJobBridge(delivery,ji,enabled=True,lease=case.lease,attempt=case.attempt,
      epoch=case.epoch,generation=case.generation,guard=lambda:True,timeout=2)
  motion=ExperimentalTCPMotion(delivery,enabled=True);ref=[];requested=threading.Event();answered=threading.Event();attempted=threading.Event()
  def reentrant():
   if requested.is_set():
    # The response may become readable before the responder thread can set its
    # completion event. Wait for that event after the request was received so
    # this guard always exercises the reentrant call during the active pull.
    if not answered.wait(1):raise RuntimeError('fake responder did not answer')
    attempted.set()
    try:job.run_one(_scheduler=ref[0])
    except RuntimeError:pass
   return True
  ipc=ExperimentalSourceMotionIPC(mi,motion,enabled=True,lease='1'*32,attempt='2'*32,
      epoch=case.epoch,generation=case.generation,guard=reentrant,validate_peer=lambda p:True,timeout=2)
  scheduler=ExperimentalJobMotionScheduler(job,ipc,enabled=True);ref.append(scheduler)
  def fake_motion():
   parts=mo.recv(769).decode().split('|')
   requested.set()
   mo.send('|'.join(['S','1'*32,'2'*32,'NO_SAMPLE',case.epoch,case.generation,parts[-1]]).encode())
   answered.set()
  thread=threading.Thread(target=fake_motion);thread.start()
  with self.assertRaises(RuntimeError):scheduler.step()
  thread.join(2);self.assertFalse(thread.is_alive());self.assertTrue(answered.is_set());self.assertTrue(attempted.is_set())
  self.assertEqual(scheduler.state,'failed');self.assertEqual(job.state,'failed')
  self.assertTrue(delivery._failed);self.assertTrue(case.base.case.host._failed)

 def test_job_rearm_precedes_confirmed_motion_registration(self):
  case=fixture.SourceBridgeTest('test_correlated_finished_after_rearm');case.setUp();self.addCleanup(case.doCleanups)
  a=case.base.case.connect();b=case.base.case.connect()
  case.base.case.offer_and_claim(a);case.base.case.offer_and_claim(b)
  case.base.client(a,1);case.base.client(b,2);case.base.case.host.tick(.1)
  delivery=ExperimentalTCPJobDelivery(case.base.case.host,enabled=True,timeout=1)
  job_in=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);job_in.bind(('127.0.0.1',0))
  job_out=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);job_out.bind(('127.0.0.1',0))
  self.addCleanup(job_in.close);self.addCleanup(job_out.close)
  job_in.connect(job_out.getsockname());job_out.connect(job_in.getsockname());case.start_source(job_out,'ok')
  job=ExperimentalSourceJobBridge(delivery,job_in,enabled=True,lease=case.lease,attempt=case.attempt,
      epoch=case.epoch,generation=case.generation,guard=lambda:True,timeout=2)
  motion=ExperimentalTCPMotion(delivery,enabled=True)
  m_in=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);m_in.bind(('127.0.0.1',0))
  m_out=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);m_out.bind(('127.0.0.1',0))
  self.addCleanup(m_in.close);self.addCleanup(m_out.close)
  m_in.connect(m_out.getsockname());m_out.connect(m_in.getsockname())
  ipc=ExperimentalSourceMotionIPC(m_in,motion,enabled=True,lease='1'*32,attempt='2'*32,
       epoch=case.epoch,generation=case.generation,guard=lambda:True,validate_peer=lambda p:p==m_out.getsockname(),timeout=2)
  scheduler=ExperimentalJobMotionScheduler(job,ipc,enabled=True)
  self.assertTrue(select.select([job_in],[],[],3)[0])
  kind,event=scheduler.step();self.assertEqual((kind,event['index']),('job',10055))
  self.assertEqual(case.releases,[(case.epoch,1,10055)])
  def fake_motion():
   parts=m_out.recv(769).decode().split('|')
   m_out.send('|'.join(['S','1'*32,'2'*32,'NO_SAMPLE',case.epoch,case.generation,parts[-1]]).encode())
  thread=threading.Thread(target=fake_motion);thread.start()
  self.assertEqual(scheduler.step(),('motion',None));thread.join(2);self.assertFalse(thread.is_alive())
  self.assertEqual(scheduler.state,'ready');scheduler.close()
if __name__=='__main__':unittest.main()
