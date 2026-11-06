"""PySide6 desktop app: connect, plot live vibration, flag faults, log to CSV.

    python -m vibmon.app
"""
import sys
import time
from collections import deque

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QMainWindow, QPushButton, QStatusBar, QVBoxLayout, QWidget,
)

from .acquisition import MODE_POLL, MODE_STREAM, AcquisitionThread
from .datalog import CsvLogger
from .monitor import Status, StatusMonitor, Thresholds
from .serial_link import available_ports

HISTORY_S = 60
STATUS_COLORS = {
    Status.DISCONNECTED: "#6b7280",
    Status.OK: "#16a34a",
    Status.WARN: "#d97706",
    Status.FAULT: "#dc2626",
}
DEFAULT_SIM_URL = "socket://localhost:7000"


class MainWindow(QMainWindow):
    def __init__(self, log_dir="logs"):
        super().__init__()
        self.setWindowTitle("Vibration Monitor")
        self.resize(1100, 600)
        self.log_dir = log_dir
        self.monitor = StatusMonitor()
        self.thread = None
        self.logger = None
        self.t0 = None
        self.times = deque()
        self.values = deque()
        self.counts = {"readings": 0, "warn": 0, "fault": 0, "timeouts": 0, "bad": 0}
        self._link_failed = False

        self._build_ui()
        self._refresh_ports()
        self._show_status()

    # ---- layout ------------------------------------------------------------

    def _build_ui(self):
        conn = QGroupBox("Connection")
        f = QFormLayout(conn)
        self.port_box = QComboBox(editable=True)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_ports)
        row = QHBoxLayout()
        row.addWidget(self.port_box, 1)
        row.addWidget(self.refresh_btn)
        f.addRow("Port", row)
        self.baud_box = QComboBox()
        self.baud_box.addItems(["9600", "57600", "115200", "230400"])
        self.baud_box.setCurrentText("115200")
        f.addRow("Baud", self.baud_box)
        self.mode_box = QComboBox()
        self.mode_box.addItem("Controller (poll READ?)", MODE_POLL)
        self.mode_box.addItem("Sensor node (stream)", MODE_STREAM)
        f.addRow("Mode", self.mode_box)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._toggle_connection)
        f.addRow(self.connect_btn)

        ctrl = QGroupBox("Controller")
        f2 = QFormLayout(ctrl)
        self.gain_spin = QDoubleSpinBox(minimum=0.1, maximum=10.0, singleStep=0.1, value=1.0, decimals=2)
        self.gain_btn = QPushButton("Set gain")
        self.gain_btn.clicked.connect(self._send_gain)
        row2 = QHBoxLayout()
        row2.addWidget(self.gain_spin, 1)
        row2.addWidget(self.gain_btn)
        f2.addRow("Gain", row2)

        limits = QGroupBox("Limits (g RMS)")
        f3 = QFormLayout(limits)
        t = self.monitor.thresholds
        self.warn_spin = QDoubleSpinBox(minimum=0.05, maximum=20, singleStep=0.1, value=t.warn)
        self.fault_spin = QDoubleSpinBox(minimum=0.1, maximum=50, singleStep=0.1, value=t.fault)
        self.warn_spin.valueChanged.connect(self._limits_changed)
        self.fault_spin.valueChanged.connect(self._limits_changed)
        f3.addRow("Warn at", self.warn_spin)
        f3.addRow("Fault at", self.fault_spin)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setMinimumHeight(64)
        font = QFont()
        font.setPointSize(20)
        font.setBold(True)
        self.status_label.setFont(font)
        self.reason_label = QLabel(wordWrap=True)
        self.value_label = QLabel("-- g")
        self.value_label.setAlignment(Qt.AlignCenter)
        vf = QFont()
        vf.setPointSize(14)
        self.value_label.setFont(vf)
        self.counts_label = QLabel()
        self.log_label = QLabel(wordWrap=True)
        self.log_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        side = QVBoxLayout()
        for w in (conn, ctrl, limits, self.status_label, self.value_label,
                  self.reason_label, self.counts_label, self.log_label):
            side.addWidget(w)
        side.addStretch(1)
        side_w = QWidget()
        side_w.setLayout(side)
        side_w.setFixedWidth(330)

        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget(background="w")
        self.plot.setLabel("left", "Vibration", units="g")
        self.plot.setLabel("bottom", "Time", units="s")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        # connect="finite" leaves a visible gap where a NaN marks a missed reply
        self.curve = self.plot.plot(pen=pg.mkPen("#2563eb", width=2), connect="finite")
        self.warn_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#d97706", style=Qt.DashLine))
        self.fault_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#dc2626", style=Qt.DashLine))
        self.plot.addItem(self.warn_line)
        self.plot.addItem(self.fault_line)
        self._update_limit_lines()

        root = QHBoxLayout()
        root.addWidget(side_w)
        root.addWidget(self.plot, 1)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self._set_connected_ui(False)

    # ---- helpers -------------------------------------------------------------

    def _refresh_ports(self):
        current = self.port_box.currentText()
        self.port_box.clear()
        self.port_box.addItems(available_ports() + [DEFAULT_SIM_URL])
        self.port_box.setCurrentText(current or DEFAULT_SIM_URL)

    def _set_connected_ui(self, connected):
        self.connect_btn.setText("Disconnect" if connected else "Connect")
        for w in (self.port_box, self.refresh_btn, self.baud_box, self.mode_box):
            w.setEnabled(not connected)
        poll = self.mode_box.currentData() == MODE_POLL
        self.gain_spin.setEnabled(connected and poll)
        self.gain_btn.setEnabled(connected and poll)

    def _show_status(self):
        s = self.monitor.status
        self.status_label.setText(s.value)
        self.status_label.setStyleSheet(
            f"background:{STATUS_COLORS[s]}; color:white; border-radius:8px;")
        self.reason_label.setText(self.monitor.reason)
        c = self.counts
        self.counts_label.setText(
            f"Readings {c['readings']}   WARN {c['warn']}   FAULT {c['fault']}\n"
            f"Timeouts {c['timeouts']}   Bad replies {c['bad']}")

    def _update_limit_lines(self):
        self.warn_line.setValue(self.monitor.thresholds.warn)
        self.fault_line.setValue(self.monitor.thresholds.fault)

    def _status_changed(self):
        s = self.monitor.status
        if s is Status.WARN:
            self.counts["warn"] += 1
        elif s is Status.FAULT:
            self.counts["fault"] += 1
        if self.logger:
            self.logger.status_change(s, self.monitor.reason)
        self.statusBar().showMessage(f"{time.strftime('%H:%M:%S')}  {s.value}: {self.monitor.reason}")

    # ---- user actions ------------------------------------------------------

    def _toggle_connection(self):
        if self.thread is None:
            self.connect_to(self.port_box.currentText().strip(), int(self.baud_box.currentText()),
                            self.mode_box.currentData())
        else:
            self.disconnect_link()

    def connect_to(self, url, baudrate=115200, mode=MODE_POLL):
        self.mode_box.setCurrentIndex(self.mode_box.findData(mode))
        self.port_box.setCurrentText(url)
        self.times.clear()
        self.values.clear()
        self.t0 = None
        self._link_failed = False
        self.logger = CsvLogger(CsvLogger.default_path(self.log_dir))
        self.log_label.setText(f"Logging to {self.logger.path}")
        self.thread = AcquisitionThread(url, baudrate, mode)
        self.thread.reading.connect(self._on_reading)
        self.thread.timed_out.connect(self._on_timeout)
        self.thread.bad_reply.connect(self._on_bad_reply)
        self.thread.device_fault.connect(self._on_device_fault)
        self.thread.device_ok.connect(self._on_device_ok)
        self.thread.link_opened.connect(self._on_opened)
        self.thread.link_lost.connect(self._on_lost)
        self.thread.command_result.connect(self._on_command_result)
        self.thread.finished.connect(self._on_thread_finished)
        self._set_connected_ui(True)
        self.connect_btn.setEnabled(False)
        self.thread.start()

    def disconnect_link(self):
        if self.thread:
            self.thread.stop()

    def _send_gain(self):
        if self.thread:
            self.thread.request_gain(self.gain_spin.value())

    def _limits_changed(self):
        try:
            self.monitor.thresholds = Thresholds(self.warn_spin.value(), self.fault_spin.value(),
                                                 self.monitor.thresholds.max_missed)
        except ValueError as exc:
            self.statusBar().showMessage(f"Limits not applied: {exc}")
            return
        self._update_limit_lines()
        if self.logger:
            self.logger.event(f"limits set warn={self.warn_spin.value():g} fault={self.fault_spin.value():g}")

    # ---- thread signals ----------------------------------------------------

    def _on_opened(self, url):
        self.connect_btn.setEnabled(True)
        self.logger.event(f"connected to {url}")
        self.statusBar().showMessage(f"Connected to {url}")

    def _on_lost(self, reason):
        if self.logger:
            self.logger.event(f"link closed: {reason}")
        self._link_failed = reason != "disconnected by user"
        if self._link_failed:
            # losing the port mid-run is a fault, not a normal disconnect
            self.monitor.on_link_lost(reason)
            self._status_changed()
        self.statusBar().showMessage(f"Link closed: {reason}")

    def _on_thread_finished(self):
        if not self._link_failed:
            self.monitor.on_disconnect()
        self.thread = None
        if self.logger:
            self.logger.close()
            self.logger = None
        self.connect_btn.setEnabled(True)
        self._set_connected_ui(False)
        self._show_status()

    def _append_point(self, t, value):
        if self.t0 is None:
            self.t0 = t
        x = t - self.t0
        self.times.append(x)
        self.values.append(value)
        while self.times and self.times[0] < x - HISTORY_S:
            self.times.popleft()
            self.values.popleft()
        self.curve.setData(list(self.times), list(self.values))

    def _on_reading(self, t, value):
        self._append_point(t, value)
        self.counts["readings"] += 1
        changed = self.monitor.on_value(value)
        self.logger.reading(value, self.monitor.status)
        if changed:
            self._status_changed()
        self.value_label.setText(f"{value:.3f} g")
        self._show_status()

    def _on_timeout(self):
        self.counts["timeouts"] += 1
        self._append_point(time.monotonic(), float("nan"))
        self.logger.event("timeout waiting for reply")
        if self.monitor.on_timeout():
            self._status_changed()
        self._show_status()

    def _on_bad_reply(self, msg):
        self.counts["bad"] += 1
        self.logger.event(f"bad reply: {msg}")
        self._show_status()

    def _on_device_fault(self, code):
        if self.monitor.on_device_fault(code):
            self._status_changed()
        self._show_status()

    def _on_device_ok(self):
        if self.monitor.on_device_ok():
            self.logger.event("controller fault cleared (STATUS? OK)")

    def _on_command_result(self, ok, msg):
        self.logger.event(msg)
        self.statusBar().showMessage(msg)

    def closeEvent(self, event):
        if self.thread:
            self.thread.stop()
            self.thread.wait(2000)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
