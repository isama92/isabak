# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`isabak` is a Linux-only Python CLI that backs up self-hosted Docker services (filesystem trees, MySQL/MariaDB/Postgres dumps, *Arr stack API backups) and optionally transfers the result to a remote Borg repository. There is no test suite (listed as a TODO in the README).

## Commands

```bash
# Install runtime deps (Python 3.12+, see __pycache__ tags)
pip install -r requirements.txt

# Bootstrap config (both files are required at runtime in the working dir)
cp .env.example .env
cp config.yaml.example config.yaml

# Run a backup cycle
python main.py

# Verbose run while iterating
LOG_LEVEL=debug python main.py
```

The process must run on the Docker host (it shells out to `docker exec` / `docker run`) and needs `borg`, `rsync`, `gzip`, and `docker` on `PATH`. Postgres backups pull the `prodrigestivill/postgres-backup-local` image at runtime.

`LOG_LEVEL` accepts `error` (default), `warning`, `info`, `debug` — any other value raises `ValueError` at startup (`src/isabak/logs.py`).

## Architecture

### Entry flow

Top-level `main.py` is a thin shim around `src.isabak.main:main`. The real pipeline (`src/isabak/main.py`) is strictly sequential:

1. **Logging bootstrap is implicit** — `src/isabak/logs.py` loads `.env` (if present) and calls `logging.basicConfig` *at module import*, so by the time any other module's `logger = get_logger(__name__)` runs, the root logger is configured. There is no explicit setup call in `main()`. If you add a new top-level module, ensure it imports `logs.py` (directly or transitively) before any logging occurs.
2. `load_config()` — parses `config.yaml` from CWD; returns `None` and aborts on missing/empty/malformed file.
3. `merge_config()` — overlays env onto YAML. Only `DOMAIN` and `DESTINATION` are merged here; everything else (DB credentials, borg passphrase, etc.) lives in YAML only.
4. `services_backup(config, report)` runs whenever the `services` key is present, so an empty or `null` value fails validation instead of being skipped. It returns `False` if any service failed or a service has no backup type configured (e.g. a misspelt key). Without a `services` key the run is Borg-only and prune is allowed.
5. `check_unknown_keys()` (`src/isabak/config.py`) rejects unknown keys at the top level, per service, in `borg` and per `borg.folders` entry. It records a failed step and blocks prune, but the run continues. When adding a config option, add it to the matching allow-list there.
6. `borg_transfer(config["borg"], report, prune=services_ok and config_ok)` runs when `borg` is a dict. `borg: false` skips it on purpose, anything else (missing, empty, malformed) is a failed step. Borg runs *after* services, so it can pick up the freshly-written backup tree. When a service failed, `borg create` still runs but `prune`/`compact` are skipped, so retention never rotates out the last good archive.
7. Every stage records its outcome in a `RunReport` (`src/isabak/report.py`). `main()` wraps the pipeline (`run()`) in `try/except Exception`, then always logs a summary: at ERROR when anything failed (so cron emails it), at INFO on success (so successful runs stay quiet). It exits with status 1 when any step failed. Every `False` path that ends a stage must add a failed step, or the run would be reported as successful. Summary notes use `describe_error()`, never `str(e)`, because a `CalledProcessError` message includes argv, which can contain credentials.

### Destination layout (destructive)

`get_base_destination()` in `src/isabak/config.py` **wipes and recreates** `<destination>/isabak/` on every run. Before that it refuses (returns `None`) when `destination` does not exist or sits on the same device as `/` (unless `allow_destination_on_root_fs: true`), because an unmounted data disk leaves an empty mount point on the system disk. Sources go through `check_source()` in `src/isabak/helpers.py` (must exist and be non-empty): `fs` fails the service, borg skips only that entry (no create, no prune) and the loop continues. Per-service output goes into `<destination>/isabak/<service.name>/<backup-type>/` where `<backup-type>` ∈ `{fs, mysql, mariadb, postgres, arr, jellyfin}`. This is intentional — the README warns users to verify backups, and Borg is the long-term store.

