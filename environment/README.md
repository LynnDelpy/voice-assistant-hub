# environment

The disposable sandbox. Everything here is a throwaway harness around the code in
`../project`. Nothing here is part of the hub itself.

## Files

* `docker-compose.yml` defines one service, `hub`. It bind-mounts `../project`
  (live code, read-only) and `../project/config` (config and manifests), and
  mounts a named volume `vahub-state` at `/var/lib/vahub` for disposable state.
* `Dockerfile` builds a Python 3.12 image. It creates two virtual environments
  with `uv`: one for the hub, one for the `time` module. Each module having its
  own venv is the contract from the plan (separate dependency graphs, separate
  processes).
* `entrypoint.sh` prepares the module data directory and starts the hub.
* `sandbox.sh` is a thin wrapper over `docker compose` with the commands you use
  day to day.

## Commands

```bash
./sandbox.sh up      # build and start (detached)
./sandbox.sh reset   # down -v (wipe state) then up: a clean slate
./sandbox.sh down    # stop and remove containers
./sandbox.sh logs    # follow logs
./sandbox.sh ps      # container status
./sandbox.sh shell   # a shell inside the running container
./sandbox.sh test    # run pytest inside the sandbox
./sandbox.sh build   # rebuild the image only
```

## How live editing works

The image copies `project/` at build time only so the venvs can be created. At
run time the compose file bind-mounts the host `project/` over `/app/project`, so
the code the container runs is your working copy. `PYTHONPATH` points imports at
the mount, and the module manifest sets its own `pythonpath` to the mounted
module source. Edit a file, restart the hub (`./sandbox.sh reset` or a plain
`docker compose restart hub`), and the change is live. A rebuild is only needed
when dependencies change.

## Ports

Only `8080` is published to the host. Inside the container the hub binds
`0.0.0.0`, which is fine because nothing else shares the container's network. In
production the hub binds the internal bridge and sits behind the proxy.
