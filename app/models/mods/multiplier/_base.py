from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import inspect
from typing import Any, ClassVar

from app.models.mods.definition import APIMod, get_default_setting


def combination(mod1: str, mod2: str):
    def wrapper(func):
        setattr(func, "_combination", (mod1, mod2))
        return func

    return wrapper


def _get_caller_name() -> str:
    return inspect.stack()[2].function


def _mod_method_name(acronym: str) -> str:
    if acronym.endswith("K") and acronym[:-1].isdigit():
        return f"k{acronym[:-1]}"
    return acronym.lower()


class _ModWrapper:
    def __init__(self, mod: APIMod, ruleset_id: int):
        self.mod = mod
        self.ruleset_id = ruleset_id

    @property
    def mod_name(self) -> str:
        return self.mod["acronym"]

    def __getattr__(self, item) -> Any:
        if "settings" in self.mod and item in self.mod["settings"]:
            return self.mod["settings"][item]
        return get_default_setting(self.ruleset_id, self.mod, item)

    def is_uses_default(self) -> bool:
        return all(
            value == get_default_setting(self.ruleset_id, self.mod, setting)
            for setting, value in self.mod.get("settings", {}).items()
        )


@dataclass
class ModMultiplierContext:
    mods: list[APIMod]
    cs: float
    ar: float
    od: float
    hp: float
    client_version: str
    date: datetime
    ruleset_id: int

    def mod(self, acronym: str) -> _ModWrapper | None:
        for mod in self.mods:
            if mod["acronym"] == acronym.upper():
                return _ModWrapper(mod, self.ruleset_id)
        return None

    @property
    def me(self) -> _ModWrapper:
        caller = _get_caller_name()
        mod = self.mod(caller)
        if mod is None:
            raise ValueError(f"Mod '{caller}' not found in context")
        return mod


class ModMultiplierCalculator:
    combinations: ClassVar[list[tuple[tuple[str, str], Callable[["ModMultiplierCalculator"], float]]]]

    def __init_subclass__(cls):
        super().__init_subclass__()

        cls.combinations = []

        for _, obj in cls.__dict__.items():
            if hasattr(obj, "_combination"):
                cls.combinations.append((obj._combination, obj))

    def __init__(self, context: ModMultiplierContext):
        self.context = context

    def calculate(self) -> float:
        multiplier = 1.0
        remaining_mods = {mod["acronym"] for mod in self.context.mods}

        if len(self.context.mods) > 1:
            for combination, func in self.combinations:
                if remaining_mods.issuperset(combination):
                    multiplier *= func(self)
                    remaining_mods.difference_update(combination)
        for mod in remaining_mods:
            method_name = _mod_method_name(mod)
            method = getattr(self, method_name, None)
            if method is None:
                method = getattr(self, f"{method_name}_", None)
            if method is None:
                method = getattr(self, mod, None)
            if method is not None:
                multiplier *= method()

        return multiplier

    @property
    def me(self) -> _ModWrapper:
        caller = _get_caller_name()
        mod = self.mod(caller)
        if mod is None:
            raise ValueError(f"Mod '{caller}' not found in context")
        return mod

    def mod(self, acronym: str) -> _ModWrapper | None:
        return self.context.mod(acronym)
