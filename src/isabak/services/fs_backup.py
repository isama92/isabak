from src.isabak.helpers import check_source, replace_env_vars, run_command
from os.path import join as path_join
from os import makedirs
from src.isabak.logs import get_logger

logger = get_logger(__name__)

rsync_timeout = 2 * 3600


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
        run_command(cmd, timeout=rsync_timeout, check=True)
    except Exception as e:
        logger.exception(e, stack_info=True)
        return False

    logger.debug(f"copied {folder} to {destination}")

    logger.debug("fs backup completed successfully")
    return True


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
