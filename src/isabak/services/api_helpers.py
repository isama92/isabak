from errno import ELOOP
from os import (
    O_NOFOLLOW,
    O_NONBLOCK,
    O_RDONLY,
    chmod,
    fstat,
    listdir,
    open as os_open,
    utime,
)
from os.path import isfile as path_isfile, join as path_join
from re import compile as re_compile
from shutil import copyfileobj
from stat import S_IMODE, S_ISREG
from src.isabak.helpers import strip_control_characters
from src.isabak.logs import get_logger
from src.isabak.verify import verify_zip

# shared by the *arr and jellyfin backups, which both ask the app for a backup
# over its api and then copy the new file from the app's backup folder

logger = get_logger(__name__)

# enough for the reason an app gave (e.g. "database disk image is malformed")
# without pasting a whole error page into the cron mail
reason_max_chars = 300
html_tags = re_compile(r"<[^>]+>")
reason_fields = ("message", "detail", "title", "errorMessage", "description")


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


def describe_response(response, headers: dict[str, str]) -> str:
    # "code 500: <the reason the app gave>", or just "code 500" without a body
    reason = response_reason(response, api_secrets(headers))
    code = f"code {response.status_code}"
    return f"{code}: {reason}" if reason else code


def response_reason(response, secrets: list[str]) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    # *arr answers {"message": ...} or a list of validation errors
    if isinstance(body, list) and body and isinstance(body[0], dict):
        body = body[0]
    if isinstance(body, dict):
        text = next((str(body[f]) for f in reason_fields if body.get(f)), "")
    else:
        text = response.text or ""
    # masked on the raw text, before anything can cut a key in half
    for secret in secrets:
        text = text.replace(secret, "***")
    text = html_tags.sub(" ", text)
    # one line, printable characters only (no escapes, no bidi controls that
    # reorder what the mail shows): it ends up in a log line and an email
    text = " ".join(strip_control_characters(text).split())
    text = "".join(char for char in text if char.isprintable())
    if len(text) > reason_max_chars:
        text = text[:reason_max_chars] + "..."
    return text


def api_secrets(headers: dict[str, str]) -> list[str]:
    # the api key, in case an app echoes the request back in its error: every
    # header value except the fixed json ones, plus the token inside
    # "MediaBrowser Token=...", longest first so a whole value goes before a part
    values = [v for k, v in headers.items() if k not in ("Accept", "Content-Type")]
    secrets = values + [v.partition("Token=")[2] for v in values]
    return sorted({s for s in secrets if s}, key=len, reverse=True)


def list_backup_files(folder: str) -> set[str] | None:
    try:
        return {
            file for file in listdir(folder) if path_isfile(path_join(folder, file))
        }
    except OSError as e:
        logger.error(f"backup folder '{folder}' could not be read: {e}")
        return None


def copy_backup(kind: str, folder: str, files: list[str], destination: str) -> bool:
    # the step only succeeds with a new backup zip that was copied and checked:
    # the caller deletes the app's older backups right after this
    zips = 0
    try:
        for file in files:
            copy_error = copy_file(
                path_join(folder, file), path_join(destination, file)
            )
            if copy_error == "missing":
                logger.warning(f"{kind} backup {file} disappeared before the copy")
                continue
            if copy_error is not None:
                logger.error(f"{kind} backup {file} {copy_error}")
                return False
            logger.debug(f"copied {file} to {destination}")
            if not file.endswith(".zip"):
                continue
            # a zip copied while the app was still writing it fails here
            verify_error = verify_zip(path_join(destination, file))
            if verify_error is not None:
                logger.error(f"{kind} backup {file} {verify_error}")
                return False
            zips += 1
    except OSError as e:
        logger.exception(f"{kind} backup copy failed: {e}", stack_info=True)
        return False

    if zips == 0:
        logger.error(f"no {kind} backup zip was copied")
        return False

    return True


def copy_file(src: str, dest: str) -> str | None:
    # the folder is writable by the app container and isabak runs as root, so a
    # symlink there could make root copy any file on the host into the backup.
    # opened once without following links, checked and read through that one
    # descriptor, so a link swapped in after a check is still refused
    try:
        fd = os_open(src, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
    except FileNotFoundError:
        return "missing"
    except OSError as e:
        if e.errno == ELOOP:
            return "is a symlink, refusing to follow it"
        raise
    with open(fd, "rb") as source:
        info = fstat(source.fileno())
        if not S_ISREG(info.st_mode):
            return "is not a regular file"
        with open(dest, "wb") as target:
            copyfileobj(source, target)
    # what copy2 kept: permission bits and times, taken from the descriptor
    chmod(dest, S_IMODE(info.st_mode))
    utime(dest, ns=(info.st_atime_ns, info.st_mtime_ns))
    return None
