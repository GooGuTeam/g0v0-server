import datetime
from enum import Enum
from inspect import isclass
import json
import os
import sys
from types import NoneType, UnionType
from typing import Any, Literal, Union, get_origin

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import Settings

from pydantic import AliasChoices, BaseModel, HttpUrl
from pydantic_core import PydanticUndefined
from pydantic_settings import BaseSettings

commit = sys.argv[1] if len(sys.argv) > 1 else "unknown"

BASE_TYPE_MAPPING: dict[type[Any], str] = {
    str: "string",
    int: "integer",
    float: "float",
    bool: "boolean",
    list: "array",
    dict: "object",
    NoneType: "null",
    HttpUrl: "string",
    type(Any): "any",
}


def mapping_type(typ: type) -> str:
    base_type = BASE_TYPE_MAPPING.get(typ)
    if base_type:
        return base_type
    if (origin := get_origin(typ)) is Union or origin is UnionType:
        args = list(typ.__args__)
        if len(args) == 1:
            return mapping_type(args[0])
        if len(args) == 2 and NoneType in args:
            non_none = next(a for a in args if a is not NoneType)
            return mapping_type(non_none)
        return " | ".join(mapping_type(a) for a in args)
    elif get_origin(typ) is list:
        args = typ.__args__
        if len(args) == 1:
            return f"array<{mapping_type(args[0])}>"
        return "array"
    elif get_origin(typ) is dict:
        args = typ.__args__
        if len(args) == 2:
            return f"object<{mapping_type(args[0])}, {mapping_type(args[1])}>"
        return "object"
    elif get_origin(typ) is Literal:
        return f"enum({', '.join([str(n) for n in typ.__args__])})"
    elif isclass(typ) and issubclass(typ, Enum):
        return f"enum({', '.join([e.value for e in typ])})"
    elif isclass(typ) and issubclass(typ, BaseSettings):
        return typ.__name__
    return "unknown"


def serialize_default(value: Any) -> Any:
    if value is PydanticUndefined:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, HttpUrl):
        return str(value)
    return value


result: dict[str, Any] = {
    "$commit": commit if commit != "unknown" else None,
    "$timestamp": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
}

for name, field in Settings.model_fields.items():
    if len(field.metadata) == 0:
        continue

    section = field.metadata[0]

    if section not in result:
        result[section] = {}

    alias = field.alias or name
    env_name = alias.upper()

    aliases: list[str] = []
    other_aliases = field.validation_alias
    if isinstance(other_aliases, str):
        if other_aliases.upper() != env_name:
            aliases.append(other_aliases.upper())
    elif isinstance(other_aliases, AliasChoices):
        for a in other_aliases.convert_to_aliases():
            alias_str = str(a[0]).upper() if isinstance(a, list) and len(a) > 0 else str(a).upper()
            if alias_str != env_name:
                aliases.append(alias_str)

    entry: dict[str, Any] = {
        "$type": mapping_type(field.annotation),  # pyright: ignore[reportArgumentType]
        "$default": serialize_default(field.default),
    }

    if aliases:
        entry["$aliases"] = aliases

    result[section][env_name] = entry

print(json.dumps(result, indent=4, ensure_ascii=False))
