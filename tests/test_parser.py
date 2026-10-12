import math

import pytest

from vibmon.parser import (
    Ack, DeviceError, Fault, Gain, Ok, ParseError, ReplyTimeout, Sample, Value,
    parse_reply, parse_sample,
)


@pytest.mark.parametrize("raw, expected", [
    (b"OK\n", Ok()),
    (b"ACK\r\n", Ack()),
    (b"VAL 0.532\n", Value(0.532)),
    (b"val 1.5\n", Value(1.5)),
    (b"VAL   -0.01  \n", Value(-0.01)),
    (b"GAIN 2.5\n", Gain(2.5)),
    (b"FAULT OVERTEMP\n", Fault("OVERTEMP")),
    (b"FAULT\n", Fault("UNSPECIFIED")),
    (b"ERR UNKNOWN_COMMAND\n", DeviceError("UNKNOWN_COMMAND")),
    ("VAL 3e-2", Value(0.03)),
])
def test_valid_replies(raw, expected):
    assert parse_reply(raw) == expected


@pytest.mark.parametrize("raw", [
    b"HELLO\n",
    b"VAL\n",
    b"VAL abc\n",
    b"VAL nan\n",
    b"VAL inf\n",
    b"OK extra\n",
    b"GAIN two\n",
    b"\xff\xfe\n",
    b"VAL 0.5 0.6\n",
])
def test_garbage_raises_parse_error(raw):
    with pytest.raises(ParseError):
        parse_reply(raw)


@pytest.mark.parametrize("raw", [None, b"", b"\n", b"   \r\n"])
def test_timeout_and_empty_reads(raw):
    with pytest.raises(ReplyTimeout):
        parse_reply(raw)


def test_parse_sample():
    s = parse_sample(b"123456,0.010,-0.020,0.981\n")
    assert s == Sample(123456, 0.010, -0.020, 0.981)
    assert s.magnitude == pytest.approx(math.sqrt(0.01**2 + 0.02**2 + 0.981**2))


@pytest.mark.parametrize("raw", [
    b"1,2,3\n",
    b"1,2,3,4,5\n",
    b"t,0.1,0.2,0.3\n",
    b"1,0.1,x,0.3\n",
])
def test_bad_samples(raw):
    with pytest.raises(ParseError):
        parse_sample(raw)


def test_sample_timeout():
    with pytest.raises(ReplyTimeout):
        parse_sample(b"")
