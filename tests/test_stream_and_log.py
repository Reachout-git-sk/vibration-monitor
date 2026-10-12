import csv
import math
from datetime import datetime

import pytest

from vibmon.datalog import CsvLogger
from vibmon.monitor import Status
from vibmon.parser import Sample
from vibmon.stream import RmsWindow


def test_rms_of_constant_gravity_is_zero():
    w = RmsWindow(10)
    for i in range(10):
        w.add(Sample(i, 0.0, 0.0, 1.0))
    assert w.rms() == pytest.approx(0.0)


def test_rms_of_square_wave():
    w = RmsWindow(100)
    for i in range(100):
        w.add(Sample(i, 0, 0, 1.0 + (0.5 if i % 2 else -0.5)))
    assert w.rms() == pytest.approx(0.5)


def test_window_only_keeps_last_n():
    w = RmsWindow(4)
    for i in range(4):
        w.add(Sample(i, 0, 0, 5.0 if i < 2 else 1.0))
    for i in range(4, 8):
        w.add(Sample(i, 0, 0, 1.0))
    assert w.rms() == pytest.approx(0.0)


def test_out_of_order_samples_counted_as_dropped():
    w = RmsWindow(5)
    w.add(Sample(10, 0, 0, 1))
    w.add(Sample(10, 0, 0, 1))
    w.add(Sample(5, 0, 0, 1))
    assert w.dropped == 2


def test_csv_logger(tmp_path):
    path = tmp_path / "run.csv"
    log = CsvLogger(path)
    ts = datetime(2026, 10, 12, 9, 30, 0)
    log.reading(0.41234, Status.OK, ts=ts)
    log.status_change(Status.WARN, "reading 1.2 g >= warn limit 1 g", ts=ts)
    log.event("connected to socket://localhost:7000", ts=ts)
    log.close()

    rows = list(csv.DictReader(path.open()))
    assert [r["kind"] for r in rows] == ["reading", "status", "event"]
    assert rows[0]["value"] == "0.4123" and rows[0]["status"] == "OK"
    assert rows[1]["status"] == "WARN"
    assert rows[0]["timestamp"] == "2026-10-12T09:30:00.000"


def test_csv_logger_appends_without_second_header(tmp_path):
    path = tmp_path / "run.csv"
    for _ in range(2):
        log = CsvLogger(path)
        log.event("hello")
        log.close()
    lines = path.read_text().splitlines()
    assert lines[0].startswith("timestamp")
    assert len(lines) == 3
