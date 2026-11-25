"""Real loopback TCP, synthetic witness callbacks; no X4 or external network."""
import importlib.util
import json
from pathlib import Path
import socket
import sys
import threading
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/dev'))
spec=importlib.util.spec_from_file_location('job_descriptor_wire',ROOT/'tools/dev/authority_job_descriptor_loopback.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def pair():
    listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    listener.bind(('127.0.0.1',0));listener.listen(1)
    client=socket.create_connection(listener.getsockname(),timeout=2)
    server,_=listener.accept();listener.close()
    return client,server


def descriptor(job_sequence=17):
    return dict(version=1,type='job_descriptor',attempt='a'*32,epoch='b'*32,
                transport_sequence=1,job_sequence=job_sequence,index=10055,
                macro='ship_gen_m_tugboat_01_a_macro',faction='pioneers',
                sector_macro='cluster_115_sector001_macro',x=-132217.203125,y=6004.3818359375,z=169081.265625)


class JobDescriptorWire(unittest.TestCase):
    def test_real_tcp_witness_before_ack_distinct_job_sequence(self):
        for seq in (1,17,9007199254740991):
            with self.subTest(sequence=seq):
                client,server=pair();events=[];errors=[];sent=descriptor(seq)
                def receive():
                    try:
                        got=m.receive_descriptor(server,'a'*32,'b'*32,seq,
                            lambda d: events.append(('witness',d)) or True,time.monotonic()+3,lambda:True)
                        events.append(('complete',got))
                    except BaseException as exc:errors.append(exc)
                thread=threading.Thread(target=receive);thread.start()
                self.assertTrue(m.send_descriptor(client,sent,time.monotonic()+3,lambda:True))
                thread.join(3);self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
                self.assertEqual(events,[('witness',sent),('complete',sent)])
                self.assertEqual(client.fileno(),-1);self.assertEqual(server.fileno(),-1)

    def test_refused_context_malformed_or_extra_fields_never_call_witness(self):
        cases=[]
        for field,value in [('attempt','c'*32),('epoch','c'*32),('transport_sequence',2),('transport_sequence',True),
                            ('job_sequence',0),('job_sequence',True),('job_sequence',9007199254740992),
                            ('index',3),('index',True),('x',True),('x',float('inf')),('x',10000001),('x',10**400),
                            ('sector_macro','null'),('faction',''),('actor','0x123'),('seed',7183)]:
            d=descriptor();d[field]=value;cases.append(json.dumps(d).encode()+b'\n')
        cases.extend([b'{"version":1,"version":1}\n',b'{"x":NaN}\n',b'x'*1025+b'\n',
                      json.dumps(descriptor()).encode()+b'\n'+json.dumps(descriptor()).encode()+b'\n'])
        d=descriptor(18);cases.append(json.dumps(d).encode()+b'\n')
        for data in cases:
            with self.subTest(data=data[:100]):
                client,server=pair();client.sendall(data);called=[]
                with self.assertRaises((ValueError,ConnectionError)):
                    m.receive_descriptor(server,'a'*32,'b'*32,17,lambda d:called.append(d) or True,time.monotonic()+2,lambda:True)
                client.settimeout(1)
                try:received=client.recv(1024)
                except ConnectionResetError:received=b''
                self.assertEqual(received,b'');self.assertEqual(called,[]);self.assertEqual(server.fileno(),-1);client.close()

    def test_no_ack_if_witness_refuses_raises_or_guard_is_lost(self):
        for outcome in (False,1,RuntimeError('unavailable'),'guard'):
            with self.subTest(outcome=outcome):
                client,server=pair();client.sendall(json.dumps(descriptor()).encode()+b'\n')
                alive={'yes':True}
                def witness(_):
                    if isinstance(outcome,BaseException):raise outcome
                    if outcome=='guard':alive['yes']=False;return True
                    return outcome
                with self.assertRaises(RuntimeError):
                    m.receive_descriptor(server,'a'*32,'b'*32,17,witness,time.monotonic()+2,lambda:alive['yes'])
                self.assertEqual(client.recv(1024),b'');client.close()

    def test_witness_timeout_no_ack(self):
        client,server=pair();client.sendall(json.dumps(descriptor()).encode()+b'\n')
        def witness(_):time.sleep(.03);return True
        with self.assertRaises(TimeoutError):
            m.receive_descriptor(server,'a'*32,'b'*32,17,witness,time.monotonic()+.01,lambda:True)
        self.assertEqual(client.recv(1024),b'');client.close()

    def test_sender_rejects_wrong_correlated_ack(self):
        for change in ({'job_sequence':18},{'job_sequence':True},{'transport_sequence':True},
                       {'index':3},{'attempt':'e'*32},{'type':'ack'},{'actor':'42'}):
            with self.subTest(change=change):
                client,server=pair()
                def peer():
                    data=b''
                    while not data.endswith(b'\n'):data+=server.recv(1024)
                    ack=m._ack(descriptor());ack.update(change)
                    server.sendall(json.dumps(ack).encode()+b'\n');server.close()
                thread=threading.Thread(target=peer);thread.start()
                with self.assertRaises(ValueError):m.send_descriptor(client,descriptor(),time.monotonic()+2,lambda:True)
                thread.join(2);self.assertFalse(thread.is_alive())

    def test_bad_expected_sequence_or_unix_socket_rejected(self):
        client,server=pair()
        with self.assertRaises(ValueError):
            m.receive_descriptor(server,'a'*32,'b'*32,True,lambda _:True,time.monotonic()+2,lambda:True)
        client.close()
        client,server=socket.socketpair(socket.AF_UNIX if hasattr(socket, 'AF_UNIX') else socket.AF_INET6)
        with self.assertRaises(ValueError):m.send_descriptor(client,descriptor(),time.monotonic()+2,lambda:True)
        server.close()

    def test_guard_failure_interrupts_waiting_receiver(self):
        client,server=pair();active=threading.Event();active.set();started=threading.Event();errors=[]
        def guard():started.set();return active.is_set()
        def receive():
            try:m.receive_descriptor(server,'a'*32,'b'*32,17,lambda _:True,time.monotonic()+5,guard)
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=receive,daemon=True)
        try:
            thread.start();self.assertTrue(started.wait(2));active.clear();thread.join(1.5)
            self.assertFalse(thread.is_alive());self.assertEqual(len(errors),1)
            self.assertIsInstance(errors[0],RuntimeError);self.assertEqual(str(errors[0]),'guard failed')
            self.assertEqual(server.fileno(),-1)
        finally:
            active.clear();client.close();server.close();thread.join(2)

if __name__=='__main__':unittest.main()
