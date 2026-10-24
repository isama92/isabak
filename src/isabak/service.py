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


def services_backup(config: dict) -> bool:
    logger.info("starting services backup")

    base_destination = config.get("destination")
    services = config.get("services")

    if not check_options(base_destination, services):
        return False

    base_destination = get_base_destination(base_destination)

    if base_destination is None:
        return False

    failed_services = []

    for service in services:
        service_name = service.get("name")
        logger.info(f"{service_name} starting")

        destination = str(path_join(base_destination, service_name, ""))

        makedirs(destination, exist_ok=True)

        results = []

        if service.get("fs") is not None:
            results.append(fs_backup(service_name, service.get("fs"), destination))

        if service.get("mysql") is not None:
            results.append(
                mysql_backup(
                    service_name,
                    service.get("mysql"),
                    config.get("mysql", {}),
                    destination,
                )
            )

        if service.get("mariadb") is not None:
            results.append(
                mariadb_backup(
                    service_name,
                    service.get("mariadb"),
                    config.get("mariadb", {}),
                    destination,
                )
            )

        if service.get("postgres") is not None:
            results.append(
                postgres_backup(service_name, service.get("postgres"), destination)
            )

        if service.get("arr") is not None:
            results.append(
                arr_backup(
                    service_name,
                    service.get("arr"),
                    config.get("domain"),
                    destination,
                )
            )

        if service.get("jellyfin") is not None:
            results.append(
                jellyfin_backup(
                    service_name,
                    service.get("jellyfin"),
                    config.get("domain"),
                    destination,
                )
            )

        if not results:
            logger.error(f"{service_name} has no backup type configured")
            failed_services.append(service_name)
        elif not all(results):
            failed_services.append(service_name)

        logger.info(f"{service_name} finished")

    if failed_services:
        logger.error(f"services backup failed for: {', '.join(failed_services)}")
        return False

    logger.info("services backup completed")
    return True


def check_options(destination, services) -> bool:
    if not isinstance(destination, str):
        logger.error("destination is required")
        return False
    if not isinstance(services, list):
        logger.error("services is malformed")
        return False
    if not services:
        logger.error("services is empty")
        return False
    for service in services:
        if not isinstance(service, dict):
            logger.error(f"services is malformed")
            return False
        if not isinstance(service.get("name"), str):
            logger.error("services name is required")
            return False
    return True
