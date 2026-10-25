"""Elapsed-time source for Python 2.7/Linux and Python 3; never wall fallback."""
from __future__ import print_function
import ctypes
import ctypes.util
import sys
import time


def _linux_clock():
    if not sys.platform.startswith('linux'):
        raise RuntimeError('Python2 monotonic clock requires Linux')
    class Timespec(ctypes.Structure):
        _fields_ = [('tv_sec', ctypes.c_long), ('tv_nsec', ctypes.c_long)]
    libraries = [None, ctypes.util.find_library('rt')]
    for name in libraries:
        try:
            lib = ctypes.CDLL(name, use_errno=True)
            fn = lib.clock_gettime
        except (OSError, AttributeError):
            continue
        fn.argtypes = [ctypes.c_int, ctypes.POINTER(Timespec)]
        fn.restype = ctypes.c_int
        def read(fn=fn, library=lib):
            value = Timespec()
            if fn(1, ctypes.byref(value)) != 0:  # Linux CLOCK_MONOTONIC
                error = ctypes.get_errno()
                raise OSError(error, 'clock_gettime(CLOCK_MONOTONIC) failed')
            return value.tv_sec + value.tv_nsec / 1000000000.0
        read()  # Fail at initialization, not silently to wall time.
        return read
    raise RuntimeError('clock_gettime(CLOCK_MONOTONIC) unavailable')


monotonic = getattr(time, 'monotonic', None) or _linux_clock()


class Deadline(object):
    def __init__(self, seconds, clock=None):
        self.clock = clock or monotonic
        self.end = self.clock() + seconds

    def expired(self):
        return self.clock() >= self.end
