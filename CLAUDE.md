# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`isabak` is a Linux-only Python CLI that backs up self-hosted Docker services (filesystem trees, MySQL/MariaDB/Postgres dumps, *Arr stack API backups) and optionally transfers the result to a remote Borg repository. There is no test suite (listed as a TODO in the README).

## Commands

```bash
# Install runtime deps (Python 3.12+, see __pycache__ tags)
pip install -r requirements.txt

# Bootstrap config (read from the working dir, else from the project root)
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

1. **Logging bootstrap is implicit** (with an optional `LOG_FILE`: a `TimedRotatingFileHandler`, midnight, 30 days, at `min(INFO, LOG_LEVEL)`; `PrivateLogFileHandler` opens every file, also after rotation, with `O_NOFOLLOW`, requires a regular single-link file and sets `0600` through `fchmod` on the descriptor; a missing folder is created with mode `0700`; `check_log_folder()` walks `realpath` of the folder up to `/` and refuses any component not owned by root (or the running uid) or writable by group or others; existing folders are never chmodded; the console handler keeps `LOG_LEVEL`, so cron mail is unchanged) — `src/isabak/logs.py` loads `.env` (if present) and calls `logging.basicConfig` *at module import*, so by the time any other module's `logger = get_logger(__name__)` runs, the root logger is configured. There is no explicit setup call in `main()`. If you add a new top-level module, ensure it imports `logs.py` (directly or transitively) before any logging occurs.
2. `load_config()` — parses `config.yaml` from CWD, or from the project root when CWD has none (`config_folder()` / `project_file()` in `logs.py`; `.env` is always read from that same folder, so a stray `.env` cannot override another folder's config); returns `None` and aborts on a missing, unreadable, empty or malformed file, and the error names the resolved path. A YAML syntax error is reported with its line and column only, never the offending line, which could hold a password.
3. `merge_config()` — overlays env onto YAML. Only `DOMAIN` and `DESTINATION` are merged here; everything else (DB credentials, borg passphrase, etc.) lives in YAML only.
4. `services_backup(config, report)` runs whenever the `services` key is present, so an empty or `null` value fails validation instead of being skipped. It returns `False` if any service failed or a service has no backup type configured (e.g. a misspelt key). Without a `services` key the run is Borg-only and prune is allowed.
5. `check_unknown_keys()` (`src/isabak/config.py`) rejects unknown keys at the top level, per service, in `borg` and per `borg.folders` entry. It records a failed step and blocks prune, but the run continues. When adding a config option, add it to the matching allow-list there.
6. `borg_transfer(config["borg"], report, prune=services_ok and config_ok)` runs when `borg` is a dict. `borg: false` skips it on purpose, anything else (missing, empty, malformed) is a failed step. Borg runs *after* services, so it can pick up the freshly-written backup tree. When a service failed, `borg create` still runs but `prune`/`compact` are skipped, so retention never rotates out the last good archive.
7. Every stage records its outcome in a `RunReport` (`src/isabak/report.py`). `main()` wraps the pipeline (`run()`) in `try/except Exception`, then always logs a summary: at ERROR when anything failed (so cron emails it), at INFO on success (so successful runs stay quiet). It exits with status 1 when any step failed. Every `False` path that ends a stage must add a failed step, or the run would be reported as successful. Summary notes use `describe_error()`, never `str(e)`, because a `CalledProcessError` message includes argv, which can contain credentials.

### Destination layout (destructive)

`get_base_destination()` in `src/isabak/config.py` **recreates** `<destination>/isabak/` on every run (`keep_last_good()`: the previous tree is renamed to `isabak.last-good` when its `isabak-run.json` says `ok`, otherwise it is removed and the existing `isabak.last-good` stays; `services_backup()` calls `drop_last_good()` once every service succeeded), with mode `0700` so only root can enter it (files inside keep their source permissions through `rsync -a` and `copy2`, so the folder mode is what protects them; do not rely on a process umask instead). Before that it refuses (returns `None`) when there is not enough free space (`check_free_space()`: 1.2 x the larger of the `isabak` and `isabak.last-good` trees, at least `min_free_gb`, counting what `keep_last_good()` will free), or when `destination` does not exist or sits on the same device as `/` (a relative `destination` is already refused by `check_options()` in `service.py`) (unless `allow_destination_on_root_fs: true`), because an unmounted data disk leaves an empty mount point on the system disk. Sources go through `check_source()` in `src/isabak/helpers.py` (must exist and be non-empty): `fs` fails the service, borg skips only that entry (no create, no prune) and the loop continues. Per-service output goes into `<destination>/isabak/<service.name>/<backup-type>/` where `<backup-type>` ∈ `{fs, mysql, mariadb, postgres, arr, jellyfin, sqlite}`. This is intentional — the README warns users to verify backups, and Borg is the long-term store.

### Service module contract

Each file in `src/isabak/services/` exports one `*_backup()` function and follows the same shape:

- The entry function returns `bool`: `True` on success, `False` on any failure. Never return `None`.
- A top-level `check_options(...)` that logs `*.<field> is required` and returns a frozen options dataclass (e.g. `FsOptions`), or `None` on missing or invalid config. Text values go through `as_text()` (`helpers.py`), which also accepts YAML numbers such as `db_name: 12345`. Secrets (passwords, API keys, the borg passphrase) go through `as_secret()` instead, which logs a WARNING (field name only, never the value) when YAML did not give text, because unquoted `0755` arrives as `493`; the entry function returns `False` on failure rather than raising.
- The entry function `makedirs(destination, exist_ok=True)` for its own subfolder, then runs the work inside a `try/except Exception` that calls `logger.exception(...)` and returns `False`. A failure in one service must not abort the rest of the run — keep this log-and-continue style when adding new backup types, but always report the failure through the return value.
- Wire any new service into `services_backup()` in `src/isabak/service.py` with the same `if service.get("<key>") is not None: results.append(run_step(report, service_name, "<key>", <fn>, ...))` pattern. `run_step()` times the call, records it in the report and turns any exception that escapes the module into a failed step, so one service can never abort the rest of the run or the borg stage.

Notable per-module specifics:

- **mysql / mariadb**: both modules are thin wrappers that pass a `DumpTool` (kind, binary, extra flags) to `dump_backup()` in `src/isabak/services/sql_dump.py`, which holds the shared logic. Optional `routines: true` in the top-level block adds `--routines --events` (needs `SHOW_ROUTINE` and `EVENT` privileges, off by default). Write a temporary `/backup.cnf` inside the DB container via `docker exec`, run the dump piped through `gzip` on the host, then delete the cnf file. MariaDB uses `/usr/bin/mariadb-dump` (not `mysqldump`) — the README notes this is required for MariaDB ≥ 10.5.
- **postgres**: does *not* use the running DB container. Spawns a one-shot container of the top-level `postgres.image` (default `prodrigestivill/postgres-backup-local`, meant to be pinned by digest in config) on the configured Docker `network`, mounts a `tmp/` dir, then moves the produced `latest` symlink target out and `rmtree`s the tmp dir. The container's combined stdout/stderr goes to a `TemporaryFile` (bounded memory, kept on timeout) and is logged at ERROR when it fails or times out, at DEBUG otherwise: only the first 8 KiB and last 32 KiB are read, at most the first 10 and last 40 lines are logged, control characters and terminal escapes are stripped, the password (plain and URL-encoded) and `://user:...@` credentials are masked, and every line is prefixed `container: ` so it cannot pass for an isabak log line.
- **arr**: hits the *Arr REST API (`/system/backup`, `POST /command {"name":"Backup"}`), snapshots the files in the on-disk `folder` before the request, waits until `GET /command/{id}` reports the backup command `completed` (`wait_command()`, up to `command_timeout`; a failed, aborted, cancelled or orphaned command fails the step; without an id in the response it falls back to the zip check), then polls for up to ~10s until a file not in that snapshot appears, then `shutil.copy2`s only those new files and checks the zips. Only after that, `delete_old_backups()` deletes older backups through the API, and only those of type `manual` (the kind isabak creates), never the new one and never the app's scheduled or update backups; a cleanup failure is logged at ERROR but does not fail the step. Leftover files never count as the new backup, and copying nothing is a failure. **jellyfin** follows the same order: snapshot, create, wait for a new file whose size stays the same for `stable_seconds` in a row (all within `creation_timeout`, Jellyfin has no status API), copy and check, then delete only older `jellyfin-backup-*.zip` files. `subdomain` is optional; when omitted the URL is built directly against `domain`.
- **sqlite**: for each path in `sqlite.files`, checks the SQLite header, rejects symlinks, opens the database read-only via a `file:` URI (never read-write, a read-only failure is an error), copies it with `Connection.backup()` into `<file>.tmp`, sets `journal_mode=DELETE`, runs `PRAGMA integrity_check` and renames to the final name. Before that, `holds_open()` confirms through `/proc/self/fd` that the file SQLite actually has open is the inode that passed the checks, so a symlink swapped in between check and open is caught (it does not use a `/proc/self/fd/N` path for the connection, which would break the `-wal`/`-shm` names). A `-wal`/`-shm` the open created is `fchown`ed to the database owner only if it is a regular, root-owned, single-link file on the database's filesystem, opened with `O_NOFOLLOW` (the folder is writable by the app container). One bad file fails the step but the other files still run.
- **Verification** (`src/isabak/verify.py`): every dump and zip is read back after writing. `verify_sql_gzip()` decompresses to the end and looks for the dump tool's completion marker in the last 4 KiB (mysql/mariadb remove the file and raise, postgres removes it and returns `False`). `verify_zip()` runs `testzip()`; arr and jellyfin fail on a bad zip, and jellyfin's `copy_backup()` now returns `bool` and fails when nothing was copied.
- **fs**: plain `rsync -a <folder>/ <destination>/fs/`, stderr captured. Exit code 24 (`rsync_vanished`, files deleted during the copy) is success with a warning log, any other non-zero exit fails the service and logs the last 30 stderr lines at ERROR. Optional `fs.max_age_hours`: after the copy, `check_freshness()` fails the service when the newest regular file in the copy (`newest_file()` on the destination, `os.walk` + `lstat`, symlinks ignored) is older than the limit or more than `clock_skew_seconds` in the future. File names in the message have control characters stripped and line breaks escaped. Tool output goes through `strip_control_characters()` (`helpers.py`, shared with the Postgres output) and is prefixed (`rsync: `, `container: `) so it cannot pass for an isabak log line. The trailing slash on `folder` is added by `path_join(folder, "")` and matters for rsync semantics.

