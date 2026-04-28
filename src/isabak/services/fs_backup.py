from src.isabak.helpers import replace_env_vars
from os.path import join as path_join, exists as path_exists
from os import makedirs
import subprocess
from src.isabak.logs import get_logger

logger = get_logger(__name__)


def fs_backup(service_name: str, service_options: dict, destination: str):
    logger.debug(f"fs backup for service '{service_name}' started")

    folder = service_options.get("folder")
    include = service_options.get("include") or []
    exclude = service_options.get("exclude") or []

    if not check_options(service_name, folder, include, exclude):
        return

    destination = path_join(destination, "fs", "")
    makedirs(destination, exist_ok=True)

    try:
        folder = replace_env_vars(folder)
    except KeyError as e:
        logger.error(f"{e}, {service_name}.fs.folder")
        return

    folder = path_join(folder, "")

    if not path_exists(folder):
        logger.error(
            f"{service_name}.fs.folder '{folder}' does not exist in filesystem"
        )
        return

    cmd = ["rsync", "-a"]
    for pattern in exclude:
        cmd.append(f"--exclude={pattern}")
    for pattern in include:
        cmd.append(f"--include={pattern}")
    if include:
        cmd.append("--exclude=*")
    cmd += [folder, destination]

    try:
        subprocess.run(cmd, check=True)
    except Exception as e:
        logger.exception(e, stack_info=True)
        return

    logger.debug(f"copied {folder} to {destination}")

    logger.debug("fs backup completed successfully")


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
