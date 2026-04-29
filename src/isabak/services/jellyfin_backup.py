import requests
from json import dumps as json_dumps
from time import sleep
from os import listdir, makedirs, remove, stat
from os.path import isfile as path_isfile, join as path_join
from shutil import copy2 as shutil_copy2
from src.isabak.helpers import replace_env_vars
from src.isabak.logs import get_logger

logger = get_logger(__name__)


def jellyfin_backup(
    service_name: str, service_options: dict, domain: str | None, destination: str
):
    logger.debug(f"jellyfin backup for service '{service_name}' started")

    subdomain = service_options.get("subdomain")
    api_key = service_options.get("api_key")
    folder = service_options.get("folder")
    secure = service_options.get("secure", True)
    options = service_options.get("options", {})

    if not check_options(service_name, domain, api_key, folder):
        return

    destination = path_join(destination, "jellyfin", "")
    makedirs(destination, exist_ok=True)

    try:
        folder = replace_env_vars(folder)
    except KeyError as e:
        logger.error(f"{e}, {service_name}.jellyfin.folder")
        return

    base_url = build_base_url(domain, subdomain, secure)
    headers = build_headers(api_key)
    body = build_options_body(options)

    if not delete_existing_backups(folder):
        return

    if not create_backup(base_url, headers, body):
        return

    if not wait_backup_creation(folder):
        return

    copy_backup(folder, destination)

    logger.debug("jellyfin backup completed successfully")


def build_base_url(domain: str, subdomain: str | None, secure: bool) -> str:
    scheme = "https" if secure else "http"
    host = f"{subdomain}.{domain}" if subdomain else domain
    return f"{scheme}://{host}"


def build_headers(api_key: str) -> dict:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"MediaBrowser Token={api_key}",
    }


def build_options_body(options: dict) -> dict:
    return {
        "Database": options.get("database", True),
        "Metadata": options.get("metadata", False),
        "Trickplay": options.get("trickplay", False),
        "Subtitles": options.get("subtitles", False),
    }


def delete_existing_backups(folder: str) -> bool:
    try:
        deleted = 0
        for file in listdir(folder):
            src = path_join(folder, file)
            if not path_isfile(src):
                continue
            remove(src)
            deleted += 1
        logger.debug(f"{deleted} existing backups deleted")
        return True
    except Exception as e:
        logger.exception(e, stack_info=True)
        return False


def create_backup(base_url: str, headers: dict, body: dict) -> bool:
    try:
        response = requests.post(
            f"{base_url}/Backup/Create",
            json_dumps(body),
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as e:
        logger.error(f"backup creation request failed: {e}")
        return False

    if response.status_code != 200:
        logger.error(f"backup creation request failed with code {response.status_code}")
        return False

    return True


def wait_backup_creation(folder: str) -> bool:
    loop_n = 0
    while True:
        if listdir(folder):
            break
        if loop_n > 14:
            logger.error("backup was not created in time")
            return False
        sleep(1)
        loop_n += 1

    while True:
        size_before = folder_total_size(folder)
        sleep(1)
        size_after = folder_total_size(folder)
        if size_before > 0 and size_before == size_after:
            return True
        if loop_n > 29:
            logger.error("backup file size did not stabilize in time")
            return False
        loop_n += 1


def folder_total_size(folder: str) -> int:
    total = 0
    for file in listdir(folder):
        src = path_join(folder, file)
        if not path_isfile(src):
            continue
        total += stat(src).st_size
    return total


def copy_backup(folder: str, destination: str):
    for file in listdir(folder):
        src = path_join(folder, file)
        if not path_isfile(src):
            continue
        dest = path_join(destination, file)
        shutil_copy2(src, dest)
        logger.debug(f"copied {src} to {dest}")


def check_options(
    service_name: str,
    domain: str | None,
    api_key: str | None,
    folder: str | None,
) -> bool:
    if domain is None:
        logger.error("domain is required")
        return False
    if api_key is None:
        logger.error(f"{service_name}.jellyfin.api_key is required")
        return False
    if folder is None:
        logger.error(f"{service_name}.jellyfin.folder is required")
        return False
    return True
