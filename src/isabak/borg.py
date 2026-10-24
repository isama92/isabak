import subprocess
from src.isabak.helpers import replace_env_vars
from src.isabak.logs import get_logger

logger = get_logger(__name__)


def borg_transfer(options: dict, prune: bool = True) -> bool:
    logger.info("starting borg transfers")

    repository = options.get("repository")
    passphrase = options.get("passphrase")
    folders = options.get("folders")

    if not check_options(repository, passphrase, folders):
        return False

    if not prune:
        logger.warning("borg prune and compact skipped, a service backup failed")

    for entry in folders:
        entry_borg_env = borg_env(repository, passphrase, entry)
        try:
            borg_create(entry_borg_env, entry)
            if prune:
                borg_prune(entry_borg_env)
                borg_compact(entry_borg_env)
        except subprocess.CalledProcessError as e:
            logger.exception(e)
            log_std_outputs(e, True)
            return False
        except Exception as e:
            logger.exception(e)
            return False

    logger.info("borg transfers completed")
    return True


def borg_env(base_repository: str, passphrase: str, entry: dict) -> dict:
    return {
        "BORG_REPO": base_repository + entry.get("repository"),
        "BORG_PASSPHRASE": passphrase,
    }


def borg_create(env: dict, entry: dict):
    folder = entry.get("folder")
    folder = replace_env_vars(folder)

    compression = entry.get("compression")
    compression = compression if compression else "none"

    logger.debug(f"{env.get('BORG_REPO')} transfer started")

    # fmt: off
    borg_cmd = [
        "borg", "create",
        "--stats",
        "--compression", compression,
        "::{hostname}-{now}",
        folder,
    ]
    # fmt: on
    process = subprocess.run(
        borg_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        env=env,
    )
    log_std_outputs(process)
    logger.debug(f"{env.get('BORG_REPO')} transfer completed")


def borg_prune(env: dict):
    logger.debug(f"{env.get('BORG_REPO')} prune started")
    # fmt: off
    borg_cmd = [
        "borg", "prune",
        "--glob-archives", "{hostname}-*",
        "--keep-daily", "7",
        "--keep-weekly", "2",
        "--keep-monthly", "1"
    ]
    # fmt: on
    process = subprocess.run(
        borg_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        env=env,
    )
    log_std_outputs(process)
    logger.debug(f"{env.get('BORG_REPO')} prune completed")


def borg_compact(env: dict):
    logger.debug(f"{env.get('BORG_REPO')} compact started")
    borg_cmd = ["borg", "compact"]
    process = subprocess.run(
        borg_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        env=env,
    )
    log_std_outputs(process)
    logger.debug(f"{env.get('BORG_REPO')} compact completed")


def check_options(
    repository: str | None, passphrase: str | None, folders: list | None
) -> bool:
    if repository is None:
        logger.error("borg.repository is required")
        return False
    if passphrase is None:
        logger.error("borg.passphrase is required")
        return False
    if folders is None:
        logger.error("borg.folders is required")
        return False
    for entry in folders:
        if not isinstance(entry, dict):
            logger.error("borg.folders format is invalid")
            return False
        if not isinstance(entry.get("repository"), str):
            logger.error("borg.folders.repository is required")
            return False
        if not isinstance(entry.get("folder"), str):
            logger.error("borg.folders.folder is required")
            return False
    return True


def log_std_outputs(pipe, error=False):
    log = logger.debug if not error else logger.error
    if pipe.stdout:
        log(pipe.stdout.decode().strip())
    if pipe.stderr:
        log(pipe.stderr.decode().strip())
