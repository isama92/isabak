from os import environ, makedirs, getuid, getgid, remove
from os.path import join as path_join, exists as path_exists, realpath as path_realpath
from shutil import move as shutil_move
from shutil import rmtree as shutil_rmtree
import subprocess
from io import SEEK_END
from re import compile as re_compile
from tempfile import TemporaryFile
from typing import BinaryIO
from urllib.parse import quote
from src.isabak.helpers import run_command
from src.isabak.logs import get_logger
from src.isabak.verify import postgres_dump_marker, verify_sql_gzip

logger = get_logger(__name__)

container_timeout = 3600
# set postgres.image in config.yaml to pin a tag and digest, e.g.
# prodrigestivill/postgres-backup-local:17@sha256:..., so a new upstream push is
# never run with the database passwords without you choosing it
default_image = "prodrigestivill/postgres-backup-local"
# enough for the reason a dump failed without flooding the email
output_first_lines = 10
output_last_lines = 40
output_head_bytes = 8 * 1024
output_tail_bytes = 32 * 1024
# terminal escape sequences and control characters, except tab and newline
control_characters = re_compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f]")
url_credentials = re_compile(r"(://[^:/@\s]+:)[^@\s]+(@)")


def postgres_backup(
    service_name: str, service_options: dict, db_options: dict, destination: str
) -> bool:
    logger.debug(f"postgres backup for service '{service_name}' started")

    db_name = service_options.get("db_name")
    db_container = service_options.get("container")
    db_network = service_options.get("network")
    db_username = service_options.get("username")
    db_password = service_options.get("password")
    image = db_options.get("image", default_image)

    if not check_options(
        service_name, db_name, db_container, db_network, db_username, db_password
    ):
        return False
    if not isinstance(image, str) or not image:
        logger.error("postgres.image must be an image name")
        return False

    destination = path_join(destination, "postgres", "")
    makedirs(destination, exist_ok=True)

    tmp_folder = path_join(destination, "tmp")
    makedirs(tmp_folder, exist_ok=True)
    logger.debug("tmp folder created")

    if not create_backup(
        db_name, db_container, db_network, db_username, db_password, tmp_folder, image
    ):
        return False

    backup_path = path_join(tmp_folder, "last", f"{db_name}-latest.sql.gz")
    backup_path = path_realpath(backup_path)

    if not path_exists(backup_path):
        logger.error(f"backup file {backup_path} does not exist")
        return False

    backup_path_dest = path_join(destination, f"db_{db_name}.sql.gz")

    shutil_move(backup_path, backup_path_dest)
    logger.debug("backup moved to backup folder")

    shutil_rmtree(tmp_folder)
    logger.debug("tmp folder removed")

    # exit code 0 does not prove the file is whole, read it back
    verify_error = verify_sql_gzip(backup_path_dest, postgres_dump_marker)
    if verify_error is not None:
        logger.error(f"{service_name} postgres dump {verify_error}")
        remove(backup_path_dest)
        return False

    logger.debug("postgres backup completed successfully")
    return True


def create_backup(
    db_name: str,
    db_container: str,
    db_network: str,
    db_username: str,
    db_password: str,
    tmp_folder: str,
    image: str = default_image,
) -> bool:
    # fmt: off
    docker_cmd = [
        "docker", "run", "--rm",
        "-v", f"{tmp_folder}:/backups",
        "-u", f"{getuid()}:{getgid()}",
        "--network", db_network,
        "-e", f"POSTGRES_HOST={db_container}",
        "-e", f"POSTGRES_DB={db_name}",
        "-e", f"POSTGRES_USER={db_username}",
        # no value: docker copies it from its own env, keeping it out of argv
        "-e", "POSTGRES_PASSWORD",
        image, "/backup.sh"
    ]
    # fmt: on

    # output goes to a file, not memory, so a runaway container cannot fill the
    # ram, and what it printed before a timeout is still there to log
    with TemporaryFile() as output:
        try:
            run_command(
                docker_cmd,
                timeout=container_timeout,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=True,
                env={**environ, "POSTGRES_PASSWORD": db_password},
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.exception(e, stack_info=True)
            # the container's own output says why: wrong password, unknown host,
            # pg_dump older than the server, ...
            log_output(output, db_password, logger.error)
            shutil_rmtree(tmp_folder)
            return False

        log_output(output, db_password, logger.debug)

    logger.debug("backup created")
    return True


def log_output(output: BinaryIO, db_password: str, log):
    size = output.seek(0, SEEK_END)
    if size == 0:
        return
    output.seek(0)
    if size <= output_head_bytes + output_tail_bytes:
        lines = clean_output(output.read(), db_password).splitlines()
        skipped = len(lines) - output_first_lines - output_last_lines
        if skipped > 0:
            marker = f"... {skipped} lines skipped"
            lines = lines[:output_first_lines] + [marker] + lines[-output_last_lines:]
    else:
        # only the start (first error) and the end (final error) are read
        head = clean_output(output.read(output_head_bytes), db_password).splitlines()
        output.seek(size - output_tail_bytes)
        tail = clean_output(output.read(), db_password).splitlines()[1:]
        marker = f"... {size - output_head_bytes - output_tail_bytes} bytes skipped"
        lines = head[:output_first_lines] + [marker] + tail[-output_last_lines:]
    # prefixed, so a line printed by the container cannot pass for an isabak log line
    log("\n".join(f"container: {line}" for line in lines))


def clean_output(data: bytes, db_password: str) -> str:
    text = control_characters.sub("", data.decode(errors="replace"))
    # pg_dump does not print the password, masked anyway in case a future image does
    if db_password:
        for secret in {db_password, quote(db_password, safe="")}:
            text = text.replace(secret, "***")
    return url_credentials.sub(r"\1***\2", text).strip()


def check_options(
    service_name: str,
    db_name: str | None,
    db_container: str | None,
    db_network: str | None,
    db_username: str | None,
    db_password: str | None,
) -> bool:
    if db_name is None:
        logger.error(f"{service_name}.postgres.db_name is required")
        return False
    if db_container is None:
        logger.error(f"{service_name}.postgres.container is required")
        return False
    if db_network is None:
        logger.error(f"{service_name}.postgres.network is required")
        return False
    if db_username is None:
        logger.error(f"{service_name}.postgres.username is required")
        return False
    if db_password is None:
        logger.error(f"{service_name}.postgres.password is required")
        return False
    return True
