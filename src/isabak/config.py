from src.isabak.logs import get_logger
from yaml import safe_load as yaml_load
from os import getenv
from os import chmod, makedirs, stat
from os.path import isdir as path_isdir, join as path_join
from shutil import rmtree

logger = get_logger(__name__)

app_name = "isabak"
config_file_path = "config.yaml"

top_level_keys = {
    "domain",
    "destination",
    "allow_destination_on_root_fs",
    "mysql",
    "mariadb",
    "services",
    "borg",
}
service_keys = {
    "name",
    "fs",
    "mysql",
    "mariadb",
    "postgres",
    "arr",
    "jellyfin",
    "sqlite",
}
borg_keys = {"repository", "passphrase", "folders", "keep"}
borg_folder_keys = {"repository", "folder", "compression"}


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


def check_unknown_keys(config: dict) -> bool:
    # a misspelt key is otherwise ignored silently, e.g. "Borg:" skips the upload
    ok = report_unknown_keys("", config, top_level_keys)

    services = config.get("services")
    if isinstance(services, list):
        for service in services:
            if isinstance(service, dict):
                prefix = f"services.{service.get('name')}."
                ok = report_unknown_keys(prefix, service, service_keys) and ok

    borg = config.get("borg")
    if isinstance(borg, dict):
        ok = report_unknown_keys("borg.", borg, borg_keys) and ok
        folders = borg.get("folders")
        if isinstance(folders, list):
            for index, entry in enumerate(folders):
                if isinstance(entry, dict):
                    prefix = f"borg.folders[{index}]."
                    ok = report_unknown_keys(prefix, entry, borg_folder_keys) and ok

    return ok


def report_unknown_keys(prefix: str, section: dict, allowed: set[str]) -> bool:
    unknown = sorted(str(key) for key in section if key not in allowed)
    for key in unknown:
        logger.error(f"{prefix}{key} is not a known option")
    return not unknown


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
        # root only: the tree holds dumps, api keys and password hashes, and the
        # files inside keep their source permissions (rsync -a, copy2), so the
        # folder itself is what keeps other users out
        chmod(base_destination, 0o700)
    except Exception as e:
        logger.error(f"could not create backup destination folder ({e})")
        return None

    return base_destination
