#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Integration test: prove that a real workload preempts the keepalive filler.

Run on a machine where oci-keepalive is installed and active:
    sudo python3 tests/test_preemption.py

It measures real CPU time from /proc/<pid>/stat over a sampling window, not
ps's lifetime average, so the numbers reflect the contention period only.
"""
import os
import subprocess
import sys
import time

HZ = os.sysconf("SC_CLK_TCK")
BUSY_SRC = (
    "x=0\n"
    "while True:\n"
    "    x=(x*6364136223846793005+1442695040888963407)&0xFFFFFFFFFFFFFFFF\n"
)


def stat_fields(pid):
    try:
        with open("/proc/{}/stat".format(pid), "r", encoding="ascii") as f:
            return f.read().split()
    except OSError:
        return None


def cpu_seconds(pid):
    fields = stat_fields(pid)
    if not fields or len(fields) < 15:
        return 0.0
    return (int(fields[13]) + int(fields[14])) / HZ  # utime + stime


def nice_of(pid):
    fields = stat_fields(pid)
    return int(fields[18]) if fields and len(fields) > 18 else 0


def find_pids():
    out = subprocess.run(
        ["pgrep", "-f", "keepalive.py"], capture_output=True, text=True
    ).stdout
    pids = [int(x) for x in out.split() if x.isdigit()]
    workers, main_pid = [], None
    for pid in pids:
        fields = stat_fields(pid)
        if not fields or len(fields) < 4:
            continue
        if int(fields[3]) in pids:
            workers.append(pid)
        else:
            main_pid = pid
    return main_pid, workers


def shares(pids, seconds):
    t0 = time.monotonic()
    before = {pid: cpu_seconds(pid) for pid in pids}
    time.sleep(seconds)
    elapsed = time.monotonic() - t0
    after = {pid: cpu_seconds(pid) for pid in pids}
    return {pid: (after[pid] - before[pid]) / elapsed for pid in pids}


def main():
    main_pid, workers = find_pids()
    if not workers:
        print("FAIL: no keepalive worker found. Is the service active?")
        return 2
    worker = workers[0]
    print("keepalive main={} worker={} nice={}".format(main_pid, worker, nice_of(worker)))

    baseline = shares([worker], 3)[worker]
    print("IDLE       : worker gets {:.1%} of one core".format(baseline))

    business = subprocess.Popen(
        [sys.executable, "-c", BUSY_SRC],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(1.5)  # let the feedback controller back off first
        measured = shares([business.pid, worker], 6)
        biz_share = measured[business.pid]
        worker_share = measured[worker]
        print(
            "UNDER LOAD : business (nice={}) gets {:.1%} | worker gets {:.1%}".format(
                nice_of(business.pid), biz_share, worker_share
            )
        )
    finally:
        business.kill()
        business.wait()

    time.sleep(6)
    recovered = shares([worker], 3)[worker]
    print("RECOVERY   : worker back to {:.1%} of one core".format(recovered))

    ok = biz_share > 0.85 and worker_share < 0.15 and recovered > 0.15
    print(
        "RESULT     : {}".format(
            "PASS - real workload preempts the filler" if ok else "FAIL - review the service"
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
