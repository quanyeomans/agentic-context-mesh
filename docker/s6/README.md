# s6-overlay service definitions

This tree is COPYed into the kairix runtime image by the `Dockerfile`. It
follows the **s6-overlay v3.x** layout — the current stable shape for
running multiple supervised processes inside one container.

## Layout

```
docker/s6/
├── services/                  → COPYed to /etc/services.d/ in the image
│   ├── kairix-api/
│   │   ├── run                exec line for `kairix mcp serve --transport http`
│   │   └── finish             logs the exit code to stderr when the service exits
│   └── kairix-worker/
│       ├── run                exec line for `kairix worker run`
│       └── finish             logs the exit code to stderr when the service exits
└── cont-init.d/               → COPYed to /etc/cont-init.d/ in the image
    └── 01-onboard-check       runs `kairix init --user` on first boot if
                               /var/lib/kairix/index.sqlite is missing
```

## How s6 uses these

- `cont-init.d/*` scripts run once at boot, in numeric order, BEFORE any
  service starts. They must exit 0 or s6 aborts the container.
- `services/<name>/run` is the long-running process. s6 supervises it: if
  it crashes, s6 restarts it. SIGTERM to the container is forwarded.
- `services/<name>/finish` runs every time the service exits (clean or
  crashing). Used here only to log the exit code for `docker logs`.

See the parent plan `docs/architecture/` for the unified-container
design rationale.
