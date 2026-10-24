from src.isabak.services.fs_backup import fs_backup
from src.isabak.services.mysql_backup import mysql_backup
from src.isabak.services.mariadb_backup import mariadb_backup
from src.isabak.services.postgres_backup import postgres_backup
from src.isabak.services.arr_backup import arr_backup
from src.isabak.services.jellyfin_backup import jellyfin_backup
from src.isabak.services.sqlite_backup import sqlite_backup
from src.isabak.logs import get_logger
from src.isabak.config import drop_last_good, get_base_destination, manifest_name
from src.isabak.report import RunReport, describe_error
from os import makedirs
from os.path import isabs, join as path_join
from time import monotonic

logger = get_logger(__name__)


def services_backup(config: dict, report: RunReport) -> bool:
    logger.info("starting services backup")

    base_destination = config.get("destination")
    services = config.get("services")

    if not check_options(base_destination, services):
        report.add("services", "config", False, 0, "invalid services or destination")
        return False

    base_destination = get_base_destination(
        base_destination,
        config.get("allow_destination_on_root_fs") is True,
        config.get("min_free_gb"),
    )

    if base_destination is None:
        report.add("services", "destination", False, 0, "unavailable")
        return False

    failed_services = []

    for service in services:
        service_name = service.get("name")
        logger.info(f"{service_name} starting")

        destination = str(path_join(base_destination, service_name, ""))

        try:
            makedirs(destination, exist_ok=True)
        except Exception as e:
            logger.exception(e)
            report.add(service_name, "folder", False, 0, describe_error(e))
            failed_services.append(service_name)
            continue

        results = []

        if service.get("fs") is not None:
            results.append(
                run_step(
                    report,
                    service_name,
                    "fs",
                    fs_backup,
                    service_name,
                    service.get("fs"),
                    destination,
                )
            )

        if service.get("mysql") is not None:
            results.append(
                run_step(
                    report,
                    service_name,
                    "mysql",
                    mysql_backup,
                    service_name,
                    service.get("mysql"),
                    config.get("mysql", {}),
                    destination,
                )
            )

        if service.get("mariadb") is not None:
            results.append(
                run_step(
                    report,
                    service_name,
                    "mariadb",
                    mariadb_backup,
                    service_name,
                    service.get("mariadb"),
                    config.get("mariadb", {}),
                    destination,
                )
            )

        if service.get("postgres") is not None:
            results.append(
                run_step(
                    report,
                    service_name,
                    "postgres",
                    postgres_backup,
                    service_name,
                    service.get("postgres"),
                    config.get("postgres", {}),
                    destination,
                )
            )

        if service.get("arr") is not None:
            results.append(
                run_step(
                    report,
                    service_name,
                    "arr",
                    arr_backup,
                    service_name,
                    service.get("arr"),
                    config.get("domain"),
                    destination,
                )
            )

        if service.get("jellyfin") is not None:
            results.append(
                run_step(
                    report,
                    service_name,
                    "jellyfin",
                    jellyfin_backup,
                    service_name,
                    service.get("jellyfin"),
                    config.get("domain"),
                    destination,
                )
            )

        if service.get("sqlite") is not None:
            results.append(
                run_step(
                    report,
                    service_name,
                    "sqlite",
                    sqlite_backup,
                    service_name,
                    service.get("sqlite"),
                    destination,
                )
            )

        if not results:
            logger.error(f"{service_name} has no backup type configured")
            report.add(service_name, "config", False, 0, "no backup type configured")
            failed_services.append(service_name)
        elif not all(results):
            failed_services.append(service_name)

        logger.info(f"{service_name} finished")

    # goes into the borg archive with the backups it describes
    write_manifest(base_destination, report)

    # this tree is complete, the previous one is not needed any more
    if not failed_services and report.ok():
        drop_last_good(base_destination)

    if failed_services:
        logger.error(f"services backup failed for: {', '.join(failed_services)}")
        return False

    logger.info("services backup completed")
    return True


def write_manifest(base_destination: str, report: RunReport):
    try:
        report.write_manifest(path_join(base_destination, manifest_name))
    except OSError as e:
        logger.exception(e)
        report.add("services", "manifest", False, 0, describe_error(e))


def run_step(
    report: RunReport, service_name: str, backup_type: str, backup_fn, *args
) -> bool:
    # an exception escaping one backup must not abort the other services or borg
    started = monotonic()
    try:
        ok = backup_fn(*args)
    except Exception as e:
        logger.exception(e)
        report.add(
            service_name, backup_type, False, monotonic() - started, describe_error(e)
        )
        return False
    report.add(service_name, backup_type, ok, monotonic() - started)
    return ok


def check_options(destination, services) -> bool:
    if not isinstance(destination, str):
        logger.error("destination is required")
        return False
    if not isabs(destination):
        # a relative path lands wherever the job happens to run, e.g. an empty
        # DESTINATION or one built from an unset variable
        logger.error(f"destination '{destination}' must be an absolute path")
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
