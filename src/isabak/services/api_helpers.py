from os import listdir
from os.path import isfile as path_isfile, join as path_join
from shutil import copy2 as shutil_copy2
from src.isabak.logs import get_logger
from src.isabak.verify import verify_zip

# shared by the *arr and jellyfin backups, which both ask the app for a backup
# over its api and then copy the new file from the app's backup folder

logger = get_logger(__name__)


def build_host_url(domain: str, subdomain: str | None, secure: bool) -> str:
    scheme = "https" if secure else "http"
    host = f"{subdomain}.{domain}" if subdomain else domain
    return f"{scheme}://{host}"


def build_json_headers(auth: dict[str, str]) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **auth,
    }


def list_backup_files(folder: str) -> set[str] | None:
    try:
        return {
            file for file in listdir(folder) if path_isfile(path_join(folder, file))
        }
    except OSError as e:
        logger.error(f"backup folder '{folder}' could not be read: {e}")
        return None


def copy_backup(kind: str, folder: str, files: list[str], destination: str) -> bool:
    copied = 0
    try:
        for file in files:
            src = path_join(folder, file)
            if not path_isfile(src):
                logger.warning(f"{kind} backup {file} disappeared before the copy")
                continue
            dest = path_join(destination, file)
            shutil_copy2(src, dest)
            logger.debug(f"copied {src} to {dest}")
            copied += 1
            # a zip copied while the app was still writing it fails here
            verify_error = verify_zip(dest) if file.endswith(".zip") else None
            if verify_error is not None:
                logger.error(f"{kind} backup {file} {verify_error}")
                return False
    except OSError as e:
        logger.exception(e, stack_info=True)
        return False

    if copied == 0:
        logger.error("no backup files were copied")
        return False

    return True
