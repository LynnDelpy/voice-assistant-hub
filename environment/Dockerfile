# Dev sandbox image for the hub. Pins Python 3.12 regardless of the host.
# Build context is the repo root (see docker-compose.yml), so both `project/`
# and this file's sibling `entrypoint.sh` are reachable.
FROM python:3.12-slim

# uv, pinned for reproducibility
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd -m -u 10001 vahub

WORKDIR /app

# Copy the source for a build-time editable install. At runtime the compose file
# bind-mounts the host `project/` over `/app/project`, so this copy is shadowed
# by live code. The venvs live outside the mount (/opt/vh) and survive it; they
# provide the third-party deps, while PYTHONPATH points imports at the mount.
COPY project /app/project

# Hub venv (with dev extras so `pytest` runs inside the sandbox).
RUN uv venv /opt/vh/hub --python /usr/local/bin/python3.12 \
 && uv pip install --python /opt/vh/hub/bin/python -e "/app/project[dev]"

# Each module gets its own venv and dependency graph. This is the contract the
# plan insists on: one venv per module, spawned from a command in the manifest.
#
# Built in a loop over project/modules/*, so the venv is derived from the
# directory name and a newly added module can never be forgotten. (Hand-listing
# them is what once left the notify module without an interpreter to start.)
# The manifest command must therefore be /opt/vh/mod/<dirname>/bin/python.
RUN set -eu; for d in /app/project/modules/*/; do \
      name="$(basename "$d")"; \
      echo "building venv for module: $name"; \
      uv venv "/opt/vh/mod/$name" --python /usr/local/bin/python3.12; \
      uv pip install --python "/opt/vh/mod/$name/bin/python" -e "$d"; \
    done

COPY environment/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
 && mkdir -p /var/lib/vahub /etc/vahub \
 && chown -R vahub:vahub /var/lib/vahub /etc/vahub /opt/vh

USER vahub
ENV VAHUB_CONFIG=/etc/vahub/config.yaml
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
