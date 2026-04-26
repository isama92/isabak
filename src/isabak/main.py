from src.isabak.config import (
    app_name,
    load_config,
    merge_config,
)
from src.isabak.logs import get_logger
from src.isabak.service import services_backup
from src.isabak.borg import borg_transfer

logger = get_logger(__name__)


def main():
    logger.info(f"{app_name} started")

    config = load_config()

    if config is None:
        return

    config = merge_config(config)

    logger.debug("configuration loaded")

    if config.get("services"):
        services_backup(config)

    if config.get("borg"):
        borg_transfer(config.get("borg"))

    logger.info(f"{app_name} finished")


if __name__ == "__main__":
    main()
