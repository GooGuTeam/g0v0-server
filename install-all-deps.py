from pathlib import Path
import subprocess

from pydantic import Field
from pydantic_settings import BaseSettings

META_FILENAME = "plugin.json"


class Config(BaseSettings):
    plugin_dirs: list[str] = Field(default=["./plugins"])


config = Config()
plugin_dirs = config.plugin_dirs

for plugin_dir in plugin_dirs:
    plugin_path = Path(plugin_dir)
    if not plugin_path.is_dir():
        print(f"Plugin directory '{plugin_dir}' does not exist or is not a directory.")
        continue

    for plugin in Path(plugin_dir).iterdir():
        if not plugin.is_dir():
            continue
        meta_files = Path(plugin) / META_FILENAME
        if not meta_files.exists():
            print(f"Plugin directory '{plugin}' does not contain '{META_FILENAME}', skipping.")
            continue

        try:
            print(f"Installing dependencies for plugin '{plugin.name}'...")
            subprocess.run(["uv", "pip", "install", str(plugin.absolute())], check=True)  # noqa: S603, S607
        except subprocess.CalledProcessError as e:
            print(f"Failed to install dependencies for plugin '{plugin.name}': {e}")
