"""Email verification service.

Provides email sending functionality for user verification.
"""

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import secrets
import smtplib
import string

from app.config import settings
from app.log import logger


class EmailService:
    """Email sending service.

    Handles SMTP email sending for verification and other notifications.
    """

    def __init__(self):
        self.smtp_server = settings.smtp_server
        self.smtp_port = settings.smtp_port
        self.smtp_username = settings.smtp_username
        self.smtp_password = settings.smtp_password
        self.from_email = settings.from_email
        self.from_name = settings.from_name

    def generate_verification_code(self) -> str:
        """Generate an 8-digit verification code.

        Returns:
            8-digit numeric verification code.
        """
        # Only use digits to avoid confusion
        return "".join(secrets.choice(string.digits) for _ in range(8))

    async def send_verification_email(self, email: str, code: str, username: str) -> bool:
        """Send verification email.

        Args:
            email: Recipient email address.
            code: Verification code.
            username: User's username.

        Returns:
            True if email was sent successfully, False otherwise.
        """
        try:
            msg = MIMEMultipart()
            msg["From"] = f"{self.from_name} <{self.from_email}>"
            msg["To"] = email
            msg["Subject"] = "邮箱验证 - Email Verification"

            # HTML email content
            html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        .container {{
            max-width: 600px;
            margin: 0 auto;
            font-family: Arial, sans-serif;
            line-height: 1.6;
        }}
        .header {{
            background: #ED8EA6;
            color: white;
            padding: 20px;
            text-align: center;
            border-radius: 10px 10px 0 0;
        }}
        .content {{
            background: #f9f9f9;
            padding: 30px;
            border: 1px solid #ddd;
        }}
        .code {{
            background: #fff;
            border: 2px solid #ED8EA6;
            border-radius: 8px;
            padding: 15px;
            text-align: center;
            font-size: 24px;
            font-weight: bold;
            letter-spacing: 3px;
            margin: 20px 0;
            color: #333;
        }}
        .footer {{
            background: #333;
            color: #fff;
            padding: 15px;
            text-align: center;
            border-radius: 0 0 10px 10px;
            font-size: 12px;
        }}
        .warning {{
            background: #fff3cd;
            border: 1px solid #ffeaa7;
            border-radius: 5px;
            padding: 10px;
            margin: 15px 0;
            color: #856404;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>osu! 邮箱验证</h1>
            <p>Email Verification</p>
        </div>

        <div class="content">
            <h2>你好 {username}！</h2>
            <p>感谢你注册我们的 osu! 服务器。为了完成账户验证，请输入以下验证码：</p>

            <div class="code">{code}</div>

            <p>这个验证码将在 <strong>10 分钟后过期</strong>。</p>

            <div class="warning">
                <strong>注意：</strong>
                <ul>
                    <li>请不要与任何人分享这个验证码</li>
                    <li>如果你没有请求此验证码，请忽略这封邮件</li>
                    <li>验证码只能使用一次</li>
                </ul>
            </div>

            <p>如果你有任何问题，请联系我们的支持团队。</p>

            <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">

            <h3>Hello {username}!</h3>
            <p>Thank you for registering on our osu! server. To complete your account verification, please enter the following verification code:</p>

            <p>This verification code will expire in <strong>10 minutes</strong>.</p>

            <p><strong>Important:</strong> Do not share this verification code with anyone. If you did not request this code, please ignore this email.</p>
        </div>

        <div class="footer">
            <p>© 2025 g0v0! Private Server. 此邮件由系统自动发送，请勿回复。</p>
            <p>This email was sent automatically, please do not reply.</p>
        </div>
    </div>
</body>
</html>
            """  # noqa: E501

            msg.attach(MIMEText(html_content, "html", "utf-8"))

            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                if self.smtp_username and self.smtp_password:
                    server.starttls()
                    server.login(self.smtp_username, self.smtp_password)

                server.send_message(msg)

            logger.info(f"Successfully sent verification code to {email}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False


# Global email service instance
email_service = EmailService()
