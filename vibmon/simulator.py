"""Stand-in for the vibration controller (and the sensor node) so the app can be
developed and tested without hardware.

Command mode (default) answers the controller protocol:
    python -m vibmon.simulator --tcp 7000          app connects to socket://localhost:7000
    python -m vibmon.simulator --port COM11        one end of a com0com / socat pair

Stream mode imitates the STM32 sensor node, pushing `timestamp,ax,ay,az` lines:
    python -m vibmon.simulator --tcp 7001 --stream --rate 200

Faults are injected at random: short spikes above the fault limit, and periods
of silence where the controller stops answering.
"""
import argparse
import math
import random
import socket
import time


class ControllerSim:
    def __init__(self, base_rms=0.35, spike_prob=0.02, silence_prob=0.004,
                 silence_s=2.0, seed=None, clock=time.monotonic):
        self.gain = 1.0
        self.base_rms = base_rms
        self.spike_prob = spike_prob
        self.silence_prob = silence_prob
        self.silence_s = silence_s
        self.rng = random.Random(seed)
        self.clock = clock
        self.t0 = clock()
        self.silent_until = 0.0
        self.faults_injected = {"spike": 0, "silence": 0}

    def reading(self):
        t = self.clock() - self.t0
        # slow drift (machine load changing) plus sensor noise
        level = self.base_rms * (1 + 0.25 * math.sin(2 * math.pi * t / 20)) + self.rng.gauss(0, 0.03)
        if self.rng.random() < self.spike_prob:
            self.faults_injected["spike"] += 1
            level = self.rng.uniform(2.2, 3.5)
        return max(0.0, level * self.gain)

    def handle(self, line):
        """Return the reply for one command line, or None to stay silent."""
        now = self.clock()
        if now < self.silent_until:
            return None
        if self.silence_prob and self.rng.random() < self.silence_prob:
            self.faults_injected["silence"] += 1
            self.silent_until = now + self.silence_s
            return None

        cmd = line.strip()
        upper = cmd.upper()
        if upper == "STATUS?":
            return "OK"
        if upper == "READ?":
            return f"VAL {self.reading():.4f}"
        if upper == "GAIN?":
            return f"GAIN {self.gain:g}"
        if upper.startswith("SET GAIN"):
            arg = cmd[len("SET GAIN"):].strip()
            try:
                g = float(arg)
            except ValueError:
                return "ERR BAD_ARGUMENT"
            if not 0.1 <= g <= 10:
                return "ERR OUT_OF_RANGE"
            self.gain = g
            return "ACK"
        return "ERR UNKNOWN_COMMAND"


class SensorNodeSim:
    """Generates the same line format the STM32 firmware sends."""

    def __init__(self, rate_hz=200, vib_hz=50, amplitude_g=0.3, seed=None):
        self.rate_hz = rate_hz
        self.vib_hz = vib_hz
        self.amplitude_g = amplitude_g
        self.rng = random.Random(seed)
        self.n = 0

    def next_line(self):
        t = self.n / self.rate_hz
        ts_ms = int(t * 1000)
        a = self.amplitude_g * math.sin(2 * math.pi * self.vib_hz * t)
        ax = 0.5 * a + self.rng.gauss(0, 0.01)
        ay = 0.3 * a + self.rng.gauss(0, 0.01)
        az = 1.0 + a + self.rng.gauss(0, 0.01)
        self.n += 1
        return f"{ts_ms},{ax:.4f},{ay:.4f},{az:.4f}"


# ---- transports --------------------------------------------------------------

def _serve_commands(read_line, write_line, sim):
    while True:
        line = read_line()
        if line is None:
            return
        if not line.strip():
            continue
        reply = sim.handle(line)
        if reply is not None:
            write_line(reply)


def _serve_stream(write_line, node, rate_hz):
    period = 1.0 / rate_hz
    next_t = time.perf_counter()
    while True:
        write_line(node.next_line())
        next_t += period
        delay = next_t - time.perf_counter()
        if delay > 0:
            time.sleep(delay)


def run_tcp(port, make_handler):
    with socket.create_server(("127.0.0.1", port)) as srv:
        print(f"simulator listening on socket://localhost:{port}")
        while True:
            conn, addr = srv.accept()
            print(f"client connected from {addr[0]}:{addr[1]}")
            with conn, conn.makefile("rwb", buffering=0) as f:
                def read_line():
                    data = f.readline()
                    return data.decode("ascii", "replace") if data else None

                def write_line(text):
                    f.write((text + "\n").encode("ascii"))

                try:
                    make_handler(read_line, write_line)
                except (ConnectionError, OSError):
                    pass
            print("client disconnected")


def run_serial(port, baud, make_handler):
    import serial

    with serial.Serial(port, baud, timeout=None) as ser:
        print(f"simulator on {port} @ {baud} baud")

        def read_line():
            return ser.readline().decode("ascii", "replace")

        def write_line(text):
            ser.write((text + "\n").encode("ascii"))

        make_handler(read_line, write_line)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--tcp", type=int, metavar="PORT")
    where.add_argument("--port", metavar="SERIAL_PORT")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--stream", action="store_true", help="imitate the sensor node instead of the controller")
    p.add_argument("--rate", type=int, default=200, help="stream mode sample rate in Hz")
    p.add_argument("--spike-prob", type=float, default=0.02)
    p.add_argument("--silence-prob", type=float, default=0.004)
    p.add_argument("--no-faults", action="store_true")
    p.add_argument("--seed", type=int)
    args = p.parse_args(argv)

    def make_handler(read_line, write_line):
        if args.stream:
            _serve_stream(write_line, SensorNodeSim(args.rate, seed=args.seed), args.rate)
        else:
            sim = ControllerSim(
                spike_prob=0 if args.no_faults else args.spike_prob,
                silence_prob=0 if args.no_faults else args.silence_prob,
                seed=args.seed,
            )
            _serve_commands(read_line, write_line, sim)

    try:
        if args.tcp:
            run_tcp(args.tcp, make_handler)
        else:
            run_serial(args.port, args.baud, make_handler)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
