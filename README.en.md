<div align="center">

# oci-keepalive

**Keep Oracle Cloud Always Free instances out of the "idle" bucket — without getting in the way of your real workloads.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20systemd-1f6feb?logo=linux&logoColor=white)](https://systemd.io/)
[![Oracle Cloud](https://img.shields.io/badge/Oracle%20Cloud-Always%20Free-f80000?logo=oracle&logoColor=white)](https://www.oracle.com/cloud/free/)

[中文文档](README.md)

</div>

---

## The problem

Oracle reclaims Always Free instances that sit idle. The usual advice is to run `stress`, timed `dd` loops, or even a miner — which either slows your own sites to a crawl, violates the terms of service, or leaves you with no idea whether you actually meet the threshold.

`oci-keepalive` takes a different approach:

> **Fill only the CPU time nobody is using, and step aside the moment a real workload shows up.** Then show you the exact metric Oracle uses (7-day CPU P95).

No cracking, no billing tricks, no network calls, no mining, no firewall changes. One Python script, one systemd unit, ~10 MB of RAM, and a built-in proof that you are compliant.

---

## What Oracle actually checks

From the official docs, [*Reclamation of Idle Compute Instances*](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm):

> Idle Always Free compute instances **may** be reclaimed by Oracle. Oracle will deem ... instances as idle if, during a **7-day period**, the following are true:
> - CPU utilization for the **95th percentile** is less than **20%**
> - Network utilization is less than **20%**
> - Memory utilization is less than **20%** *(applies to A1 shapes only)*

Three details most tutorials get wrong:

| Detail | Meaning |
|---|---|
| **AND, not OR** | All conditions must hold. Breaking **any one** of them is enough |
| **CPU uses P95** | Not the mean. >5% of the week above 20% pushes P95 over the line |
| **Threshold is 20%** | The 10% / 15% figures circulating online are outdated (verified Nov 2026) |

Therefore the cheapest, least invasive fix is to keep **CPU P95 above 20%**. There is no need to fabricate 20% network bandwidth or pin gigabytes of RAM.

> Oracle says resources *may* be reclaimed; the docs do not state whether that means stop or terminate, nor whether a warning email is sent. Check the official docs and your tenancy notices.

---

## Features

- **Adaptive feedback control** — samples `/proc/stat` every second and fills only the gap to `TARGET` (default 28%)
- **Three isolation layers** — `Nice=19`, `CPUWeight=1`, and a hard `CPUQuota`; plus `MemoryMax=64M`
- **Multi-core aware** — one worker per visible CPU, and the installer computes `CPUQuota` from core count
- **7-day P95 self check** — `oci-keepalive-check` tells you SAFE / AT RISK, with `--json` for automation
- **Config separated from code** — edit `/etc/oci-keepalive.conf`, no unit file surgery
- **Single-instance lock** — accidental double starts cannot double the load
- **Optional load guard** — `KEEPALIVE_MAX_LOAD` pauses filling when load average spikes
- **Zero dependencies** — Python stdlib + systemd only; no packages, no cron, no network
- **Idempotent install / one-command uninstall** — re-running upgrades scripts and preserves your config
- **Reproducible preemption test** — `tests/test_preemption.py` measures real CPU time from `/proc`

---

## Quick start

```bash
curl -fsSL https://raw.githubusercontent.com/mkmkgo/oci-always-free-keepalive/main/install.sh | sudo bash
```

Requires `systemd`, `Python >= 3.8`, and root. From a clone: `sudo bash install.sh`.

```bash
sudo systemctl status oci-keepalive      # active (running) + enabled
sudo journalctl -u oci-keepalive -f      # live cpu= / fill= lines
oci-keepalive-check                      # 7-day P95 report
```

---

## How it works

```
busy = clamp( busy + (TARGET - util) * PERIOD * GAIN , 0 , PERIOD * CAP )
```

`util` is total CPU utilization measured from `/proc/stat`. When real work pushes `util` above the target, `busy` decays to **0** within a few cycles. When the machine is idle, `busy` rises until total utilization converges on `TARGET`. Workers burn time with pure integer arithmetic — no allocations, no syscalls, no I/O.

Measured on an A1 Flex (1 OCPU / 6 GB, Ubuntu 24.04) while a PHP build was running:

| Scenario | Business CPU | Log line | Keepalive CPU |
|---|---|---|---|
| Build saturating the core | ~94% | `cpu=100.0% fill=0.0% target=28%` | **1.9%** |
| Target temporarily raised to 150% | — | `fill 0% -> 60% -> 90% (cap)` | fill path verified |
| Idle (measured) | ~1% | `cpu=28.7% fill=54.2% target=28%` | 25-26%, total CPU steady at 28% |

`fill` is the controller's duty cycle (throttled by `CPUQuota`), not the actual CPU share; use `cpu=` and the self-check P95 to judge compliance.

---

## Verify: does a real workload preempt the filler?

`tests/test_preemption.py` measures real CPU time from `/proc/<pid>/stat` (not `ps`'s lifetime average). Measured on an A1 Flex (1 OCPU / 6 GB):

```text
IDLE       : worker gets 20.0% of one core
UNDER LOAD : business (nice=0) gets 98.8% | worker gets 0.7%
RECOVERY   : worker back to 26.7%
RESULT     : PASS - real workload preempts the filler
```

The service log moves from `fill=54.5%` to `fill=7.4%` under load, and cgroup `cpu.stat` shows `nr_throttled=3537 / nr_periods=5610`: `Nice=19` + `CPUWeight=1` hand almost all time slices to the business, `CPUQuota` hard-limits the filler before the controller even reacts, and the feedback loop drops the fill to near zero, then recovers when the business stops.

Caveats: CFS is not zero-latency hard preemption (a few milliseconds at most); a business process that is itself `nice 19` has no priority over the filler; for I/O-bound workloads (high load, low CPU) enable `KEEPALIVE_MAX_LOAD`. Memory is capped at 64 MB (~9 MB observed), there is no network activity, and disk I/O is one small sample per minute at idle priority.

---

## Prove it: the 7-day check

The service appends one sample per minute to `/var/lib/oci-keepalive/metrics.jsonl`, automatically pruned to a rolling 7 days. `check.py` computes the same percentile Oracle uses:

```text
==============================================================
  oci-keepalive  |  Oracle Always Free idle-risk self check
==============================================================
  service          active / enabled
  metrics          10080 sample(s), 7.00 day(s) covered
  monitoring       running (OCI console CPU metrics)
  target CPU       28.0%   |   official idle line 20% (7-day P95)
--------------------------------------------------------------
  CPU P95          28.4%   (above 20% threshold)
  CPU average      27.9%
  fill duty        22.1%   (controller duty cycle, not CPU share)
--------------------------------------------------------------
  [OK]   CPU P95 >= 20%: the 7-day idle condition is broken.
==============================================================
```

```bash
oci-keepalive-check --json     # machine-readable
oci-keepalive-check --strict   # exit 1 = unsafe, 2 = no data
```

It also reports whether the OCI monitoring agent is running — without it, the console may show no CPU curve at all.

---

## Configuration

Edit `/etc/oci-keepalive.conf`, then `sudo systemctl restart oci-keepalive`.

| Variable | Default | Description |
|---|---|---|
| `KEEPALIVE_TARGET` | `0.28` | Target total CPU utilization (0.01–1.0). Official line is 20% |
| `KEEPALIVE_PERIOD` | `1.0` | Controller period in seconds |
| `KEEPALIVE_CAP` | `0.90` | Max fraction of a period spent burning CPU |
| `KEEPALIVE_GAIN` | `0.6` | Integral gain |
| `KEEPALIVE_LOG_EVERY` | `60` | Status log interval, 0 disables |
| `KEEPALIVE_METRICS_EVERY` | `60` | Sample interval, 0 disables |
| `KEEPALIVE_METRICS_FILE` | `/var/lib/oci-keepalive/metrics.jsonl` | Sample file |
| `KEEPALIVE_MAX_LOAD` | `0` | Pause filling above this 1-min load average; 0 disables |

After raising `KEEPALIVE_TARGET`, re-run `install.sh` so `CPUQuota` is recalculated for the new target.

---

## Stop, disable, uninstall

| Goal | Command | After reboot |
|---|---|---|
| Pause temporarily | `sudo systemctl stop oci-keepalive` | Starts again |
| Disable for good, keep files | `sudo systemctl disable --now oci-keepalive` | Stays off |
| Re-enable | `sudo systemctl enable --now oci-keepalive` | Auto-starts |
| Uninstall | `curl -fsSL .../uninstall.sh \| sudo bash` | Config + metrics kept |
| Uninstall and wipe data | `curl -fsSL .../uninstall.sh \| sudo bash -s -- --purge` | Everything removed |

---

## FAQ

**Will it slow my sites down?** That is what it is built to avoid. Real work raises measured utilization, the fill decays to zero, and `Nice=19` / `CPUWeight=1` / `CPUQuota` guarantee business processes win. In the test above, keepalive used 1.9% while a build used 94%.

**Is this against the rules?** Oracle's policy describes utilization, not intent. This project does not fake metrics, mine, phone home, or alter network settings. Policies can change — verify the current terms yourself. Upgrading to **Pay As You Go** is another common option (Always Free limits still cost nothing), but Oracle's docs do **not** explicitly state an exemption, and exceeding free limits is billable.

**Why not fake network/memory too?** Because the conditions are ANDed. CPU P95 ≥ 20% alone breaks the conjunction.

**How do I confirm on Oracle's side?** OCI Console → Compute → Instances → your instance → **Metrics → CPU Utilization**. `oci-keepalive-check` reports whether the monitoring agent is alive.

**What about 2 / 4 OCPU shapes?** Just re-run `install.sh`: worker count follows CPU affinity and `CPUQuota` is recalculated.

**Non-systemd?** Not supported yet. The script runs standalone but loses auto-start and resource limits.

---

## License

[MIT](LICENSE). Not affiliated with Oracle. Oracle Cloud and Always Free are Oracle trademarks.
