"""CCC2 bounded atomic-file IPC, Python 2.7/Linux and Python 3.
Offline fixtures qualify protocol/scheduling only, not game/cabinet latency.
"""
from __future__ import print_function
import ctypes
import os
import sys
import time
import uuid
from clock import monotonic


class BoundedList(list):
    """JSON-compatible retained tail. dropped/total expose incomplete evidence."""
    def __init__(self, limit):
        list.__init__(self)
        if limit < 0:
            raise ValueError('Negative diagnostic limit')
        self.limit = limit
        self.dropped = 0
        self.total = 0

    def append(self, item):
        self.total += 1
        list.append(self, item)
        excess = len(self) - self.limit
        if excess > 0:
            del self[:excess]
            self.dropped += excess

    def extend(self, items):
        for item in items:
            self.append(item)

    @property
    def truncated(self):
        return self.dropped != 0

    def metadata(self):
        return dict(limit=self.limit, retained=len(self), total=self.total,
                    dropped=self.dropped, truncated=self.truncated)


def _names(root):
    """Lazy directory enumeration; Python2 os.listdir is intentionally forbidden."""
    if hasattr(os, 'scandir'):
        iterator = os.scandir(root)
        try:
            for entry in iterator:
                yield entry.name
        finally:
            iterator.close()
    else:
        # Native Python2 target is Linux. Do not use unbounded listdir/glob.
        if not sys.platform.startswith('linux'):
            raise RuntimeError('Python2 directory enumeration requires Linux')
        class Dirent(ctypes.Structure):
            _fields_ = [('ino', ctypes.c_ulong), ('offset', ctypes.c_long),
                        ('reclen', ctypes.c_ushort), ('type', ctypes.c_ubyte),
                        ('name', ctypes.c_char * 256)]
        libc = ctypes.CDLL(None, use_errno=True)
        libc.opendir.argtypes = [ctypes.c_char_p]
        libc.opendir.restype = ctypes.c_void_p
        libc.readdir.argtypes = [ctypes.c_void_p]
        libc.readdir.restype = ctypes.POINTER(Dirent)
        libc.closedir.argtypes = [ctypes.c_void_p]
        libc.closedir.restype = ctypes.c_int
        path = root.encode(sys.getfilesystemencoding()) if not isinstance(root, bytes) else root
        directory = libc.opendir(path)
        if not directory:
            raise OSError(ctypes.get_errno(), 'opendir failed')
        try:
            while True:
                ctypes.set_errno(0)
                entry = libc.readdir(directory)
                if not entry:
                    error = ctypes.get_errno()
                    if error:
                        raise OSError(error, 'readdir failed')
                    break
                name = entry.contents.name
                if name not in (b'.', b'..'):
                    yield name.decode(sys.getfilesystemencoding())
        finally:
            libc.closedir(directory)


