"""Append every reading and every status change to a CSV file."""
import csv
from datetime import datetime
from pathlib import Path

FIELDS = ["timestamp", "kind", "value", "status", "detail"]


class CsvLogger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists() or self.path.stat().st_size == 0
        self._f = self.path.open("a", newline="", encoding="utf-8")
        self._w = csv.DictWriter(self._f, fieldnames=FIELDS)
        if new:
            self._w.writeheader()
        self.rows = 0

    @staticmethod
    def default_path(directory="logs"):
        return Path(directory) / f"vibration_{datetime.now():%Y%m%d_%H%M%S}.csv"

    def _write(self, kind, value="", status="", detail="", ts=None):
        ts = ts or datetime.now()
        self._w.writerow({
            "timestamp": ts.isoformat(timespec="milliseconds"),
            "kind": kind,
            "value": f"{value:.4f}" if isinstance(value, float) else value,
            "status": getattr(status, "value", status),
            "detail": detail,
        })
        self._f.flush()  # a crash should not lose the readings that led up to it
        self.rows += 1

    def reading(self, value, status, ts=None):
        self._write("reading", value, status, ts=ts)

    def status_change(self, status, reason, ts=None):
        self._write("status", status=status, detail=reason, ts=ts)

    def event(self, detail, ts=None):
        self._write("event", detail=detail, ts=ts)

    def close(self):
        self._f.close()
