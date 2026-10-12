"""Run the app against the simulator and save a screenshot and a GIF for the README.

    python tools/capture_demo.py
"""
import socket
import sys
import threading
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vibmon.app import MainWindow  # noqa: E402
from vibmon.simulator import ControllerSim, _serve_commands, run_tcp  # noqa: E402

DOCS = ROOT / "docs"
RUN_S = 30
FRAME_MS = 400
GIF_FROM_S = 14
GAIN_UP_MS = 18000    # push readings into the WARN band to show SET GAIN working
GAIN_DOWN_MS = 25200


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def to_pil(pixmap):
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pixmap.save(buf, "PNG")
    from io import BytesIO
    return Image.open(BytesIO(bytes(buf.data()))).convert("RGB")


def main():
    DOCS.mkdir(exist_ok=True)
    port = free_port()
    sim = ControllerSim(spike_prob=0.015, silence_prob=0.003, silence_s=2.0, seed=7)
    threading.Thread(target=run_tcp, args=(port, lambda r, w: _serve_commands(r, w, sim)), daemon=True).start()

    app = QApplication(sys.argv)
    w = MainWindow(log_dir="logs")
    w.show()
    QTimer.singleShot(300, lambda: w.connect_to(f"socket://localhost:{port}"))

    frames = []
    elapsed = {"ms": 0}

    def tick():
        elapsed["ms"] += FRAME_MS
        if elapsed["ms"] == GAIN_UP_MS:
            w.gain_spin.setValue(2.8)
            w.gain_btn.click()
        if elapsed["ms"] == GAIN_DOWN_MS:
            w.gain_spin.setValue(1.0)
            w.gain_btn.click()
        if elapsed["ms"] >= GIF_FROM_S * 1000:
            frames.append(to_pil(w.grab()).resize((720, 393), Image.LANCZOS))
        if elapsed["ms"] >= RUN_S * 1000:
            w.grab().save(str(DOCS / "screenshot.png"))
            w.disconnect_link()
            timer.stop()
            QTimer.singleShot(800, app.quit)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(FRAME_MS)
    app.exec()

    pal = [f.quantize(colors=48, method=Image.MEDIANCUT) for f in frames]
    pal[0].save(DOCS / "demo.gif", save_all=True, append_images=pal[1:], duration=FRAME_MS, loop=0, optimize=True)
    print(f"saved {DOCS / 'screenshot.png'} and {DOCS / 'demo.gif'} ({len(frames)} frames)")
    print(f"readings={w.counts['readings']} warn={w.counts['warn']} fault={w.counts['fault']} "
          f"timeouts={w.counts['timeouts']} injected={sim.faults_injected}")


if __name__ == "__main__":
    main()
