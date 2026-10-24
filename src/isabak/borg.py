import subprocess
from os import environ, sep
from os.path import realpath
from time import monotonic
from src.isabak.helpers import check_source, replace_env_vars, run_command
from src.isabak.logs import get_logger
from src.isabak.report import RunReport, describe_error

logger = get_logger(__name__)

# a first run of a large source can take hours, borg resumes from its checkpoints
create_timeout = 4 * 3600
maintenance_timeout = 3600

# complete archives are pruned by these, archives from failed runs carry their own
# prefix so they never take a daily, weekly or monthly slot from a complete one
default_keep = {"daily": 7, "weekly": 2, "monthly": 1, "partial_days": 7}
partial_prefix = "partial-"


def borg_transfer(
    options: dict,
    report: RunReport,
    prune: bool = True,
    partial_tree: str | None = None,
) -> bool:
    # partial_tree: the isabak tree when a service failed, the entry holding it
    # is archived under the partial- prefix
    logger.info("starting borg transfers")

    repository = options.get("repository")
    passphrase = options.get("passphrase")
    folders = options.get("folders")
    keep = check_keep(options.get("keep"))

    if not check_options(repository, passphrase, folders) or keep is None:
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
        partial = partial_tree is not None and holds_tree(entry["folder"], partial_tree)
        try:
            warned = borg_create(entry_borg_env, entry, partial)
            if prune:
                borg_prune(entry_borg_env, keep)
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

        notes = []
        if partial:
            notes.append("archived as partial, a service failed")
        if warned:
            notes.append("archive created with warnings, see log")
        if note:
            notes.append(note)
        entry_note = ", ".join(notes)
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


def holds_tree(folder: str, tree: str) -> bool:
    folder = realpath(replace_env_vars(folder))
    tree = realpath(tree)
    return tree == folder or tree.startswith(folder.rstrip(sep) + sep)


def check_keep(keep) -> dict | None:
    if keep is None:
        return dict(default_keep)
    if not isinstance(keep, dict):
        logger.error("borg.keep must be a mapping")
        return None
    unknown = sorted(str(key) for key in keep if key not in default_keep)
    if unknown:
        logger.error(f"borg.keep.{unknown[0]} is not a known option")
        return None
    merged = {**default_keep, **keep}
    for key, value in merged.items():
        # bool is an int in python, "daily: yes" must not become 1
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            logger.error(f"borg.keep.{key} must be a whole number of 0 or more")
            return None
    # all zero would let prune delete every archive
    if merged["daily"] < 1 or merged["partial_days"] < 1:
        logger.error("borg.keep.daily and borg.keep.partial_days must be at least 1")
        return None
    return merged


def borg_env(base_repository: str, passphrase: str, entry: dict) -> dict:
    # on top of the inherited environment, so PATH, HOME, LANG, SSH_AUTH_SOCK and
    # any BORG_* settings from vars still reach borg and ssh
    return {
        **environ,
        "BORG_REPO": base_repository + entry.get("repository"),
        "BORG_PASSPHRASE": passphrase,
    }


def borg_create(env: dict, entry: dict, partial: bool = False) -> bool:
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
        f"::{partial_prefix if partial else ''}{{hostname}}-{{now}}",
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


def borg_prune(env: dict, keep: dict):
    logger.debug(f"{env.get('BORG_REPO')} prune started")
    # complete archives only, partial-{hostname}-* does not match {hostname}-*
    # fmt: off
    complete_cmd = [
        "borg", "prune",
        "--glob-archives", "{hostname}-*",
        "--keep-daily", str(keep["daily"]),
        "--keep-weekly", str(keep["weekly"]),
        "--keep-monthly", str(keep["monthly"]),
    ]
    partial_cmd = [
        "borg", "prune",
        "--glob-archives", f"{partial_prefix}{{hostname}}-*",
        "--keep-within", f"{keep['partial_days']}d",
    ]
    # fmt: on
    for borg_cmd in (complete_cmd, partial_cmd):
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
