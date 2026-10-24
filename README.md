# isabak

Backup your docker services.

## Compatibilities

Tested on:
- Ubuntu 24.04
- Debian 12

I don't think it works on Windows systems.

## Dependencies

Install all the dependencies by running `pip install -r requirements.txt`.

## Configuration

Run `cp .env.example .env` and `cp config.yaml.example config.yaml` to create the base config files and edit them as needed.

The `.env` variable will override the `config.yaml` variables.
e.g. if you set both the `destination` in the `config.yaml` and the `DESTINATION` in the `.env`, the `.env` value will be used.

`.env` supports environment variables, e.g. `DESTINATION="${BACKUP_BASE_PATH}backups"` 

All services paths can use ${} to add an environment variable in the path itself. e.g. `folder: "${YOUR_SERVICE_BASE_PATH}/your_service_files"` where the env variable `YOUR_SERVICE_BASE_PATH` is `/home/user/` will become `/home/user/your_service_files`. In this specific situations, env variables can only be named with letter numbers and underscores (`_`). 

### Yaml List

- `domain`: used to generate backups for the ARR stack.
- `destination`: place where backups will be created. It must already exist and must not be on the root filesystem, see [Mount guards](#mount-guards).
- `allow_destination_on_root_fs` (optional, default `false`): set to `true` if `destination` is intentionally on the same filesystem as `/`.
- `borg`: required. Set `borg: false` to run without an off-site copy on purpose. A missing, empty or misspelt `borg` key fails the run instead of silently skipping the upload.

Unknown keys at the top level, in a service entry, in `borg` or in a `borg.folders` entry are reported as errors and fail the run (exit 1, borg prune skipped), so a typo cannot silently disable a backup. The backups that are configured correctly still run.

### Env List

- `DOMAIN`: same as yaml
- `DESTINATION`: same as yaml
- `LOG_LEVEL`: possible values are `error`, `warning`, `info`, `debug`, default is `error`

### Env to Yaml mapping

Some Env configuration will override the Yaml configuration:

- DOMAIN => domain
- DESTINATION => destination

## Notes

MariaDB dump tool changed from mysqldump to mariadb-dump since mariadb 10.5, prior version would not dump the database correctly.

Do a run and **check all the backups are working**. Also check the content of gzipped files.

Every artefact is read back after it is written, and a failed check fails that service:

- MySQL, MariaDB and Postgres dumps are decompressed to the end (gzip CRC) and must end with the dump tool's own completion line (`-- Dump completed` for mysqldump and mariadb-dump, `-- PostgreSQL database dump complete` for pg_dump). A dump that was cut off is deleted.
- *Arr and Jellyfin zips must open, contain at least one entry and pass a CRC check of every entry, which catches a zip copied while the app was still writing it.
- `sqlite` copies must pass `PRAGMA integrity_check` (see below).

A folder `isabak` will be created at the `destination` and backups will be added inside, the folder will be deleted and recreated each usage. It is created with mode `0700`, so only root can open it: the backups contain database dumps, API keys and password hashes. Use `sudo` to inspect them.

If omitted, borg default compression will be set to `none`.

### Mount guards

When a data disk is not mounted, its mount point (e.g. `/srv/dev-disk-by-uuid-…` on OpenMediaVault) is still there as an empty folder on the system disk. Backing that up would look like a success, and borg prune would then rotate the real archives out. To prevent this:

- `destination` must already exist (it is no longer created) and must be on a different filesystem than `/`, unless `allow_destination_on_root_fs: true` is set. Otherwise the services stage fails before anything is deleted or written.
- Every `fs.folder` and every `borg.folders[].folder` must exist and must not be empty. An `fs` source that fails this check fails that service. A borg source that fails it is skipped (no `create`, no `prune`), the other borg entries still run, and the run exits with an error.

### `fs.include` / `fs.exclude`

`fs.exclude` and `fs.include` accept a list of [rsync filter patterns](https://download.samba.org/pub/rsync/rsync.1#FILTER_RULES) anchored at `fs.folder`. A leading `/` anchors the pattern to the source root, otherwise it matches anywhere in the tree. Use `dir/***` to match a directory and everything under it recursively.

- `exclude` only: rsync default behavior (everything is copied except matching paths).
- `include` only: only matching paths are copied (a final `--exclude=*` is appended). Each parent directory must be listed explicitly, e.g. `["/foo/", "/foo/bar/", "/foo/bar/keep.txt"]`.
- both: `exclude` patterns are evaluated first (so they take priority), then `include`, then the implicit `--exclude=*` from `include`.

### `jellyfin`

Drives Jellyfin's built-in backup feature (Jellyfin 10.10+) through the REST API to produce a crash-consistent zip — safer than `rsync`'ing the live SQLite DB.

- `subdomain` (optional): combined with the top-level `domain` to build the base URL (e.g. `subdomain: "jellyfin"` + `domain: "example.com"` → `https://jellyfin.example.com`). Omit to use `domain` directly.
- `api_key` (required): create one in Jellyfin Dashboard → API Keys.
- `folder` (required): host-side path where Jellyfin writes backup zips. Maps to the container's `<DataPath>/backups/` (default `/config/data/backups/`).
- `secure` (optional, default `true`): use `https`.
- `options` (optional): which content categories to include in the zip. Defaults to DB only — `database: true`, all others `false`. Available toggles: `database`, `metadata`, `trickplay`, `subtitles`. The zip always contains system and user configuration; these flags only add the named content categories on top.

**Deletion behavior**: Jellyfin has no built-in retention and no `DELETE` API for backups, so every isabak run wipes existing files in `folder` before triggering a new backup. Do not point `folder` at a directory that holds anything other than Jellyfin's own backup zips.

You can use `fs` and `jellyfin` on the same service entry — `fs` covers everything outside the DB (settings, plugins, etc., with whatever excludes you set), `jellyfin` covers the DB consistently.

### `sqlite`

Takes a consistent copy of SQLite databases while the app keeps running, using SQLite's online backup API (Python's built-in `sqlite3`, nothing to install). Copying a live database with `rsync` can capture the main file and its `-wal` at different moments and produce a broken or incomplete copy, so use this for any app that stores its data in SQLite.

```yaml
services:
  - name: navidrome
    fs:
      folder: "${SRV_DIR_DOCKER}/navidrome/data"
      exclude: ["/navidrome.db*"]   # optional, skip the live database in the plain copy
    sqlite:
      files:
        - "${SRV_DIR_DOCKER}/navidrome/data/navidrome.db"
```

- `files` (required): host-side paths of the database files, `${VAR}` supported. File names must be unique within a service.
- Each file must exist, be a regular file (symlinks are refused), be non-empty and start with the SQLite header. It is opened read-only (never read-write), copied to `<destination>/isabak/<name>/sqlite/<file>` through a `.tmp` file, converted to a single self-contained file (`journal_mode=DELETE`), checked with `PRAGMA integrity_check` and only then renamed to its final name.
- If opening the database creates a `-wal` or `-shm` file next to it, isabak gives that file to the database's owner so the app is never locked out. It only does so for a regular, single-link file it just created, never through a symlink, because the folder is writable by the app.
- Works on local disks. Do not use it for databases on NFS or SMB shares, where SQLite locking is unreliable.

**Restore:** stop the app, copy the `.db` file back over the original, delete any `-wal` and `-shm` files next to it, start the app.

## TODO

### Features
- allow arrays in yaml config services, to have multiple fs or multiple DBs
- add home assistant backups (ask creation through api like arr?)
- better plex backups, they are huge
- borg prune customisation (currently hard-coded `--keep-daily 7 --keep-weekly 2 --keep-monthly 1`)
- automated tests
- run services in parallel

### Bugs / correctness
- `borg.py` passes `env={"BORG_REPO":..., "BORG_PASSPHRASE":...}` to `subprocess.run`, which replaces the whole environment (including `PATH`). Should merge into `os.environ.copy()` so cron/systemd contexts with a stripped env can still find the `borg` binary.
- `arr_backup.wait_backup_creation` returns as soon as the *Arr backup folder is non-empty, but only previous backups are deleted via the API (not on disk). If stale files are still present, the run copies them and never waits for the freshly-requested backup. Snapshot the listing before triggering the backup and wait for a *new* file (or empty the folder first).
- `mysql_backup`/`mariadb_backup` build a `bash -c` string with f-string interpolated credentials to write `/backup.cnf` inside the container. A password containing `"`, `\`, `` ` `` or `$` will break the heredoc or be executed as shell. Pass the cnf via `docker cp` from a tempfile, or feed credentials over stdin, instead of constructing a shell command.
- `requirements.txt` pins `dotenv==0.9.9`, which is a thin wrapper that depends on `python-dotenv`. Pin `python-dotenv` directly.
- `arr_backup.copy_backup` does not clean the *Arr source folder after copying, so on-disk usage there grows until *Arr itself rotates.

### Design / structure
- Project assumes it is run from the repo root (`from src.isabak....` imports, relative `config.yaml`/`.env` paths). Add a `pyproject.toml`, expose an `isabak` console script, and accept `--config` / `--env` CLI flags so it can be installed and invoked from cron without `cd`.
- Only `DOMAIN` and `DESTINATION` are env-overridable. DB passwords and `borg.passphrase` must live in `config.yaml`, which makes integration with secret stores / systemd credentials awkward. Either extend `merge_config` to cover them, or run `${VAR}` expansion across all string values (not just paths).
- Two separate env-var expansion mechanisms exist: python-dotenv inside `.env`, plus the custom `${VAR}` regex in `helpers.py` that only runs on path fields. Picking one consistent expansion pass over the whole config would be simpler.
- Failure semantics are inconsistent: service backups swallow exceptions and continue; `borg_transfer` aborts the whole loop on the first `CalledProcessError`. `main()` always returns success, so a partial failure is invisible to cron. Decide on a policy and exit non-zero when any stage failed.
