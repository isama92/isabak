from os import getenv, scandir
from os.path import isdir as path_isdir
from re import compile as re_compile


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
