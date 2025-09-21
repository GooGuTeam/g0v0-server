from __future__ import annotations

import datetime
from enum import Enum
import importlib.util
import json
from pathlib import Path
import sys
from types import NoneType, UnionType
from typing import Any, Union, get_origin

from pydantic import AliasChoices, BaseModel, HttpUrl
from pydantic_settings import BaseSettings

file_path = Path("./app/config.py").resolve()

spec = importlib.util.spec_from_file_location("config", str(file_path))
module = importlib.util.module_from_spec(spec)  # pyright: ignore[reportArgumentType]
sys.modules["my_module"] = module
spec.loader.exec_module(module)  # pyright: ignore[reportOptionalMemberAccess]

model: type[BaseSettings] = module.Settings

commit = sys.argv[1] if len(sys.argv) > 1 else "unknown"

doc = []
uncategorized = []


def new_paragraph(name: str, has_sub_paragraph: bool) -> None:
    doc.append("")
    doc.append(f"## {name}")
    if desc := model.model_config["json_schema_extra"]["paragraphs_desc"].get(name):  # type: ignore
        doc.append(desc)
    if not has_sub_paragraph:
        doc.append("| 变量名 | 描述 | 类型 | 默认值 |")
        doc.append("|------|------|--------|------|")


def new_sub_paragraph(name: str) -> None:
    doc.append("")
    doc.append(f"### {name}")
    doc.append("| 变量名 | 描述 | 类型 | 默认值 |")
    doc.append("|------|------|--------|------|")


def serialize_default(value: Any) -> str:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        return value or '""'
    try:
        if isinstance(value, BaseModel):
            return value.model_dump_json()
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


BASE_TYPE_MAPPING = {
    str: "string",
    int: "integer",
    float: "float",
    bool: "boolean",
    list: "array",
    dict: "object",
    NoneType: "null",
    HttpUrl: "string (url)",
}


def mapping_type(typ: type) -> str:
    base_type = BASE_TYPE_MAPPING.get(typ)
    if base_type:
        return base_type
    if (origin := get_origin(typ)) is Union or origin is UnionType:
        args = list(typ.__args__)
        if len(args) == 1:
            return mapping_type(args[0])
        return " / ".join(mapping_type(a) for a in args)
    elif get_origin(typ) is list:
        args = typ.__args__
        if len(args) == 1:
            return f"array[{mapping_type(args[0])}]"
        return "array"
    if issubclass(typ, Enum):
        return f"enum({', '.join([e.value for e in typ])})"
    elif issubclass(typ, BaseSettings):
        return typ.__name__
    return "unknown"


last_paragraph = ""
last_sub_paragraph = ""
for name, field in model.model_fields.items():
    if len(field.metadata) == 0:
        uncategorized.append((name, field))
        continue
    sub_paragraph = ""
    paragraph = field.metadata[0]
    if len(field.metadata) > 1 and isinstance(field.metadata[1], str):
        sub_paragraph = field.metadata[1]
    if paragraph != last_paragraph:
        last_paragraph = paragraph
        new_paragraph(paragraph, has_sub_paragraph=bool(sub_paragraph))
    if sub_paragraph and sub_paragraph != last_sub_paragraph:
        last_sub_paragraph = sub_paragraph
        new_sub_paragraph(sub_paragraph)

    alias = field.alias or name
    aliases = []
    other_aliases = field.validation_alias
    if isinstance(other_aliases, str):
        if other_aliases != alias:
            aliases.append(other_aliases)
    elif isinstance(other_aliases, AliasChoices):
        for a in other_aliases.convert_to_aliases():
            if a != alias:
                aliases.extend(a)

    ins_doc = f"({', '.join([f'`{a.upper()}`' for a in aliases])}) " if aliases else ""
    doc.append(
        f"| `{alias.upper()}` {ins_doc}| {field.description or ''} "
        f"| {mapping_type(field.annotation)} | `{serialize_default(field.default)}` |"  # pyright: ignore[reportArgumentType]
    )

doc.extend(
    [
        module.SPECTATOR_DOC,
        "",
        f"> 上次生成：{datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M:%S %Z')}"
        f"于提交 {f'[`{commit}`](https://github.com/GooGuTeam/g0v0-server/commit/{commit})' if commit != 'unknown' else 'unknown'}",  # noqa: E501
        "",
        "> **注意: 在生产环境中，请务必更改默认的密钥和密码！**",
    ]
)
print("\n".join(doc))
