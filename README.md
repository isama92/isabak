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
- `destination`: place where backups will be created

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

A folder `isabak` will be created at the `destination` and backups will be added inside, the folder will be deleted and recreated each usage.

If omitted, borg default compression will be set to `none`.

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
