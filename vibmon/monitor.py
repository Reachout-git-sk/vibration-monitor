"""Decide OK / WARN / FAULT from readings and missed replies."""
from dataclasses import dataclass
from enum import Enum


class Status(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    OK = "OK"
    WARN = "WARN"
    FAULT = "FAULT"


@dataclass
class Thresholds:
    warn: float = 1.0      # g RMS
    fault: float = 2.0     # g RMS
    max_missed: int = 3    # consecutive timeouts before declaring the link lost

    def __post_init__(self):
        if not 0 < self.warn < self.fault:
            raise ValueError("need 0 < warn < fault")
        if self.max_missed < 1:
            raise ValueError("max_missed must be at least 1")


class StatusMonitor:
    def __init__(self, thresholds=None):
        self.thresholds = thresholds or Thresholds()
        self.status = Status.DISCONNECTED
        self.reason = "not connected"
        self.missed = 0
        self.device_fault = None  # code of a latched controller fault

    def _set(self, status, reason):
        changed = status != self.status
        self.status, self.reason = status, reason
        return changed

    def on_value(self, value):
        """Feed one reading. Returns True if the status changed."""
        self.missed = 0
        if self.device_fault is not None:
            return False  # a controller fault holds FAULT until STATUS? says OK again
        t = self.thresholds
        if value >= t.fault:
            return self._set(Status.FAULT, f"reading {value:.3f} g >= fault limit {t.fault:g} g")
        if value >= t.warn:
            return self._set(Status.WARN, f"reading {value:.3f} g >= warn limit {t.warn:g} g")
        return self._set(Status.OK, "within limits")

    def on_timeout(self):
        self.missed += 1
        if self.device_fault is not None:
            return False
        if self.missed >= self.thresholds.max_missed:
            return self._set(Status.FAULT, f"no reply for {self.missed} consecutive polls")
        return False

    def on_device_fault(self, code):
        self.device_fault = code
        return self._set(Status.FAULT, f"controller reported fault {code}")

    def on_device_ok(self):
        """STATUS? answered OK. Returns True if this released a latched fault.

        The status stays FAULT until the next reading sets it from the limits.
        """
        if self.device_fault is None:
            return False
        self.device_fault = None
        return True

    def on_link_lost(self, reason):
        return self._set(Status.FAULT, f"connection lost: {reason}")

    def on_disconnect(self):
        self.missed = 0
        self.device_fault = None
        return self._set(Status.DISCONNECTED, "not connected")
