#!/usr/bin/env bash
# One-shot, idempotent: creates an internal CA + one shared node cert (all SANs) + root client cert in the `certs` volume.
# Copies ONLY public ca.crt to /certs-public (bind mount) for Caddy / clients. ca.key stays in the volume.
set -eu
D=/certs
if [ ! -f $D/ca.crt ]; then
  echo "creating CA + certs for: ${CERT_HOSTS}"
  cockroach cert create-ca --certs-dir=$D --ca-key=$D/ca.key --lifetime=87600h
  cockroach cert create-node ${CERT_HOSTS} --certs-dir=$D --ca-key=$D/ca.key --lifetime=43800h
  cockroach cert create-client root --certs-dir=$D --ca-key=$D/ca.key --lifetime=43800h
else
  echo "certs already exist"
fi
mkdir -p /certs-public
# Docker creates a DIRECTORY when a missing file is bind-mounted; if a previous run left one, replace it by the real file.
[ -d /certs-public/ca.crt ] && { echo "repairing: /certs-public/ca.crt was a directory"; rm -rf /certs-public/ca.crt; }
cp $D/ca.crt /certs-public/ca.crt && chmod 644 /certs-public/ca.crt && [ -f /certs-public/ca.crt ]
ls -l $D
