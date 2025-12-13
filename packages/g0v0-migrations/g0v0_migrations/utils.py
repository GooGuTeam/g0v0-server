from pathlib import Path
import tomllib


def detect_g0v0_server_path() -> Path | None:
    """Detect the g0v0 server path from the current working directory to parents.

    Returns:
        The path to the g0v0 server, or None if not found.
    """
    cwd = Path.cwd()
    for path in [cwd, *list(cwd.parents)]:
        if (pyproject := (path / "pyproject.toml")).exists():
            content = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            if "project" in content and content["project"].get("name") == "g0v0-server":
                return path.resolve()

    return None