class Transport(object):
    def __init__(self, root, max_pending=20000, clock=None, wall_clock=None,
                 max_requests=32, max_payload=1024, max_bytes=32768,
                 max_backlog=512, max_scan_entries=None, max_consume_seconds=0.005,
                 gap_timeout=1.0, client_timeout=3.0, diagnostic_limit=4096):
        self.clock = clock or monotonic
        self.wall_clock = wall_clock or time.time
        self.max_pending = max_pending
        self.max_requests = max_requests
        self.max_payload = max_payload
        self.max_bytes = max_bytes
        self.max_backlog = max_backlog
        self.max_scan_entries = (2 * max_backlog + 16 if max_scan_entries is None
                                 else max_scan_entries)
        self.max_consume_seconds = max_consume_seconds
        self.gap_timeout = gap_timeout
        self.client_timeout = client_timeout
        for value in (max_pending, max_requests, max_payload, max_bytes,
                      max_backlog, self.max_scan_entries):
            if not isinstance(value, int) or value <= 0:
                raise ValueError('Capacity limits must be positive integers')
        if max_bytes < max_payload or min(max_consume_seconds, gap_timeout, client_timeout) <= 0:
            raise ValueError('Invalid byte/time budget')
        self.history = BoundedList(diagnostic_limit)
        self.requests = BoundedList(diagnostic_limit)
        # Atomic exclusive creation: existing IPC directory is never reused.
        os.makedirs(root)
        self.root = root
        self.session = uuid.uuid4().hex
        self.request_seq = 0
        self.heartbeat = 0
        self.last_client = None  # monotonic, NOT a human timestamp
        self.status = 'READY'
        self.error = None
        self.arrays = [[0]*64, [0]*90, [0]*5]
        self.queues = [[], [], []]
        self.ends = [0, 0, 0]
        self.acks = [0, 0, 0]
        self._gap_seq = None
        self._gap_since = None
        self.last_consume = dict(requests=0, bytes=0, scanned=0)

    def diagnostics(self):
        return dict(history=self.history.metadata(), requests=self.requests.metadata())

    def _fail(self, message):
        self.status = 'ERROR'
        self.error = self.error or message
        raise RuntimeError(self.error)

    def capture(self, arrays):
        for k, (values, size) in enumerate(zip(arrays, (64, 90, 5))):
            if len(values) != size:
                raise ValueError('Wrong output dimensions')
            for n, value in enumerate(values):
                value = int(bool(value))
                if value != self.arrays[k][n]:
                    if len(self.queues[k]) >= self.max_pending:
                        self._fail('Output queue overflow; refusing edge loss')
                    self.ends[k] += 1
                    event = (self.ends[k], n, value)
                    self.queues[k].append(event)
                    self.history.append([self.wall_clock(), 'CLG'[k]] + list(event))
                    self.arrays[k][n] = value

    def _liveness(self):
        if (self.status in ('RUNNING', 'PAUSED') and self.last_client is not None
                and self.clock() - self.last_client >= self.client_timeout):
            self._fail('Client heartbeat expired')

    def _backlog(self):
        next_seq = self.request_seq + 1
        later = False
        incomplete = False
        count = 0
        iterator = _names(self.root)
        try:
            for name in iterator:
                count += 1
                self.last_consume['scanned'] = count
                if count > self.max_scan_entries:
                    self._fail('IPC directory scan budget exceeded')
                stem, suffix = os.path.splitext(name)
                if suffix not in ('.req', '.ready'):
                    continue
                if not stem.isdigit() or len(stem) > 20 or str(int(stem)) != stem:
                    self._fail('Invalid request filename')
                seq = int(stem)
                if seq <= self.request_seq:
                    self._fail('Stale request file')
                if seq - self.request_seq > self.max_backlog:
                    self._fail('Outstanding request backlog exceeded')
                if seq > next_seq:
                    later = True
                else:
                    incomplete = True
        finally:
            iterator.close()
        ready = os.path.exists(os.path.join(self.root, str(next_seq) + '.ready'))
        now = self.clock()
        if (later or incomplete) and not ready:
            if self._gap_seq != next_seq:
                self._gap_seq, self._gap_since = next_seq, now
            if now - self._gap_since >= self.gap_timeout:
                self._fail('Missing next ready request expired')
        else:
            self._gap_seq, self._gap_since = None, None

    def consume(self, callback):
        """Return processed count; yield at count/bytes/elapsed budget or STOP.
        A single filesystem operation/callback cannot be preempted by this API.
        """
        if self.error:
            self._fail(self.error)
        self.last_consume = dict(requests=0, bytes=0, scanned=0)
        start = self.clock()
        try:
            self._liveness()
            self._backlog()  # one bounded scan per call, never listdir
            while self.last_consume['requests'] < self.max_requests:
                # Always permit one bounded request after the bounded scan;
                # otherwise scan overhead alone could starve all input forever.
                if (self.last_consume['requests'] and
                        self.clock() - start >= self.max_consume_seconds):
                    break
                self._liveness()
                seq = self.request_seq + 1
                stem = os.path.join(self.root, str(seq))
                if not os.path.exists(stem + '.ready'):
                    break
                size = os.path.getsize(stem + '.req')
                if size > self.max_payload:
                    self._fail('Request payload exceeded')
                remaining = self.max_bytes - self.last_consume['bytes']
                if size > remaining:
                    break
                with open(stem + '.req', 'rb') as source:
                    payload = source.read(min(self.max_payload, remaining) + 1)
                if len(payload) > self.max_payload or len(payload) > remaining:
                    self._fail('Request payload changed beyond budget')
                self.last_consume['bytes'] += len(payload)
                fields = payload.decode('ascii').strip().split()
                if len(fields) < 3 or fields[0] != self.session or int(fields[1]) != seq:
                    raise ValueError('Wrong session/request sequence')
                command = fields[2:]
                self.requests.append([self.wall_clock(), seq] + command)
                if command[0] == 'ACK':
                    if len(command) != 3 or command[1] not in ('C', 'L', 'G'):
                        raise ValueError('Invalid acknowledgement')
                    k = 'CLG'.index(command[1])
                    ack = int(command[2])
                    if not self.acks[k] <= ack <= self.ends[k]:
                        raise ValueError('Ack outside issued sequence')
                    self.acks[k] = ack
                    self.queues[k] = [e for e in self.queues[k] if e[0] > ack]
                elif command != ['PING']:
                    callback(command)
                self.last_client = self.clock()
                self.request_seq = seq
                self.last_consume['requests'] += 1
                os.unlink(stem + '.ready')
                os.unlink(stem + '.req')
                if command == ['STOP']:
                    break
            self._liveness()
            return self.last_consume['requests']
        except Exception as exc:
            self.status = 'ERROR'
            self.error = self.error or str(exc)
            raise

    def publish(self):
        if self.error:
            self.status = 'ERROR'
        self.heartbeat += 1
        lines = ['CCC2 %s %d %s %d' % (self.session, self.heartbeat,
                                      self.status, self.request_seq)]
        lines += [' '.join(map(str, a)) for a in self.arrays]
        lines += [';'.join('%d,%d,%d' % e for e in q) for q in self.queues]
        temp = os.path.join(self.root, 'state.tmp')
        with open(temp, 'w') as output:
            output.write('\n'.join(lines) + '\n')
        os.rename(temp, os.path.join(self.root, 'state.txt'))
