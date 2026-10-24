from src.isabak.logs import get_logger
from yaml import safe_load as yaml_load
from os import getenv
from os import makedirs, stat
from os.path import isdir as path_isdir, join as path_join
from shutil import rmtree

logger = get_logger(__name__)

app_name = "isabak"
config_file_path = "config.yaml"


def load_config() -> dict | None:
    logger.debug(f"loading {config_file_path}")

    try:
        f = open(config_file_path, "r")
    except FileNotFoundError:
        logger.error(f"{config_file_path} not found")
        return None

    config = yaml_load(f)

    if not isinstance(config, dict):
        logger.error(f"{config_file_path} is empty or malformed")
        return None

    logger.debug(f"{config_file_path} loaded")

    return config


def merge_config(config: dict) -> dict:
    logger.debug(f"merging env to config")

    env_destination = getenv("DESTINATION")
    if env_destination is not None:
        config["destination"] = env_destination

    env_domain = getenv("DOMAIN")
    if env_domain is not None:
        config["domain"] = env_domain

    logger.debug(f"env to config merge completed")

    return config


def get_base_destination(destination: str, allow_root_fs: bool = False) -> str | None:
    # an unmounted disk leaves an empty mount point on the system disk
    if not path_isdir(destination):
        logger.error(
            f"destination '{destination}' does not exist, is its disk mounted?"
        )
        return None

    if not allow_root_fs and stat(destination).st_dev == stat("/").st_dev:
        logger.error(
            f"destination '{destination}' is on the root filesystem, "
            "is its disk mounted? "
            "set allow_destination_on_root_fs: true if this is intended"
        )
        return None

    base_destination = path_join(destination, app_name)

    try:
        rmtree(base_destination)
    except Exception as e:
        logger.debug(f"could not remove existing backup destination folder ({e})")

    try:
        makedirs(base_destination)
    except Exception as e:
        logger.error(f"could not create backup destination folder ({e})")
        return None

    return base_destination
