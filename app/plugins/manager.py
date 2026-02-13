from dataclasses import dataclass
import importlib
import inspect
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


@dataclass
class ManagedPlugin:
    meta: PluginMeta
    path: Path
    module_name: str
    module: ModuleType | None = None


class PluginManager:
    def __init__(self):
        self.plugins: list[ManagedPlugin] = []

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
                    self.plugins.append(
                        ManagedPlugin(meta=meta, path=Path(plugin), module_name=path_to_module_name(Path(plugin)))
                    )
                    logger.debug(f"Detected plugin: {meta.name} (ID: {meta.id}, Version: {meta.version})")
                except Exception as e:
                    logger.exception(f"Failed to load plugin metadata from '{meta_files}': {e}")

    def _determine_load_order(self) -> list[ManagedPlugin]:
        plugin_map = {m.meta.id: m for m in self.plugins}
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
                dep = plugin_map.get(dep)
                if dep:
                    max_dep_order = max(max_dep_order, dfs(dep.meta, depth + 1))
                else:
                    raise RuntimeError(f"Plugin '{meta.id}' depends on '{dep}' which is not detected.")

            rec_stack.remove(meta.id)
            visited.add(meta.id)
            order[meta.id] = max_dep_order + 1
            return order[meta.id]

        for managed_plugin in self.plugins:
            if managed_plugin.meta.id not in visited:
                dfs(managed_plugin.meta)

        return sorted(self.plugins, key=lambda t: order[t.meta.id])

    def load_all_plugins(self):
        self.detect_plugins()
        load_order = self._determine_load_order()
        for managed_plugin in load_order:
            try:
                module = importlib.import_module(managed_plugin.module_name)
                managed_plugin.module = module
                logger.opt(colors=True).info(
                    f"Loaded plugin: <y>{managed_plugin.meta.name}</y> "
                    f"(ID: {managed_plugin.meta.id}, Version: {managed_plugin.meta.version})"
                )
            except Exception:
                logger.exception(f"Failed to load plugins module for '{managed_plugin.meta.id}'")
                continue

        # process plugin router
        from .api_router import plugin_router, plugin_routers

        for plugin_id, router in plugin_routers.items():
            plugin_router.include_router(router, prefix=f"/plugins/{plugin_id}")
            logger.debug(f"Registered API router for plugin '{plugin_id}' at '/api/plugins/{plugin_id}'")

    def get_plugin_by_module_name(self, module_name: str) -> ManagedPlugin | None:
        for plugin in self.plugins:
            if plugin.module_name == module_name:
                return plugin
        return None

    def get_plugin_from_frame(self) -> ManagedPlugin | None:
        current_frame = inspect.currentframe()
        if current_frame is None:
            return None
        frame = current_frame
        while frame := frame.f_back:
            module_name = (module := inspect.getmodule(frame)) and module.__name__
            if module_name is None:
                return None

            if module_name.startswith("app"):
                continue

            plugin = self.get_plugin_by_module_name(module_name)
            if plugin:
                return plugin

        return None


plugin_manager = PluginManager()
