import importlib
from pathlib import Path
from types import ModuleType

from app.config import settings
from app.log import log
from app.models.plugin import META_FILENAME, PluginMeta

logger = log("PluginManager")


def path_to_module_name(path: Path) -> str:
    rel_path = path.resolve().relative_to(Path.cwd().resolve())
    if rel_path.stem == "__init__":
        return ".".join(rel_path.parts[:-1])
    else:
        return ".".join((*rel_path.parts[:-1], rel_path.stem))


class PluginManager:
    def __init__(self):
        self.plugins: list[tuple[PluginMeta, ModuleType]] = []
        self.detected_plugins: set[tuple[PluginMeta, Path]] = set()

    def detect_plugins(self):
        for plugin_dir in settings.plugin_dirs:
            for plugin in Path(plugin_dir).iterdir():
                if not plugin.is_dir():
                    continue
                meta_files = Path(plugin) / META_FILENAME
                if not meta_files.exists():
                    logger.warning(f"Plugin directory '{plugin}' does not contain '{META_FILENAME}', skipping.")
                    continue
                try:
                    meta = PluginMeta.model_validate_json(meta_files.read_text())
                    self.detected_plugins.add((meta, Path(plugin)))
                    logger.debug(f"Detected plugin: {meta.name} (ID: {meta.id}, Version: {meta.version})")
                except Exception as e:
                    logger.exception(f"Failed to load plugin metadata from '{meta_files}': {e}")

    def _determine_load_order(self) -> list[tuple[PluginMeta, Path]]:
        plugin_map = {m.id: m for m, _ in self.detected_plugins}
        visited = set()
        rec_stack = set()
        order = {}

        def dfs(meta: PluginMeta, depth: int = 0) -> int:
            if meta.id in visited:
                return order[meta.id]
            if meta.id in rec_stack:
                raise RuntimeError(f"Circular dependency detected involving plugin '{meta.id}'")

            rec_stack.add(meta.id)
            max_dep_order = 0

            for dep in meta.dependencies:
                dep_meta = plugin_map.get(dep)
                if dep_meta:
                    max_dep_order = max(max_dep_order, dfs(dep_meta, depth + 1))
                else:
                    raise RuntimeError(f"Plugin '{meta.id}' depends on '{dep}' which is not detected.")

            rec_stack.remove(meta.id)
            visited.add(meta.id)
            order[meta.id] = max_dep_order + 1
            return order[meta.id]

        for meta, _ in self.detected_plugins:
            if meta.id not in visited:
                dfs(meta)

        return sorted(self.detected_plugins, key=lambda t: order[t[0].id])

    def load_all_plugins(self):
        self.detect_plugins()
        load_order = self._determine_load_order()
        for meta, path in load_order:
            logger.opt(colors=True).info(f"Loading plugin: <y>{meta.name}</y> (ID: {meta.id}, Version: {meta.version})")

            try:
                module = importlib.import_module(path_to_module_name(path))
                self.plugins.append((meta, module))
            except Exception:
                logger.exception(f"Failed to load plugins module for '{meta.id}'")
                continue


plugin_manager = PluginManager()
