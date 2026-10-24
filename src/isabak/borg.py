import subprocess
from time import monotonic
from src.isabak.helpers import check_source, replace_env_vars, run_command
from src.isabak.logs import get_logger
from src.isabak.report import RunReport, describe_error

logger = get_logger(__name__)

# a first run of a large source can take hours, borg resumes from its checkpoints
create_timeout = 4 * 3600
maintenance_timeout = 3600


def borg_transfer(options: dict, report: RunReport, prune: bool = True) -> bool:
    logger.info("starting borg transfers")

    repository = options.get("repository")
    passphrase = options.get("passphrase")
    folders = options.get("folders")

    if not check_options(repository, passphrase, folders):
        report.add("borg", "config", False, 0, "invalid borg configuration")
        return False

    note = ""
    if not prune:
        logger.warning("borg prune and compact skipped, a service backup failed")
        note = "prune and compact skipped"

    failed = False

    # every entry is its own repository, so one failure never stops the others
    for entry in folders:
        started = monotonic()

        # a guard failure is specific to this source, so the other entries still run
        source_error = check_entry_source(entry)
        if source_error is not None:
            logger.error(f"{entry['repository']}: {source_error}, archive skipped")
            report.add("borg", entry["repository"], False, 0, source_error)
            failed = True
            continue

        entry_borg_env = borg_env(repository, passphrase, entry)
        try:
            warned = borg_create(entry_borg_env, entry)
            if prune:
                borg_prune(entry_borg_env)
                borg_compact(entry_borg_env)
        except Exception as e:
            logger.exception(e)
            if isinstance(e, subprocess.CalledProcessError):
                log_std_outputs(e, True)
            report.add(
                "borg",
                entry["repository"],
                False,
                monotonic() - started,
                describe_error(e),
            )
            failed = True
            continue

        entry_note = note
        if warned:
            entry_note = "archive created with warnings, see log"
            if note:
                entry_note += f", {note}"
        report.add(
            "borg",
            entry["repository"],
            True,
            monotonic() - started,
            entry_note,
            warning=warned,
        )
        failed = failed or warned

    if failed:
        logger.error("borg transfers completed with errors or warnings")
        return False

    logger.info("borg transfers completed")
    return True


def check_entry_source(entry: dict) -> str | None:
    try:
        folder = replace_env_vars(entry["folder"])
    except KeyError as e:
        return str(e)
    source_error = check_source(folder)
    if source_error is not None:
        return f"source '{folder}' {source_error}"
    return None


def borg_env(base_repository: str, passphrase: str, entry: dict) -> dict:
    return {
        "BORG_REPO": base_repository + entry.get("repository"),
        "BORG_PASSPHRASE": passphrase,
    }


def borg_create(env: dict, entry: dict) -> bool:
    # True when borg finished with warnings, exit code 1 still writes the archive
    folder = entry.get("folder")
    folder = replace_env_vars(folder)

    compression = entry.get("compression")
    compression = compression if compression else "none"

    logger.debug(f"{env.get('BORG_REPO')} transfer started")

    # fmt: off
    borg_cmd = [
        "borg", "create",
        "--stats",
        "--compression", compression,
        "::{hostname}-{now}",
        folder,
    ]
    # fmt: on
    process = run_command(
        borg_cmd,
        timeout=create_timeout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if process.returncode == 1:
        logger.error(f"{env.get('BORG_REPO')} archive created with warnings")
        log_std_outputs(process, True)
        return True
    if process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode, borg_cmd, process.stdout, process.stderr
        )
    log_std_outputs(process)
    logger.debug(f"{env.get('BORG_REPO')} transfer completed")
    return False


def borg_prune(env: dict):
    logger.debug(f"{env.get('BORG_REPO')} prune started")
    # fmt: off
    borg_cmd = [
        "borg", "prune",
        "--glob-archives", "{hostname}-*",
        "--keep-daily", "7",
        "--keep-weekly", "2",
        "--keep-monthly", "1"
    ]
    # fmt: on
    process = run_command(
        borg_cmd,
        timeout=maintenance_timeout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        env=env,
    )
    log_std_outputs(process)
    logger.debug(f"{env.get('BORG_REPO')} prune completed")


def borg_compact(env: dict):
    logger.debug(f"{env.get('BORG_REPO')} compact started")
    borg_cmd = ["borg", "compact"]
    process = run_command(
        borg_cmd,
        timeout=maintenance_timeout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        env=env,
    )
    log_std_outputs(process)
    logger.debug(f"{env.get('BORG_REPO')} compact completed")


def check_options(
    repository: str | None, passphrase: str | None, folders: list | None
) -> bool:
    if repository is None:
        logger.error("borg.repository is required")
        return False
    if passphrase is None:
        logger.error("borg.passphrase is required")
        return False
    if folders is None:
        logger.error("borg.folders is required")
        return False
    for entry in folders:
        if not isinstance(entry, dict):
            logger.error("borg.folders format is invalid")
            return False
        if not isinstance(entry.get("repository"), str):
            logger.error("borg.folders.repository is required")
            return False
        if not isinstance(entry.get("folder"), str):
            logger.error("borg.folders.folder is required")
            return False
    return True


def log_std_outputs(pipe, error=False):
    log = logger.debug if not error else logger.error
    if pipe.stdout:
        log(pipe.stdout.decode().strip())
    if pipe.stderr:
        log(pipe.stderr.decode().strip())
