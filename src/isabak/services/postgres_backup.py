from dataclasses import dataclass
from os import environ, makedirs, getuid, getgid, remove
from os.path import join as path_join, exists as path_exists, realpath as path_realpath
from shutil import move as shutil_move
from shutil import rmtree as shutil_rmtree
import subprocess
from io import SEEK_END
from re import compile as re_compile
from tempfile import TemporaryFile
from typing import IO
from urllib.parse import quote
from src.isabak.helpers import as_secret, as_text, run_command, strip_control_characters
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
url_credentials = re_compile(r"(://[^:/@\s]+:)[^@\s]+(@)")


def postgres_backup(
    service_name: str, service_options: dict, db_options: dict, destination: str
) -> bool:
    logger.debug(f"postgres backup for service '{service_name}' started")

    options = check_options(service_name, service_options, db_options)
    if options is None:
        return False
    db_name = options.db_name

    destination = path_join(destination, "postgres", "")
    makedirs(destination, exist_ok=True)

    tmp_folder = path_join(destination, "tmp")
    makedirs(tmp_folder, exist_ok=True)
    logger.debug("tmp folder created")

    if not create_backup(
        db_name,
        options.container,
        options.network,
        options.username,
        options.password,
        tmp_folder,
        options.image,
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


def log_output(output: IO[bytes], db_password: str, log):
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
    text = strip_control_characters(data.decode(errors="replace"))
    # pg_dump does not print the password, masked anyway in case a future image does
    if db_password:
        for secret in {db_password, quote(db_password, safe="")}:
            text = text.replace(secret, "***")
    return url_credentials.sub(r"\1***\2", text).strip()


@dataclass(frozen=True)
class PostgresOptions:
    db_name: str
    container: str
    network: str
    username: str
    password: str
    image: str


def check_options(
    service_name: str, service_options: dict, db_options: dict
) -> PostgresOptions | None:
    values: dict[str, str] = {}
    for field in ("db_name", "container", "network", "username", "password"):
        if field == "password":
            name = f"{service_name}.postgres.password"
            value = as_secret(service_options.get(field), name)
        else:
            value = as_text(service_options.get(field))
        if value is None:
            logger.error(f"{service_name}.postgres.{field} is required")
            return None
        values[field] = value
    image = db_options.get("image", default_image)
    if not isinstance(image, str) or not image:
        logger.error("postgres.image must be an image name")
        return None
    return PostgresOptions(**values, image=image)
