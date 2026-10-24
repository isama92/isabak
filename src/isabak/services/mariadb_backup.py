from os.path import join as path_join
from os import makedirs, remove
import subprocess
from src.isabak.logs import get_logger

logger = get_logger(__name__)


def mariadb_backup(
    service_name: str, service_options: dict, db_options: dict, destination: str
):
    logger.debug(f"mariadb backup for service '{service_name}' started")

    db_name = service_options.get("db_name")
    db_container = db_options.get("container")
    db_username = db_options.get("username")
    db_password = db_options.get("password")

    if not check_options(service_name, db_name, db_container, db_username, db_password):
        return

    destination = path_join(destination, "mariadb", "")
    makedirs(destination, exist_ok=True)

    try:
        create_credentials(db_container, db_username, db_password)
        try:
            create_backup(db_name, db_container, destination)
        finally:
            delete_credentials(db_container)
    except Exception as e:
        logger.exception(e, stack_info=True)
        return

    logger.debug("mariadb backup completed successfully")


def create_credentials(db_container: str, db_username: str, db_password: str):
    cmd = f"""echo -e "[client]\\nuser={db_username}\\npassword={db_password}\\nhost=localhost\\n" > /backup.cnf"""
    subprocess.run(["docker", "exec", db_container, "bash", "-c", cmd], check=True)


def create_backup(db_name: str, db_container: str, destination: str):
    dump_cmd = [
        "docker",
        "exec",
        db_container,
        "/usr/bin/mariadb-dump",
        "--defaults-extra-file=/backup.cnf",
        "--single-transaction",
        db_name,
    ]

    out_path = path_join(destination, "mariadb.sql.gz")
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
        logger.error(f"{service_name}.mariadb.db_name is required")
        return False
    if db_container is None:
        logger.error(f"mariadb.container is required")
        return False
    if db_username is None:
        logger.error(f"mariadb.username is required")
        return False
    if db_password is None:
        logger.error(f"mariadb.password is required")
        return False
    return True


def delete_credentials(db_container: str):
    subprocess.run(["docker", "exec", db_container, "rm", "/backup.cnf"], check=True)
