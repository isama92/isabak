import sqlite3
from contextlib import closing
from os import (
    O_NOFOLLOW,
    O_NONBLOCK,
    O_RDONLY,
    close,
    fchown,
    fstat,
    lstat,
    makedirs,
    open as os_open,
    remove,
    replace,
    stat_result,
)
from os.path import (
    abspath,
    basename,
    exists as path_exists,
    join as path_join,
)
from pathlib import Path
from stat import S_ISLNK, S_ISREG
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
    # the folder is writable by the app, so a symlink there could point root at
    # another service's database
    try:
        info = lstat(source)
    except FileNotFoundError:
        return "does not exist, is its disk mounted?"
    if S_ISLNK(info.st_mode):
        return "is a symlink, refusing to follow it"
    if not S_ISREG(info.st_mode):
        return "is not a regular file"
    if info.st_size == 0:
        return "is empty"
    with open(source, "rb") as f:
        if f.read(len(sqlite_header)) != sqlite_header:
            return "is not a SQLite database"
    return None


def backup_database(source: str, target: str):
    # the backup api takes a consistent snapshot while the app keeps writing,
    # unlike a file copy of the database and its -wal at different moments
    identity = lstat(source)
    sidecars_before = {s for s in sidecar_suffixes if path_exists(source + s)}
    tmp = target + ".tmp"
    if path_exists(tmp):
        remove(tmp)

    try:
        # read-only only: since SQLite 3.22 this also works for a WAL database
        # without a -shm, so a failure is a real error and never retried writable
        copy_database(Path(abspath(source)).as_uri() + "?mode=ro", tmp)
        if not same_file(identity, source):
            raise ValueError(f"{source} was replaced during the backup")
        replace(tmp, target)
    finally:
        if path_exists(tmp):
            remove(tmp)
        fix_sidecar_owner(source, identity, sidecars_before)


def same_file(identity: stat_result, path: str) -> bool:
    try:
        current = lstat(path)
    except FileNotFoundError:
        return False
    return (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino)


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


def fix_sidecar_owner(source: str, owner: stat_result, sidecars_before: set[str]):
    # a -wal or -shm created by root would lock the app out of its own database.
    # the folder is writable by the app, so only touch a regular file root just
    # created next to the database: no symlink, no hardlink to another file,
    # same filesystem, checked and changed through one descriptor (no race)
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
            if (
                S_ISREG(info.st_mode)
                and info.st_uid == 0
                and info.st_nlink == 1
                and info.st_dev == owner.st_dev
            ):
                fchown(fd, owner.st_uid, owner.st_gid)
            else:
                logger.warning(f"{source}{suffix} left as is, unexpected file")
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
