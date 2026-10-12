"""Turn raw lines from the controller (or the sensor node) into typed values.

Controller replies (command/response mode):
    OK                      reply to STATUS?
    FAULT <code>            reply to STATUS? when the controller reports a fault
    VAL <float>             reply to READ?, vibration in g RMS
    ACK                     reply to SET GAIN <x>
    GAIN <float>            reply to GAIN?
    ERR <message>           command rejected

Sensor node stream (no command needed):
    <timestamp_ms>,<ax>,<ay>,<az>
"""
import math
from dataclasses import dataclass


class ParseError(ValueError):
    """The line arrived but does not match the protocol."""


class ReplyTimeout(Exception):
    """Nothing came back before the serial timeout."""


@dataclass(frozen=True)
class Ok:
    pass


@dataclass(frozen=True)
class Ack:
    pass


@dataclass(frozen=True)
class Fault:
    code: str


@dataclass(frozen=True)
class Value:
    value: float


@dataclass(frozen=True)
class Gain:
    value: float


@dataclass(frozen=True)
class DeviceError:
    message: str


@dataclass(frozen=True)
class Sample:
    timestamp_ms: int
    ax: float
    ay: float
    az: float

    @property
    def magnitude(self):
        return math.sqrt(self.ax ** 2 + self.ay ** 2 + self.az ** 2)


def _decode(raw):
    if raw is None:
        raise ReplyTimeout("no reply")
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ParseError(f"non-ascii bytes: {bytes(raw)!r}") from exc
    line = raw.strip()
    if not line:
        # pySerial returns b"" when readline() hits its timeout
        raise ReplyTimeout("empty read")
    return line


def _number(text, line):
    try:
        v = float(text)
    except ValueError:
        raise ParseError(f"bad number in {line!r}") from None
    if not math.isfinite(v):
        raise ParseError(f"non-finite number in {line!r}")
    return v


def parse_reply(raw):
    line = _decode(raw)
    head, _, rest = line.partition(" ")
    head = head.upper()
    rest = rest.strip()

    if head == "OK" and not rest:
        return Ok()
    if head == "ACK" and not rest:
        return Ack()
    if head == "VAL" and rest:
        return Value(_number(rest, line))
    if head == "GAIN" and rest:
        return Gain(_number(rest, line))
    if head == "FAULT":
        return Fault(rest or "UNSPECIFIED")
    if head == "ERR":
        return DeviceError(rest or "unknown error")
    raise ParseError(f"unrecognised reply {line!r}")


def parse_sample(raw):
    line = _decode(raw)
    parts = line.split(",")
    if len(parts) != 4:
        raise ParseError(f"expected 4 fields, got {len(parts)} in {line!r}")
    try:
        ts = int(parts[0])
    except ValueError:
        raise ParseError(f"bad timestamp in {line!r}") from None
    ax, ay, az = (_number(p, line) for p in parts[1:])
    return Sample(ts, ax, ay, az)
