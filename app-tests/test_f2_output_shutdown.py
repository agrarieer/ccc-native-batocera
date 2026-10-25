"""Python2/3 offline actual Output tests: inert transport, never opens a socket.
Synthetic RGB bytes are scheduling fixtures, not native/physical render evidence.
"""
from __future__ import print_function
import contextlib
import threading
import time
import unittest
import cabinet_output as output

RGB=b'\x80'*(256*64*3)


@contextlib.contextmanager
def replace(obj,name,value):
    old=getattr(obj,name)
    setattr(obj,name,value)
    try: yield
    finally: setattr(obj,name,old)


class FastStop(object):
    """Skip reset settle only; all other waits are real and interruptible."""
    def __init__(self):
        self.event=threading.Event(); self.waiting=threading.Event(); self.waits=[]
    def is_set(self): return self.event.is_set()
    def set(self): self.event.set()
    def wait(self,delay):
        self.waits.append(delay)
        if delay==.25: return self.is_set()
        self.waiting.set()
        return self.event.wait(delay)


def fixture():
    # Disabled construction cannot start network/thread/evidence operations.
    value=output.Output(None,'unused-inert-run',enabled=False)
    value.enabled=True
    value.stop=FastStop()
    value.pending=RGB
    value.wake.set()
    return value


def start(value):
    value.thread=threading.Thread(target=value.worker)
    value.thread.daemon=True
    value.thread.start()


