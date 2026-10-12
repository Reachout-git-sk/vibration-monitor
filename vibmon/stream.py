"""Turn raw accelerometer samples from the sensor node into a vibration level."""
from collections import deque
import math


class RmsWindow:
    """RMS of the dynamic part of |a| over the last `size` samples.

    The accelerometer always sees ~1 g of gravity, so subtract the window mean
    before taking RMS; what is left is the vibration.
    """

    def __init__(self, size=200):
        if size < 2:
            raise ValueError("window size must be at least 2")
        self.size = size
        self._buf = deque(maxlen=size)
        self.last_timestamp = None
        self.dropped = 0

    def add(self, sample):
        if self.last_timestamp is not None and sample.timestamp_ms <= self.last_timestamp:
            self.dropped += 1  # out of order or repeated sample
            return
        self.last_timestamp = sample.timestamp_ms
        self._buf.append(sample.magnitude)

    @property
    def ready(self):
        return len(self._buf) == self.size

    def rms(self):
        n = len(self._buf)
        if n < 2:
            return 0.0
        mean = sum(self._buf) / n
        return math.sqrt(sum((x - mean) ** 2 for x in self._buf) / n)
