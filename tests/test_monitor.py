import pytest

from vibmon.monitor import Status, StatusMonitor, Thresholds


def make():
    return StatusMonitor(Thresholds(warn=1.0, fault=2.0, max_missed=3))


def test_starts_disconnected():
    assert make().status is Status.DISCONNECTED


@pytest.mark.parametrize("value, status", [
    (0.0, Status.OK),
    (0.99, Status.OK),
    (1.0, Status.WARN),
    (1.99, Status.WARN),
    (2.0, Status.FAULT),
    (9.0, Status.FAULT),
])
def test_threshold_bands(value, status):
    m = make()
    m.on_value(value)
    assert m.status is status


def test_reports_only_transitions():
    m = make()
    assert m.on_value(0.2) is True
    assert m.on_value(0.3) is False
    assert m.on_value(1.5) is True
    assert m.on_value(0.1) is True


def test_fault_after_consecutive_timeouts():
    m = make()
    m.on_value(0.2)
    assert m.on_timeout() is False
    assert m.on_timeout() is False
    assert m.status is Status.OK
    assert m.on_timeout() is True
    assert m.status is Status.FAULT
    assert "no reply" in m.reason


def test_good_reading_resets_missed_counter():
    m = make()
    m.on_value(0.2)
    m.on_timeout()
    m.on_timeout()
    m.on_value(0.2)
    m.on_timeout()
    m.on_timeout()
    assert m.status is Status.OK


def test_device_fault_and_disconnect():
    m = make()
    m.on_value(0.2)
    m.on_device_fault("OVERTEMP")
    assert m.status is Status.FAULT and "OVERTEMP" in m.reason
    m.on_disconnect()
    assert m.status is Status.DISCONNECTED


@pytest.mark.parametrize("kwargs", [
    {"warn": 2.0, "fault": 1.0},
    {"warn": 0, "fault": 1.0},
    {"max_missed": 0},
])
def test_bad_thresholds_rejected(kwargs):
    with pytest.raises(ValueError):
        Thresholds(**kwargs)
