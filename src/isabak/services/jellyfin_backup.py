import requests
from fnmatch import fnmatch
from json import dumps as json_dumps
from time import monotonic, sleep
from os import listdir, makedirs, remove, stat
from os.path import isfile as path_isfile, join as path_join
from shutil import copy2 as shutil_copy2
from dataclasses import dataclass
from src.isabak.helpers import as_text, replace_env_vars
from src.isabak.logs import get_logger
from src.isabak.verify import verify_zip

logger = get_logger(__name__)

# the only files isabak ever deletes in the jellyfin backup folder
backup_pattern = "jellyfin-backup-*.zip"
# a backup with metadata can take minutes, a stalled one must not hang the run
creation_timeout = 600
# seconds in a row the new file must keep the same size before it is copied
stable_seconds = 3


def jellyfin_backup(
    service_name: str, service_options: dict, domain: str | None, destination: str
) -> bool:
    logger.debug(f"jellyfin backup for service '{service_name}' started")

    options = check_options(service_name, service_options, domain)
    if options is None:
        return False

    destination = path_join(destination, "jellyfin", "")
    makedirs(destination, exist_ok=True)

    try:
        folder = replace_env_vars(options.folder)
    except KeyError as e:
        logger.error(f"{e}, {service_name}.jellyfin.folder")
        return False

    base_url = build_base_url(options.domain, options.subdomain, options.secure)
    headers = build_headers(options.api_key)
    body = build_options_body(options.content)

    # create, copy and check first, delete old backups only after that: a failed
    # run must never leave jellyfin, or the local tree, without any backup
    existing_files = list_backup_files(folder)
    if existing_files is None:
        return False

    if not create_backup(base_url, headers, body):
        return False

    new_files = wait_backup_creation(folder, existing_files)
    if not new_files:
        return False

    if not copy_backup(folder, new_files, destination):
        return False

    delete_old_backups(folder, new_files)

    logger.debug("jellyfin backup completed successfully")
    return True


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


def list_backup_files(folder: str) -> set[str] | None:
    try:
        return {
            file for file in listdir(folder) if path_isfile(path_join(folder, file))
        }
    except OSError as e:
        logger.error(f"backup folder '{folder}' could not be read: {e}")
        return None


def delete_old_backups(folder: str, keep: list[str]):
    # jellyfin has no retention and no delete api. only its own backup zips are
    # removed, never the new one and never anything else in the folder. the
    # backup itself succeeded, so a failure here is reported but not fatal
    deleted = 0
    try:
        for file in listdir(folder):
            path = path_join(folder, file)
            if file in keep or not fnmatch(file, backup_pattern):
                continue
            if path_isfile(path):
                remove(path)
                deleted += 1
    except OSError as e:
        logger.error(f"old jellyfin backups could not be removed: {e}")
    logger.debug(f"{deleted} old backups deleted")


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


def wait_backup_creation(folder: str, existing_files: set[str]) -> list[str]:
    # only a file that was not there before the request counts as the new backup
    deadline = monotonic() + creation_timeout
    while True:
        files = list_backup_files(folder)
        if files is None:
            return []
        new_files = sorted(files - existing_files)
        if new_files:
            break
        if monotonic() > deadline:
            logger.error("backup was not created in time")
            return []
        sleep(1)

    # jellyfin has no status api, so "done" means the size stopped changing for
    # several seconds in a row; a pause in writing is still caught by the zip check
    stable = 0
    size_before = files_total_size(folder, new_files)
    while stable < stable_seconds:
        if monotonic() > deadline:
            logger.error("backup file size did not stabilize in time")
            return []
        sleep(1)
        size_after = files_total_size(folder, new_files)
        stable = stable + 1 if size_after > 0 and size_after == size_before else 0
        size_before = size_after

    logger.debug(f"new backup files: {', '.join(new_files)}")
    return new_files


def files_total_size(folder: str, files: list[str]) -> int:
    total = 0
    for file in files:
        try:
            total += stat(path_join(folder, file)).st_size
        except OSError:
            continue
    return total


def copy_backup(folder: str, files: list[str], destination: str) -> bool:
    copied = 0
    try:
        for file in files:
            src = path_join(folder, file)
            if not path_isfile(src):
                continue
            dest = path_join(destination, file)
            shutil_copy2(src, dest)
            logger.debug(f"copied {src} to {dest}")
            copied += 1
            # a zip copied while jellyfin was still writing it fails here
            verify_error = verify_zip(dest) if file.endswith(".zip") else None
            if verify_error is not None:
                logger.error(f"jellyfin backup {file} {verify_error}")
                return False
    except OSError as e:
        logger.exception(e, stack_info=True)
        return False

    if copied == 0:
        logger.error("no backup files were copied")
        return False

    return True


@dataclass(frozen=True)
class JellyfinOptions:
    domain: str
    subdomain: str | None
    api_key: str
    folder: str
    secure: bool
    # which content categories go into the zip, see build_options_body()
    content: dict


def check_options(
    service_name: str, service_options: dict, domain: str | None
) -> JellyfinOptions | None:
    subdomain = service_options.get("subdomain")
    api_key = as_text(service_options.get("api_key"))
    folder = service_options.get("folder")
    secure = service_options.get("secure", True)
    content = service_options.get("options") or {}

    if not isinstance(domain, str):
        logger.error("domain is required")
        return None
    if api_key is None:
        logger.error(f"{service_name}.jellyfin.api_key is required")
        return None
    if not isinstance(folder, str):
        logger.error(f"{service_name}.jellyfin.folder is required")
        return None
    if subdomain is not None and not isinstance(subdomain, str):
        logger.error(f"{service_name}.jellyfin.subdomain must be text")
        return None
    if not isinstance(content, dict):
        logger.error(f"{service_name}.jellyfin.options must be a mapping")
        return None
    return JellyfinOptions(domain, subdomain, api_key, folder, bool(secure), content)
