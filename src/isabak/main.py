import sys
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
        sys.exit(1)

    config = merge_config(config)

    logger.debug("configuration loaded")

    services_ok = True
    borg_ok = True

    if "services" in config:
        services_ok = services_backup(config)

    if config.get("borg"):
        borg_ok = borg_transfer(config.get("borg"), prune=services_ok)

    if not (services_ok and borg_ok):
        logger.error(f"{app_name} finished with errors")
        sys.exit(1)

    logger.info(f"{app_name} finished")


if __name__ == "__main__":
    main()
