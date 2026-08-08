# environment

The disposable sandbox: a throwaway harness around the hub in `../project`.
Nothing in here is part of the hub itself.

You normally do not need to read or edit anything in this directory. All
configuration lives in the single `../.env` (see `../.env.example`), and the
commands are driven from the repo root with `../vahub`.

## Files

* `docker-compose.yml` the stack: `hub`, a real `homeassistant` with virtual
  devices, `ha-init` (one-shot headless onboarding), `ntfy` (push
  notifications), and an opt-in `proxy` for TLS with client certificates.
  Every value in it comes from `../.env`.
* `docker-compose.realha.yml` overlay for `--real-ha`: skips the simulated Home
  Assistant and points the hub at your own instance.
* `Dockerfile` builds a Python 3.12 image with one virtualenv for the hub and
  one per module directory, so each module keeps its own dependency graph.
* `sandbox.sh` the command wrapper (`../vahub` forwards to it).
* `homeassistant/init/onboard.py` creates the long-lived token for the
  simulated Home Assistant so no manual setup is needed.
* `proxy/` Caddyfile and a dev certificate generator for the mTLS mode.

## Commands

Run these from the repo root as `./vahub <command>`:

```bash
./vahub up [--real-ha] [--tls]   build, start, wait, report module states
./vahub status                   containers plus per-module state
./vahub logs [service]           follow logs
./vahub modlog notify            stderr of a single hub module
./vahub push "hello"             send a test notification end to end
./vahub reset                    wipe volumes and restart in the same mode
./vahub down                     stop
./vahub test                     run the suite inside the sandbox
```

The active mode is stored in `.sandbox-mode` so every later command acts on the
services that were actually started, and containers left over from a previous
mode are removed on the next `up`.

## How live editing works

The image copies `project/` at build time so the virtualenvs can be created. At
run time the compose file bind-mounts the host `project/` over `/app/project`,
so the code that runs is your working copy, and `../config` over `/etc/vahub`.
Edit a file and restart the hub (`./vahub up`, or `docker compose restart hub`)
and the change is live. A rebuild is only needed when dependencies change or a
module is added.

## Ports

* `HUB_PORT` (8080) the hub and its web console.
* `NTFY_PORT` (2586) the notification server, published on all interfaces
  because your phone connects to it directly.
* `PROXY_PORT` (8443) the mTLS proxy, only with `--tls`.

`HUB_BIND` controls which host interface the hub is published on. The hub has no
authentication of its own, so on an untrusted network set it to `127.0.0.1` and
reach it through the proxy instead. In production the hub binds the internal
bridge and only the proxy is exposed.
