import json
from pathlib import Path
import re
import tomllib


def detect_g0v0_server_path() -> Path | None:
    """Detect the g0v0 server path from the current working directory to parents.

    Returns:
        The path to the g0v0 server, or None if not found.
    """
    cwd = Path.cwd()
    for path in [cwd, *list(cwd.parents)]:
        if (pyproject := (path / "pyproject.toml")).exists():
            try:
                content = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError:
                continue
            if "project" in content and content["project"].get("name") == "g0v0-server":
                return path.resolve()

    return None


def get_plugin_id(plugin_path: Path) -> str:
    """Get the plugin ID from the plugin.json file.

    Args:
        plugin_path: The path to the plugin directory.

    Returns:
        The plugin ID.

    Raises:
    """
    if not plugin_path.joinpath("plugin.json").exists():
        raise ValueError(f"No plugin.json found at {plugin_path / 'plugin.json'}.")
    try:
        meta = json.loads(plugin_path.joinpath("plugin.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed plugin.json at {plugin_path / 'plugin.json'}: {e}")
    plugin_id = meta.get("id")
    if plugin_id is None:
        raise ValueError(f"Could not detect plugin id from {plugin_path / 'plugin.json'}.")
    if re.match(r"^[a-z0-9\-]+$", plugin_id) is None:
        raise ValueError(
            f"Invalid plugin id '{plugin_id}' in {plugin_path / 'plugin.json'}. Must match '^[a-z0-9\\-]+$'."
        )
    return plugin_id
