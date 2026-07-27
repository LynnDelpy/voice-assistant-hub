#!/bin/sh
set -e

# Data dir for module working directories (module cwd from the manifest).
mkdir -p /var/lib/vahub/modules/time

# First-party code comes from the bind-mounted source, not the image copy.
export PYTHONPATH=/app/project

exec /opt/vh/hub/bin/python -m vahub
