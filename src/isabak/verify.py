import gzip
import zipfile
import zlib

# last line each dump tool writes once the dump finished, absent from a cut-off dump
mysql_dump_marker = b"-- Dump completed"
postgres_dump_marker = b"-- PostgreSQL database dump complete"


def verify_sql_gzip(path: str, marker: bytes) -> str | None:
    # reading to the end checks the gzip crc, the tail shows the dump finished
    tail = b""
    try:
        with gzip.open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                tail = (tail + chunk)[-4096:]
    except (OSError, EOFError, zlib.error) as e:
        return f"is not a valid gzip file ({e})"
    if not tail:
        return "is empty"
    if marker not in tail:
        return "is incomplete, the end-of-dump marker is missing"
    return None


def verify_zip(path: str) -> str | None:
    try:
        with zipfile.ZipFile(path) as archive:
            if not archive.namelist():
                return "is an empty zip"
            broken = archive.testzip()
    except (zipfile.BadZipFile, OSError, EOFError, zlib.error) as e:
        return f"is not a valid zip file ({e})"
    if broken is not None:
        return f"has a corrupt entry '{broken}'"
    return None
