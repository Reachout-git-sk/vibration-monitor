import pytest
import serial

from vibmon.parser import Ack, Ok, ParseError, ReplyTimeout, Value
from vibmon.serial_link import CommandRejected, LinkError, SerialLink

from fakes import FakeSerial, opener_for


def link_with(replies, **fake_kwargs):
    fake = FakeSerial(replies, **fake_kwargs)
    return SerialLink("COM9", baudrate=9600, timeout=0.2, opener=opener_for(fake)).open(), fake


def test_open_passes_settings_to_pyserial():
    link, fake = link_with({})
    assert fake.opened_with == ("COM9", {"baudrate": 9600, "timeout": 0.2})
    assert link.is_open


def test_status_read_and_gain():
    link, fake = link_with({
        "STATUS?": b"OK\n",
        "READ?": b"VAL 0.412\n",
        "SET GAIN 2.5": b"ACK\n",
    })
    assert link.status() == Ok()
    assert link.read_value() == Value(0.412)
    assert link.set_gain(2.5) == Ack()
    assert fake.written == ["STATUS?", "READ?", "SET GAIN 2.5"]


def test_rejected_gain_raises():
    link, _ = link_with({"SET GAIN 50": b"ERR OUT_OF_RANGE\n"})
    with pytest.raises(CommandRejected, match="OUT_OF_RANGE"):
        link.set_gain(50)


def test_timeout_raises_reply_timeout():
    link, _ = link_with({"READ?": None})
    with pytest.raises(ReplyTimeout):
        link.read_value()


def test_garbage_raises_parse_error():
    link, _ = link_with({"READ?": b"\x00\x13garbage\n"})
    with pytest.raises(ParseError):
        link.read_value()


def test_input_buffer_cleared_before_each_command():
    link, fake = link_with({"READ?": b"VAL 0.1\n"})
    before = fake.flushed_input
    link.read_value()
    link.read_value()
    assert fake.flushed_input == before + 2


def test_write_failure_becomes_link_error():
    link, _ = link_with({}, fail_on_write=True)
    with pytest.raises(LinkError, match="write failed"):
        link.status()


def test_query_when_closed_raises():
    link, _ = link_with({})
    link.close()
    with pytest.raises(LinkError):
        link.status()


def test_open_failure_becomes_link_error():
    def bad_opener(url, **kw):
        raise serial.SerialException("could not open port 'COM99'")
    with pytest.raises(LinkError, match="COM99"):
        SerialLink("COM99", opener=bad_opener).open()


def test_read_sample_stream_mode():
    fake = FakeSerial()
    fake._pending = b"1000,0.1,0.2,0.98\n"
    link = SerialLink("COM9", opener=opener_for(fake))
    link._ser = fake
    s = link.read_sample()
    assert s.timestamp_ms == 1000 and s.az == 0.98


def test_line_cut_off_by_timeout_is_not_a_reading():
    # readline() hit its timeout after "VAL 0.4"; the rest of "VAL 0.41\n" never came
    link, _ = link_with({"READ?": b"VAL 0.4"})
    with pytest.raises(ReplyTimeout, match="incomplete line"):
        link.read_value()


def test_stream_sample_cut_off_by_timeout():
    fake = FakeSerial()
    fake._pending = b"1000,0.1,0.2"
    link = SerialLink("COM9", opener=opener_for(fake))
    link._ser = fake
    with pytest.raises(ReplyTimeout):
        link.read_sample()
