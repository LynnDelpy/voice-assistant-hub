#!/usr/bin/env bash
# Thin wrapper around docker compose for the disposable hub sandbox.
set -euo pipefail
cd "$(dirname "$0")"

cmd="${1:-help}"
case "$cmd" in
  up)     docker compose up --build -d
          echo "hub -> http://localhost:8080  (status page at /)" ;;
  down)   docker compose down ;;
  reset)  # kill and restart from a clean slate (state volume wiped)
          docker compose down -v
          docker compose up --build -d
          echo "clean slate -> http://localhost:8080" ;;
  logs)   docker compose logs -f ;;
  ps)     docker compose ps ;;
  shell)  docker compose exec hub /bin/bash 2>/dev/null || docker compose exec hub /bin/sh ;;
  test)   # -p no:cacheprovider because /app/project is a read-only mount
          docker compose run --rm --no-deps --entrypoint /opt/vh/hub/bin/python hub \
            -m pytest -q -p no:cacheprovider /app/project ;;
  build)  docker compose build ;;
  real-ha) # point the hub at YOUR real Home Assistant (set HA_URL + HA_TOKEN in .env)
          docker compose -f docker-compose.yml -f docker-compose.realha.yml up --build -d --remove-orphans
          echo "hub -> http://localhost:8080  (using your real HA; the simulated HA is not running)"
          echo "reminder: update project/config/policy.yaml so the gate allows YOUR entity ids" ;;
  tls)    # DEMO HA behind the mTLS proxy (M2)
          ./proxy/gen-certs.sh
          docker compose --profile tls up --build -d
          echo "proxy -> https://localhost:8443  (demo HA; import proxy/certs/client.p12 into your browser first)"
          echo "curl   -> curl --cacert proxy/certs/ca.crt --cert proxy/certs/client.crt --key proxy/certs/client.key https://localhost:8443/health" ;;
  real-ha-tls) # YOUR real HA behind the mTLS proxy (needs HA_URL + HA_TOKEN in .env)
          ./proxy/gen-certs.sh
          docker compose -f docker-compose.yml -f docker-compose.realha.yml --profile tls up --build -d --remove-orphans
          echo "proxy -> https://localhost:8443  (your real HA; import proxy/certs/client.p12 into your browser first)"
          echo "plain -> http://localhost:8080   (no auth, loopback only)" ;;
  down-tls) docker compose --profile tls down ;;
  *)      echo "usage: $0 {up|down|reset|logs|ps|shell|test|build|real-ha|tls|real-ha-tls|down-tls}" ;;
esac
