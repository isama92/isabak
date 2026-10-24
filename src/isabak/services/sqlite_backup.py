import sqlite3
from contextlib import closing
from os import (
    O_NOFOLLOW,
    O_NONBLOCK,
    O_RDONLY,
    close,
    fchown,
    fstat,
    makedirs,
    open as os_open,
    remove,
    replace,
    stat,
)
from stat import S_ISREG
from os.path import (
    abspath,
    basename,
    exists as path_exists,
    getsize,
    isfile as path_isfile,
    join as path_join,
)
from pathlib import Path
from src.isabak.helpers import replace_env_vars
from src.isabak.logs import get_logger

logger = get_logger(__name__)

sqlite_header = b"SQLite format 3\x00"
sidecar_suffixes = ("-wal", "-shm")


def sqlite_backup(service_name: str, service_options: dict, destination: str) -> bool:
    logger.debug(f"sqlite backup for service '{service_name}' started")

    files = service_options.get("files")

    if not check_options(service_name, files):
        return False

    destination = path_join(destination, "sqlite", "")
    makedirs(destination, exist_ok=True)

    ok = True

    # one bad database must not stop the others of the same service
    for file in files:
        try:
            source = replace_env_vars(file)
        except KeyError as e:
            logger.error(f"{e}, {service_name}.sqlite.files")
            ok = False
            continue

        source_error = check_database(source)
        if source_error is not None:
            logger.error(f"{service_name}.sqlite.files '{source}' {source_error}")
            ok = False
            continue

        try:
            backup_database(source, path_join(destination, basename(source)))
        except Exception as e:
            logger.exception(e, stack_info=True)
            ok = False
            continue

        logger.debug(f"copied {source} to {destination}")

    if ok:
        logger.debug("sqlite backup completed successfully")
    return ok


def check_database(source: str) -> str | None:
    if not path_isfile(source):
        return "does not exist, is its disk mounted?"
    if getsize(source) == 0:
        return "is empty"
    with open(source, "rb") as f:
        if f.read(len(sqlite_header)) != sqlite_header:
            return "is not a SQLite database"
    return None


def backup_database(source: str, target: str):
    # the backup api takes a consistent snapshot while the app keeps writing,
    # unlike a file copy of the database and its -wal at different moments
    sidecars_before = {s for s in sidecar_suffixes if path_exists(source + s)}
    tmp = target + ".tmp"
    if path_exists(tmp):
        remove(tmp)

    try:
        try:
            copy_database(Path(abspath(source)).as_uri() + "?mode=ro", tmp)
        except sqlite3.OperationalError as e:
            # read-only opens of a WAL database can fail without a -shm file,
            # a normal connection is safe because nothing is written through it
            logger.debug(f"read-only open of {source} failed ({e}), retrying")
            if path_exists(tmp):
                remove(tmp)
            copy_database(Path(abspath(source)).as_uri(), tmp)
        replace(tmp, target)
    finally:
        if path_exists(tmp):
            remove(tmp)
        fix_sidecar_owner(source, sidecars_before)


def copy_database(source_uri: str, tmp: str):
    with closing(sqlite3.connect(source_uri, uri=True, timeout=30)) as src:
        with closing(sqlite3.connect(tmp)) as dst:
            src.backup(dst)
            # one self-contained file, no -wal or -shm next to the copy
            dst.execute("PRAGMA journal_mode=DELETE")
            # capped, a badly damaged file would otherwise list every page
            problems = [row[0] for row in dst.execute("PRAGMA integrity_check(5)")]
    if problems != ["ok"]:
        details = "; ".join(" ".join(problem.split()) for problem in problems)
        if len(details) > 300:
            details = details[:300] + "..."
        raise ValueError(f"integrity check of the copy failed: {details}")


def fix_sidecar_owner(source: str, sidecars_before: set[str]):
    # a -wal or -shm created by root would lock the app out of its own database.
    # the folder is writable by the app, so never follow a symlink planted there
    # and only touch regular files root created, through one descriptor (no race)
    owner = stat(source)
    for suffix in sidecar_suffixes:
        if suffix in sidecars_before:
            continue
        try:
            fd = os_open(source + suffix, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        except FileNotFoundError:
            continue
        except OSError as e:
            logger.warning(f"{source}{suffix} left as is, not a regular file ({e})")
            continue
        try:
            info = fstat(fd)
            if S_ISREG(info.st_mode) and info.st_uid == 0:
                fchown(fd, owner.st_uid, owner.st_gid)
        finally:
            close(fd)


def check_options(service_name: str, files: list | None) -> bool:
    if not isinstance(files, list) or not files:
        logger.error(f"{service_name}.sqlite.files is required")
        return False
    if not all(isinstance(file, str) for file in files):
        logger.error(f"{service_name}.sqlite.files must be a list of paths")
        return False
    names = [basename(file) for file in files]
    if len(names) != len(set(names)):
        logger.error(f"{service_name}.sqlite.files must have unique file names")
        return False
    return True
