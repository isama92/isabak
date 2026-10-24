from dataclasses import dataclass
from os.path import join as path_join
from os import makedirs, remove
import subprocess
from time import monotonic
from src.isabak.helpers import as_secret, as_text, run_command, stop_process
from src.isabak.logs import get_logger
from src.isabak.verify import mysql_dump_marker, verify_sql_gzip

logger = get_logger(__name__)

CNF_WRITE_CMD = "umask 077 && cat > /backup.cnf"

exec_timeout = 120
dump_timeout = 3600


@dataclass(frozen=True)
class DumpTool:
    # kind names the config block, the output folder and the dump file
    kind: str
    binary: str
    extra_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class DumpOptions:
    db_name: str
    container: str
    username: str
    password: str
    routines: bool


def dump_backup(
    tool: DumpTool,
    service_name: str,
    service_options: dict,
    db_options: dict,
    destination: str,
) -> bool:
    logger.debug(f"{tool.kind} backup for service '{service_name}' started")

    options = check_options(tool, service_name, service_options, db_options)
    if options is None:
        return False

    destination = path_join(destination, tool.kind, "")
    makedirs(destination, exist_ok=True)

    try:
        create_credentials(options.container, options.username, options.password)
        try:
            create_backup(tool, options, destination)
        finally:
            delete_credentials(options.container)
    except Exception as e:
        logger.exception(e, stack_info=True)
        return False

    logger.debug(f"{tool.kind} backup completed successfully")
    return True


def create_credentials(db_container: str, db_username: str, db_password: str):
    # credentials go over stdin, never argv, so they cannot leak via ps or tracebacks
    password = db_password.replace("\\", "\\\\")
    cnf = f'[client]\nuser="{db_username}"\npassword="{password}"\nhost=localhost\n'
    run_command(
        ["docker", "exec", "-i", db_container, "sh", "-c", CNF_WRITE_CMD],
        timeout=exec_timeout,
        input=cnf.encode(),
        check=True,
    )


def create_backup(tool: DumpTool, options: DumpOptions, destination: str):
    dump_cmd = [
        "docker",
        "exec",
        options.container,
        tool.binary,
        "--defaults-extra-file=/backup.cnf",
        "--single-transaction",
        *tool.extra_flags,
    ]
    if options.routines:
        # stored procedures, functions and events, off by default because they
        # need the SHOW_ROUTINE and EVENT privileges
        dump_cmd += ["--routines", "--events"]
    dump_cmd.append(options.db_name)

    out_path = path_join(destination, f"{tool.kind}.sql.gz")
    with open(out_path, "wb") as f:
        dump_proc = subprocess.Popen(dump_cmd, stdout=subprocess.PIPE)
        gzip = subprocess.Popen(["gzip"], stdin=dump_proc.stdout, stdout=f)
        # gzip holds its own copy of the pipe, so the dump gets SIGPIPE if gzip dies
        if dump_proc.stdout is not None:
            dump_proc.stdout.close()
        deadline = monotonic() + dump_timeout
        try:
            dump_rc = dump_proc.wait(timeout=dump_timeout)
            gzip_rc = gzip.wait(timeout=max(1, deadline - monotonic()))
        except subprocess.TimeoutExpired:
            # stops the docker exec client, the dump inside the container may
            # still run until it finishes on its own
            stop_process(dump_proc)
            stop_process(gzip)
            dump_rc = None

    if dump_rc is None:
        remove(out_path)
        raise subprocess.TimeoutExpired(dump_cmd, dump_timeout)

    if dump_rc != 0:
        remove(out_path)
        raise subprocess.CalledProcessError(dump_rc, dump_cmd)
    if gzip_rc != 0:
        remove(out_path)
        raise subprocess.CalledProcessError(gzip_rc, ["gzip"])

    # exit code 0 does not prove the file is whole, read it back
    verify_error = verify_sql_gzip(out_path, mysql_dump_marker)
    if verify_error is not None:
        remove(out_path)
        raise ValueError(f"{tool.kind}.sql.gz {verify_error}")


def check_options(
    tool: DumpTool, service_name: str, service_options: dict, db_options: dict
) -> DumpOptions | None:
    kind = tool.kind
    db_name = as_text(service_options.get("db_name"))
    db_container = as_text(db_options.get("container"))
    db_username = as_text(db_options.get("username"))
    db_password = as_secret(db_options.get("password"), f"{kind}.password")
    routines = db_options.get("routines", False)

    if not isinstance(db_name, str):
        logger.error(f"{service_name}.{kind}.db_name is required")
        return None
    if not isinstance(db_container, str):
        logger.error(f"{kind}.container is required")
        return None
    if not isinstance(db_username, str):
        logger.error(f"{kind}.username is required")
        return None
    if not isinstance(db_password, str):
        logger.error(f"{kind}.password is required")
        return None
    for field, value in (("username", db_username), ("password", db_password)):
        if any(char in value for char in ('"', "\n", "\r")):
            logger.error(f"{kind}.{field} cannot contain double quotes or line breaks")
            return None
    if not isinstance(routines, bool):
        logger.error(f"{kind}.routines must be true or false")
        return None
    return DumpOptions(db_name, db_container, db_username, db_password, routines)


def delete_credentials(db_container: str):
    run_command(
        ["docker", "exec", db_container, "rm", "/backup.cnf"],
        timeout=exec_timeout,
        check=True,
    )
