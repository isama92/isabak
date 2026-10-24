from os.path import join as path_join
from os import makedirs, remove
import subprocess
from src.isabak.logs import get_logger

logger = get_logger(__name__)


def mysql_backup(
    service_name: str, service_options: dict, db_options: dict, destination: str
) -> bool:
    logger.debug(f"mysql backup for service '{service_name}' started")

    db_name = service_options.get("db_name")
    db_container = db_options.get("container")
    db_username = db_options.get("username")
    db_password = db_options.get("password")

    if not check_options(service_name, db_name, db_container, db_username, db_password):
        return False

    destination = path_join(destination, "mysql", "")
    makedirs(destination, exist_ok=True)

    try:
        create_credentials(db_container, db_username, db_password)
        try:
            create_backup(db_name, db_container, destination)
        finally:
            delete_credentials(db_container)
    except Exception as e:
        logger.exception(e, stack_info=True)
        return False

    logger.debug("mysql backup completed successfully")
    return True


def create_credentials(db_container: str, db_username: str, db_password: str):
    cmd = f"""echo -e "[client]\\nuser={db_username}\\npassword={db_password}\\nhost=localhost\\n" > /backup.cnf"""
    subprocess.run(["docker", "exec", db_container, "bash", "-c", cmd], check=True)


def create_backup(db_name: str, db_container: str, destination: str):

    dump_cmd = [
        "docker",
        "exec",
        db_container,
        "/usr/bin/mysqldump",
        "--defaults-extra-file=/backup.cnf",
        "--single-transaction",
        "--set-gtid-purged=OFF",
        db_name,
    ]

    out_path = path_join(destination, "mysql.sql.gz")
    with open(out_path, "wb") as f:
        dump_proc = subprocess.Popen(dump_cmd, stdout=subprocess.PIPE)
        gzip = subprocess.Popen(["gzip"], stdin=dump_proc.stdout, stdout=f)
        dump_proc.stdout.close()
        gzip_rc = gzip.wait()
        dump_rc = dump_proc.wait()

    if dump_rc != 0:
        remove(out_path)
        raise subprocess.CalledProcessError(dump_rc, dump_cmd)
    if gzip_rc != 0:
        remove(out_path)
        raise subprocess.CalledProcessError(gzip_rc, ["gzip"])


def check_options(
    service_name: str,
    db_name: str | None,
    db_container: str | None,
    db_username: str | None,
    db_password: str | None,
) -> bool:
    if db_name is None:
        logger.error(f"{service_name}.mysql.db_name is required")
        return False
    if db_container is None:
        logger.error(f"mysql.container is required")
        return False
    if db_username is None:
        logger.error(f"mysql.username is required")
        return False
    if db_password is None:
        logger.error(f"mysql.password is required")
        return False
    return True


def delete_credentials(db_container: str):
    subprocess.run(["docker", "exec", db_container, "rm", "/backup.cnf"], check=True)
