from src.isabak.helpers import (
    check_source,
    replace_env_vars,
    run_command,
    strip_control_characters,
)
from os.path import join as path_join
from os import makedirs
import subprocess
from src.isabak.logs import get_logger

logger = get_logger(__name__)

rsync_timeout = 2 * 3600
# "some files vanished before they could be transferred"
rsync_vanished = 24
# enough to see which files failed without flooding the email
stderr_lines = 30


def fs_backup(service_name: str, service_options: dict, destination: str) -> bool:
    logger.debug(f"fs backup for service '{service_name}' started")

    folder = service_options.get("folder")
    include = service_options.get("include") or []
    exclude = service_options.get("exclude") or []

    if not check_options(service_name, folder, include, exclude):
        return False

    destination = path_join(destination, "fs", "")
    makedirs(destination, exist_ok=True)

    try:
        folder = replace_env_vars(folder)
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

    logger.debug("fs backup completed successfully")
    return True


def tail(stderr: bytes | None) -> str:
    text = strip_control_characters((stderr or b"").decode(errors="replace"))
    lines = text.strip().splitlines()
    if len(lines) > stderr_lines:
        lines = [f"... {len(lines) - stderr_lines} earlier lines skipped"] + lines[
            -stderr_lines:
        ]
    # prefixed, file names in rsync's output cannot pass for an isabak log line
    return "\n".join(f"rsync: {line}" for line in lines)


def check_options(
    service_name: str,
    folder: str | None,
    include: list,
    exclude: list,
):
    if folder is None:
        logger.error(f"{service_name}.fs.folder is required")
        return False
    if not isinstance(include, list):
        logger.error(f"{service_name}.fs.include must be a list")
        return False
    if not isinstance(exclude, list):
        logger.error(f"{service_name}.fs.exclude must be a list")
        return False
    return True
