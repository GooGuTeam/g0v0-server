"""
BBCode处理服务
基于 osu-web 官方实现的 BBCode 解析器
支持所有 osu! 官方 BBCode 标签
"""

import html
import re
from typing import ClassVar
from urllib.parse import urlparse

from app.models.userpage import (
    ContentEmptyError,
    ContentTooLongError,
    ForbiddenTagError,
    ImageMapTooLargeError,
)

import bleach
from bleach.css_sanitizer import CSSSanitizer


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

    IMAGEMAP_MAX_AREAS: ClassVar[int] = 200
    IMAGEMAP_MAX_BODY_LENGTH: ClassVar[int] = 5000
    IMAGEMAP_MAX_LINE_LENGTH: ClassVar[int] = 500

    @classmethod
    def parse_bbcode(cls, text: str) -> str:
        """
        解析BBCode文本并转换为HTML
        基于 osu-web BBCodeFromDB.php 的实现

        Args:
            text: 包含BBCode的原始文本

        Returns:
            转换后的HTML字符串
        """
        if not text:
            return ""

        # 预处理：转义HTML实体
        text = html.escape(text)

        # 按照 osu-web 的解析顺序进行处理
        # 块级标签处理
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
    def _parse_audio(cls, text: str) -> str:
        """解析 [audio] 标签"""
        pattern = r"\[audio\]([^\[]+)\[/audio\]"

        def replace_audio(match):
            url = match.group(1).strip()
            return f'<audio controls preload="none" src="{url}"></audio>'

        return re.sub(pattern, replace_audio, text, flags=re.IGNORECASE)

    @classmethod
    def _parse_bold(cls, text: str) -> str:
        """解析 [b] 标签"""
        text = re.sub(r"\[b\]", "<strong>", text, flags=re.IGNORECASE)
        text = re.sub(r"\[/b\]", "</strong>", text, flags=re.IGNORECASE)
        return text

    @classmethod
    def _parse_box(cls, text: str) -> str:
        """解析 [box] 和 [spoilerbox] 标签"""
        # [box=title] 格式
        pattern = r"\[box=([^\]]+)\](.*?)\[/box\]"

        def replace_box_with_title(match):
            title = match.group(1)
            content = match.group(2)
            return (
                f"<div class='js-spoilerbox bbcode-spoilerbox'>"
                f"<button type='button' class='js-spoilerbox__link bbcode-spoilerbox__link' "
                f"style='background: none; border: none; cursor: pointer; padding: 0; text-align: left; width: 100%;'>"
                f"<span class='bbcode-spoilerbox__link-icon'></span>{title}</button>"
                f"<div class='js-spoilerbox__body bbcode-spoilerbox__body'>{content}</div></div>"
            )

        text = re.sub(pattern, replace_box_with_title, text, flags=re.DOTALL | re.IGNORECASE)

        # [spoilerbox] 格式
        pattern = r"\[spoilerbox\](.*?)\[/spoilerbox\]"

        def replace_spoilerbox(match):
            content = match.group(1)
            return (
                f"<div class='js-spoilerbox bbcode-spoilerbox'>"
                f"<button type='button' class='js-spoilerbox__link bbcode-spoilerbox__link' "
                f"style='background: none; border: none; cursor: pointer; padding: 0; text-align: left; width: 100%;'>"
                f"<span class='bbcode-spoilerbox__link-icon'></span>SPOILER</button>"
                f"<div class='js-spoilerbox__body bbcode-spoilerbox__body'>{content}</div></div>"
            )

        return re.sub(pattern, replace_spoilerbox, text, flags=re.DOTALL | re.IGNORECASE)

    @classmethod
    def _parse_centre(cls, text: str) -> str:
        """解析 [centre] 标签"""
        text = re.sub(r"\[centre\]", "<center>", text, flags=re.IGNORECASE)
        text = re.sub(r"\[/centre\]", "</center>", text, flags=re.IGNORECASE)
        text = re.sub(r"\[center\]", "<center>", text, flags=re.IGNORECASE)
        text = re.sub(r"\[/center\]", "</center>", text, flags=re.IGNORECASE)
        return text

    @classmethod
    def _parse_code(cls, text: str) -> str:
        """解析 [code] 标签"""
        pattern = r"\[code\]\n*(.*?)\n*\[/code\]"
        return re.sub(pattern, r"<pre>\1</pre>", text, flags=re.DOTALL | re.IGNORECASE)

    @classmethod
    def _parse_colour(cls, text: str) -> str:
        """解析 [color] 标签"""
        pattern = r"\[color=([^\]]+)\](.*?)\[/color\]"
        return re.sub(pattern, r'<span style="color:\1">\2</span>', text, flags=re.IGNORECASE)

    @classmethod
    def _parse_email(cls, text: str) -> str:
        """解析 [email] 标签"""
        # [email]email@example.com[/email]
        pattern1 = r"\[email\]([^\[]+)\[/email\]"
        text = re.sub(pattern1, r'<a rel="nofollow" href="mailto:\1">\1</a>', text, flags=re.IGNORECASE)

        # [email=email@example.com]text[/email]
        pattern2 = r"\[email=([^\]]+)\](.*?)\[/email\]"
        text = re.sub(pattern2, r'<a rel="nofollow" href="mailto:\1">\2</a>', text, flags=re.IGNORECASE)

        return text

    @classmethod
    def _parse_heading(cls, text: str) -> str:
        """解析 [heading] 标签"""
        pattern = r"\[heading\](.*?)\[/heading\]"
        return re.sub(pattern, r"<h2>\1</h2>", text, flags=re.IGNORECASE)

    @classmethod
    def _parse_image(cls, text: str) -> str:
        """解析 [img] 标签"""
        pattern = r"\[img\]([^\[]+)\[/img\]"

        def replace_image(match):
            url = match.group(1).strip()
            # TODO: 可以在这里添加图片代理支持
            # 生成带有懒加载的图片标签
            return f'<img loading="lazy" src="{url}" alt="" style="max-width: 100%; height: auto;" />'

        return re.sub(pattern, replace_image, text, flags=re.IGNORECASE)

    @classmethod
    def _parse_imagemap(cls, text: str) -> str:
        """
        解析 [imagemap] 标签
        基于 osu-web BBCodeFromDB.php 的实现
        """
        pattern = re.compile(r"\[imagemap\](.*?)\[/imagemap\]", flags=re.DOTALL | re.IGNORECASE)

        def replace_imagemap(match: re.Match[str]) -> str:
            content = match.group(1).strip()
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            if len(lines) == 0:
                return ""

            image_url, *raw_links = lines

            if not image_url:
                return ""

            # Protocol validation for image_url
            # Broad exception handling is intentional for security - any parsing error should reject the URL
            try:
                parsed = urlparse(image_url.strip())
                if parsed.scheme.lower() not in ("http", "https"):
                    return ""
            except Exception:  # noqa: S110
                return ""

            # If there are no area definitions, treat as empty (consistent with empty content)
            if not raw_links:
                return ""

            if len(raw_links) > cls.IMAGEMAP_MAX_AREAS:
                raise ImageMapTooLargeError(max_areas=cls.IMAGEMAP_MAX_AREAS)

            links = []
            total_length = 0
            for line in raw_links:
                line_length = len(line)
                if line_length > cls.IMAGEMAP_MAX_LINE_LENGTH:
                    raise ImageMapTooLargeError(max_line_length=cls.IMAGEMAP_MAX_LINE_LENGTH)

                total_length += line_length
                if total_length > cls.IMAGEMAP_MAX_BODY_LENGTH:
                    raise ImageMapTooLargeError(max_length=cls.IMAGEMAP_MAX_BODY_LENGTH)

                parts = line.split(" ", 5)
                if len(parts) < 5:
                    continue

                try:
                    left = float(parts[0])
                    top = float(parts[1])
                    width = float(parts[2])
                    height = float(parts[3])
                    href = parts[4]
                except (ValueError, IndexError):
                    continue

                if not (
                    0 <= left < 100
                    and 0 <= top < 100
                    and 0 < width <= 100
                    and 0 < height <= 100
                    and left + width <= 100
                    and top + height <= 100
                ):
                    continue

                if href != "#":
                    # Broad exception handling is intentional for security - any parsing error should reject the URL
                    try:
                        parsed = urlparse(href.strip())
                        if parsed.scheme.lower() not in ("http", "https"):
                            continue
                    except Exception:  # noqa: S110
                        continue

                title = parts[5] if len(parts) > 5 else ""
                style = f"left: {left}%; top: {top}%; width: {width}%; height: {height}%;"
                href_escaped = html.escape(href, quote=True)
                title_escaped = html.escape(title, quote=True)

                if href == "#":
                    links.append(
                        f'<span class="imagemap__link" style="{style}" title="{title_escaped}"></span>'
                    )
                else:
                    links.append(
                        f'<a class="imagemap__link" href="{href_escaped}" style="{style}" '
                        f'title="{title_escaped}"></a>'
                    )

            if not links:
                image_url_escaped = html.escape(image_url, quote=True)
                return f'<img loading="lazy" src="{image_url_escaped}" alt="" style="max-width: 100%; height: auto;" />'

            links_html = "".join(links)
            image_url_escaped = html.escape(image_url, quote=True)
            image_html = (
                f'<img class="imagemap__image" loading="lazy" src="{image_url_escaped}" alt="" '
                f'style="max-width: 100%; height: auto;" />'
            )
            return f'<div class="imagemap">{image_html}{links_html}</div>'

        return pattern.sub(replace_imagemap, text)

    @classmethod
    def _parse_italic(cls, text: str) -> str:
        """解析 [i] 标签"""
        text = re.sub(r"\[i\]", "<em>", text, flags=re.IGNORECASE)
        text = re.sub(r"\[/i\]", "</em>", text, flags=re.IGNORECASE)
        return text

    @classmethod
    def _parse_inline_code(cls, text: str) -> str:
        """解析 [c] 内联代码标签"""
        text = re.sub(r"\[c\]", "<code>", text, flags=re.IGNORECASE)
        text = re.sub(r"\[/c\]", "</code>", text, flags=re.IGNORECASE)
        return text

    @classmethod
    def _parse_list(cls, text: str) -> str:
        """解析 [list] 标签"""
        # 有序列表
        pattern = r"\[list=1\](.*?)\[/list\]"
        text = re.sub(pattern, r"<ol>\1</ol>", text, flags=re.DOTALL | re.IGNORECASE)

        # 无序列表
        pattern = r"\[list\](.*?)\[/list\]"
        text = re.sub(pattern, r"<ol class='unordered'>\1</ol>", text, flags=re.DOTALL | re.IGNORECASE)

        # 列表项
        pattern = r"\[\*\]\s*(.*?)(?=\[\*\]|\[/list\]|$)"
        text = re.sub(pattern, r"<li>\1</li>", text, flags=re.DOTALL | re.IGNORECASE)

        return text

    @classmethod
    def _parse_notice(cls, text: str) -> str:
        """解析 [notice] 标签"""
        pattern = r"\[notice\]\n*(.*?)\n*\[/notice\]"
        return re.sub(pattern, r'<div class="well">\1</div>', text, flags=re.DOTALL | re.IGNORECASE)

    @classmethod
    def _parse_profile(cls, text: str) -> str:
        """解析 [profile] 标签"""
        pattern = r"\[profile(?:=(\d+))?\](.*?)\[/profile\]"

        def replace_profile(match):
            user_id = match.group(1)
            username = match.group(2)

            if user_id:
                return f'<a href="/users/{user_id}" class="user-profile-link" data-user-id="{user_id}">{username}</a>'
            else:
                return f'<a href="/users/@{username}" class="user-profile-link">@{username}</a>'

        return re.sub(pattern, replace_profile, text, flags=re.IGNORECASE)

    @classmethod
    def _parse_quote(cls, text: str) -> str:
        """解析 [quote] 标签"""
        # [quote="author"]content[/quote]
        pattern1 = r'\[quote="([^"]+)"\]\s*(.*?)\s*\[/quote\]'
        text = re.sub(pattern1, r"<blockquote><h4>\1 wrote:</h4>\2</blockquote>", text, flags=re.DOTALL | re.IGNORECASE)

        # [quote]content[/quote]
        pattern2 = r"\[quote\]\s*(.*?)\s*\[/quote\]"
        text = re.sub(pattern2, r"<blockquote>\1</blockquote>", text, flags=re.DOTALL | re.IGNORECASE)

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
        text = re.sub(pattern, replace_size, text, flags=re.IGNORECASE)
        text = re.sub(r"\[/size\]", "</span>", text, flags=re.IGNORECASE)

        return text

    @classmethod
    def _parse_smilies(cls, text: str) -> str:
        """解析表情符号标签"""
        # 处理 phpBB 风格的表情符号标记
        pattern = r"<!-- s(.*?) --><img src=\"\{SMILIES_PATH\}/(.*?) /><!-- s\1 -->"
        return re.sub(pattern, r'<img class="smiley" src="/smilies/\2 />', text)

    @classmethod
    def _parse_spoiler(cls, text: str) -> str:
        """解析 [spoiler] 标签"""
        text = re.sub(r"\[spoiler\]", "<span class='spoiler'>", text, flags=re.IGNORECASE)
        text = re.sub(r"\[/spoiler\]", "</span>", text, flags=re.IGNORECASE)
        return text

    @classmethod
    def _parse_strike(cls, text: str) -> str:
        """解析 [s] 和 [strike] 标签"""
        text = re.sub(r"\[s\]", "<del>", text, flags=re.IGNORECASE)
        text = re.sub(r"\[/s\]", "</del>", text, flags=re.IGNORECASE)
        text = re.sub(r"\[strike\]", "<del>", text, flags=re.IGNORECASE)
        text = re.sub(r"\[/strike\]", "</del>", text, flags=re.IGNORECASE)
        return text

    @classmethod
    def _parse_underline(cls, text: str) -> str:
        """解析 [u] 标签"""
        text = re.sub(r"\[u\]", "<u>", text, flags=re.IGNORECASE)
        text = re.sub(r"\[/u\]", "</u>", text, flags=re.IGNORECASE)
        return text

    @classmethod
    def _parse_url(cls, text: str) -> str:
        """解析 [url] 标签"""
        # [url]http://example.com[/url]
        pattern1 = r"\[url\]([^\[]+)\[/url\]"
        text = re.sub(pattern1, r'<a rel="nofollow" href="\1">\1</a>', text, flags=re.IGNORECASE)

        # [url=http://example.com]text[/url]
        pattern2 = r"\[url=([^\]]+)\](.*?)\[/url\]"
        text = re.sub(pattern2, r'<a rel="nofollow" href="\1">\2</a>', text, flags=re.IGNORECASE)

        return text

    @classmethod
    def _parse_youtube(cls, text: str) -> str:
        """解析 [youtube] 标签"""
        pattern = r"\[youtube\]([a-zA-Z0-9_-]{11})\[/youtube\]"

        def replace_youtube(match):
            video_id = match.group(1)
            return (
                f"<iframe class='u-embed-wide u-embed-wide--bbcode' "
                f"src='https://www.youtube.com/embed/{video_id}?rel=0' allowfullscreen></iframe>"
            )

        return re.sub(pattern, replace_youtube, text, flags=re.IGNORECASE)

    @classmethod
    def sanitize_html(cls, html_content: str) -> str:
        """
        清理HTML内容，移除危险标签和属性
        基于 osu-web 的安全策略

        Args:
            html_content: 要清理的HTML内容

        Returns:
            清理后的安全HTML
        """
        if not html_content:
            return ""

        # 使用bleach清理HTML，配置CSS清理器以允许安全的样式
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
        # 检查内容是否为空或仅包含空白字符
        if not raw_content or not raw_content.strip():
            raise ContentEmptyError()

        # 检查长度限制（Python的len()本身支持Unicode字符计数）
        content_length = len(raw_content)
        if content_length > max_length:
            raise ContentTooLongError(content_length, max_length)

        # 检查是否包含禁止的标签
        content_lower = raw_content.lower()
        for forbidden_tag in cls.FORBIDDEN_TAGS:
            if f"[{forbidden_tag}" in content_lower or f"<{forbidden_tag}" in content_lower:
                raise ForbiddenTagError(forbidden_tag)

        # 转换BBCode为HTML
        html_content = cls.parse_bbcode(raw_content)

        # 清理HTML
        safe_html = cls.sanitize_html(html_content)

        # 包装在 bbcode 容器中
        final_html = f'<div class="bbcode">{safe_html}</div>'

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

        for match in re.finditer(tag_pattern, content, re.IGNORECASE):
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
        result = re.sub(pattern, "", text, flags=re.DOTALL | re.IGNORECASE)
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
            r"(=.*?(?=:))?(:[a-zA-Z0-9]{1,5})?\]"
        )

        return re.sub(pattern, "", text)


# 服务实例
bbcode_service = BBCodeService()
