"""
数据库字段类型工具
提供处理数据库和 Pydantic 之间类型转换的工具
"""

from typing import Any

from pydantic import field_validator
from sqlalchemy import Boolean


def bool_field_validator(field_name: str):
    """为特定布尔字段创建验证器，处理数据库中的 0/1 整数"""

    @field_validator(field_name, mode="before")
    @classmethod
    def validate_bool_field(cls, v: Any) -> bool:
        """将整数 0/1 转换为布尔值"""
        if isinstance(v, int):
            return bool(v)
        return v

    return validate_bool_field


def create_bool_field(**kwargs):
    """创建一个带有正确 SQLAlchemy 列定义的布尔字段"""
    from sqlmodel import Column, Field

    # 如果没有指定 sa_column，则使用 Boolean 类型
    if "sa_column" not in kwargs:
        # 处理 index 参数
        index = kwargs.pop("index", False)
        if index:
            kwargs["sa_column"] = Column(Boolean, index=True)
        else:
            kwargs["sa_column"] = Column(Boolean)

    return Field(**kwargs)
