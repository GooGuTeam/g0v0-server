"""
用户页面编辑相关的API模型
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class UpdateUserpageRequest(BaseModel):
    """更新用户页面请求模型（匹配官方osu-web格式）"""

    body: str = Field(
        description="用户页面的BBCode原始内容",
        max_length=60000,
        examples=["[b]Hello![/b] This is my profile page.\n[color=blue]Blue text[/color]"],
    )

    @field_validator("body")
    @classmethod
    def validate_body_content(cls, v: str) -> str:
        """验证原始内容"""
        if not v.strip():
            return ""

        # 基本长度验证
        if len(v) > 60000:
            msg = "Content too long. Maximum 60000 characters allowed."
            raise ValueError(msg)

        return v


class UpdateUserpageResponse(BaseModel):
    """更新用户页面响应模型（匹配官方osu-web格式）"""

    html: str = Field(description="处理后的HTML内容")


class UserpageResponse(BaseModel):
    """用户页面响应模型（包含html和raw，匹配官方格式）"""

    html: str = Field(description="处理后的HTML内容")
    raw: str = Field(description="原始BBCode内容")


class ValidateBBCodeRequest(BaseModel):
    """验证BBCode请求模型"""

    content: str = Field(description="要验证的BBCode内容", max_length=60000)


class ValidateBBCodeResponse(BaseModel):
    """验证BBCode响应模型"""

    valid: bool = Field(description="BBCode是否有效")
    errors: list[str] = Field(default_factory=list, description="错误列表")
    preview: dict[str, str] = Field(description="预览内容")