### Path env-var interpolation

Anywhere a config value is a filesystem path (service `fs.folder`, `arr.folder`, `borg.folders[].folder`), it is run through `replace_env_vars()` in `src/isabak/helpers.py`. Syntax is `${VAR}` only — names match `[a-zA-Z0-9_]+`, and a missing variable raises `KeyError`. This is a *separate mechanism* from python-dotenv's own substitution inside `.env` itself; both layers exist and are documented in the README.

### Borg stage

`src/isabak/borg.py` iterates `borg.folders[]` and for each entry runs `borg create` → `borg prune` → `borg compact` with `BORG_REPO` set to `repository + entry.repository` (joined as written; `check_options()` refuses a `//` in the path part of the result) and `BORG_PASSPHRASE` from config, on top of the inherited environment (so `PATH`, `HOME`, `SSH_AUTH_SOCK` and `BORG_*` settings from `vars` reach borg). Prune runs twice per entry: complete archives with `--glob-archives '{hostname}-*'` and `borg.keep` (`check_keep()`, defaults 7/2/1 daily/weekly/monthly, `daily` ≥ 1), and partial archives with `--glob-archives 'partial-{hostname}-*' --keep-within <partial_days>d`. When a service failed, `main()` passes `partial_tree` (`<destination>/isabak`) and the entry whose folder holds that tree (`holds_tree()`, realpath comparison) is created as `partial-{hostname}-{now}`. `services_backup()` writes `isabak-run.json` (the `RunReport` steps, no exception messages) into the tree before borg runs. Default compression when omitted is `none` (also documented in README). Each entry is its own repository, so a failure in one entry is recorded and the loop continues with the next. `borg create` exit code 1 means the archive was written with warnings (e.g. a file changed or could not be read): the entry's prune and compact still run, the step is recorded as `WARN` (`warning=True` in `RunReport.add`) and the run exits 1 so the warning is alerted. Exit code 2 or higher is a failure for that entry, with no prune.

## Conventions worth preserving

- No pull requests in this project: commit directly to the current branch (usually `main`).
- Imports use the absolute `src.isabak....` prefix everywhere (see commit `a5020df import refactor`); the project is run from the repo root, not as an installed package.
- Logging is the only user feedback channel — there is no CLI output otherwise. Keep `logger.info` for stage boundaries, `logger.debug` for per-step detail, `logger.error` for user-actionable config problems.
- `check_options()` validators return typed options or `None` and log; do not raise for config errors. Run `mypy --platform linux` (the code uses Linux-only `os` functions).
- Every external command goes through `run_command()` (`src/isabak/helpers.py`) with an explicit `timeout=`, never bare `subprocess.run`. It sends SIGTERM, waits `stop_grace_seconds`, then SIGKILL, so borg releases its lock and `docker run` removes its container. Limits are module constants (`rsync_timeout`, `dump_timeout`, `create_timeout`, …). The MySQL/MariaDB dump pipeline uses `stop_process()` on both `Popen`s. `describe_error()` reports a `TimeoutExpired` as `timed out after …`.
- `# fmt: off` / `# fmt: on` blocks around long subprocess argv lists are intentional — keep them readable as one-flag-per-line.
