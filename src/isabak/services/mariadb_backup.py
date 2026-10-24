from src.isabak.services.sql_dump import DumpTool, dump_backup

# mariadb-dump, not mysqldump: mysqldump does not dump MariaDB 10.5+ correctly
tool = DumpTool("mariadb", "/usr/bin/mariadb-dump")


def mariadb_backup(
    service_name: str, service_options: dict, db_options: dict, destination: str
) -> bool:
    return dump_backup(tool, service_name, service_options, db_options, destination)
