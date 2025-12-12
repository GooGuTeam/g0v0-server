"""
用户页面编辑相关的API模型
"""

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


class UserpageError(Exception):
    """用户页面处理错误基类"""

    def __init__(self, message: str, code: str = "userpage_error"):
        self.message = message
        self.code = code
        super().__init__(message)


class ContentTooLongError(UserpageError):
    """内容过长错误"""

    def __init__(self, current_length: int, max_length: int):
        message = f"Content too long. Maximum {max_length} characters allowed, got {current_length}."
        super().__init__(message, "content_too_long")
        self.current_length = current_length
        self.max_length = max_length


class ContentEmptyError(UserpageError):
    """内容为空错误"""

    def __init__(self):
        super().__init__("Content cannot be empty.", "content_empty")


class BBCodeValidationError(UserpageError):
    """BBCode验证错误"""

    def __init__(self, errors: list[str]):
        message = f"BBCode validation failed: {'; '.join(errors)}"
        super().__init__(message, "bbcode_validation_error")
        self.errors = errors


class ForbiddenTagError(UserpageError):
    """禁止标签错误"""

    def __init__(self, tag: str):
        message = f"Forbidden tag '{tag}' is not allowed."
        super().__init__(message, "forbidden_tag")
        self.tag = tag


class MaliciousBBCodeError(UserpageError):
    """恶意BBCode错误"""

    def __init__(self, detail: str):
        message = f"Malicious BBCode detected: {detail}"
        super().__init__(message, "malicious_bbcode")
        self.detail = detail
