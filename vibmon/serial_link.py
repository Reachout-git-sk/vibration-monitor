"""Thin wrapper over pySerial for the controller's text protocol.

`url` is anything pySerial's serial_for_url accepts:
    COM3, /dev/ttyUSB0      real or virtual serial port
    socket://localhost:7000 the bundled simulator over TCP (no virtual port driver needed)
    loop://                 loopback, handy for quick experiments
"""
import serial
from serial.tools import list_ports

from .parser import DeviceError, ReplyTimeout, parse_reply, parse_sample


class LinkError(Exception):
    """Port could not be opened, or dropped while in use."""


class CommandRejected(Exception):
    pass


def available_ports():
    return [p.device for p in list_ports.comports()]


class SerialLink:
    def __init__(self, url, baudrate=115200, timeout=0.5, opener=serial.serial_for_url):
        self.url = url
        self.baudrate = baudrate
        self.timeout = timeout
        self._opener = opener
        self._ser = None

    @property
    def is_open(self):
        return self._ser is not None and self._ser.is_open

    def open(self):
        try:
            self._ser = self._opener(self.url, baudrate=self.baudrate, timeout=self.timeout)
        except (serial.SerialException, OSError, ValueError) as exc:
            raise LinkError(f"could not open {self.url}: {exc}") from exc
        self._ser.reset_input_buffer()
        return self

    def close(self):
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def _require_open(self):
        if not self.is_open:
            raise LinkError("link is not open")

    def _readline(self):
        try:
            line = self._ser.readline()
        except (serial.SerialException, OSError) as exc:
            raise LinkError(f"read failed: {exc}") from exc
        # readline() also returns when the timeout hits halfway through a line;
        # "VAL 0.4" without its newline may be the start of "VAL 0.41", so it
        # is an incomplete reply, not a reading
        if line and not line.endswith(b"\n"):
            raise ReplyTimeout(f"incomplete line {bytes(line)!r}")
        return line

    def query(self, command):
        """Send one command and parse the single-line reply.

        Raises ReplyTimeout if nothing arrives, ParseError on garbage.
        """
        self._require_open()
        # drop a late reply from a previous timed-out command so it is not
        # mistaken for the answer to this one
        self._ser.reset_input_buffer()
        try:
            self._ser.write(f"{command}\n".encode("ascii"))
            self._ser.flush()
        except (serial.SerialException, OSError) as exc:
            raise LinkError(f"write failed: {exc}") from exc
        return parse_reply(self._readline())

    def status(self):
        return self.query("STATUS?")

    def read_value(self):
        return self.query("READ?")

    def set_gain(self, gain):
        reply = self.query(f"SET GAIN {gain:g}")
        if isinstance(reply, DeviceError):
            raise CommandRejected(reply.message)
        return reply

    def read_sample(self):
        """Stream mode: read one `timestamp,ax,ay,az` line from the sensor node."""
        self._require_open()
        return parse_sample(self._readline())
