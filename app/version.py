from pathlib import Path

from app.config import settings
from app.path import CONFIG_DIR

VERSION = "0.2.0"
CHANGES = {
    "0.2.0": (
        lambda: settings.enable_v2_ipc,
        "=== Migrate to v2 Realtime ===\nWe has officially adopted the V2 series of "
        "Realtime as our realtime game server.\nAnd the original spectator-server will no longer be used.\n"
        "View https://docs.g0v0.top/lazer/deploy/migrate-from-spectator-server.html "
        "to migrate from the original spectator-server.\n"
        "Pull Request: https://github.com/GooGuTeam/g0v0-server/pull/208",
    )
}


ver_file = Path(CONFIG_DIR) / "server-ver"


def version_check() -> None:
    if not ver_file.exists():
        # init file with the original version
        current_version = "0.1.0"
        ver_file.write_text(current_version)
    else:
        current_version = ver_file.read_text().strip()
    changes = []
    for ver, (condition, message) in CHANGES.items():
        if current_version < ver and condition():
            changes.append(message)
    if changes:
        raise RuntimeError(
            "⚠️  The following changes are required for version compatibility:\n\n"
            + "\n\n".join(changes)
            + f"\n\nIf you have already made these changes, please change config/server-ver to {VERSION}."
        )
