# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] - 2026-11-16

### Added

- Adaptive CPU floor controller (`keepalive.py`): integral feedback on total CPU utilization, one worker per visible CPU, yields completely to real workloads.
- systemd unit with `Nice=19`, `CPUWeight=1`, `CPUQuota`, `MemoryMax=64M`, `IOSchedulingClass=idle`, `NoNewPrivileges=true`, and boot-time enable.
- Rolling 7-day metric samples and `check.py` / `oci-keepalive-check` self check that computes CPU P95 against Oracle's 20% idle line, with `--json` and `--strict` modes.
- External config file `/etc/oci-keepalive.conf`, including `KEEPALIVE_MAX_LOAD` guard for I/O-heavy workloads.
- Idempotent `install.sh` with root/systemd/Python checks, remote-or-local file sourcing, and automatic `CPUQuota` calculation from core count.
- `uninstall.sh` with `--purge` option.
- Single-instance lock to prevent duplicate fillers.
- Bilingual README, MIT license, contributing guide, and CI (py_compile, bash -n, shellcheck).

### Fixed

- Singleton lock no longer falls back to a second path (which allowed a duplicate instance); installer now creates one shared world-writable lock.
- Self-check no longer double-multiplies the 20% threshold in its verdict lines.
