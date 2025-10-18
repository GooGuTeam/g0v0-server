"""
邮件模板服务
使用 Jinja2 模板引擎，支持多语言邮件
"""

from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from app.config import settings
from app.log import logger

from jinja2 import Environment, FileSystemLoader, Template


class EmailTemplateService:
    """邮件模板服务，支持多语言"""

    # 中文国家/地区代码列表
    CHINESE_COUNTRIES: ClassVar[list[str]] = [
        "CN",  # 中国大陆
        "TW",  # 台湾
        "HK",  # 香港
        "MO",  # 澳门
        "SG",  # 新加坡（有中文使用者）
    ]

    def __init__(self):
        """初始化 Jinja2 模板引擎"""
        # 模板目录路径
        template_dir = Path(__file__).parent.parent / "templates" / "email"

        # 创建 Jinja2 环境
        self.env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

        logger.info(f"Email template service initialized with template directory: {template_dir}")

    def get_language(self, country_code: str | None) -> str:
        """
        根据国家代码获取语言

        Args:
            country_code: ISO 3166-1 alpha-2 国家代码（如 CN, US）

        Returns:
            语言代码（zh 或 en）
        """
        if not country_code:
            return "en"

        # 转换为大写
        country_code = country_code.upper()

        # 检查是否是中文国家/地区
        if country_code in self.CHINESE_COUNTRIES:
            return "zh"

        return "en"

    def render_template(
        self,
        template_name: str,
        language: str,
        context: dict[str, Any],
    ) -> str:
        """
        渲染模板

        Args:
            template_name: 模板名称（不含语言后缀和扩展名）
            language: 语言代码（zh 或 en）
            context: 模板上下文数据

        Returns:
            渲染后的模板内容
        """
        try:
            # 构建模板文件名
            template_file = f"{template_name}_{language}.html"

            # 加载并渲染模板
            template: Template = self.env.get_template(template_file)
            return template.render(**context)

        except Exception as e:
            logger.error(f"Failed to render template {template_name}_{language}: {e}")
            # 如果渲染失败且不是英文，尝试使用英文模板
            if language != "en":
                logger.warning(f"Falling back to English template for {template_name}")
                return self.render_template(template_name, "en", context)
            raise

    def render_text_template(
        self,
        template_name: str,
        language: str,
        context: dict[str, Any],
    ) -> str:
        """
        渲染纯文本模板

        Args:
            template_name: 模板名称（不含语言后缀和扩展名）
            language: 语言代码（zh 或 en）
            context: 模板上下文数据

        Returns:
            渲染后的纯文本内容
        """
        try:
            # 构建模板文件名
            template_file = f"{template_name}_{language}.txt"

            # 加载并渲染模板
            template: Template = self.env.get_template(template_file)
            return template.render(**context)

        except Exception as e:
            logger.error(f"Failed to render text template {template_name}_{language}: {e}")
            # 如果渲染失败且不是英文，尝试使用英文模板
            if language != "en":
                logger.warning(f"Falling back to English text template for {template_name}")
                return self.render_text_template(template_name, "en", context)
            raise

    def render_verification_email(
        self,
        username: str,
        code: str,
        country_code: str | None = None,
        expiry_minutes: int = 10,
    ) -> tuple[str, str, str]:
        """
        渲染验证邮件

        Args:
            username: 用户名
            code: 验证码
            country_code: 国家代码
            expiry_minutes: 验证码过期时间（分钟）

        Returns:
            (主题, HTML内容, 纯文本内容)
        """
        # 获取语言
        language = self.get_language(country_code)

        # 准备模板上下文
        context = {
            "username": username,
            "code": code,
            "expiry_minutes": expiry_minutes,
            "server_name": settings.from_name,
            "year": datetime.now().year,
        }

        # 渲染 HTML 和纯文本模板
        html_content = self.render_template("verification", language, context)
        text_content = self.render_text_template("verification", language, context)

        # 根据语言设置主题
        if language == "zh":
            subject = f"邮箱验证 - {settings.from_name}"
        else:
            subject = f"Email Verification - {settings.from_name}"

        return subject, html_content, text_content


# 全局邮件模板服务实例
_email_template_service: EmailTemplateService | None = None


def get_email_template_service() -> EmailTemplateService:
    """获取或创建邮件模板服务实例"""
    global _email_template_service
    if _email_template_service is None:
        _email_template_service = EmailTemplateService()
    return _email_template_service
