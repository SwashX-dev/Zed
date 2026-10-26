#!/usr/bin/env bash
# Usage: tests/vm/compare-drvs.sh VM-TEST-LOG PREDICTED-DRV
#
# When the vm-zed test reports that the VM evaluated a different system than
# predicted, it prints the VM's derivation graph (VMDRV lines).  This lists
# the derivations the VM has that the host's prediction does not, deepest
# (fewest dependants) last, so the root cause is at the bottom.
set -euo pipefail
log=$1
pred=$2
grep -o 'VMDRV /nix/store/[^ ]* .*' "$log" | awk '{print $2, $3}' | sort -u >/tmp/vm-drvs
nix derivation show -r "$pred" | jq -r '(.derivations // .) | to_entries[] | .key' | sort -u >/tmp/host-drvs
join -v1 /tmp/vm-drvs /tmp/host-drvs
