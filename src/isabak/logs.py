import logging
from os import getenv


def get_logger(name: str):
    return logging.getLogger(name)


def set_basic_log_config() -> None:
    logging.basicConfig(
        level=get_log_level(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


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
