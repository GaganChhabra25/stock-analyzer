# Production deployment

GitHub `master` is the only source of truth. Do not edit tracked source files
on the server. A meaningful dirty production worktree makes deployment fail
instead of silently overwriting the change.

Every normal push computes affected processes and deploys only those targets:

- `options/nifty_ws.py` -> web + `nifty-ws`
- `options/crudeoil_ws.py` -> web + `crudeoil-ws`
- collector/retention/cron code -> web + `cron-worker`
- shared Docker/runtime dependencies -> web + all three Docker services
- docs/tests/research -> source sync only

Docker images are tagged with the exact Git commit SHA. All requested images
are built before any running container is replaced. A failed build therefore
cannot interrupt ingestion. A failed restart or revision check restores the
previous Git commit and previous container images.

`workflow_dispatch` intentionally deploys all processes. Use it only for an
explicit full production refresh. Database volumes are never recreated by the
deployment script.
