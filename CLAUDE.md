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

1. `load_env()` — reads `.env` from CWD if present.
2. `set_basic_log_config()` — must be called *before* any other logger is used; submodules grab loggers at import time via `get_logger(__name__)`.
3. `load_config()` — parses `config.yaml` from CWD; returns `None` and aborts on missing/empty/malformed file.
4. `merge_config()` — overlays env onto YAML. Only `DOMAIN` and `DESTINATION` are merged here; everything else (DB credentials, borg passphrase, etc.) lives in YAML only.
5. `services_backup(config)` runs if `services` key exists.
6. `borg_transfer(config["borg"])` runs if `borg` key exists. Borg runs *after* services, so it can pick up the freshly-written backup tree.

### Destination layout (destructive)

`get_base_destination()` in `src/isabak/config.py` **wipes and recreates** `<destination>/isabak/` on every run. Per-service output goes into `<destination>/isabak/<service.name>/<backup-type>/` where `<backup-type>` ∈ `{fs, mysql, mariadb, postgres, arr}`. This is intentional — the README warns users to verify backups, and Borg is the long-term store.

### Service module contract

Each file in `src/isabak/services/` exports one `*_backup()` function and follows the same shape:

- A top-level `check_options(...)` that logs `*.<field> is required` and returns `False` on missing config; the entry function early-returns on failure rather than raising.
- The entry function `makedirs(destination, exist_ok=True)` for its own subfolder, then runs the work inside a `try/except Exception` that calls `logger.exception(...)` and returns. A failure in one service must not abort the rest of the run — keep this swallow-and-continue style when adding new backup types.
- Wire any new service into `services_backup()` in `src/isabak/service.py` with the same `if service.get("<key>") is not None:` pattern.

Notable per-module specifics:

- **mysql / mariadb**: write a temporary `/backup.cnf` inside the DB container via `docker exec`, run the dump piped through `gzip` on the host, then delete the cnf file. MariaDB uses `/usr/bin/mariadb-dump` (not `mysqldump`) — the README notes this is required for MariaDB ≥ 10.5.
- **postgres**: does *not* use the running DB container. Spawns a one-shot `prodrigestivill/postgres-backup-local` container on the configured Docker `network`, mounts a `tmp/` dir, then moves the produced `latest` symlink target out and `rmtree`s the tmp dir.
- **arr**: hits the *Arr REST API (`/system/backup`, `POST /command {"name":"Backup"}`), polls the on-disk `folder` for up to ~10s waiting for the new backup file to appear, then `shutil.copy2`s everything in that folder. `subdomain` is optional; when omitted the URL is built directly against `domain`.
- **fs**: plain `rsync -a <folder>/ <destination>/fs/`. The trailing slash on `folder` is added by `path_join(folder, "")` and matters for rsync semantics.

### Path env-var interpolation

Anywhere a config value is a filesystem path (service `fs.folder`, `arr.folder`, `borg.folders[].folder`), it is run through `replace_env_vars()` in `src/isabak/helpers.py`. Syntax is `${VAR}` only — names match `[a-zA-Z0-9_]+`, and a missing variable raises `KeyError`. This is a *separate mechanism* from python-dotenv's own substitution inside `.env` itself; both layers exist and are documented in the README.

### Borg stage

`src/isabak/borg.py` iterates `borg.folders[]` and for each entry runs `borg create` → `borg prune` → `borg compact` with `BORG_REPO` set to `repository + entry.repository` and `BORG_PASSPHRASE` from config. Prune policy is hard-coded: `--keep-daily 7 --keep-weekly 2 --keep-monthly 1` with `--glob-archives '{hostname}-*'`. Default compression when omitted is `none` (also documented in README). Unlike services, a `CalledProcessError` here aborts the *entire* borg loop — the rationale is that a broken repo/passphrase will fail every entry.

## Conventions worth preserving

- Imports use the absolute `src.isabak....` prefix everywhere (see commit `a5020df import refactor`); the project is run from the repo root, not as an installed package.
- Logging is the only user feedback channel — there is no CLI output otherwise. Keep `logger.info` for stage boundaries, `logger.debug` for per-step detail, `logger.error` for user-actionable config problems.
- `check_options()` validators return bool and log; do not raise for config errors.
- `# fmt: off` / `# fmt: on` blocks around long subprocess argv lists are intentional — keep them readable as one-flag-per-line.
