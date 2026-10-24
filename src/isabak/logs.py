import logging
from logging.handlers import TimedRotatingFileHandler
from os import O_APPEND, O_CREAT, O_WRONLY, chmod, getenv, makedirs, open as os_open
from os.path import dirname, exists as path_exists
from dotenv import load_dotenv

env_file_path = ".env"
log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
# one file per day, a month of history
log_file_days = 30


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
    # after a rotation, is readable by root only
    def _open(self):
        fd = os_open(self.baseFilename, O_WRONLY | O_CREAT | O_APPEND, 0o600)
        chmod(self.baseFilename, 0o600)
        return open(fd, self.mode, encoding=self.encoding, errors=self.errors)


def add_log_file(path: str, console_level: int):
    # the console (cron mail) stays at LOG_LEVEL, the file always keeps the info
    # history: stage boundaries, the summary, warnings such as vanished files
    folder = dirname(path)
    # only a folder isabak creates is made root only, never an existing shared
    # one such as /var/log
    if folder and not path_exists(folder):
        makedirs(folder, mode=0o700)
        chmod(folder, 0o700)
    handler = PrivateLogFileHandler(
        path, when="midnight", backupCount=log_file_days, encoding="utf-8"
    )
    handler.setLevel(min(logging.INFO, console_level))
    handler.setFormatter(logging.Formatter(log_format))
    logging.getLogger().addHandler(handler)


# Bootstrap on first import so module-level get_logger() calls elsewhere
# resolve against a configured root logger.
if path_exists(env_file_path):
    load_dotenv(env_file_path)

console_level = get_log_level()
logging.basicConfig(level=console_level, format=log_format)

log_file = getenv("LOG_FILE")
if log_file:
    logging.getLogger().handlers[0].setLevel(console_level)
    logging.getLogger().setLevel(min(logging.INFO, console_level))
    add_log_file(log_file, console_level)
