"""OFFLINE synthetic protocol fixtures against the real staged source.
No game imports, VPX, network, cabinet, devices or runtime launches.
"""
from __future__ import print_function
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import clock as clock_module
import transport as transport_module
from transport import Transport, BoundedList
from clock import Deadline

ROOT = os.path.dirname(os.path.abspath(__file__))


class FakeClock(object):
    def __init__(self, value=100.0):
        self.value = value
    def __call__(self):
        return self.value
    def advance(self, seconds):
        self.value += seconds


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='offline-fixture-', dir=ROOT)
        self.elapsed = FakeClock()
        self.wall = FakeClock(1000000)
        self.count = 0

    def tearDown(self):
        shutil.rmtree(self.home)

    def make(self, **kwargs):
        self.count += 1
        return Transport(os.path.join(self.home, 'ipc' + str(self.count)),
                         clock=self.elapsed, wall_clock=self.wall, **kwargs)

    def put(self, t, seq, command='PING', ready=True, session=None):
        stem = os.path.join(t.root, str(seq))
        with open(stem + '.req', 'wb') as output:
            output.write(('%s %d %s\n' % (session or t.session, seq, command)).encode('ascii'))
        if ready:
            with open(stem + '.ready', 'wb') as output:
                output.write(b'')

    def assert_transport_failure(self, t, text):
        with self.assertRaises(Exception) as raised:
            t.consume(lambda command: None)
        self.assertIn(text, str(raised.exception))
        self.assertEqual(t.status, 'ERROR')
        self.assertTrue(t.error)
        with self.assertRaises(RuntimeError):
            t.consume(lambda command: None)

    def test_fresh_directory_and_nonce(self):
        first = self.make()
        second = self.make()
        self.assertNotEqual(first.session, second.session)
        self.assertEqual(len(first.session), 32)
        with self.assertRaises(OSError):
            Transport(first.root)

    def test_large_mixed_burst_yields_and_orders(self):
        t = self.make(max_requests=7, max_backlog=256)
        expected = []
        for seq in range(1, 202):
            command = ('SW %d %d' % (seq % 120, seq % 2) if seq % 2
                       else 'ACK C 0')
            self.put(t, seq, command)
            if seq % 2:
                expected.append(command.split())
        seen = []
        ticks = publishes = 0
        batches = []
        while t.request_seq < 201:
            batches.append(t.consume(seen.append))
            ticks += 1
            # Exercise actual publication every yielded game-loop opportunity.
            if os.name == 'nt' and os.path.exists(os.path.join(t.root, 'state.txt')):
                os.unlink(os.path.join(t.root, 'state.txt'))
            t.publish()
            publishes += 1
        self.assertEqual(seen, expected)
        self.assertEqual(batches, [7]*28 + [5])
        self.assertEqual((ticks, publishes), (29, 29))
        self.assertEqual([row[1] for row in t.requests], list(range(1, 202)))

    def test_continuous_producer_cannot_monopolize(self):
        t = self.make(max_requests=5, max_backlog=32)
        self.put(t, 1, 'SW 1 1')
        def producer(command):
            self.put(t, t.request_seq + 2, 'SW 1 1')
        ticks = 0
        for unused in range(50):
            self.assertEqual(t.consume(producer), 5)
            self.assertLessEqual(t.last_consume['bytes'], t.max_bytes)
            ticks += 1
        self.assertEqual((t.request_seq, ticks), (250, 50))
        self.assertTrue(os.path.exists(os.path.join(t.root, '251.ready')))

    def test_continuous_producer_loop_rechecks_deadline(self):
        t = self.make(max_requests=4)
        self.put(t, 1, 'SW 1 1')
        deadline = Deadline(0.025, clock=self.elapsed)
        ticks = 0
        publications = 0
        def producer(command):
            self.put(t, t.request_seq + 2, 'SW 1 1')
        while not deadline.expired():
            t.consume(producer)
            if deadline.expired():
                break
            ticks += 1
            if os.name == 'nt' and os.path.exists(os.path.join(t.root, 'state.txt')):
                os.unlink(os.path.join(t.root, 'state.txt'))
            t.publish()
            publications += 1
            self.elapsed.advance(0.01)
        self.assertEqual((ticks, publications, t.request_seq), (3, 3, 12))
        self.assertTrue(deadline.expired())

    def test_stop_behind_backlog_reached_in_bounded_batches(self):
        t = self.make(max_requests=5)
        for seq in range(1, 22):
            self.put(t, seq, 'STOP' if seq == 21 else 'SW 1 1')
        seen = []
        calls = 0
        while not seen or seen[-1] != ['STOP']:
            self.assertLess(calls, 5)
            self.assertLessEqual(t.consume(seen.append), 5)
            calls += 1
        self.assertEqual((calls, t.request_seq), (5, 21))

    def test_elapsed_budget_yields(self):
        t = self.make(max_requests=50, max_consume_seconds=0.005)
        for seq in range(1, 21):
            self.put(t, seq, 'SW 1 1')
        def expensive(command):
            self.elapsed.advance(0.002)
        self.assertEqual(t.consume(expensive), 3)
        self.assertEqual(t.request_seq, 3)

    def test_slow_scan_still_allows_one_request(self):
        t = self.make(max_requests=50)
        self.put(t, 1)
        original = t._backlog
        def slow_scan():
            original()
            self.elapsed.advance(1)
        t._backlog = slow_scan
        self.assertEqual(t.consume(lambda command: None), 1)

    def test_payload_cap_and_sticky_error_publication(self):
        t = self.make(max_payload=64, max_bytes=64)
        self.put(t, 1, 'SW ' + '9'*10000)
        self.assert_transport_failure(t, 'payload exceeded')
        self.assertEqual(t.request_seq, 0)
        t.status = 'STOPPED'
        t.publish()
        with open(os.path.join(t.root, 'state.txt')) as source:
            self.assertIn(' ERROR 0', source.readline())

    def test_exact_payload_and_aggregate_byte_cap(self):
        size = 32 + len(' 1 PING\n')
        t = self.make(max_payload=size, max_bytes=size*2, max_requests=20)
        for seq in range(1, 5):
            self.put(t, seq)
        self.assertEqual(t.consume(lambda command: None), 2)
        self.assertEqual(t.last_consume['bytes'], size*2)
        self.assertEqual(t.consume(lambda command: None), 2)

    def test_backlog_window_excess(self):
        t = self.make(max_backlog=8)
        self.put(t, 9)
        self.assert_transport_failure(t, 'backlog exceeded')

    def test_bounded_directory_enumeration_without_listdir(self):
        t = self.make(max_scan_entries=6)
        for seq in range(10):
            with open(os.path.join(t.root, 'junk' + str(seq)), 'w') as output:
                output.write('')
        old = os.listdir
        def forbidden(*args):
            raise AssertionError('Unbounded listdir used')
        os.listdir = forbidden
        try:
            self.assert_transport_failure(t, 'scan budget exceeded')
            self.assertEqual(t.last_consume['scanned'], 7)
        finally:
            os.listdir = old

    def test_gap_expires_with_monotonic_time_only(self):
        t = self.make(gap_timeout=1)
        self.put(t, 2)
        self.assertEqual(t.consume(lambda command: None), 0)
        self.wall.advance(-99999999)
        self.elapsed.advance(0.5)
        self.assertEqual(t.consume(lambda command: None), 0)
        self.wall.advance(999999999)
        self.elapsed.advance(0.5)
        self.assert_transport_failure(t, 'Missing next ready request expired')

    def test_partial_request_without_ready_expires(self):
        t = self.make(gap_timeout=1)
        self.put(t, 1, ready=False)
        t.consume(lambda command: None)
        self.elapsed.advance(1)
        self.assert_transport_failure(t, 'Missing next ready request expired')

    def test_gap_closure_preserves_contiguous_order(self):
        t = self.make(max_requests=1)
        self.put(t, 2, 'SW 2 1')
        self.assertEqual(t.consume(lambda command: None), 0)
        self.elapsed.advance(0.75)
        self.put(t, 1, 'SW 1 1')
        seen = []
        self.assertEqual(t.consume(seen.append), 1)
        self.assertEqual(t.consume(seen.append), 1)
        self.assertEqual(seen, [['SW', '1', '1'], ['SW', '2', '1']])

    def test_client_expiry_backward_and_forward_wall(self):
        for status in ('RUNNING', 'PAUSED'):
            for jump in (-100000000, 100000000):
                t = self.make()
                t.status = status
                self.put(t, 1)
                t.consume(lambda command: None)
                self.wall.advance(jump)
                self.elapsed.advance(2.5)
                self.assertEqual(t.consume(lambda command: None), 0)
                self.elapsed.advance(0.5)
                # A queued request must not resurrect an already expired client.
                self.put(t, 2)
                self.assert_transport_failure(t, 'heartbeat expired')
                self.assertEqual(t.request_seq, 1)

    def test_healthy_heartbeat_ignores_wall_changes(self):
        t = self.make()
        t.status = 'RUNNING'
        for seq in range(1, 10):
            self.wall.advance((-1 if seq % 2 else 1)*1000000000)
            self.elapsed.advance(2)
            self.put(t, seq)
            self.assertEqual(t.consume(lambda command: None), 1)
        self.assertEqual(t.status, 'RUNNING')

    def test_pulse_retained_until_exact_ack_with_pruned_diagnostics(self):
        t = self.make(diagnostic_limit=1)
        arrays = [[0]*64, [0]*90, [0]*5]
        for state in (1, 0, 1, 0):
            for channel in range(3):
                arrays[channel][0] = state
            t.capture(arrays)
        expected = [(1, 0, 1), (2, 0, 0), (3, 0, 1), (4, 0, 0)]
        self.assertEqual(t.queues, [expected, expected, expected])
        self.assertEqual(t.ends, [4, 4, 4])
        self.assertEqual(t.diagnostics()['history']['dropped'], 11)
        t.publish()
        with open(os.path.join(t.root, 'state.txt')) as source:
            lines = source.read().splitlines()
        self.assertEqual(lines[4:], ['1,0,1;2,0,0;3,0,1;4,0,0']*3)
        for seq, command in enumerate(('ACK C 2', 'ACK C 2', 'ACK L 4', 'ACK G 4'), 1):
            self.put(t, seq, command)
        t.consume(lambda command: None)
        self.assertEqual(t.acks, [2, 4, 4])
        self.assertEqual(t.queues, [expected[2:], [], []])
        self.assertEqual(t.diagnostics()['requests']['dropped'], 3)

    def test_invalid_acks_fail_closed(self):
        for command in ('ACK C 1', 'ACK CL 0', 'ACK Z 0', 'ACK C -1'):
            t = self.make()
            self.put(t, 1, command)
            self.assert_transport_failure(t, 'Ack outside' if command in ('ACK C 1', 'ACK C -1') else 'Invalid acknowledgement')

    def test_regressing_ack_rejected(self):
        t = self.make()
        arrays = [[1]*64, [0]*90, [0]*5]
        t.capture(arrays)
        self.put(t, 1, 'ACK C 2')
        t.consume(lambda command: None)
        self.put(t, 2, 'ACK C 1')
        self.assert_transport_failure(t, 'Ack outside')

    def test_wrong_session_sequence_and_callback_errors_are_sticky(self):
        t = self.make()
        self.put(t, 1, session='wrong')
        self.assert_transport_failure(t, 'Wrong session')
        t = self.make()
        self.put(t, 1)
        with open(os.path.join(t.root, '1.req'), 'w') as output:
            output.write('%s 2 PING' % t.session)
        self.assert_transport_failure(t, 'Wrong session/request sequence')
        t = self.make()
        self.put(t, 1, 'SW 1 1')
        def broken(command):
            raise ValueError('callback failed')
        with self.assertRaises(ValueError):
            t.consume(broken)
        self.assertEqual((t.status, t.request_seq), ('ERROR', 0))

    def test_output_overflow_never_silently_drops(self):
        t = self.make(max_pending=1)
        arrays = [[0]*64, [0]*90, [0]*5]
        arrays[0][9] = 1
        t.capture(arrays)
        arrays[0][9] = 0
        with self.assertRaises(RuntimeError):
            t.capture(arrays)
        self.assertEqual(t.queues[0], [(1, 9, 1)])
        self.assertEqual(t.status, 'ERROR')

    def test_diagnostic_lists_and_json_are_explicitly_incomplete(self):
        values = BoundedList(3)
        values.extend(range(100))
        self.assertEqual(values, [97, 98, 99])
        self.assertEqual(values.metadata(), dict(limit=3, retained=3, total=100,
                                                dropped=97, truncated=True))
        self.assertEqual(json.loads(json.dumps(values)), [97, 98, 99])
        zero = BoundedList(0)
        zero.append('not retained')
        self.assertEqual(zero, [])
        self.assertEqual(zero.dropped, 1)
        t = self.make(diagnostic_limit=3)
        for seq in range(1, 101):
            self.put(t, seq)
            t.consume(lambda command: None)
        self.assertEqual([row[1] for row in t.requests], [98, 99, 100])
        self.assertEqual(t.requests.total, 100)
        self.assertEqual(t.requests.dropped, 97)

    def test_runner_deadline_and_stop_rechecked_after_consume(self):
        # Offline loop fixture of documented integration order, NOT CCGame.
        t = self.make(max_requests=2)
        deadline = Deadline(1, clock=self.elapsed)
        ticks = []
        self.put(t, 1, 'SW 1 1')
        def expensive(command):
            self.elapsed.advance(2)
        if not deadline.expired():
            t.consume(expensive)
            if not deadline.expired():
                ticks.append(1)
        self.assertEqual(ticks, [])
        t = self.make(max_requests=8)
        self.put(t, 1, 'STOP')
        self.put(t, 2, 'SW 1 1')
        state = {'stop': False}
        def stop(command):
            state['stop'] = command == ['STOP']
        t.consume(stop)
        if not state['stop']:
            ticks.append(1)
        self.assertEqual((t.request_seq, ticks), (1, []))
        self.assertTrue(os.path.exists(os.path.join(t.root, '2.ready')))
        deadline = Deadline(1, clock=self.elapsed)
        self.wall.advance(-10000000000)
        self.assertFalse(deadline.expired())
        self.wall.advance(20000000000)
        self.assertFalse(deadline.expired())
        self.elapsed.advance(1)
        self.assertTrue(deadline.expired())

    def test_native_clock_provider(self):
        start = clock_module.monotonic()
        time.sleep(0.05)  # Windows monotonic may have a ~15.6ms resolution.
        self.assertGreater(clock_module.monotonic(), start)
        if sys.platform.startswith('linux'):
            # Exercise ctypes fallback explicitly under Python3 as well.
            fallback = clock_module._linux_clock()
            before = fallback()
            time.sleep(0.003)
            self.assertGreater(fallback(), before)
            self.assertLess(abs(fallback() - clock_module.monotonic()), 0.1)


if __name__ == '__main__':
    print('OFFLINE SYNTHETIC FIXTURES; no game/cabinet launch; Python ' + sys.version)
    unittest.main(verbosity=2)
