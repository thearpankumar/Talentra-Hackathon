#!/usr/bin/env bash
# Phase 1: `cockroach init` (idempotent). crdb2/crdb3 only start after this succeeds so node ids are 1,2,3 in order.
set -u
if [ "${CRDB_SECURE:-0}" = "1" ]; then A="--certs-dir=/certs"; else A="--insecure"; fi
for i in $(seq 1 30); do
  out=$(cockroach init $A --host=crdb1:26257 2>&1); rc=$?
  echo "$out"
  if [ $rc -eq 0 ] || echo "$out" | grep -qi "already been initialized"; then echo "init ok"; exit 0; fi
  sleep 2
done
echo "init failed"; exit 1
