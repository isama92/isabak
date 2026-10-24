import subprocess
from os import getenv, scandir
from os.path import isdir as path_isdir
from re import compile as re_compile

# how long a process gets to exit after SIGTERM before it is killed
stop_grace_seconds = 60


def run_command(
    cmd: list[str],
    timeout: float,
    check: bool = False,
    input: bytes | None = None,
    **kwargs,
) -> subprocess.CompletedProcess:
    # like subprocess.run(timeout=...), which sends SIGKILL straight away. SIGTERM
    # first lets borg release its repository lock and docker run stop and remove
    # its container, a kill would leave both behind
    stdin = subprocess.PIPE if input is not None else None
    with subprocess.Popen(cmd, stdin=stdin, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_process(process)
            raise subprocess.TimeoutExpired(cmd, timeout) from None
    if check and process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, cmd, stdout, stderr)
    return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)


def stop_process(process: subprocess.Popen):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.communicate(timeout=stop_grace_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()


def check_source(folder: str) -> str | None:
    # an unmounted disk leaves an empty mount point, backing it up looks like success
    if not path_isdir(folder):
        return "does not exist, is its disk mounted?"
    try:
        with scandir(folder) as entries:
            if next(entries, None) is None:
                return "is empty, is its disk mounted?"
    except OSError as e:
        return f"cannot be read ({e.strerror})"
    return None


def replace_env_vars(folder: str) -> str:
    pattern = re_compile(r"\$\{([a-zA-Z0-9_]+)\}")

    def replacer(match):
        env_var_name = match.group(1)
        env_var = getenv(env_var_name)
        if env_var is None:
            raise KeyError(
                f"environment variable '{env_var_name}' is required, used in '{folder}'"
            )
        return env_var

    folder = pattern.sub(replacer, folder)
    return folder
