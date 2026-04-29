import requests
from json import dumps as json_dumps
from time import sleep
from os import listdir, makedirs
from os.path import isfile as path_isfile, join as path_join
from shutil import copy2 as shutil_copy2
from src.isabak.helpers import replace_env_vars
from src.isabak.logs import get_logger

logger = get_logger(__name__)


def arr_backup(
    service_name: str, service_options: dict, domain: str | None, destination: str
):
    logger.debug(f"arr backup for service '{service_name}' started")

    subdomain = service_options.get("subdomain")
    endpoint = service_options.get("endpoint")
    api_key = service_options.get("api_key")
    folder = service_options.get("folder")
    secure = service_options.get("secure", True)

    if not check_options(service_name, domain, endpoint, api_key, folder):
        return

    destination = path_join(destination, "arr", "")
    makedirs(destination, exist_ok=True)

    try:
        folder = replace_env_vars(folder)
    except KeyError as e:
        logger.error(f"{e}, {service_name}.arr.folder")
        return

    base_url = build_base_url(domain, subdomain, endpoint, secure)
    headers = build_headers(api_key)

    if not delete_existing_backups(base_url, headers):
        return

    if not create_backup(base_url, headers):
        return

    if not wait_backup_creation(folder):
        return

    copy_backup(folder, destination)

    logger.debug(f"arr backup completed successfully")


def build_base_url(
    domain: str, subdomain: str | None, endpoint: str, secure: bool
) -> str:
    scheme = "https" if secure else "http"
    host = f"{subdomain}.{domain}" if subdomain else domain

    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint

    return f"{scheme}://{host}{endpoint}"


def build_headers(api_key: str) -> dict:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
    }


def delete_existing_backups(base_url: str, headers: dict) -> bool:
    try:
        response = requests.get(
            f"{base_url}/system/backup", headers=headers, timeout=30
        )
    except requests.RequestException as e:
        logger.error(f"backups list request failed: {e}")
        return False

    if response.status_code != 200:
        logger.error(f"backups list request failed with code {response.status_code}")
        return False

    backups = response.json()

    logger.debug(f"{len(backups)} backups found'")

    for backup in backups:
        backup_id = backup.get("id")
        if backup_id is None:
            logger.error(f"backup did not contain the backup id")
            return False
        try:
            response = requests.delete(
                f"{base_url}/system/backup/{backup_id}", headers=headers, timeout=30
            )
        except requests.RequestException as e:
            logger.error(f"deletion of backup '{backup_id}' failed: {e}")
            return False
        if response.status_code != 200:
            logger.error(
                f"deletion of backup '{backup_id}' failed with code {response.status_code}"
            )
            return False

    logger.debug(f"{len(backups)} backups deleted")
    return True


def create_backup(base_url: str, headers: dict) -> bool:
    try:
        response = requests.post(
            f"{base_url}/command",
            json_dumps({"name": "Backup"}),
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as e:
        logger.error(f"backup creation request failed: {e}")
        return False

    if response.status_code != 201:
        logger.error(f"backup creation request failed with code {response.status_code}")
        return False

    return True


def wait_backup_creation(folder: str) -> bool:
    loop_n = 0
    while True:
        if listdir(folder):
            return True
        if loop_n > 10:
            logger.error("backup was not created in time")
            return False
        sleep(1)
        loop_n += 1


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
    endpoint: str | None,
    api_key: str | None,
    folder: str | None,
) -> bool:
    if domain is None:
        logger.error(f"domain is required")
        return False
    if endpoint is None:
        logger.error(f"{service_name}.api.endpoint is required")
        return False
    if api_key is None:
        logger.error(f"{service_name}.api.api_key is required")
        return False
    if folder is None:
        logger.error(f"{service_name}.api.folder is required")
        return False
    return True
