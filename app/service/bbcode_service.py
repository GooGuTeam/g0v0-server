"""
BBCode markup language to HTML.

This module provides functionality to parse BBCode into HTML, sanitize the HTML,
and validate BBCode syntax, based on the implementation from osu-web.

Reference:
    - https://osu.ppy.sh/wiki/BBCode
    - https://github.com/ppy/osu-web/blob/master/app/Libraries/BBCodeFromDB.php
"""

import html
from typing import ClassVar

from app.models.userpage import (
    ContentEmptyError,
    ContentTooLongError,
    ForbiddenTagError,
)

import bleach
from bleach.css_sanitizer import CSSSanitizer
import regex as re

HTTP_PATTERN = re.compile(r"^https?://", re.IGNORECASE)
REGEX_TIMEOUT = 5


class BBCodeService:
    """BBCode处理服务类 - 基于 osu-web 官方实现"""

    # 允许的HTML标签和属性 - 基于官方实现
    ALLOWED_TAGS: ClassVar[list[str]] = [
        "a",
        "audio",
        "blockquote",
        "br",
        "button",
        "center",
        "code",
        "del",
        "div",
        "em",
        "h2",
        "h4",
        "iframe",
        "img",
        "li",
        "ol",
        "p",
        "pre",
        "span",
        "strong",
        "u",
        "ul",
        # imagemap 相关
        "map",
        "area",
        # 自定义容器
        "details",
        "summary",
    ]

    ALLOWED_ATTRIBUTES: ClassVar[dict[str, list[str]]] = {
        "a": ["href", "rel", "class", "data-user-id", "target", "style", "title"],
        "audio": ["controls", "preload", "src"],
        "blockquote": [],
        "button": ["type", "class", "style"],
        "center": [],
        "code": [],
        "div": ["class", "style"],
        "details": ["class"],
        "h2": [],
        "h4": [],
        "iframe": ["class", "src", "allowfullscreen", "width", "height", "frameborder"],
        "img": ["class", "loading", "src", "width", "height", "usemap", "alt", "style"],
        "map": ["name"],
        "area": ["href", "style", "title", "class"],
        "ol": ["class"],
        "span": ["class", "style", "title"],
        "summary": [],
        "ul": ["class"],
        "*": ["class"],
    }

    # 危险的BBCode标签（不允许）
    FORBIDDEN_TAGS: ClassVar[list[str]] = [
        "script",
        "iframe",
        "object",
        "embed",
        "form",
        "input",
        "textarea",
        "select",
        "option",
        "meta",
        "link",
        "style",
        "title",
        "head",
        "html",
        "body",
    ]

    @classmethod
    def parse_bbcode(cls, text: str) -> str:
        """
        Parse BBCode text and convert it to HTML.
        Based on osu-web's BBCodeFromDB.php implementation.

        Args:
            text: Original text containing BBCode

        Returns:
            Converted HTML string
        """
        if not text:
            return ""

        text = html.escape(text)

        text = cls._parse_imagemap(text)
        text = cls._parse_box(text)
        text = cls._parse_code(text)
        text = cls._parse_list(text)
        text = cls._parse_notice(text)
        text = cls._parse_quote(text)
        text = cls._parse_heading(text)

        # 行内标签处理
        text = cls._parse_audio(text)
        text = cls._parse_bold(text)
        text = cls._parse_centre(text)
        text = cls._parse_inline_code(text)
        text = cls._parse_colour(text)
        text = cls._parse_email(text)
        text = cls._parse_image(text)
        text = cls._parse_italic(text)
        text = cls._parse_size(text)
        text = cls._parse_smilies(text)
        text = cls._parse_spoiler(text)
        text = cls._parse_strike(text)
        text = cls._parse_underline(text)
        text = cls._parse_url(text)
        text = cls._parse_youtube(text)
        text = cls._parse_profile(text)

        # 换行处理
        text = text.replace("\n", "<br />")

        return text

    @classmethod
    def make_tag(
        cls,
        tag: str,
        content: str,
        attributes: dict[str, str] | None = None,
        self_closing: bool = False,
    ) -> str:
        """Generate an HTML tag with optional attributes."""
        attr_str = ""
        if attributes:
            attr_parts = [f'{key}="{html.escape(value)}"' for key, value in attributes.items()]
            attr_str = " " + " ".join(attr_parts)

        if self_closing:
            return f"<{tag}{attr_str} />"
        else:
            return f"<{tag}{attr_str}>{content}</{tag}>"

    @classmethod
    def _parse_audio(cls, text: str) -> str:
        """解析 [audio] 标签"""
        pattern = r"\[audio\]([^\[]+)\[/audio\]"

        def replace_audio(match):
            url = match.group(1).strip()
            return cls.make_tag("audio", "", attributes={"controls": "", "preload": "none", "src": url})

        return re.sub(pattern, replace_audio, text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)

    @classmethod
    def _parse_bold(cls, text: str) -> str:
        """解析 [b] 标签"""
        text = re.sub(r"\[b\]", "<strong>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/b\]", "</strong>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        return text

    @classmethod
    def _parse_box(cls, text: str) -> str:
        """解析 [box] 和 [spoilerbox] 标签"""
        # [box=title] 格式
        pattern = r"\[box=([^\]]+)\](.*?)\[/box\]"

        def replace_box_with_title(match):
            title = match.group(1)
            content = match.group(2)

            icon = cls.make_tag("span", "", attributes={"class": "bbcode-spoilerbox__link-icon"})
            button_content = icon + title
            button = cls.make_tag(
                "button",
                button_content,
                attributes={
                    "type": "button",
                    "class": "js-spoilerbox__link bbcode-spoilerbox__link",
                    "style": (
                        "background: none; border: none; cursor: pointer; padding: 0; text-align: left; width: 100%;"
                    ),
                },
            )
            body = cls.make_tag("div", content, attributes={"class": "js-spoilerbox__body bbcode-spoilerbox__body"})
            return cls.make_tag("div", button + body, attributes={"class": "js-spoilerbox bbcode-spoilerbox"})

        text = re.sub(pattern, replace_box_with_title, text, flags=re.DOTALL | re.IGNORECASE, timeout=REGEX_TIMEOUT)

        # [spoilerbox] 格式
        pattern = r"\[spoilerbox\](.*?)\[/spoilerbox\]"

        def replace_spoilerbox(match):
            content = match.group(1)

            icon = cls.make_tag("span", "", attributes={"class": "bbcode-spoilerbox__link-icon"})
            button_content = icon + "SPOILER"
            button = cls.make_tag(
                "button",
                button_content,
                attributes={
                    "type": "button",
                    "class": "js-spoilerbox__link bbcode-spoilerbox__link",
                    "style": (
                        "background: none; border: none; cursor: pointer; padding: 0; text-align: left; width: 100%;"
                    ),
                },
            )
            body = cls.make_tag("div", content, attributes={"class": "js-spoilerbox__body bbcode-spoilerbox__body"})
            return cls.make_tag("div", button + body, attributes={"class": "js-spoilerbox bbcode-spoilerbox"})

        return re.sub(pattern, replace_spoilerbox, text, flags=re.DOTALL | re.IGNORECASE, timeout=REGEX_TIMEOUT)

    @classmethod
    def _parse_centre(cls, text: str) -> str:
        """解析 [centre] 标签"""
        text = re.sub(r"\[centre\]", "<center>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/centre\]", "</center>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[center\]", "<center>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/center\]", "</center>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        return text

    @classmethod
    def _parse_code(cls, text: str) -> str:
        """解析 [code] 标签"""
        pattern = r"\[code\]\n*(.*?)\n*\[/code\]"

        def replace_code(match):
            return cls.make_tag("pre", match.group(1))

        return re.sub(pattern, replace_code, text, flags=re.DOTALL | re.IGNORECASE, timeout=REGEX_TIMEOUT)

    @classmethod
    def _parse_colour(cls, text: str) -> str:
        """解析 [color] 标签"""
        pattern = r"\[color=([^\]]+)\](.*?)\[/color\]"

        def replace_colour(match):
            return cls.make_tag("span", match.group(2), attributes={"style": f"color:{match.group(1)}"})

        return re.sub(pattern, replace_colour, text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)

    @classmethod
    def _parse_email(cls, text: str) -> str:
        """解析 [email] 标签"""
        # [email]email@example.com[/email]
        pattern1 = r"\[email\]([^\[]+)\[/email\]"

        def replace_email1(match):
            email = match.group(1)
            return cls.make_tag("a", email, attributes={"rel": "nofollow", "href": f"mailto:{email}"})

        text = re.sub(
            pattern1,
            replace_email1,
            text,
            flags=re.IGNORECASE,
            timeout=REGEX_TIMEOUT,
        )

        # [email=email@example.com]text[/email]
        pattern2 = r"\[email=([^\]]+)\](.*?)\[/email\]"

        def replace_email2(match):
            email = match.group(1)
            content = match.group(2)
            return cls.make_tag("a", content, attributes={"rel": "nofollow", "href": f"mailto:{email}"})

        text = re.sub(
            pattern2,
            replace_email2,
            text,
            flags=re.IGNORECASE,
            timeout=REGEX_TIMEOUT,
        )

        return text

    @classmethod
    def _parse_heading(cls, text: str) -> str:
        """解析 [heading] 标签"""
        pattern = r"\[heading\](.*?)\[/heading\]"

        def replace_heading(match):
            return cls.make_tag("h2", match.group(1))

        return re.sub(pattern, replace_heading, text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)

    @classmethod
    def _parse_image(cls, text: str) -> str:
        """解析 [img] 标签"""
        pattern = r"\[img\]([^\[]+)\[/img\]"

        def replace_image(match):
            url = match.group(1).strip()
            # TODO: 可以在这里添加图片代理支持
            # 生成带有懒加载的图片标签
            return cls.make_tag(
                "img",
                "",
                attributes={"loading": "lazy", "src": url, "alt": "", "style": "max-width: 100%; height: auto;"},
                self_closing=True,
            )

        return re.sub(pattern, replace_image, text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)

    @classmethod
    def _parse_imagemap(cls, text: str) -> str:
        """
        Parse [imagemap] tag.
        Use a simple parser to avoid ReDos vulnerabilities.

        Structure:
           [imagemap]
           IMAGE_URL
           X(int) Y(int) WIDTH(int) HEIGHT(int) REDIRECT(url or #) TITLE(optional)
           ...
           [/imagemap]

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#imagemap
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L132
        """
        redirect_pattern = re.compile(r"^(#|https?://[^\s]+|mailto:[^\s]+)$", re.IGNORECASE)

        def replace_imagemap(match: re.Match) -> str:
            content = match.group(1)
            content = html.unescape(content)

            result = ["<div class='imagemap'>"]
            lines = content.strip().splitlines()
            if len(lines) < 2:
                return text
            image_url = lines[0].strip()
            if not HTTP_PATTERN.match(image_url, timeout=REGEX_TIMEOUT):
                return text
            result.append(
                cls.make_tag(
                    "img",
                    "",
                    attributes={"src": image_url, "loading": "lazy", "class": "imagemap__image"},
                    self_closing=True,
                )
            )

            for line in lines[1:]:
                parts = line.strip().split()
                if len(parts) < 5:
                    continue
                x, y, width, height, redirect = parts[:5]
                title = " ".join(parts[5:]) if len(parts) > 5 else ""
                if not redirect_pattern.match(redirect, timeout=REGEX_TIMEOUT):
                    continue

                result.append(
                    cls.make_tag(
                        "a" if redirect == "#" else "span",
                        "",
                        attributes={
                            "href": redirect,
                            "style": f"left: {x}%; top: {y}%; width: {width}%; height: {height}%;",
                            "title": title,
                            "class": "imagemap__link",
                        },
                        self_closing=True,
                    )
                )
            result.append("</div>")
            return "".join(result)

        imagemap_box = re.sub(
            r"\[imagemap\]((?:(?!\[/imagemap\]).)*?)\[/imagemap\]",
            replace_imagemap,
            text,
            flags=re.DOTALL | re.IGNORECASE,
            timeout=REGEX_TIMEOUT,
        )
        return imagemap_box

    @classmethod
    def _parse_italic(cls, text: str) -> str:
        """解析 [i] 标签"""
        text = re.sub(r"\[i\]", "<em>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/i\]", "</em>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        return text

    @classmethod
    def _parse_inline_code(cls, text: str) -> str:
        """解析 [c] 内联代码标签"""
        text = re.sub(r"\[c\]", "<code>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/c\]", "</code>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        return text

    @classmethod
    def _parse_list(cls, text: str) -> str:
        """解析 [list] 标签"""
        # 有序列表
        pattern = r"\[list=1\](.*?)\[/list\]"

        def replace_ordered(match):
            return cls.make_tag("ol", match.group(1))

        text = re.sub(pattern, replace_ordered, text, flags=re.DOTALL | re.IGNORECASE, timeout=REGEX_TIMEOUT)

        # 无序列表
        pattern = r"\[list\](.*?)\[/list\]"

        def replace_unordered(match):
            return cls.make_tag("ol", match.group(1), attributes={"class": "unordered"})

        text = re.sub(
            pattern,
            replace_unordered,
            text,
            flags=re.DOTALL | re.IGNORECASE,
            timeout=REGEX_TIMEOUT,
        )

        # 列表项
        pattern = r"\[\*\]\s*(.*?)(?=\[\*\]|\[/list\]|$)"

        def replace_item(match):
            return cls.make_tag("li", match.group(1))

        text = re.sub(pattern, replace_item, text, flags=re.DOTALL | re.IGNORECASE, timeout=REGEX_TIMEOUT)

        return text

    @classmethod
    def _parse_notice(cls, text: str) -> str:
        """解析 [notice] 标签"""
        pattern = r"\[notice\]\n*(.*?)\n*\[/notice\]"

        def replace_notice(match):
            return cls.make_tag("div", match.group(1), attributes={"class": "well"})

        return re.sub(
            pattern,
            replace_notice,
            text,
            flags=re.DOTALL | re.IGNORECASE,
            timeout=REGEX_TIMEOUT,
        )

    @classmethod
    def _parse_profile(cls, text: str) -> str:
        """解析 [profile] 标签"""
        pattern = r"\[profile(?:=(\d+))?\](.*?)\[/profile\]"

        def replace_profile(match):
            user_id = match.group(1)
            username = match.group(2)

            if user_id:
                return cls.make_tag(
                    "a",
                    username,
                    attributes={"href": f"/users/{user_id}", "class": "user-profile-link", "data-user-id": user_id},
                )
            else:
                return cls.make_tag(
                    "a", f"@{username}", attributes={"href": f"/users/@{username}", "class": "user-profile-link"}
                )

        return re.sub(pattern, replace_profile, text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)

    @classmethod
    def _parse_quote(cls, text: str) -> str:
        """解析 [quote] 标签"""
        # [quote="author"]content[/quote]
        # Handle both raw quotes and HTML-escaped quotes (&quot;)
        pattern1 = r'\[quote=(?:&quot;|")(.+?)(?:&quot;|")\]\s*(.*?)\s*\[/quote\]'

        def replace_quote1(match):
            author = match.group(1)
            content = match.group(2)
            heading = cls.make_tag("h4", f"{author} wrote:")
            return cls.make_tag("blockquote", heading + content)

        text = re.sub(
            pattern1,
            replace_quote1,
            text,
            flags=re.DOTALL | re.IGNORECASE,
            timeout=REGEX_TIMEOUT,
        )

        # [quote]content[/quote]
        pattern2 = r"\[quote\]\s*(.*?)\s*\[/quote\]"

        def replace_quote2(match):
            return cls.make_tag("blockquote", match.group(1))

        text = re.sub(
            pattern2,
            replace_quote2,
            text,
            flags=re.DOTALL | re.IGNORECASE,
            timeout=REGEX_TIMEOUT,
        )

        return text

    @classmethod
    def _parse_size(cls, text: str) -> str:
        """解析 [size] 标签"""

        def replace_size(match):
            size = int(match.group(1))
            # 限制字体大小范围 (30-200%)
            size = max(30, min(200, size))
            return f'<span style="font-size:{size}%;">'

        pattern = r"\[size=(\d+)\]"
        text = re.sub(pattern, replace_size, text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/size\]", "</span>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)

        return text

    @classmethod
    def _parse_smilies(cls, text: str) -> str:
        """解析表情符号标签"""
        # 处理 phpBB 风格的表情符号标记
        pattern = r"<!-- s(.*?) --><img src=\"\{SMILIES_PATH\}/(.*?) /><!-- s\1 -->"
        return re.sub(pattern, r'<img class="smiley" src="/smilies/\2 />', text, timeout=REGEX_TIMEOUT)

    @classmethod
    def _parse_spoiler(cls, text: str) -> str:
        """解析 [spoiler] 标签"""
        text = re.sub(r"\[spoiler\]", "<span class='spoiler'>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/spoiler\]", "</span>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        return text

    @classmethod
    def _parse_strike(cls, text: str) -> str:
        """解析 [s] 和 [strike] 标签"""
        text = re.sub(r"\[s\]", "<del>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/s\]", "</del>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[strike\]", "<del>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/strike\]", "</del>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        return text

    @classmethod
    def _parse_underline(cls, text: str) -> str:
        """解析 [u] 标签"""
        text = re.sub(r"\[u\]", "<u>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/u\]", "</u>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        return text

    @classmethod
    def _parse_url(cls, text: str) -> str:
        """解析 [url] 标签"""
        # [url]http://example.com[/url]
        pattern1 = r"\[url\]([^\[]+)\[/url\]"

        def replace_url1(match):
            url = match.group(1)
            return cls.make_tag("a", url, attributes={"rel": "nofollow", "href": url})

        text = re.sub(pattern1, replace_url1, text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)

        # [url=http://example.com]text[/url]
        pattern2 = r"\[url=([^\]]+)\](.*?)\[/url\]"

        def replace_url2(match):
            url = match.group(1)
            content = match.group(2)
            return cls.make_tag("a", content, attributes={"rel": "nofollow", "href": url})

        text = re.sub(pattern2, replace_url2, text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)

        return text

    @classmethod
    def _parse_youtube(cls, text: str) -> str:
        """解析 [youtube] 标签"""
        pattern = r"\[youtube\]([a-zA-Z0-9_-]{11})\[/youtube\]"

        def replace_youtube(match):
            video_id = match.group(1)
            return cls.make_tag(
                "iframe",
                "",
                attributes={
                    "class": "u-embed-wide u-embed-wide--bbcode",
                    "src": f"https://www.youtube.com/embed/{video_id}?rel=0",
                    "allowfullscreen": "",
                },
            )

        return re.sub(pattern, replace_youtube, text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)

    @classmethod
    def sanitize_html(cls, html_content: str) -> str:
        """
        Clean and sanitize HTML content to prevent XSS attacks.
        Uses bleach to allow only a safe subset of HTML tags and attributes.

        Args:
            html_content: Original HTML content

        Returns:
            Sanitized HTML content
        """
        if not html_content:
            return ""

        css_sanitizer = CSSSanitizer(
            allowed_css_properties=[
                "color",
                "background",
                "background-color",
                "font-size",
                "font-weight",
                "font-style",
                "text-decoration",
                "text-align",
                "left",
                "top",
                "width",
                "height",
                "position",
                "margin",
                "padding",
                "max-width",
                "max-height",
                "aspect-ratio",
                "z-index",
                "display",
                "border",
                "border-none",
                "cursor",
            ]
        )

        cleaned = bleach.clean(
            html_content,
            tags=cls.ALLOWED_TAGS,
            attributes=cls.ALLOWED_ATTRIBUTES,
            protocols=["http", "https", "mailto"],
            css_sanitizer=css_sanitizer,
            strip=True,
        )

        return cleaned

    @classmethod
    def process_userpage_content(cls, raw_content: str, max_length: int = 60000) -> dict[str, str]:
        """
        处理用户页面内容
        基于 osu-web 的处理流程

        Args:
            raw_content: 原始BBCode内容
            max_length: 最大允许长度（字符数，支持多字节字符）

        Returns:
            包含raw和html两个版本的字典
        """
        if not raw_content or not raw_content.strip():
            raise ContentEmptyError()

        content_length = len(raw_content)
        if content_length > max_length:
            raise ContentTooLongError(content_length, max_length)

        content_lower = raw_content.lower()
        for forbidden_tag in cls.FORBIDDEN_TAGS:
            if f"[{forbidden_tag}" in content_lower or f"<{forbidden_tag}" in content_lower:
                raise ForbiddenTagError(forbidden_tag)

        html_content = cls.parse_bbcode(raw_content)
        safe_html = cls.sanitize_html(html_content)

        # 包装在 bbcode 容器中
        final_html = cls.make_tag("div", safe_html, attributes={"class": "bbcode"})

        return {"raw": raw_content, "html": final_html}

    @classmethod
    def validate_bbcode(cls, content: str) -> list[str]:
        """
        验证BBCode语法并返回错误列表
        基于 osu-web 的验证逻辑

        Args:
            content: 要验证的BBCode内容

        Returns:
            错误消息列表
        """
        errors = []

        # 检查内容是否仅包含引用（参考官方逻辑）
        content_without_quotes = cls._remove_block_quotes(content)
        if content.strip() and not content_without_quotes.strip():
            errors.append("Content cannot contain only quotes")

        # 检查标签配对
        tag_stack = []
        tag_pattern = r"\[(/?)(\w+)(?:=[^\]]+)?\]"

        for match in re.finditer(tag_pattern, content, re.IGNORECASE, timeout=REGEX_TIMEOUT):
            is_closing = match.group(1) == "/"
            tag_name = match.group(2).lower()

            if is_closing:
                if not tag_stack:
                    errors.append(f"Closing tag '[/{tag_name}]' without opening tag")
                elif tag_stack[-1] != tag_name:
                    errors.append(f"Mismatched closing tag '[/{tag_name}]', expected '[/{tag_stack[-1]}]'")
                else:
                    tag_stack.pop()
            else:
                # 特殊处理自闭合标签（只有列表项 * 是真正的自闭合）
                if tag_name not in ["*"]:
                    tag_stack.append(tag_name)

        # 检查未关闭的标签
        for unclosed_tag in tag_stack:
            errors.append(f"Unclosed tag '[{unclosed_tag}]'")

        return errors

    @classmethod
    def _remove_block_quotes(cls, text: str) -> str:
        """
        移除引用块（参考 osu-web BBCodeFromDB::removeBlockQuotes）

        Args:
            text: 原始文本

        Returns:
            移除引用后的文本
        """
        # 基于官方实现的简化版本
        # 移除 [quote]...[/quote] 和 [quote=author]...[/quote]
        pattern = r"\[quote(?:=[^\]]+)?\].*?\[/quote\]"
        result = re.sub(pattern, "", text, flags=re.DOTALL | re.IGNORECASE, timeout=REGEX_TIMEOUT)
        return result.strip()

    @classmethod
    def remove_bbcode_tags(cls, text: str) -> str:
        """
        移除所有BBCode标签，只保留纯文本
        用于搜索索引等场景
        基于官方实现
        """
        # 基于官方实现的完整BBCode标签模式
        pattern = (
            r"\[/?(\*|\*:m|audio|b|box|color|spoilerbox|centre|center|code|email|heading|i|img|"
            r"list|list:o|list:u|notice|profile|quote|s|strike|u|spoiler|size|url|youtube|c)"
            r"(?:=.*?)?(:[a-zA-Z0-9]{1,5})?\]"
        )

        return re.sub(pattern, "", text, timeout=REGEX_TIMEOUT)


# 服务实例
bbcode_service = BBCodeService()
