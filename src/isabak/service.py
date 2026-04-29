from src.isabak.services.fs_backup import fs_backup
from src.isabak.services.mysql_backup import mysql_backup
from src.isabak.services.mariadb_backup import mariadb_backup
from src.isabak.services.postgres_backup import postgres_backup
from src.isabak.services.arr_backup import arr_backup
from src.isabak.services.jellyfin_backup import jellyfin_backup
from src.isabak.logs import get_logger
from src.isabak.config import get_base_destination
from os import makedirs
from os.path import join as path_join

logger = get_logger(__name__)


def services_backup(config: dict):
    logger.info("starting services backup")

    base_destination = config.get("destination")
    services = config.get("services")

    if not check_options(base_destination, services):
        return

    base_destination = get_base_destination(base_destination)

    if base_destination is None:
        return

    for service in services:
        service_name = service.get("name")
        logger.info(f"{service_name} starting")

        destination = str(path_join(base_destination, service_name, ""))

        makedirs(destination, exist_ok=True)

        if service.get("fs") is not None:
            fs_backup(service_name, service.get("fs"), destination)

        if service.get("mysql") is not None:
            mysql_backup(
                service_name,
                service.get("mysql"),
                config.get("mysql", {}),
                destination,
            )

        if service.get("mariadb") is not None:
            mariadb_backup(
                service_name,
                service.get("mariadb"),
                config.get("mariadb", {}),
                destination,
            )

        if service.get("postgres") is not None:
            postgres_backup(service_name, service.get("postgres"), destination)

        if service.get("arr") is not None:
            arr_backup(
                service_name,
                service.get("arr"),
                config.get("domain"),
                destination,
            )

        if service.get("jellyfin") is not None:
            jellyfin_backup(
                service_name,
                service.get("jellyfin"),
                config.get("domain"),
                destination,
            )

        logger.info(f"{service_name} finished")

    logger.info("services backup completed")


def check_options(destination, services) -> bool:
    if not isinstance(destination, str):
        logger.error("destination is required")
        return False
    if not isinstance(services, list):
        logger.error("services is malformed")
        return False
    for service in services:
        if not isinstance(service, dict):
            logger.error(f"services is malformed")
            return False
        if not isinstance(service.get("name"), str):
            logger.error("services name is required")
            return False
    return True
