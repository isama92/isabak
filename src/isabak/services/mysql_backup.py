from src.isabak.services.sql_dump import DumpTool, dump_backup

tool = DumpTool("mysql", "/usr/bin/mysqldump", ("--set-gtid-purged=OFF",))


def mysql_backup(
    service_name: str, service_options: dict, db_options: dict, destination: str
) -> bool:
    return dump_backup(tool, service_name, service_options, db_options, destination)
