from pathlib import Path
import subprocess

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

META_FILENAME = "plugin.json"
PYPROJECT_FILENAME = "pyproject.toml"
REQUIREMENTS_FILENAME = "requirements.txt"


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    plugin_dirs: list[str] = Field(default=["./plugins"])


def run_subprocess(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True)  # noqa: S603
    except subprocess.CalledProcessError as e:
        print(f"Command '{' '.join(command)}' failed with error: {e}")


config = Config()
plugin_dirs = config.plugin_dirs

for plugin_dir in plugin_dirs:
    print(f"Installing dependencies for plugins in directory: {plugin_dir}")
    plugin_path = Path(plugin_dir)
    if not plugin_path.is_dir():
        print(f"Plugin directory '{plugin_dir}' does not exist or is not a directory.")
        continue

    for plugin in Path(plugin_dir).iterdir():
        if not plugin.is_dir() or plugin.name.startswith("."):
            continue
        meta_files = Path(plugin) / META_FILENAME
        if not meta_files.exists():
            print(f"Plugin directory '{plugin}' does not contain '{META_FILENAME}', skipping.")
            continue

        if not (plugin / PYPROJECT_FILENAME).exists() and not (plugin / REQUIREMENTS_FILENAME).exists():
            print(
                f"Plugin '{plugin.name}' does not contain '{PYPROJECT_FILENAME}' "
                f"or '{REQUIREMENTS_FILENAME}', skipping."
            )
            continue
        elif (plugin / REQUIREMENTS_FILENAME).exists():
            run_subprocess(["uv", "pip", "install", "-r", str(plugin / REQUIREMENTS_FILENAME)])
            continue
        elif (plugin / PYPROJECT_FILENAME).exists():
            run_subprocess(["uv", "pip", "install", "-e", str(plugin.absolute())])
            continue
