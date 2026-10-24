import logging
from logging.handlers import TimedRotatingFileHandler
from os import (
    O_APPEND,
    O_CREAT,
    O_NOFOLLOW,
    O_WRONLY,
    close,
    fchmod,
    fstat,
    geteuid,
    getenv,
    makedirs,
    open as os_open,
    stat as os_stat,
)
from os.path import abspath, dirname, exists as path_exists, join as path_join, realpath
from stat import S_ISREG
from dotenv import load_dotenv

env_file_path = ".env"
# the folder holding main.py, src/isabak/logs.py is two levels below it
project_root = dirname(dirname(dirname(abspath(__file__))))
log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
# one file per day, a month of history
log_file_days = 30


def project_file(name: str) -> str:
    # the current folder wins, as before; otherwise the project folder, so a
    # cron line without "cd" still finds config.yaml and .env
    if path_exists(name):
        return name
    return path_join(project_root, name)


def get_logger(name: str):
    return logging.getLogger(name)


def get_log_level() -> int:
    log_level = getenv("LOG_LEVEL", "error")
    if log_level == "debug":
        return logging.DEBUG
    elif log_level == "info":
        return logging.INFO
    elif log_level == "warning":
        return logging.WARNING
    elif log_level == "error":
        return logging.ERROR
    else:
        raise ValueError("Invalid log level")


class PrivateLogFileHandler(TimedRotatingFileHandler):
    # tracebacks and paths end up in the log, so every file, also each new one
    # after a rotation, is readable by root only. isabak runs as root, so a
    # symlink or hardlink in place of the log must never redirect the write or
    # the chmod to another file: no following, and the mode set on the fd
    def _open(self):
        flags = O_WRONLY | O_CREAT | O_APPEND | O_NOFOLLOW
        fd = os_open(self.baseFilename, flags, 0o600)
        info = fstat(fd)
        if not S_ISREG(info.st_mode) or info.st_nlink != 1:
            close(fd)
            raise OSError(f"{self.baseFilename} is not a plain log file")
        fchmod(fd, 0o600)
        return open(fd, self.mode, encoding=self.encoding, errors=self.errors)


def check_log_folder(folder: str):
    # every folder from / down to the log must belong to root (or us) and not be
    # writable by others, otherwise another user could swap one of them for a
    # link after this check. existing folders are never chmodded, /var/log stays
    current = folder
    while True:
        info = os_stat(current)
        if info.st_uid not in (0, geteuid()) or info.st_mode & 0o022:
            raise ValueError(
                f"LOG_FILE folder {current} is owned or writable by another user, "
                "use a root-only folder such as /var/log/isabak"
            )
        parent = dirname(current)
        if parent == current:
            return
        current = parent


def add_log_file(path: str, console_level: int):
    # the console (cron mail) stays at LOG_LEVEL, the file always keeps the info
    # history: stage boundaries, the summary, warnings such as vanished files
    folder = dirname(realpath(path))
    if not path_exists(folder):
        # mode applies to the new folder, the umask can only remove bits
        makedirs(folder, mode=0o700)
    check_log_folder(folder)
    handler = PrivateLogFileHandler(
        path, when="midnight", backupCount=log_file_days, encoding="utf-8"
    )
    handler.setLevel(min(logging.INFO, console_level))
    handler.setFormatter(logging.Formatter(log_format))
    logging.getLogger().addHandler(handler)


# Bootstrap on first import so module-level get_logger() calls elsewhere
# resolve against a configured root logger.
if path_exists(project_file(env_file_path)):
    load_dotenv(project_file(env_file_path))

console_level = get_log_level()
logging.basicConfig(level=console_level, format=log_format)

log_file = getenv("LOG_FILE")
if log_file:
    logging.getLogger().handlers[0].setLevel(console_level)
    logging.getLogger().setLevel(min(logging.INFO, console_level))
    add_log_file(log_file, console_level)
