#!/usr/bin/env bash
# oci-keepalive uninstaller.
# --purge (or OCI_KEEPALIVE_PURGE=1) also removes config and collected metrics.
set -euo pipefail

RAW="https://raw.githubusercontent.com/mkmkgo/oci-always-free-keepalive/main"

log() { printf '\033[0;32m[+]\033[0m %s\n' "$*"; }
die() { printf '\033[0;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || die "Root required. Use: curl ... | sudo bash"

PURGE=0
if [[ "${1:-}" == "--purge" ]] || [[ "${OCI_KEEPALIVE_PURGE:-0}" == "1" ]]; then
  PURGE=1
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl disable --now oci-keepalive >/dev/null 2>&1 || true
fi

rm -f /etc/systemd/system/oci-keepalive.service
rm -rf /opt/oci-keepalive
rm -f /usr/local/bin/oci-keepalive-check
rm -f /run/oci-keepalive.lock /tmp/oci-keepalive.lock

if [[ "${PURGE}" -eq 1 ]]; then
  rm -f /etc/oci-keepalive.conf
  rm -rf /var/lib/oci-keepalive
  log "Removed config and metrics."
else
  log "Kept /etc/oci-keepalive.conf and /var/lib/oci-keepalive (use --purge to remove)."
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
fi

log "Uninstalled. Reinstall: curl -fsSL ${RAW}/install.sh | sudo bash"
