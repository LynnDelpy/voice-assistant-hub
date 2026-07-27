#!/usr/bin/env bash
# Generate a dev CA, a server cert for the proxy, and one client cert.
# For the real deployment you would issue one client cert per device and keep
# the CA key offline. This is a sandbox convenience, not a production PKI.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p certs && cd certs

if [ -f ca.crt ] && [ "${1:-}" != "--force" ]; then
  echo "certs already exist (use --force to regenerate). CA: $(pwd)/ca.crt"
  exit 0
fi

echo "== CA =="
openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -days 3650 -subj "/CN=vahub-dev-CA"

echo "== server cert (localhost) =="
openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr -subj "/CN=localhost"
printf "subjectAltName=DNS:localhost,IP:127.0.0.1\n" > server.ext
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 825 -extfile server.ext

echo "== client cert (one device) =="
openssl req -newkey rsa:2048 -nodes -keyout client.key -out client.csr -subj "/CN=dev-device"
openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out client.crt -days 825
# Bundle for browser import (no password, dev only).
openssl pkcs12 -export -inkey client.key -in client.crt -certfile ca.crt -out client.p12 -passout pass:

rm -f server.csr client.csr
echo
echo "done. certs in $(pwd)"
echo "  browser: import client.p12 (empty password), then open https://localhost:8443"
echo "  curl:    curl --cacert $(pwd)/ca.crt --cert $(pwd)/client.crt --key $(pwd)/client.key https://localhost:8443/health"
