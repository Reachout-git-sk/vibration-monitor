"""Background thread that owns the serial link.

All blocking reads happen here so the GUI thread never waits on the port.
Results are handed back through Qt signals, which Qt delivers on the GUI
thread (queued connection across threads).
"""
import queue
import time

from PySide6.QtCore import QThread, Signal

from .parser import DeviceError, Fault, Ok, ParseError, ReplyTimeout, Value
from .serial_link import CommandRejected, LinkError, SerialLink
from .stream import RmsWindow

MODE_POLL = "poll"      # controller: send READ?, wait for VAL
MODE_STREAM = "stream"  # sensor node: samples arrive on their own


class AcquisitionThread(QThread):
    reading = Signal(float, float)        # (monotonic time, value in g)
    timed_out = Signal()
    bad_reply = Signal(str)
    device_fault = Signal(str)
    device_ok = Signal()
    link_opened = Signal(str)
    link_lost = Signal(str)
    command_result = Signal(bool, str)

    def __init__(self, url, baudrate=115200, mode=MODE_POLL, interval_ms=100,
                 status_every=10, window=200, emit_every=20, link_factory=SerialLink):
        super().__init__()
        self.url = url
        self.baudrate = baudrate
        self.mode = mode
        self.interval = interval_ms / 1000
        self.status_every = status_every
        self.window_size = window
        self.emit_every = emit_every
        self._link_factory = link_factory
        self._commands = queue.Queue()
        self._stop = False

    def stop(self):
        self._stop = True

    def request_gain(self, gain):
        self._commands.put(("gain", gain))

    # ---- thread body -----------------------------------------------------

    def run(self):
        timeout = 0.5 if self.mode == MODE_POLL else 1.0
        link = self._link_factory(self.url, baudrate=self.baudrate, timeout=timeout)
        try:
            link.open()
        except LinkError as exc:
            self.link_lost.emit(str(exc))
            return
        self.link_opened.emit(self.url)

        try:
            if self.mode == MODE_STREAM:
                self._stream_loop(link)
            else:
                self._poll_loop(link)
        except LinkError as exc:
            self.link_lost.emit(str(exc))
            return
        finally:
            link.close()
        self.link_lost.emit("disconnected by user")

    def _run_commands(self, link):
        while True:
            try:
                kind, arg = self._commands.get_nowait()
            except queue.Empty:
                return
            if kind == "gain":
                try:
                    link.set_gain(arg)
                    self.command_result.emit(True, f"gain set to {arg:g}")
                except CommandRejected as exc:
                    self.command_result.emit(False, f"gain {arg:g} rejected: {exc}")
                except (ReplyTimeout, ParseError) as exc:
                    self.command_result.emit(False, f"gain {arg:g} not confirmed: {exc}")

    def _poll_loop(self, link):
        n = 0
        while not self._stop:
            started = time.monotonic()
            self._run_commands(link)
            try:
                if self.status_every and n % self.status_every == 0:
                    reply = link.status()
                    if isinstance(reply, Fault):
                        self.device_fault.emit(reply.code)
                    elif isinstance(reply, Ok):
                        self.device_ok.emit()
                reply = link.read_value()
                if isinstance(reply, Value):
                    self.reading.emit(time.monotonic(), reply.value)
                elif isinstance(reply, DeviceError):
                    self.bad_reply.emit(f"READ? rejected: {reply.message}")
                else:
                    self.bad_reply.emit(f"unexpected reply to READ?: {reply}")
            except ReplyTimeout:
                self.timed_out.emit()
            except ParseError as exc:
                self.bad_reply.emit(str(exc))
            n += 1
            elapsed = time.monotonic() - started
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)

    def _stream_loop(self, link):
        window = RmsWindow(self.window_size)
        n = 0
        while not self._stop:
            try:
                window.add(link.read_sample())
            except ReplyTimeout:
                self.timed_out.emit()
                continue
            except ParseError as exc:
                self.bad_reply.emit(str(exc))
                continue
            n += 1
            if window.ready and n % self.emit_every == 0:
                self.reading.emit(time.monotonic(), window.rms())
