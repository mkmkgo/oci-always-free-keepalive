#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
oci-keepalive-check -- verify the rolling 7-day CPU P95 against Oracle's
Always Free idle threshold (20%).

Usage:
    oci-keepalive-check            human-readable report
    oci-keepalive-check --json     machine-readable output
    oci-keepalive-check --strict   non-zero exit code when unsafe / no data
"""
import argparse
import json
import os
import subprocess
import sys
import time

CONF_FILE = "/etc/oci-keepalive.conf"
DEFAULT_METRICS = "/var/lib/oci-keepalive/metrics.jsonl"
THRESHOLD = 0.20          # official: CPU P95 must NOT stay below 20%
WINDOW = 7 * 86400
FULL_SAMPLES = 7 * 1440   # one sample per minute for a full week

LINE = "=" * 62
DASH = "-" * 62


def load_conf():
    conf = {}
    try:
        with open(CONF_FILE, "r", encoding="utf-8") as f:
            for raw in f:
                row = raw.strip()
                if not row or row.startswith("#") or "=" not in row:
                    continue
                key, value = row.split("=", 1)
                conf[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return conf


def _run(args):
    try:
        return subprocess.run(
            args, capture_output=True, text=True, timeout=5
        ).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def service_state():
    return (
        _run(["systemctl", "is-active", "oci-keepalive"]),
        _run(["systemctl", "is-enabled", "oci-keepalive"]),
    )


def monitoring_state():
    try:
        out = subprocess.run(
            ["ps", "-eo", "args"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if "unifiedmonitoring" in out or "oracle-cloud-agent" in out:
        return "running"
    return "not-detected"


def percentile(values, p):
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def read_metrics(path):
    cutoff = time.time() - WINDOW
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                try:
                    item = json.loads(raw)
                    ts = float(item.get("ts", 0))
                    if ts >= cutoff:
                        rows.append(
                            (
                                ts,
                                float(item.get("cpu", 0)),
                                float(item.get("fill", 0)),
                            )
                        )
                except (ValueError, TypeError, AttributeError):
                    continue
    except OSError:
        pass
    return rows


def collect():
    conf = load_conf()
    path = conf.get("KEEPALIVE_METRICS_FILE", DEFAULT_METRICS)
    try:
        target = float(conf.get("KEEPALIVE_TARGET", 0.28))
    except (TypeError, ValueError):
        target = 0.28

    rows = read_metrics(path)
    cpus = [r[1] for r in rows]
    fills = [r[2] for r in rows]
    span = (max(r[0] for r in rows) - min(r[0] for r in rows)) if rows else 0.0

    active, enabled = service_state()
    p95 = percentile(cpus, 0.95)
    result = {
        "service": {"active": active, "enabled": enabled},
        "monitoring_agent": monitoring_state(),
        "metrics_file": path,
        "samples": len(rows),
        "window_days": round(span / 86400, 2),
        "cpu_p95": round(p95, 4),
        "cpu_avg": round(sum(cpus) / len(cpus), 4) if cpus else 0.0,
        "fill_avg": round(sum(fills) / len(fills), 4) if fills else 0.0,
        "target": round(target, 4),
        "threshold": THRESHOLD,
        "full_window": span >= 6.5 * 86400,
        "safe": bool(rows) and p95 >= THRESHOLD,
    }
    return result


def render(r):
    print(LINE)
    print("  oci-keepalive  |  Oracle Always Free idle-risk self check")
    print(LINE)
    print("  service          {} / {}".format(r["service"]["active"], r["service"]["enabled"]))
    print("  metrics          {} sample(s), {:.2f} day(s) covered, file {}".format(
        r["samples"], r["window_days"], r["metrics_file"]))
    print("  monitoring       {} (OCI console CPU metrics)".format(r["monitoring_agent"]))
    print("  target CPU       {:.1%}   |   official idle line {:.0%} (7-day P95)".format(
        r["target"], r["threshold"]))
    print(DASH)

    if not r["samples"]:
        print("  CPU P95          n/a")
        print("  [WAIT] No samples yet. Wait ~1 minute, then run this again.")
        print(LINE)
        return

    print("  CPU P95          {:.1%}   ({} {:.0%} threshold)".format(
        r["cpu_p95"], "above" if r["cpu_p95"] >= r["threshold"] else "BELOW", r["threshold"]))
    print("  CPU average      {:.1%}".format(r["cpu_avg"]))
    print("  fill duty        {:.1%}   (controller duty cycle, not CPU share)".format(r["fill_avg"]))
    print(DASH)

    if r["safe"]:
        print("  [OK]   CPU P95 >= {:.0%}: the 7-day idle condition is broken.".format(r["threshold"]))
    else:
        print("  [WARN] CPU P95 < {:.0%}: raise KEEPALIVE_TARGET or check the service.".format(r["threshold"]))
    if not r["full_window"]:
        print("  [NOTE] Less than 7 days of samples collected; result is provisional.")
    if r["monitoring_agent"] == "not-detected":
        print("  [NOTE] OCI monitoring agent not detected; console metrics may be missing.")
    print(LINE)


def main():
    ap = argparse.ArgumentParser(description="7-day CPU P95 self check for oci-keepalive")
    ap.add_argument("--json", action="store_true", help="print machine-readable JSON")
    ap.add_argument("--strict", action="store_true", help="exit 1 when unsafe, 2 when no data")
    args = ap.parse_args()

    r = collect()
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        render(r)

    if args.strict:
        if not r["samples"]:
            return 2
        if not r["safe"]:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
