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
- `postgres.image` (optional, default `prodrigestivill/postgres-backup-local`): image used for Postgres dumps. Pin it to a tag and digest, e.g. `prodrigestivill/postgres-backup-local:17@sha256:<digest>`, so a new upstream push never runs with your database passwords before you choose to update. The digest of the image you run today: `docker image inspect prodrigestivill/postgres-backup-local --format '{{index .RepoDigests 0}}'`. Its `pg_dump` must be at least as new as your newest Postgres server.
- `min_free_gb` (optional, a number of 0 or more): see [Free space](#free-space).
- `allow_destination_on_root_fs` (optional, default `false`): set to `true` if `destination` is intentionally on the same filesystem as `/`.
- `borg`: required. Set `borg: false` to run without an off-site copy on purpose. A missing, empty or misspelt `borg` key fails the run instead of silently skipping the upload.

Unknown keys at the top level, in a service entry, in `borg` or in a `borg.folders` entry are reported as errors and fail the run (exit 1, borg prune skipped), so a typo cannot silently disable a backup. The backups that are configured correctly still run.

### Env List

- `DOMAIN`: same as yaml
- `DESTINATION`: same as yaml
- `LOG_LEVEL`: possible values are `error`, `warning`, `info`, `debug`, default is `error`. Applies to the console, which is what cron emails.
- `LOG_FILE` (optional): path of a log file, e.g. `/var/log/isabak/isabak.log`. It records at `info` level (or `debug` with `LOG_LEVEL=debug`) whatever `LOG_LEVEL` is, so successful runs leave a history while the console stays quiet. Rotated at midnight, 30 days kept. The log files are created with mode `0600`. A folder isabak has to create for it gets mode `0700`. An existing folder is left as it is, but every folder from `/` down to the log must be owned by root and not writable by group or others (`/var/log` and `/var/log/isabak` are fine, anything under `/tmp` or a user's home is refused at start-up), because isabak runs as root and another user could otherwise swap a folder or the log for a link to a different file.

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

A folder `isabak` will be created at the `destination` and backups will be added inside. Each run starts a fresh `isabak` folder. When the previous run succeeded, its tree is kept as `isabak.last-good`, when it failed its tree is dropped and the older `isabak.last-good` stays, so there is always a complete local tree. A successful run removes `isabak.last-good` before borg runs. During a run, and after a failed one, the destination therefore holds two trees. It is created with mode `0700`, so only root can open it: the backups contain database dumps, API keys and password hashes. Use `sudo` to inspect them.

If omitted, borg default compression will be set to `none`.

`borg.repository` and each `borg.folders[].repository` are joined exactly as written, e.g. `ssh://user@host:22/~/borg/` + `docker-services` gives `ssh://user@host:22/~/borg/docker-services`. A doubled slash in the path after joining (e.g. `ssh://user@host:/` + `/docker-services`) is refused, since it points borg at a different path than intended.

### Borg retention and partial archives

```yaml
borg:
  keep:                # optional, these are the defaults
    daily: 7
    weekly: 2
    monthly: 1
    partial_days: 7
```

- Complete archives are named `<hostname>-<date>` and pruned with `daily`, `weekly` and `monthly`. `daily` must be at least 1, so prune can never delete every archive.
- When a service fails, the borg entry whose folder holds the `isabak` tree is archived as `partial-<hostname>-<date>`. Other entries (e.g. `/home`) keep their normal name, their content does not depend on the services. Partial archives never match the complete-archive pattern, so they never take a daily, weekly or monthly slot from a complete archive, and they are kept for `partial_days` days.
- Prune and compact are still skipped after a failed run, partial archives are cleaned up on the next successful run.
- Every run writes `isabak-run.json` into the `isabak` tree, so each archive contains the result of every step: open it first when restoring to see which services that archive really holds.

### Timeouts

Every external command has a time limit, so one stuck command fails only its own service or borg repository instead of hanging the whole run:

| Command | Limit |
|---|---|
| `rsync` (`fs`) | 2 h |
| `docker exec` writing or removing the MySQL/MariaDB credentials file | 2 min |
| `mysqldump` / `mariadb-dump` | 1 h |
| Postgres backup container | 1 h |
| `borg create` | 4 h |
| `borg prune`, `borg compact` | 1 h each |

On timeout the command gets SIGTERM and 60 s to exit before SIGKILL, so borg can release its repository lock and `docker run` can stop and remove its container. A dump started with `docker exec` keeps running inside the database container until it finishes, only the client is stopped. The step is reported as `timed out after …`. The limits are constants at the top of each module.

### Free space

Before anything is moved, deleted or written, isabak checks the free space on `destination`. It needs 1.2 times the size of the last tree (`isabak` or `isabak.last-good`, whichever is larger), counting the space that will be freed for the new run, and at least `min_free_gb` if set (in GB of 1000³ bytes). If there is not enough, the services stage fails right away with both numbers in the log and the existing trees stay untouched, borg still runs for the other entries. The first run has no previous tree, so only `min_free_gb` applies to it.

### Mount guards

When a data disk is not mounted, its mount point (e.g. `/srv/dev-disk-by-uuid-…` on OpenMediaVault) is still there as an empty folder on the system disk. Backing that up would look like a success, and borg prune would then rotate the real archives out. To prevent this:

- `destination` must already exist (it is no longer created) and must be on a different filesystem than `/`, unless `allow_destination_on_root_fs: true` is set. Otherwise the services stage fails before anything is deleted or written.
- Every `fs.folder` and every `borg.folders[].folder` must exist and must not be empty. An `fs` source that fails this check fails that service. A borg source that fails it is skipped (no `create`, no `prune`), the other borg entries still run, and the run exits with an error.

### `fs`

Copied with `rsync -a`. rsync's exit code 24 ("some files vanished before they could be transferred") counts as success: the file was deleted while rsync ran, typically a log or cache entry, so nothing that still exists is missing. The vanished files are logged at warning level (in the log file, not in the cron mail). Any other non-zero exit fails the service and logs rsync's last 30 error lines. rsync's output is cleaned of control characters and every line is prefixed `rsync: `, since file names in it are chosen by the apps.

`max_age_hours` (optional, a number above 0): after the copy, the newest regular file in the copy must be younger than this, otherwise the service fails (the copy is still kept). A file time more than 5 minutes in the future also fails, it would otherwise always look fresh. Meant for folders an app fills on its own schedule, such as Immich's nightly database dumps, so a backup job that silently stopped is noticed instead of yesterday's file being copied again forever. It checks the copy, so it sees exactly what was backed up (`include`/`exclude` applied, rsync keeps the file times), and does not follow symlinks. Pick a value just above the time between the app's job and the isabak run, e.g. `25` for a dump at 01:00 and isabak at 02:30, so one missed night already fails.

```yaml
  - name: immich_db
    fs:
      folder: "${SRV_DIR_DATA}/Media/Pictures/Immich/backups"
      max_age_hours: 25
```

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

**Deletion behavior**: Jellyfin has no built-in retention and no `DELETE` API for backups. isabak first creates the new backup, waits for a file that was not there before, copies it and checks the zip. Only then does it delete older files in `folder` that match `jellyfin-backup-*.zip`. The new backup and any other file in the folder are never touched, and if anything before the copy fails, nothing is deleted.

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
- automated tests
- run services in parallel

### Design / structure
- Project assumes it is run from the repo root (`from src.isabak....` imports, relative `config.yaml`/`.env` paths). Add a `pyproject.toml`, expose an `isabak` console script, and accept `--config` / `--env` CLI flags so it can be installed and invoked from cron without `cd`.
- Only `DOMAIN` and `DESTINATION` are env-overridable. DB passwords and `borg.passphrase` must live in `config.yaml`, which makes integration with secret stores / systemd credentials awkward. Either extend `merge_config` to cover them, or run `${VAR}` expansion across all string values (not just paths).
- Two separate env-var expansion mechanisms exist: python-dotenv inside `.env`, plus the custom `${VAR}` regex in `helpers.py` that only runs on path fields. Picking one consistent expansion pass over the whole config would be simpler.
