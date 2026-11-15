#!/usr/bin/env bash
# oci-keepalive installer -- idempotent: re-running upgrades scripts, keeps your config.
set -euo pipefail

REPO_OWNER="mkmkgo"
REPO_NAME="oci-always-free-keepalive"
BRANCH="${OCI_KEEPALIVE_BRANCH:-main}"
RAW="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${BRANCH}"

PREFIX="/opt/oci-keepalive"
UNIT="/etc/systemd/system/oci-keepalive.service"
CONF="/etc/oci-keepalive.conf"
STATE="/var/lib/oci-keepalive"
BIN="/usr/local/bin/oci-keepalive-check"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo .)"

log()  { printf '\033[0;32m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[0;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || die "Root required. Use: curl ... | sudo bash"
command -v systemctl >/dev/null 2>&1 || die "systemd is required."
[[ -d /run/systemd/system ]] || die "This system is not booted with systemd."
command -v python3 >/dev/null 2>&1 || die "python3 is required."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
  || die "Python >= 3.8 is required."

TMP="$(mktemp -d)"
cleanup() { rm -rf "${TMP}"; }
trap cleanup EXIT

fetch() {
  local name="$1"
  local dest="${TMP}/${name}"
  if [[ -f "${SCRIPT_DIR}/${name}" ]]; then
    cp "${SCRIPT_DIR}/${name}" "${dest}"
  else
    command -v curl >/dev/null 2>&1 || die "curl is required when installing remotely."
    curl -fsSL "${RAW}/${name}" -o "${dest}" || die "Failed to download ${name}."
  fi
  printf '%s\n' "${dest}"
}

log "Installing oci-keepalive ..."
install -d -m 755 "${PREFIX}" "${STATE}"
# Shared singleton lock: world-writable so a manual run sees the service's lock.
# Use touch, not ">" -- truncating an existing lock file would erase the PID
# that the running service wrote into it (flock itself lives on the inode and
# is unaffected, but the file would no longer be useful for diagnostics).
touch /run/oci-keepalive.lock 2>/dev/null || true
chmod 666 /run/oci-keepalive.lock 2>/dev/null || true
install -m 755 "$(fetch keepalive.py)" "${PREFIX}/keepalive.py"
install -m 755 "$(fetch check.py)" "${PREFIX}/check.py"
ln -sf "${PREFIX}/check.py" "${BIN}"

if [[ -f "${CONF}" ]]; then
  warn "Keeping existing config: ${CONF}"
else
  install -m 644 "$(fetch oci-keepalive.conf.example)" "${CONF}"
  log "Created config: ${CONF}"
fi

TARGET="$(awk -F= '/^[[:space:]]*KEEPALIVE_TARGET=/{v=$2; sub(/#.*/,"",v); gsub(/[^0-9.]/,"",v); if(v!=""){print v; exit}}' "${CONF}")"
[[ -n "${TARGET}" ]] || TARGET="0.28"
NCPU="$(python3 -c 'import os; print(len(os.sched_getaffinity(0)))' 2>/dev/null || nproc)"
QUOTA="$(python3 -c "import math; n=int('${NCPU}'); t=float('${TARGET}'); print(int(min(max(45, math.ceil(n*t*100)+15), n*100)))")"

UNIT_TMP="$(fetch oci-keepalive.service)"
sed -i "s|^CPUQuota=.*|CPUQuota=${QUOTA}%|" "${UNIT_TMP}"
install -m 644 "${UNIT_TMP}" "${UNIT}"

systemctl daemon-reload
systemctl enable oci-keepalive >/dev/null 2>&1
systemctl restart oci-keepalive
sleep 1
systemctl is-active --quiet oci-keepalive \
  || die "Service failed to start. Check: journalctl -u oci-keepalive -n 30"

echo
log "Done. CPUs=${NCPU} target=${TARGET} CPUQuota=${QUOTA}% | active + enabled on boot"
echo
echo "  7-day self check : oci-keepalive-check        (or: oci-keepalive-check --json)"
echo "  live log         : sudo journalctl -u oci-keepalive -f"
echo "  service status   : sudo systemctl status oci-keepalive"
echo "  pause (keepable) : sudo systemctl stop oci-keepalive"
echo "  disable on boot  : sudo systemctl disable --now oci-keepalive"
echo "  uninstall        : curl -fsSL ${RAW}/uninstall.sh | sudo bash"
echo
