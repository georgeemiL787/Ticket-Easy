# Running the cloned target application

This is separate from Team C's read-only repository discovery. The owner explicitly requested running the cloned target. Its code is executed inside a dedicated local Docker stack; discovery itself still does not import or execute it.

From PowerShell:

```powershell
Set-Location 'D:\nti project\team_c'
powershell -ExecutionPolicy Bypass -File .\scripts\start_target.ps1
```

The image is already built on this machine. To restart using it without rebuilding:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_target.ps1 -SkipBuild
```

The script uses `D:\target-system` by default and its existing `.env`. It builds the frontend/backend, starts a dedicated PostgreSQL 18 database and Mailpit, applies the target's Alembic migrations, initializes its configured first superuser, and starts the backend. Credentials are read by Docker from the target configuration and are not copied into model inputs. Use the account configured in the target `.env` to log in.

Addresses:

- Target application: http://127.0.0.1:8001
- Target API documentation: http://127.0.0.1:8001/docs
- Local email viewer: http://127.0.0.1:8026
- Team C: http://127.0.0.1:8000

Docker project name: `team-c-target`. Its database volume is `team-c-target_app-db-data`. PostgreSQL is not published on a host port, so it does not conflict with the existing host database. Runtime build/Compose adaptations are generated under Team C's ignored `data/target-runtime/` directory, outside the target checkout. The external Dockerfile preserves the target frontend build and Python 3.14, then uses pip to install the backend's declared runtime dependencies constrained to registry versions in the target `uv.lock`. It omits uv itself and development tools to reduce slow downloads. Application source, migrations and startup command remain intact.

The container sets `PYTHONPATH=/app/backend` so the target package is available to Alembic, the initial-data script and the server without an editable uv installation. This is runtime environment configuration, not a modification to target code or discovery evidence.

Running the target does not remove discovery compatibility limits. The current local-code inventory is `e6c92683-2b62-4c0a-99f0-12ba0cd5c611`: 23 route declarations, no eligible operations because the prefix is configuration-dependent and the Python 3.13 parser cannot parse the target's Python 3.14 dependency file. A running server's OpenAPI output is a separate input, not automatically substituted for code evidence. Original version labels are preserved. The existing OpenAPI upload parser supports only its documented 3.0 subset.

The owner's additional requirement for this run is unassisted discovery: use the existing business description and local path, preserve raw inventory results, and report unresolved findings. No manual prefix overrides, injected capability hints, source rewrites, schema conversion, substituted OpenAPI input, or edited model results are used to make discovery pass. Runtime health checks are separate from discovery evidence.

## Verification — 30 September 2026

The dedicated backend, database and mail viewer containers are healthy. Migrations and first-superuser initialization completed. The target homepage, target `/docs`, target `/api/v1/utils/health-check/` and Team C homepage returned HTTP 200; the health response was `true`. Target login/business actions were not exercised.

The Team C local-project API reindexed the target, validated its snapshot and reused the unchanged inventory cache: 23 route declarations, zero eligible operations. The full saved inventory compared equal before and after this run. Code-analysis and proposal endpoints returned HTTP 422 with `code_analysis_blocked` and `inventory_blocked`, respectively; no live LLM call ran for this target. No OpenAPI substitute was uploaded. `git --no-optional-locks -C D:\target-system status --porcelain` remained empty. Machine-readable checks are saved under `data/target-runtime/verification.json`.
