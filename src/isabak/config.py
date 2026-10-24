from src.isabak.logs import get_logger
from yaml import YAMLError, safe_load as yaml_load
from json import load as json_load
from os import getenv
from os import chmod, lstat, makedirs, rename, stat, walk
from os.path import exists as path_exists, isdir as path_isdir, join as path_join
from shutil import disk_usage, rmtree

logger = get_logger(__name__)

app_name = "isabak"
config_file_path = "config.yaml"
# written into the tree by every run, read back to know whether that run succeeded
manifest_name = "isabak-run.json"
# the last complete tree, kept next to the new one until the next successful run
last_good_suffix = ".last-good"
# free space needed before a run: the last tree's size times this, for growth
space_growth = 1.2
bytes_per_gb = 1000**3

top_level_keys = {
    "domain",
    "destination",
    "allow_destination_on_root_fs",
    "min_free_gb",
    "mysql",
    "mariadb",
    "postgres",
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
        with open(config_file_path, "r") as f:
            config = yaml_load(f)
    except FileNotFoundError:
        logger.error(f"{config_file_path} not found")
        return None
    except OSError as e:
        logger.error(f"{config_file_path} could not be read ({e.strerror})")
        return None
    except YAMLError as e:
        # only the problem and its position: the full message quotes the
        # offending line, which can be a password
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        problem = getattr(e, "problem", None) or "syntax error"
        logger.error(f"{config_file_path} is not valid yaml{where}: {problem}")
        return None

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


def get_base_destination(
    destination: str, allow_root_fs: bool = False, min_free_gb=None
) -> str | None:
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

    # before anything is moved or deleted: a disk that fills up half-way leaves
    # broken copies, a refused run leaves the old trees exactly as they were
    if not check_free_space(destination, base_destination, min_free_gb):
        return None

    try:
        keep_last_good(base_destination)
    except OSError as e:
        logger.error(f"could not set aside the previous backup tree ({e})")
        return None

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


def check_free_space(destination: str, base_destination: str, min_free_gb) -> bool:
    if min_free_gb is not None and (
        isinstance(min_free_gb, bool)
        or not isinstance(min_free_gb, (int, float))
        or min_free_gb < 0
    ):
        logger.error("min_free_gb must be a number of 0 or more")
        return False

    last_good = base_destination + last_good_suffix
    previous = tree_size(base_destination)
    kept = tree_size(last_good)
    # the new tree will be about as big as the last one, plus some growth
    needed = max(previous, kept) * space_growth
    if min_free_gb is not None:
        needed = max(needed, min_free_gb * bytes_per_gb)
    # what keep_last_good() frees before the new tree is written
    if previous_run_ok(base_destination):
        reclaimed = kept
    else:
        reclaimed = previous
    available = disk_usage(destination).free + reclaimed

    if available < needed:
        logger.error(
            f"not enough free space on '{destination}': "
            f"{available / bytes_per_gb:.1f} GB available, "
            f"{needed / bytes_per_gb:.1f} GB needed"
        )
        return False
    return True


def tree_size(folder: str) -> int:
    total = 0
    for root, _, files in walk(folder):
        for file in files:
            try:
                total += lstat(path_join(root, file)).st_size
            except OSError:
                continue
    return total


def keep_last_good(base_destination: str):
    # the previous run's tree becomes isabak.last-good when that run succeeded;
    # a failed run's tree is dropped and the older last-good stays, so a run of
    # failures never leaves the destination without a complete local tree
    if not path_exists(base_destination):
        return
    last_good = base_destination + last_good_suffix
    if previous_run_ok(base_destination):
        if path_exists(last_good):
            rmtree(last_good)
        rename(base_destination, last_good)
    else:
        rmtree(base_destination)


def previous_run_ok(base_destination: str) -> bool:
    try:
        with open(path_join(base_destination, manifest_name)) as f:
            return json_load(f).get("ok") is True
    except (OSError, ValueError, AttributeError):
        return False


def drop_last_good(base_destination: str):
    # called once this run's services all succeeded, before borg
    last_good = base_destination + last_good_suffix
    try:
        if path_exists(last_good):
            rmtree(last_good)
    except OSError as e:
        logger.error(f"could not remove {last_good} ({e})")
