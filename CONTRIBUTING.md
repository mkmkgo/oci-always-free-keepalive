# Contributing

Thanks for helping improve `oci-keepalive`.

## Ground rules

- **Keep it boring and safe.** No network calls, no mining, no faking OCI metrics, no changes to firewall or system configuration. The whole point is that this is a harmless, auditable CPU filler.
- **Business first.** Any change must preserve the guarantee that real workloads outrank the filler (`Nice`, `CPUWeight`, `CPUQuota`, and the adaptive feedback).
- **No dependencies.** Python standard library + systemd only.
- **Idempotency matters.** `install.sh` must be safe to re-run and must never overwrite `/etc/oci-keepalive.conf`.

## Local checks

```bash
python3 -m py_compile keepalive.py check.py
python3 check.py --json
bash -n install.sh uninstall.sh
shellcheck -x -S warning install.sh uninstall.sh   # if available
```

## Testing on a real instance

```bash
sudo bash install.sh
sudo systemctl status oci-keepalive
sudo journalctl -u oci-keepalive -f
oci-keepalive-check
```

A good regression test: start a CPU-heavy job (`timeout 10 bash -c 'while :; do :; done'`) and confirm the service log shows `fill=0.0%`; then stop it and confirm the fill climbs back toward `KEEPALIVE_TARGET`.

## Pull requests

Please include: what changed, why, which distribution/architecture you tested on, and the service log around the change. If you alter the official threshold or policy wording, link to the Oracle docs section you verified.

By contributing you agree that your code is released under the [MIT License](LICENSE).
