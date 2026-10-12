"""GUI tests: drive the real window against the simulator over TCP."""
import csv
import socket
import threading

import pytest

from vibmon.acquisition import MODE_POLL, MODE_STREAM
from vibmon.app import MainWindow
from vibmon.monitor import Status
from vibmon.simulator import ControllerSim, SensorNodeSim, _serve_commands, _serve_stream, run_tcp


def start_sim(handler):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    threading.Thread(target=run_tcp, args=(port, handler), daemon=True).start()
    return f"socket://localhost:{port}"


@pytest.fixture
def window(qtbot, tmp_path):
    w = MainWindow(log_dir=tmp_path)
    qtbot.addWidget(w)
    yield w
    if w.thread:
        w.disconnect_link()
        qtbot.waitUntil(lambda: w.thread is None, timeout=5000)


def wait_connected(qtbot, w, url, mode=MODE_POLL):
    for _ in range(20):
        w.connect_to(url, mode=mode)
        qtbot.waitUntil(lambda: w.connect_btn.isEnabled(), timeout=3000)
        if w.thread is not None:
            return
        qtbot.wait(100)
    raise AssertionError("could not connect to simulator")


def test_live_readings_plot_and_csv(qtbot, window):
    sim = ControllerSim(spike_prob=0, silence_prob=0, seed=1)
    url = start_sim(lambda r, w: _serve_commands(r, w, sim))
    wait_connected(qtbot, window, url)

    qtbot.waitUntil(lambda: window.counts["readings"] >= 10, timeout=5000)
    assert window.monitor.status is Status.OK
    assert len(window.curve.getData()[0]) >= 10

    window.gain_spin.setValue(2.0)
    window.gain_btn.click()
    qtbot.waitUntil(lambda: sim.gain == 2.0, timeout=3000)

    log_path = window.logger.path
    window.disconnect_link()
    qtbot.waitUntil(lambda: window.thread is None, timeout=5000)
    assert window.monitor.status is Status.DISCONNECTED

    rows = list(csv.DictReader(log_path.open()))
    kinds = {r["kind"] for r in rows}
    assert {"reading", "status", "event"} <= kinds
    assert any("gain set to 2" in r["detail"] for r in rows)


def test_spike_raises_fault(qtbot, window):
    sim = ControllerSim(spike_prob=1.0, silence_prob=0, seed=1)
    wait_connected(qtbot, window, start_sim(lambda r, w: _serve_commands(r, w, sim)))
    qtbot.waitUntil(lambda: window.monitor.status is Status.FAULT, timeout=5000)
    assert "fault limit" in window.monitor.reason


def test_silent_controller_raises_fault(qtbot, window):
    sim = ControllerSim(spike_prob=0, silence_prob=1.0, silence_s=60, seed=1)
    wait_connected(qtbot, window, start_sim(lambda r, w: _serve_commands(r, w, sim)))
    qtbot.waitUntil(lambda: window.monitor.status is Status.FAULT, timeout=8000)
    assert "no reply" in window.monitor.reason


def test_stream_mode_from_sensor_node(qtbot, window):
    url = start_sim(lambda r, w: _serve_stream(w, SensorNodeSim(rate_hz=200, seed=1), 200))
    wait_connected(qtbot, window, url, mode=MODE_STREAM)
    qtbot.waitUntil(lambda: window.counts["readings"] >= 3, timeout=8000)
    assert window.monitor.status is Status.OK
    assert not window.gain_btn.isEnabled()


def test_bad_port_shows_fault(qtbot, window):
    window.connect_to("COM_DOES_NOT_EXIST")
    qtbot.waitUntil(lambda: window.thread is None, timeout=5000)
    assert window.monitor.status is Status.FAULT
    assert "connection lost" in window.monitor.reason
