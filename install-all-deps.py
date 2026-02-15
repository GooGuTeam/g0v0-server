import json
import os
from pathlib import Path
import subprocess
import sys

META_FILENAME = "plugin.json"

plugin_dirs = os.environ.get("PLUGIN_DIRS", "")
try:
    plugin_dirs = json.loads(plugin_dirs)
except json.JSONDecodeError:
    print("Failed to parse PLUGIN_DIRS environment variable. Ensure it is a valid JSON array.")
    sys.exit(1)

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
