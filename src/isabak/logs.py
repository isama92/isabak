import logging
from os import getenv
from os.path import exists as path_exists
from dotenv import load_dotenv

env_file_path = ".env"


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


# Bootstrap on first import so module-level get_logger() calls elsewhere
# resolve against a configured root logger.
if path_exists(env_file_path):
    load_dotenv(env_file_path)

logging.basicConfig(
    level=get_log_level(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