### Service module contract

Each file in `src/isabak/services/` exports one `*_backup()` function and follows the same shape:

- The entry function returns `bool`: `True` on success, `False` on any failure. Never return `None`.
- A top-level `check_options(...)` that logs `*.<field> is required` and returns `False` on missing config; the entry function returns `False` on failure rather than raising.
- The entry function `makedirs(destination, exist_ok=True)` for its own subfolder, then runs the work inside a `try/except Exception` that calls `logger.exception(...)` and returns `False`. A failure in one service must not abort the rest of the run — keep this log-and-continue style when adding new backup types, but always report the failure through the return value.
- Wire any new service into `services_backup()` in `src/isabak/service.py` with the same `if service.get("<key>") is not None: results.append(run_step(report, service_name, "<key>", <fn>, ...))` pattern. `run_step()` times the call, records it in the report and turns any exception that escapes the module into a failed step, so one service can never abort the rest of the run or the borg stage.

Notable per-module specifics:

- **mysql / mariadb**: write a temporary `/backup.cnf` inside the DB container via `docker exec`, run the dump piped through `gzip` on the host, then delete the cnf file. MariaDB uses `/usr/bin/mariadb-dump` (not `mysqldump`) — the README notes this is required for MariaDB ≥ 10.5.
- **postgres**: does *not* use the running DB container. Spawns a one-shot `prodrigestivill/postgres-backup-local` container on the configured Docker `network`, mounts a `tmp/` dir, then moves the produced `latest` symlink target out and `rmtree`s the tmp dir.
- **arr**: hits the *Arr REST API (`/system/backup`, `POST /command {"name":"Backup"}`), snapshots the files in the on-disk `folder` before the request, polls for up to ~10s until a file not in that snapshot appears, then `shutil.copy2`s only those new files. Leftover files never count as the new backup, and copying nothing is a failure. `subdomain` is optional; when omitted the URL is built directly against `domain`.
- **fs**: plain `rsync -a <folder>/ <destination>/fs/`. The trailing slash on `folder` is added by `path_join(folder, "")` and matters for rsync semantics.

### Path env-var interpolation

Anywhere a config value is a filesystem path (service `fs.folder`, `arr.folder`, `borg.folders[].folder`), it is run through `replace_env_vars()` in `src/isabak/helpers.py`. Syntax is `${VAR}` only — names match `[a-zA-Z0-9_]+`, and a missing variable raises `KeyError`. This is a *separate mechanism* from python-dotenv's own substitution inside `.env` itself; both layers exist and are documented in the README.

### Borg stage

`src/isabak/borg.py` iterates `borg.folders[]` and for each entry runs `borg create` → `borg prune` → `borg compact` with `BORG_REPO` set to `repository + entry.repository` and `BORG_PASSPHRASE` from config. Prune policy is hard-coded: `--keep-daily 7 --keep-weekly 2 --keep-monthly 1` with `--glob-archives '{hostname}-*'`. Default compression when omitted is `none` (also documented in README). Unlike services, a `CalledProcessError` here aborts the *entire* borg loop — the rationale is that a broken repo/passphrase will fail every entry.

## Conventions worth preserving

- No pull requests in this project: commit directly to the current branch (usually `main`).
- Imports use the absolute `src.isabak....` prefix everywhere (see commit `a5020df import refactor`); the project is run from the repo root, not as an installed package.
- Logging is the only user feedback channel — there is no CLI output otherwise. Keep `logger.info` for stage boundaries, `logger.debug` for per-step detail, `logger.error` for user-actionable config problems.
- `check_options()` validators return bool and log; do not raise for config errors.
- `# fmt: off` / `# fmt: on` blocks around long subprocess argv lists are intentional — keep them readable as one-flag-per-line.
