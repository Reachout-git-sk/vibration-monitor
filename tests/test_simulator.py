import random
import socket
import threading

import pytest

from vibmon.parser import Ok, Value, parse_sample
from vibmon.serial_link import SerialLink
from vibmon.simulator import ControllerSim, Machine, Sensor, SensorNodeSim, _serve_commands, run_tcp
from vibmon.stream import RmsWindow


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def quiet_sim(**kw):
    return ControllerSim(spike_prob=0, silence_prob=0, seed=1, **kw)


def test_basic_commands():
    sim = quiet_sim()
    assert sim.handle("STATUS?") == "OK"
    assert sim.handle("READ?").startswith("VAL ")
    assert sim.handle("SET GAIN 2.5") == "ACK"
    assert sim.handle("GAIN?") == "GAIN 2.5"
    assert sim.handle("status?\r\n") == "OK"


@pytest.mark.parametrize("cmd, reply", [
    ("SET GAIN abc", "ERR BAD_ARGUMENT"),
    ("SET GAIN 0", "ERR OUT_OF_RANGE"),
    ("SET GAIN 11", "ERR OUT_OF_RANGE"),
    ("FOO", "ERR UNKNOWN_COMMAND"),
])
def test_errors(cmd, reply):
    assert quiet_sim().handle(cmd) == reply


def test_gain_scales_readings():
    a, b = quiet_sim(), quiet_sim()
    b.handle("SET GAIN 2")
    va = float(a.handle("READ?").split()[1])
    vb = float(b.handle("READ?").split()[1])
    assert vb == pytest.approx(2 * va, rel=1e-3)


def test_normal_readings_stay_under_warn_limit():
    sim = quiet_sim()
    values = [float(sim.handle("READ?").split()[1]) for _ in range(500)]
    assert max(values) < 1.0


def test_spike_injection():
    sim = ControllerSim(spike_prob=1.0, silence_prob=0, seed=1)
    assert float(sim.handle("READ?").split()[1]) >= 2.2
    assert sim.faults_injected["spike"] == 1


def test_silence_injection_lasts_then_recovers():
    clock = FakeClock()
    sim = ControllerSim(spike_prob=0, silence_prob=1.0, silence_s=2.0, seed=1, clock=clock)
    assert sim.handle("STATUS?") is None
    sim.silence_prob = 0
    clock.t = 1.0
    assert sim.handle("STATUS?") is None
    clock.t = 2.5
    assert sim.handle("STATUS?") == "OK"


def test_serve_commands_loop():
    lines = iter(["STATUS?\n", "\n", "READ?\n", None])
    out = []
    _serve_commands(lambda: next(lines), out.append, quiet_sim())
    assert out[0] == "OK" and out[1].startswith("VAL ")


def test_sensor_node_lines_parse_and_show_vibration():
    node = SensorNodeSim(rate_hz=200, vib_hz=50, amplitude_g=0.3, seed=1)
    win = RmsWindow(200)
    for _ in range(200):
        win.add(parse_sample(node.next_line()))
    assert win.ready
    assert win.dropped == 0
    # mostly a 0.3 g sine along z: RMS of the dynamic part is roughly 0.3/sqrt(2)
    assert 0.15 < win.rms() < 0.3


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_end_to_end_over_tcp_with_pyserial():
    port = _free_port()
    sim = quiet_sim()
    t = threading.Thread(
        target=run_tcp, args=(port, lambda r, w: _serve_commands(r, w, sim)), daemon=True)
    t.start()

    link = SerialLink(f"socket://localhost:{port}", timeout=1.0)
    for _ in range(50):
        try:
            link.open()
            break
        except Exception:
            threading.Event().wait(0.05)
    try:
        assert link.status() == Ok()
        assert isinstance(link.read_value(), Value)
        link.set_gain(3)
        assert sim.gain == 3
    finally:
        link.close()


def test_device_fault_reported_on_status():
    sim = quiet_sim()
    sim.device_fault = "OVERTEMP"
    assert sim.handle("STATUS?") == "FAULT OVERTEMP"
    sim.device_fault = None
    assert sim.handle("STATUS?") == "OK"


def test_machine_drifts_and_takes_impacts():
    m = Machine(amplitude_g=1.0, vib_hz=50, drift=0)
    assert m.accel(0.005) == pytest.approx(1.0)       # quarter cycle of 50 Hz = peak
    m.impact = (0.0, 0.01, 3.0)
    assert m.accel(0.005) == pytest.approx(3.0)


def test_sensor_adds_gravity_on_z():
    sensor = Sensor(Machine(amplitude_g=0.0), random.Random(1), noise_g=0.0)
    assert sensor.read(0.123) == (0.0, 0.0, 1.0)


def test_controller_rms_comes_from_the_sensor_samples():
    clock = FakeClock()
    sim = ControllerSim(base_rms=0.35, spike_prob=0, silence_prob=0, seed=1, clock=clock)
    sim.machine.drift = 0
    # 100 samples of the sensor, gravity removed, RMS of the 50 Hz sine = 0.35 g
    assert sim.reading() == pytest.approx(0.35, abs=0.005)
    sim.machine.amplitude_g = 0
    assert sim.reading() == pytest.approx(0.0, abs=0.02)   # only sensor noise left
