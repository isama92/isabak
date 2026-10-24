import requests
from json import dumps as json_dumps
from time import monotonic, sleep
from os import listdir, makedirs
from os.path import isfile as path_isfile, join as path_join
from shutil import copy2 as shutil_copy2
from dataclasses import dataclass
from src.isabak.helpers import as_secret, replace_env_vars
from src.isabak.logs import get_logger
from src.isabak.verify import verify_zip

logger = get_logger(__name__)

# backup type reported by the *arr api for backups started through the command
# api, which is how isabak creates them
manual_type = "manual"
# a backup file appears while the app is still writing it, so isabak waits until
# the app itself reports the command as finished
command_timeout = 600
command_poll_seconds = 2
command_failed = {"failed", "aborted", "cancelled", "orphaned"}


def arr_backup(
    service_name: str, service_options: dict, domain: str | None, destination: str
) -> bool:
    logger.debug(f"arr backup for service '{service_name}' started")

    options = check_options(service_name, service_options, domain)
    if options is None:
        return False

    destination = path_join(destination, "arr", "")
    makedirs(destination, exist_ok=True)

    try:
        folder = replace_env_vars(options.folder)
    except KeyError as e:
        logger.error(f"{e}, {service_name}.arr.folder")
        return False

    base_url = build_base_url(
        options.domain, options.subdomain, options.endpoint, options.secure
    )
    headers = build_headers(options.api_key)

    # create, copy and check first, delete old backups only after that: a failed
    # run must never leave the app, or the local tree, without any backup
    existing_files = list_backup_files(folder)
    if existing_files is None:
        return False

    created, command_id = create_backup(base_url, headers)
    if not created:
        return False
    if command_id is None:
        logger.debug("backup command id missing, relying on the zip check")
    elif not wait_command(base_url, headers, command_id):
        return False

    new_files = wait_backup_creation(folder, existing_files)
    if not new_files:
        return False

    if not copy_backup(folder, new_files, destination):
        return False

    delete_old_backups(base_url, headers, new_files)

    logger.debug("arr backup completed successfully")
    return True


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


def delete_old_backups(base_url: str, headers: dict, keep: list[str]):
    # only manual backups, the kind isabak creates, and never the new one: the
    # app's own scheduled and update backups stay. the backup itself succeeded,
    # so a failure here is reported (error, it reaches the mail) but not fatal
    try:
        response = requests.get(
            f"{base_url}/system/backup", headers=headers, timeout=30
        )
        if response.status_code != 200:
            logger.error(
                f"backups list request failed with code {response.status_code}"
            )
            return
        backups = response.json()
    except (requests.RequestException, ValueError) as e:
        logger.error(f"backups list request failed: {e}")
        return

    if not isinstance(backups, list):
        logger.error("backups list response is not a list")
        return

    deleted = 0
    for backup in backups:
        if not isinstance(backup, dict) or backup.get("type") != manual_type:
            continue
        backup_id = backup.get("id")
        if backup_id is None or backup.get("name") in keep:
            continue
        try:
            response = requests.delete(
                f"{base_url}/system/backup/{backup_id}", headers=headers, timeout=30
            )
        except requests.RequestException as e:
            logger.error(f"deletion of backup '{backup_id}' failed: {e}")
            return
        if response.status_code != 200:
            code = response.status_code
            logger.error(f"deletion of backup '{backup_id}' failed with code {code}")
            return
        deleted += 1

    logger.debug(f"{deleted} old manual backups deleted")


def create_backup(base_url: str, headers: dict) -> tuple[bool, int | None]:
    try:
        response = requests.post(
            f"{base_url}/command",
            json_dumps({"name": "Backup"}),
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as e:
        logger.error(f"backup creation request failed: {e}")
        return False, None

    if response.status_code != 201:
        logger.error(f"backup creation request failed with code {response.status_code}")
        return False, None

    try:
        command_id = response.json().get("id")
    except (ValueError, AttributeError):
        command_id = None
    if isinstance(command_id, bool) or not isinstance(command_id, int):
        command_id = None
    return True, command_id


def wait_command(base_url: str, headers: dict, command_id: int) -> bool:
    deadline = monotonic() + command_timeout
    while True:
        try:
            response = requests.get(
                f"{base_url}/command/{command_id}", headers=headers, timeout=30
            )
            status = response.json().get("status") if response.ok else None
        except (requests.RequestException, ValueError, AttributeError) as e:
            logger.error(f"backup command status request failed: {e}")
            return False
        if not response.ok:
            logger.error(
                f"backup command status request failed with code {response.status_code}"
            )
            return False
        if status == "completed":
            return True
        if status in command_failed:
            logger.error(f"backup command ended as {status}")
            return False
        if monotonic() > deadline:
            logger.error(f"backup command did not finish in {command_timeout}s")
            return False
        sleep(command_poll_seconds)


def list_backup_files(folder: str) -> set[str] | None:
    try:
        return {
            file for file in listdir(folder) if path_isfile(path_join(folder, file))
        }
    except OSError as e:
        logger.error(f"backup folder '{folder}' could not be read: {e}")
        return None


def wait_backup_creation(folder: str, existing_files: set[str]) -> list[str]:
    loop_n = 0
    while True:
        files = list_backup_files(folder)
        if files is None:
            return []
        new_files = files - existing_files
        if new_files:
            logger.debug(f"new backup files: {', '.join(sorted(new_files))}")
            return sorted(new_files)
        if loop_n > 10:
            logger.error("backup was not created in time")
            return []
        sleep(1)
        loop_n += 1


def copy_backup(folder: str, files: list[str], destination: str) -> bool:
    copied = 0
    try:
        for file in files:
            src = path_join(folder, file)
            dest = path_join(destination, file)
            shutil_copy2(src, dest)
            logger.debug(f"copied {src} to {dest}")
            copied += 1
            # a zip copied while the app was still writing it fails here
            verify_error = verify_zip(dest) if file.endswith(".zip") else None
            if verify_error is not None:
                logger.error(f"arr backup {file} {verify_error}")
                return False
    except OSError as e:
        logger.exception(e, stack_info=True)
        return False

    if copied == 0:
        logger.error("no backup files were copied")
        return False

    return True


@dataclass(frozen=True)
class ArrOptions:
    domain: str
    subdomain: str | None
    endpoint: str
    api_key: str
    folder: str
    secure: bool


def check_options(
    service_name: str, service_options: dict, domain: str | None
) -> ArrOptions | None:
    subdomain = service_options.get("subdomain")
    endpoint = service_options.get("endpoint")
    api_key = as_secret(service_options.get("api_key"), f"{service_name}.arr.api_key")
    folder = service_options.get("folder")
    secure = service_options.get("secure", True)

    if not isinstance(domain, str):
        logger.error("domain is required")
        return None
    if not isinstance(endpoint, str):
        logger.error(f"{service_name}.arr.endpoint is required")
        return None
    if api_key is None:
        logger.error(f"{service_name}.arr.api_key is required")
        return None
    if not isinstance(folder, str):
        logger.error(f"{service_name}.arr.folder is required")
        return None
    if subdomain is not None and not isinstance(subdomain, str):
        logger.error(f"{service_name}.arr.subdomain must be text")
        return None
    return ArrOptions(domain, subdomain, endpoint, api_key, folder, bool(secure))
