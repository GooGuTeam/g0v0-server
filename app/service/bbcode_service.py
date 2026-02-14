"""BBCode markup language to HTML.

This module provides functionality to parse BBCode into HTML, sanitize the HTML,
and validate BBCode syntax, based on the implementation from osu-web.

Reference:
    - osu! wiki: https://osu.ppy.sh/wiki/BBCode
    - osu-web: https://github.com/ppy/osu-web/blob/master/app/Libraries/BBCodeFromDB.php
"""

import html
from typing import ClassVar

from app.models.userpage import (
    ContentEmptyError,
    ContentTooLongError,
    ForbiddenTagError,
    MaliciousBBCodeError,
)

import bleach
from bleach.css_sanitizer import CSSSanitizer
import regex as re

HTTP_PATTERN = re.compile(r"^https?://", re.IGNORECASE)
REGEX_TIMEOUT = 5


class BBCodeService:
    """A service for parsing and sanitizing BBCode content.

    Attributes:
        ALLOWED_TAGS: A list of allowed HTML tags in sanitized content.
        ALLOWED_ATTRIBUTES: A dictionary mapping HTML tags to their allowed attributes.
        FORBIDDEN_TAGS: A list of disallowed HTML tags that should not appear in user-generated content.

    Methods:
        parse_bbcode(text: str) -> str:
            Parse BBCode text and convert it to HTML.

        make_tag(tag: str, content: str, attributes: dict[str, str] | None = None, self_closing: bool = False) -> str:
            Generate an HTML tag with optional attributes.

        sanitize_html(html_content: str) -> str:
            Clean and sanitize HTML content to prevent XSS attacks.

        process_userpage_content(raw_content: str, max_length: int = 60000) -> dict[str, str]:
            Process user page content based on osu-web's handling procedure.
    """

    # allowed HTML tags in sanitized content
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
        # imagemap
        "map",
        "area",
        # custom box
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

    # Disallowed tags that should not appear in user-generated content
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

        Args:
            text: Original text containing BBCode

        Returns:
            Converted HTML string

        Reference:
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L354
        """
        if not text:
            return ""

        text = html.escape(text)

        try:
            text = cls._parse_imagemap(text)
            text = cls._parse_box(text)
            text = cls._parse_code(text)
            text = cls._parse_list(text)
            text = cls._parse_notice(text)
            text = cls._parse_quote(text)
            text = cls._parse_heading(text)

            # inline tags
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
        except TimeoutError:
            raise MaliciousBBCodeError("Regular expression processing timed out.")

        # replace newlines with <br />
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
        """
        Parse [audio] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#audio
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L41
        """
        pattern = r"\[audio\]([^\[]+)\[/audio\]"

        def replace_audio(match):
            url = match.group(1).strip()
            return cls.make_tag("audio", "", attributes={"controls": "", "preload": "none", "src": url})

        return re.sub(pattern, replace_audio, text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)

    @classmethod
    def _parse_bold(cls, text: str) -> str:
        """
        Parse [b] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#bold
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L55
        """
        text = re.sub(r"\[b\]", "<strong>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/b\]", "</strong>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        return text

    @classmethod
    def _parse_box(cls, text: str) -> str:
        """
        Parse [box] and [spoilerbox] tags.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#box
            - https://osu.ppy.sh/wiki/en/BBCode#spoilerbox
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L63
        """
        # [box=title] format
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

        # [spoilerbox] format
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
        """
        Parse [centre] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#centre
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L86
        """
        text = re.sub(r"\[centre\]", "<center>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/centre\]", "</center>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[center\]", "<center>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/center\]", "</center>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        return text

    @classmethod
    def _parse_code(cls, text: str) -> str:
        """
        Parse [code] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#code-block
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L94
        """
        pattern = r"\[code\]\n*(.*?)\n*\[/code\]"

        def replace_code(match):
            return cls.make_tag("pre", match.group(1))

        return re.sub(pattern, replace_code, text, flags=re.DOTALL | re.IGNORECASE, timeout=REGEX_TIMEOUT)

    @classmethod
    def _parse_colour(cls, text: str) -> str:
        """
        Parse [color] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#colour
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L103
        """
        pattern = r"\[color=([^\]]+)\](.*?)\[/color\]"

        def replace_colour(match):
            return cls.make_tag("span", match.group(2), attributes={"style": f"color:{match.group(1)}"})

        return re.sub(pattern, replace_colour, text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)

    @classmethod
    def _parse_email(cls, text: str) -> str:
        """
        Parse [email] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#email
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L111
        """
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
        """
        Parse [heading] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#heading-(v1)
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L124
        """
        pattern = r"\[heading\](.*?)\[/heading\]"

        def replace_heading(match):
            return cls.make_tag("h2", match.group(1))

        return re.sub(pattern, replace_heading, text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)

    @classmethod
    def _parse_image(cls, text: str) -> str:
        """
        Parse [img] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#images
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L194
        """
        pattern = r"\[img\]([^\[]+)\[/img\]"

        def replace_image(match):
            url = match.group(1).strip()
            # TODO: image reverse proxy support
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
                        "span" if redirect == "#" else "a",
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
        """
        Parse [i] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#italic
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L186
        """
        text = re.sub(r"\[i\]", "<em>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/i\]", "</em>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        return text

    @classmethod
    def _parse_inline_code(cls, text: str) -> str:
        """
        Parse [c] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#inline-code
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L236
        """
        text = re.sub(r"\[c\]", "<code>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/c\]", "</code>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        return text

    @classmethod
    def _parse_list(cls, text: str) -> str:
        """
        Parse [list] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#formatted-lists
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L244
        """
        # ordedred list
        pattern = r"\[list=1\](.*?)\[/list\]"

        def replace_ordered(match):
            return cls.make_tag("ol", match.group(1))

        text = re.sub(pattern, replace_ordered, text, flags=re.DOTALL | re.IGNORECASE, timeout=REGEX_TIMEOUT)

        # unordered list
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

        # list item
        pattern = r"\[\*\]\s*(.*?)(?=\[\*\]|\[/list\]|$)"

        def replace_item(match):
            return cls.make_tag("li", match.group(1))

        text = re.sub(pattern, replace_item, text, flags=re.DOTALL | re.IGNORECASE, timeout=REGEX_TIMEOUT)

        return text

    @classmethod
    def _parse_notice(cls, text: str) -> str:
        """
        Parse [notice] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#notice
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L264
        """
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
        """
        Parse [profile] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#profile
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L273
        """
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
        """
        Parse [quote] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#quote
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L285
        """
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
        """
        Parse [size] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#font-size
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L326
        """

        def replace_size(match):
            size = int(match.group(1))
            # limit font size range (30-200%)
            size = max(30, min(200, size))
            return cls.make_tag("span", "", attributes={"style": f"font-size:{size}%"})

        pattern = r"\[size=(\d+)\]"
        text = re.sub(pattern, replace_size, text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/size\]", "</span>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)

        return text

    @classmethod
    def _parse_smilies(cls, text: str) -> str:
        """
        Parse smilies.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L296
        """
        # handle phpBB style smilies
        pattern = r"<!-- s(.*?) --><img src=\"\{SMILIES_PATH\}/(.*?) /><!-- s\1 -->"
        return re.sub(pattern, r'<img class="smiley" src="/smilies/\2 />', text, timeout=REGEX_TIMEOUT)

    @classmethod
    def _parse_spoiler(cls, text: str) -> str:
        """
        Parse [spoiler] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#spoiler
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L318
        """
        text = re.sub(r"\[spoiler\]", "<span class='spoiler'>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/spoiler\]", "</span>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        return text

    @classmethod
    def _parse_strike(cls, text: str) -> str:
        """
        Parse [s] and [strike] tags.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#strikethrough
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L301
        """
        text = re.sub(r"\[s\]", "<del>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/s\]", "</del>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[strike\]", "<del>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/strike\]", "</del>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        return text

    @classmethod
    def _parse_underline(cls, text: str) -> str:
        """
        Parse [u] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#underline
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L310
        """
        text = re.sub(r"\[u\]", "<u>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        text = re.sub(r"\[/u\]", "</u>", text, flags=re.IGNORECASE, timeout=REGEX_TIMEOUT)
        return text

    @classmethod
    def _parse_url(cls, text: str) -> str:
        """
        Parse [url] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#url
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L337
        """
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
        """
        Parse [youtube] tag.

        Reference:
            - https://osu.ppy.sh/wiki/en/BBCode#youtube
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L346
        """
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
        Process userpage BBCode content.

        Args:
            raw_content: Raw BBCode content
            max_length: Maximum allowed length

        Returns:
            A dictionary containing both raw and html versions
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

        # Wrap in a container div
        final_html = cls.make_tag("div", safe_html, attributes={"class": "bbcode"})

        return {"raw": raw_content, "html": final_html}

    @classmethod
    def validate_bbcode(cls, content: str) -> list[str]:
        errors = []

        # check for content that is only quotes
        content_without_quotes = cls._remove_block_quotes(content)
        if content.strip() and not content_without_quotes.strip():
            errors.append("Content cannot contain only quotes")

        # check for balanced tags
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
                # Self-closing tags
                if tag_name != "*":
                    tag_stack.append(tag_name)

        # check for any unclosed tags
        for unclosed_tag in tag_stack:
            errors.append(f"Unclosed tag '[{unclosed_tag}]'")

        return errors

    @classmethod
    def _remove_block_quotes(cls, text: str) -> str:
        """
        Remove block quotes.

        Args:
            text: Original text

        Returns:
            Text with block quotes removed

        Reference:
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L456
        """
        # remove [quote]...[/quote] blocks
        pattern = r"\[quote(?:=[^\]]+)?\].*?\[/quote\]"
        result = re.sub(pattern, "", text, flags=re.DOTALL | re.IGNORECASE, timeout=REGEX_TIMEOUT)
        return result.strip()

    @classmethod
    def remove_bbcode_tags(cls, text: str) -> str:
        """
        Remove all BBCode tags, keeping only plain text.
        Used for search indexing etc.

        Reference:
            - https://github.com/ppy/osu-web/blob/15e2d50067c8f5d3dfd2010a79a031efe0dfd10f/app/Libraries/BBCodeFromDB.php#L446
        """
        # remove all BBCode tags
        pattern = (
            r"\[/?(\*|\*:m|audio|b|box|color|spoilerbox|centre|center|code|email|heading|i|img|"
            r"list|list:o|list:u|notice|profile|quote|s|strike|u|spoiler|size|url|youtube|c)"
            r"(?:=.*?)?(:[a-zA-Z0-9]{1,5})?\]"
        )

        return re.sub(pattern, "", text, timeout=REGEX_TIMEOUT)


bbcode_service = BBCodeService()
