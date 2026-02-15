"""Email template service.

Uses Jinja2 template engine with multi-language email support.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from app.config import settings
from app.log import logger

from jinja2 import Environment, FileSystemLoader, Template


class EmailTemplateService:
    """Email template service with multi-language support.

    Attributes:
        CHINESE_COUNTRIES: List of country codes for Chinese-speaking regions.
    """

    # Chinese country/region codes
    CHINESE_COUNTRIES: ClassVar[list[str]] = [
        "CN",  # Mainland China
        "TW",  # Taiwan
        "HK",  # Hong Kong
        "MO",  # Macau
        "SG",  # Singapore (has Chinese speakers)
    ]

    def __init__(self):
        """Initialize Jinja2 template engine."""
        # Template directory path
        template_dir = Path(__file__).parent.parent / "templates" / "email"

        # Create Jinja2 environment
        self.env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

        logger.info(f"Email template service initialized with template directory: {template_dir}")

    def get_language(self, country_code: str | None) -> str:
        """Get language based on country code.

        Args:
            country_code: ISO 3166-1 alpha-2 country code (e.g., CN, US).

        Returns:
            Language code (zh or en).
        """
        if not country_code:
            return "en"

        # Convert to uppercase
        country_code = country_code.upper()

        # Check if it's a Chinese-speaking country/region
        if country_code in self.CHINESE_COUNTRIES:
            return "zh"

        return "en"

    def render_template(
        self,
        template_name: str,
        language: str,
        context: dict[str, Any],
    ) -> str:
        """Render template.

        Args:
            template_name: Template name (without language suffix and extension).
            language: Language code (zh or en).
            context: Template context data.

        Returns:
            Rendered template content.
        """
        try:
            # Build template filename
            template_file = f"{template_name}_{language}.html"

            # Load and render template
            template: Template = self.env.get_template(template_file)
            return template.render(**context)

        except Exception as e:
            logger.error(f"Failed to render template {template_name}_{language}: {e}")
            # If rendering fails and not English, try using English template
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
        """Render plain text template.

        Args:
            template_name: Template name (without language suffix and extension).
            language: Language code (zh or en).
            context: Template context data.

        Returns:
            Rendered plain text content.
        """
        try:
            # Build template filename
            template_file = f"{template_name}_{language}.txt"

            # Load and render template
            template: Template = self.env.get_template(template_file)
            return template.render(**context)

        except Exception as e:
            logger.error(f"Failed to render text template {template_name}_{language}: {e}")
            # If rendering fails and not English, try using English template
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
        """Render verification email.

        Args:
            username: Username.
            code: Verification code.
            country_code: Country code.
            expiry_minutes: Verification code expiry time in minutes.

        Returns:
            Tuple of (subject, HTML content, plain text content).
        """
        # Get language
        language = self.get_language(country_code)

        # Prepare template context
        context = {
            "username": username,
            "code": code,
            "expiry_minutes": expiry_minutes,
            "server_name": settings.from_name,
            "year": datetime.now().year,
        }

        # Render HTML and plain text templates
        html_content = self.render_template("verification", language, context)
        text_content = self.render_text_template("verification", language, context)

        # Set subject based on language
        if language == "zh":
            subject = f"邮箱验证 - {settings.from_name}"
        else:
            subject = f"Email Verification - {settings.from_name}"

        return subject, html_content, text_content


# Global email template service instance
_email_template_service: EmailTemplateService | None = None


def get_email_template_service() -> EmailTemplateService:
    """Get or create email template service instance."""
    global _email_template_service
    if _email_template_service is None:
        _email_template_service = EmailTemplateService()
    return _email_template_service
