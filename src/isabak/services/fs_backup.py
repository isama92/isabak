from dataclasses import dataclass
from src.isabak.helpers import (
    check_source,
    replace_env_vars,
    run_command,
    strip_control_characters,
)
from os.path import join as path_join, relpath
from os import lstat, makedirs, walk
from stat import S_ISREG
from time import time
import subprocess
from src.isabak.logs import get_logger

logger = get_logger(__name__)

rsync_timeout = 2 * 3600
# "some files vanished before they could be transferred"
rsync_vanished = 24
# enough to see which files failed without flooding the email
stderr_lines = 30
# tolerated clock difference before a file time counts as in the future
clock_skew_seconds = 300


def fs_backup(service_name: str, service_options: dict, destination: str) -> bool:
    logger.debug(f"fs backup for service '{service_name}' started")

    options = check_options(service_name, service_options)
    if options is None:
        return False
    include, exclude, max_age_hours = (
        options.include,
        options.exclude,
        options.max_age_hours,
    )

    destination = path_join(destination, "fs", "")
    makedirs(destination, exist_ok=True)

    try:
        folder = replace_env_vars(options.folder)
    except KeyError as e:
        logger.error(f"{e}, {service_name}.fs.folder")
        return False

    folder = path_join(folder, "")

    source_error = check_source(folder)
    if source_error is not None:
        logger.error(f"{service_name}.fs.folder '{folder}' {source_error}")
        return False

    cmd = ["rsync", "-a"]
    for pattern in exclude:
        cmd.append(f"--exclude={pattern}")
    for pattern in include:
        cmd.append(f"--include={pattern}")
    if include:
        cmd.append("--exclude=*")
    cmd += [folder, destination]

    try:
        process = run_command(cmd, timeout=rsync_timeout, stderr=subprocess.PIPE)
    except Exception as e:
        logger.exception(e, stack_info=True)
        return False

    errors = tail(process.stderr)
    if process.returncode == rsync_vanished:
        # a file deleted while rsync ran (a log, a cache entry) no longer exists,
        # so nothing is missing from the copy: logged, not alerted every night
        logger.warning(f"{service_name}.fs files vanished during the copy\n{errors}")
    elif process.returncode != 0:
        logger.error(f"{service_name}.fs rsync exited with {process.returncode}")
        if errors:
            logger.error(errors)
        return False

    logger.debug(f"copied {folder} to {destination}")

    # after the copy, on the copy: it holds exactly what was backed up (excludes
    # applied, rsync -a keeps the times), and even a stale backup is kept
    if max_age_hours is not None and not check_freshness(
        service_name, destination, max_age_hours
    ):
        return False

    logger.debug("fs backup completed successfully")
    return True


def check_freshness(service_name: str, folder: str, max_age_hours: float) -> bool:
    # for folders an app fills itself (e.g. immich's nightly database dump): if
    # the app stops writing, the copy would otherwise keep the old file forever
    newest = newest_file(folder)
    if newest is None:
        logger.error(f"{service_name}.fs copied no files to check the age of")
        return False
    path, mtime = newest
    # file names come from the app: no control characters, no line breaks
    name = strip_control_characters(relpath(path, folder)).replace("\n", "\\n")
    if mtime > time() + clock_skew_seconds:
        # a future time would always look fresh
        logger.error(f"{service_name}.fs newest file has a future time: {name}")
        return False
    age_hours = (time() - mtime) / 3600
    if age_hours > max_age_hours:
        logger.error(
            f"{service_name}.fs newest file is {age_hours:.1f}h old "
            f"(limit {max_age_hours}h): {name}"
        )
        return False
    return True


def newest_file(folder: str) -> tuple[str, float] | None:
    newest = None
    for root, _, files in walk(folder):
        for file in files:
            path = path_join(root, file)
            try:
                info = lstat(path)
            except OSError:
                continue
            # symlinks are not followed, they could point anywhere
            if S_ISREG(info.st_mode) and (newest is None or info.st_mtime > newest[1]):
                newest = (path, info.st_mtime)
    return newest


def tail(stderr: bytes | None) -> str:
    text = strip_control_characters((stderr or b"").decode(errors="replace"))
    lines = text.strip().splitlines()
    if len(lines) > stderr_lines:
        lines = [f"... {len(lines) - stderr_lines} earlier lines skipped"] + lines[
            -stderr_lines:
        ]
    # prefixed, file names in rsync's output cannot pass for an isabak log line
    return "\n".join(f"rsync: {line}" for line in lines)


@dataclass(frozen=True)
class FsOptions:
    folder: str
    include: list
    exclude: list
    max_age_hours: float | None


def check_options(service_name: str, service_options: dict) -> FsOptions | None:
    folder = service_options.get("folder")
    include = service_options.get("include") or []
    exclude = service_options.get("exclude") or []
    max_age_hours = service_options.get("max_age_hours")

    if not isinstance(folder, str):
        logger.error(f"{service_name}.fs.folder is required")
        return None
    if not isinstance(include, list):
        logger.error(f"{service_name}.fs.include must be a list")
        return None
    if not isinstance(exclude, list):
        logger.error(f"{service_name}.fs.exclude must be a list")
        return None
    if max_age_hours is not None and (
        isinstance(max_age_hours, bool)
        or not isinstance(max_age_hours, (int, float))
        or max_age_hours <= 0
    ):
        logger.error(f"{service_name}.fs.max_age_hours must be a number above 0")
        return None
    return FsOptions(folder, include, exclude, max_age_hours)
