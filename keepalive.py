#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
oci-keepalive -- adaptive CPU floor for Oracle Cloud Always Free instances.

Oracle deems an Always Free compute instance idle when, during a rolling
7-day period, ALL of the following are true:

  * 95th-percentile CPU utilization < 20%
  * Network utilization < 20%
  * Memory utilization < 20%   (A1 shapes only)

Because the conditions are ANDed, keeping CPU P95 above 20% is sufficient.
This service holds total CPU utilization near TARGET (default 28%) and yields
completely to real workloads. It does no network I/O, no mining, allocates no
large buffers, and never modifies firewall or system settings.

Configuration is read from the environment (systemd EnvironmentFile).
See oci-keepalive.conf.example for all options.
"""
import fcntl
import json
import os
import signal
import sys
import time
from multiprocessing import Event, Process, Value

VERSION = "1.0.0"

RETENTION = 7 * 86400          # keep exactly the rolling window Oracle looks at
MAX_LINES = 11000             # 7 days * 1440 samples + headroom


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        print("warn: invalid {}, using {}".format(name, default), flush=True)
        return float(default)


def _env_int(name, default):
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        print("warn: invalid {}, using {}".format(name, default), flush=True)
        return int(default)


TARGET = min(max(_env_float("KEEPALIVE_TARGET", 0.28), 0.01), 1.0)
PERIOD = max(_env_float("KEEPALIVE_PERIOD", 1.0), 0.2)
CAP = min(max(_env_float("KEEPALIVE_CAP", 0.90), 0.05), 0.95)
GAIN = min(max(_env_float("KEEPALIVE_GAIN", 0.6), 0.05), 2.0)
LOG_EVERY = _env_int("KEEPALIVE_LOG_EVERY", 60)
METRICS_EVERY = _env_int("KEEPALIVE_METRICS_EVERY", 60)
METRICS_FILE = os.environ.get(
    "KEEPALIVE_METRICS_FILE", "/var/lib/oci-keepalive/metrics.jsonl"
)
MAX_LOAD = _env_float("KEEPALIVE_MAX_LOAD", 0)  # 0 = disabled

try:
    NCPU = max(1, min(len(os.sched_getaffinity(0)), 16))
except AttributeError:
    NCPU = max(1, min(os.cpu_count() or 1, 16))


LOCK_PATH = "/run/oci-keepalive.lock"


def acquire_lock():
    """Prevent a second copy from doubling the CPU floor.

    Exactly one shared lock path. install.sh creates it world-writable (0666)
    so an unprivileged manual run sees the very same lock the service holds.
    Never fall back to another path -- that lets a second instance slip by.
    """
    try:
        handle = open(LOCK_PATH, "w")
    except OSError as exc:
        print(
            "error: cannot open {} ({}) -- install via install.sh or run as root".format(LOCK_PATH, exc),
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        print("error: another oci-keepalive instance is already running", file=sys.stderr)
        sys.exit(1)
    handle.write("{}\n".format(os.getpid()))
    handle.flush()
    return handle


def burn(seconds, stop):
    """Pure integer ALU work: no allocations, no syscalls, no I/O."""
    end = time.monotonic() + seconds
    x = 0
    while not stop.is_set() and time.monotonic() < end:
        x = (x * 6364136223846793005 + 1442695040888963407) & 0xFFFFFFFFFFFFFFFF
    return x


def worker(share, stop):
    """One worker per visible CPU; the controller decides how much to burn."""
    while not stop.is_set():
        b = min(max(float(share.value), 0.0), PERIOD * CAP)
        if b > 0.002:
            burn(b, stop)
        end = time.monotonic() + (PERIOD - b)
        while not stop.is_set():
            left = end - time.monotonic()
            if left <= 0:
                break
            time.sleep(min(left, 0.2))


def read_cpu():
    with open("/proc/stat", "r", encoding="ascii") as f:
        v = [int(x) for x in f.readline().split()[1:]]
    # user nice system idle iowait irq softirq ...
    return float(sum(v)), float(v[3] + v[4])


def overloaded():
    """Optional extra guard for I/O-bound workloads that raise load but not CPU."""
    if MAX_LOAD <= 0:
        return False
    try:
        with open("/proc/loadavg", "r", encoding="ascii") as f:
            return float(f.read().split()[0]) > MAX_LOAD
    except (OSError, ValueError, IndexError):
        return False


def append_metric(ts, cpu, fill):
    record = {
        "ts": int(ts),
        "cpu": round(cpu, 4),
        "fill": round(fill, 4),
        "target": round(TARGET, 4),
    }
    try:
        os.makedirs(os.path.dirname(METRICS_FILE) or ".", exist_ok=True)
        with open(METRICS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError as exc:
        print("warn: cannot write metrics: {}".format(exc), flush=True)


def prune_metrics(ts):
    """Drop samples older than the rolling 7-day window (once per day)."""
    cutoff = ts - RETENTION
    try:
        with open(METRICS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        keep = []
        for line in lines:
            try:
                if json.loads(line).get("ts", 0) >= cutoff:
                    keep.append(line)
            except (ValueError, AttributeError):
                continue
        keep = keep[-MAX_LINES:]
        tmp = METRICS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(keep)
        os.replace(tmp, METRICS_FILE)
    except OSError:
        pass


def main():
    lock = acquire_lock()  # keep the fd alive for the process lifetime
    stop = Event()
    share = Value("d", 0.0)
    procs = [Process(target=worker, args=(share, stop), daemon=True) for _ in range(NCPU)]
    for p in procs:
        p.start()

    def shutdown(signum, frame):
        stop.set()
        for p in procs:
            p.join(timeout=2)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    busy = 0.0
    prev = read_cpu()
    n = 0
    last_day = int(time.time() // 86400)
    print(
        "oci-keepalive {} start: target={:.0%} period={}s workers={} max_load={}".format(
            VERSION, TARGET, PERIOD, NCPU, MAX_LOAD or "off"
        ),
        flush=True,
    )

    while not stop.is_set():
        time.sleep(PERIOD)
        try:
            cur = read_cpu()
        except OSError:
            continue
        dt, di = cur[0] - prev[0], cur[1] - prev[1]
        prev = cur
        if dt <= 0:
            continue
        util = 1.0 - di / dt

        if overloaded():
            busy = 0.0
        else:
            # Integral feedback: business busy -> busy decays to 0; idle -> fills to TARGET
            busy = min(max(busy + (TARGET - util) * PERIOD * GAIN, 0.0), PERIOD * CAP)
        share.value = busy

        n += 1
        fill = busy / PERIOD
        if LOG_EVERY and n % max(1, int(LOG_EVERY / PERIOD)) == 0:
            print("cpu={:.1%} fill={:.1%} target={:.0%}".format(util, fill, TARGET), flush=True)
        if METRICS_EVERY and n % max(1, int(METRICS_EVERY / PERIOD)) == 0:
            now = time.time()
            append_metric(now, util, fill)
            day = int(now // 86400)
            if day != last_day:
                prune_metrics(now)
                last_day = day

    del lock


if __name__ == "__main__":
    if "--version" in sys.argv:
        print("oci-keepalive {}".format(VERSION))
        sys.exit(0)
    main()