class DmdTests(unittest.TestCase):
    def test_wall_jumps_do_not_change_monotonic_rate(self):
        value=fixture(); value.last_send=100.
        elapsed=[100.]; wall=[5000.]; delays=[]; packets=[]
        class Stop(object):
            def __init__(self): self.stopped=False
            def is_set(self): return self.stopped
            def set(self): self.stopped=True
            def wait(self,delay):
                if delay!=.25:
                    delays.append(delay); elapsed[0]+=delay
                return self.stopped
        value.stop=Stop()
        def send(packet,host,port):
            packets.append(packet)
            if packet==b'COLOR':
                if value.sent==0:
                    wall[0]=-100000.; value.pending=RGB; value.wake.set()
                else:
                    wall[0]=999999999.; value.stop.set()
        with replace(output,'monotonic',lambda: elapsed[0]):
            with replace(output.time,'time',lambda: wall[0]):
                with replace(output,'transact',send), replace(output,'rgb_packet',lambda rgb:b'COLOR'):
                    value.worker()
        self.assertIsNone(value.error)
        self.assertEqual(value.sent,2)
        self.assertEqual(len(delays),2)
        for delay in delays: self.assertAlmostEqual(delay,1./15)
        self.assertAlmostEqual(value.last_send,100.+2./15)

    def test_close_interrupts_rate_wait_no_color_after_black(self):
        value=fixture(); value.last_send=100.; packets=[]
        with replace(output,'monotonic',lambda:100.):
            with replace(output,'transact',lambda packet,host,port:packets.append(packet)):
                with replace(output,'rgb_packet',lambda rgb:b'BLACK' if not any(bytearray(rgb)) else b'COLOR'):
                    start(value)
                    self.assertTrue(value.stop.waiting.wait(1),'worker did not enter rate wait')
                    started=time.perf_counter() if hasattr(time,'perf_counter') else time.time()
                    result=value.close()
                    duration=(time.perf_counter() if hasattr(time,'perf_counter') else time.time())-started
        self.assertFalse(value.thread.is_alive())
        self.assertIsNone(result['error'])
        self.assertEqual(packets,[output.reset_packet(),b'BLACK',b'BLACK'])
        self.assertLess(duration,1.)
        self.assertIsNotNone(value.pending)  # Stop was checked before dequeue.

    def test_close_interrupts_initial_settle(self):
        value=fixture(); value.stop=threading.Event(); reset=threading.Event(); packets=[]
        def send(packet,host,port):
            packets.append(packet)
            if packet==output.reset_packet(): reset.set()
        with replace(output,'transact',send), replace(output,'rgb_packet',lambda rgb:b'BLACK'):
            start(value); self.assertTrue(reset.wait(1))
            result=value.close()
        self.assertFalse(value.thread.is_alive()); self.assertIsNone(result['error'])
        self.assertEqual(packets,[output.reset_packet(),b'BLACK',b'BLACK'])

    def test_close_during_transaction_joins_before_black(self):
        value=fixture(); entered=threading.Event(); release=threading.Event(); done=threading.Event()
        packets=[]; results=[]
        def send(packet,host,port):
            if packet==b'COLOR':
                packets.append('COLOR_START'); entered.set()
                if not release.wait(1): raise RuntimeError('test release timeout')
                packets.append('COLOR_DONE')
            else: packets.append(packet)
        def close():
            results.append(value.close()); done.set()
        with replace(output,'monotonic',lambda:100.):
            with replace(output,'rgb_packet',lambda rgb:b'BLACK' if not any(bytearray(rgb)) else b'COLOR'):
                with replace(output,'transact',send):
                    start(value); self.assertTrue(entered.wait(1))
                    closer=threading.Thread(target=close); closer.start()
                    self.assertTrue(value.stop.event.wait(1))
                    self.assertFalse(done.is_set())
                    self.assertNotIn(b'BLACK',packets)
                    release.set(); closer.join(2)
        self.assertTrue(done.is_set()); self.assertFalse(value.thread.is_alive())
        self.assertIsNone(results[0]['error'])
        self.assertEqual(packets,[output.reset_packet(),'COLOR_START','COLOR_DONE',b'BLACK',b'BLACK'])

    def test_stop_during_packet_build_rechecked_before_send(self):
        value=fixture(); packets=[]
        def packet(rgb): value.stop.set(); return b'COLOR'
        with replace(output,'monotonic',lambda:100.):
            with replace(output,'transact',lambda packet,host,port:packets.append(packet)):
                with replace(output,'rgb_packet',packet): value.worker()
        self.assertEqual(packets,[output.reset_packet()]); self.assertEqual(value.sent,0)

    def test_unjoined_sender_withholds_black_and_retains_error(self):
        value=fixture(); value.error='original error'; packets=[]
        class Stuck(object):
            def join(self,timeout): self.timeout=timeout
            def is_alive(self): return True
        value.thread=Stuck()
        with replace(output,'transact',lambda packet,host,port:packets.append(packet)):
            result=value.close()
        self.assertEqual(value.thread.timeout,2)
        self.assertEqual(packets,[])
        self.assertIn('original error',result['error']); self.assertIn('black cleanup withheld',result['error'])

    def test_forwarding_and_cleanup_errors_retained(self):
        value=fixture()
        def fail(*args): raise OSError('inert transaction failed')
        with replace(output,'transact',fail):
            start(value); value.thread.join(1)
            self.assertFalse(value.thread.is_alive())
            with self.assertRaises(RuntimeError): value.check()
            result=value.close()
        self.assertIn('DMD forwarding failed',result['error'])
        self.assertIn('DMD cleanup failed',result['error'])

    def test_socket_bounds_and_close_preserved(self):
        calls=[]
        class Socket(object):
            def settimeout(self,value): calls.append(('timeout',value))
            def sendall(self,packet): calls.append(('send',packet)); raise OSError('inert send failure')
            def close(self): calls.append(('close',))
        def connect(address,timeout): calls.append(('connect',address,timeout)); return Socket()
        with replace(output.socket,'create_connection',connect):
            with self.assertRaises(OSError): output.transact(b'fixture','127.0.0.1',6789)
        self.assertEqual(calls,[('connect',('127.0.0.1',6789),.35),('timeout',.35),('send',b'fixture'),('close',)])

    def test_actual_enabled_sender_and_wire_packets_with_inert_transport(self):
        packets=[]; sent=threading.Event()
        color=output.rgb_packet(RGB)
        black=output.rgb_packet(b'\0'*(256*64*3))
        def send(packet,host,port):
            packets.append(packet)
            if packet==color: sent.set()
        with replace(output,'transact',send):
            value=output.Output(None,'unused-inert-run',enabled=True)
            with value.lock: value.pending=RGB
            value.wake.set()
            try: self.assertTrue(sent.wait(1),'no color serialized by actual worker')
            finally: result=value.close()
        self.assertFalse(value.thread.is_alive()); self.assertIsNone(result['error'])
        self.assertEqual(result['sent'],1)
        self.assertEqual(packets,[output.reset_packet(),color,black,black])

    def test_disabled_close_has_no_transactions(self):
        packets=[]
        with replace(output,'transact',lambda *args:packets.append(args)):
            result=output.Output(None,'unused-inert-run').close()
        self.assertEqual(packets,[]); self.assertFalse(result['enabled'])


if __name__=='__main__': unittest.main(verbosity=2)
