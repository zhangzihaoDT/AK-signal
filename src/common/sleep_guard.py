"""Bounded macOS sleep assertions owned by the waiting scheduler or batch."""
from contextlib import contextmanager
from datetime import datetime
import math
import os
import subprocess
import sys


class SleepGuard:
    def __init__(self):
        self.process = None

    def start(self, seconds: int | None = None) -> bool:
        if sys.platform != 'darwin':
            return False
        if self.process is not None and self.process.poll() is None:
            return True
        args = ['/usr/bin/caffeinate', '-i', '-s', '-w', str(os.getpid())]
        if seconds is not None:
            args += ['-t', str(max(1, seconds))]
        self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return self.process.poll() is None

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
            self.process = None


def waiting_seconds(at: datetime) -> int:
    """Cover the daily 08:10 wake through the end of the 08:30 trigger minute.

    Begin at 08:00 so a waking daemon acquires protection before idle sleep.
    Datetimes must be in the scheduler's Asia/Shanghai timezone.
    """
    start = at.replace(hour=8, minute=0, second=0, microsecond=0)
    end = at.replace(hour=8, minute=31, second=0, microsecond=0)
    return math.ceil((end - at).total_seconds()) if start <= at < end else 0


@contextmanager
def batch_sleep_guard():
    guard = SleepGuard()
    try:
        active = guard.start()
        if active:
            print('防休眠保护已开启（本批次结束后释放）', flush=True)
        yield
    finally:
        guard.stop()
