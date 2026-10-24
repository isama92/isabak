import sys
from src.isabak.config import (
    app_name,
    check_unknown_keys,
    load_config,
    merge_config,
)
from src.isabak.logs import get_logger
from src.isabak.report import RunReport, describe_error
from src.isabak.service import services_backup
from src.isabak.borg import borg_transfer

logger = get_logger(__name__)


def main():
    logger.info(f"{app_name} started")

    report = RunReport()

    try:
        run(report)
    except Exception as e:
        logger.exception(e)
        report.add(app_name, "unexpected error", False, 0, describe_error(e))

    report.log_summary(app_name)

    if not report.ok():
        sys.exit(1)


def run(report: RunReport):
    config = load_config()

    if config is None:
        report.add("config", "config.yaml", False, 0, "missing or malformed")
        return

    config = merge_config(config)

    logger.debug("configuration loaded")

    # unknown keys fail the run and block prune, but the backups still run
    config_ok = check_unknown_keys(config)
    if not config_ok:
        report.add("config", "config.yaml", False, 0, "unknown options, see log")

    services_ok = True

    if "services" in config:
        services_ok = services_backup(config, report)

    borg = config.get("borg")
    if borg is False:
        logger.info("borg disabled in config.yaml, no off-site copy made")
    elif not isinstance(borg, dict):
        logger.error(
            "borg is missing or malformed, set borg: false to skip it on purpose"
        )
        report.add("borg", "config", False, 0, "missing or malformed")
    else:
        borg_transfer(borg, report, prune=services_ok and config_ok)


if __name__ == "__main__":
    main()
