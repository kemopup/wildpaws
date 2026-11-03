"""Conservative DText/BBCode-to-Markdown conversion for imported drafts.

This is not a full DText renderer. Unsupported presentation is kept readable
and reported to the uploader rather than converted to untrusted HTML.
"""

import html
import re
from typing import Any, Tuple
from urllib.parse import quote, urlencode, urljoin, urlsplit


_TAG = r"(?<!\[)\[(/?)([a-z][a-z0-9]*)([=,][^\]\r\n]*)?\](?!\])"
_TOKEN = re.compile(r"\\[\\`*_{}\[\]]|`+|" + _TAG, re.I)
_INLINE = re.compile(
    r'"([^"\n]+)":(?:\[([^\]\n]+)\]|([^\s<>]+))'
    r"|\[\[([^\]\n]+)\]\]|\{\{([^}\n]+)\}\}"
    r"|\b(post|thumb)\s+#([0-9]+)\b"
    r"|<?https?://[^\s<>]+>?",
    re.I,
)
_INKBUNNY_INLINE = re.compile(
    _INLINE.pattern + r"|(?<![\w#])#(?P<ibthumb>[SMLH])(?P<ibid>[0-9]{1,12})"
    r"(?:,(?P<ibpage>[0-9]{1,5}))?(?![\w,])"
    r"|(?<![\w@])(?:@|ib!)(?P<ibuser>[A-Za-z0-9_-]{1,64})(?![A-Za-z0-9_-])",
    re.I,
)


def _escape(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"([\\`*_{}\[\]|])", r"\\\1", text)
    # The local renderer treats these as local entity shortcuts even in labels.
    for char, entity in (("#", "&#35;"), ("@", "&#64;"), ("+", "&#43;")):
        text = text.replace(char, entity)
    text = re.sub(r"(?m)^(\s*)([-=]|[0-9]+[.)])(?=\s|$)", r"\1\\\2", text)
    return text


def _fenced(body: str) -> str:
    runs = [len(match.group()) for match in re.finditer(r"`+", body)]
    fence = "`" * max(3, max(runs, default=0) + 1)
    return "\n\n" + fence + "\n" + body.strip("\n") + "\n" + fence + "\n\n"


class _Converter:
    def __init__(self, provider: str):
        self.inkbunny = provider == "inkbunny"
        self.base = {
            "e621": "https://e621.net",
            "e6ai": "https://e6ai.net",
            "rule34": "https://rule34.xxx",
            "tbib": "https://tbib.org",
            "gelbooru": "https://gelbooru.com",
            "inkbunny": "https://inkbunny.net",
        }.get(provider, "https://e621.net")
        self.lossy = False

    def inkbunny_submission(self, value: str):
        match = re.fullmatch(
            r"\s*([0-9]{1,12})(?:\s*,\s*([0-9]{1,5}))?\s*", value
        )
        if not match or int(match[1]) <= 0:
            return None
        post_id = str(int(match[1]))
        path = "/s/" + post_id
        label = "Inkbunny submission " + post_id
        if match[2] is not None:
            page = int(match[2])
            if page <= 0:
                return None
            path += "-p%d-" % page
            label += ", page %d" % page
        return self.link(label, path)

    def inkbunny_embed(self, tag: str, value: str):
        if tag.endswith("thumb"):
            return self.inkbunny_submission(value)
        username = value.strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", username):
            return self.link(username, "/" + quote(username, safe=""))
        return None

    def link(self, label: str, target: str) -> str:
        try:
            target = urljoin(self.base + "/", target.strip())
            parsed = urlsplit(target)
            safe = (
                parsed.scheme in ("http", "https")
                and bool(parsed.hostname)
                and not parsed.username
                and not parsed.password
                and not any(ord(c) < 32 for c in target)
            )
        except ValueError:
            safe = False
        if not safe:
            self.lossy = True
            return _escape(label) + " (" + _escape(target) + ")"
        target = quote(target, safe=":/?#@!$&'*+,;=%~_-.")
        return "[" + _escape(label) + "](" + target + ")"

    def inline(self, text: str) -> str:
        output = []
        start = 0
        pattern = _INKBUNNY_INLINE if self.inkbunny else _INLINE
        for match in pattern.finditer(text):
            output.append(_escape(text[start : match.start()]))
            if self.inkbunny and match.group("ibthumb") is not None:
                value = match.group("ibid")
                if match.group("ibpage"):
                    value += "," + match.group("ibpage")
                link = self.inkbunny_submission(value)
                if link is None:
                    self.lossy = True
                output.append(link or _escape(match.group()))
                start = match.end()
                continue
            if self.inkbunny and match.group("ibuser") is not None:
                username = match.group("ibuser")
                output.append(self.link(username, "/" + username))
                start = match.end()
                continue
            label, bracket_url, bare_url, wiki, search, entity, post_id = (
                match.groups()[:7]
            )
            if label is not None:
                output.append(self.link(label, bracket_url or bare_url))
            elif wiki is not None:
                target, _, label = wiki.partition("|")
                if target.startswith("#"):
                    self.lossy = True
                    output.append(_escape(label or target))
                else:
                    title, _, anchor = target.partition("#")
                    path = "/wiki_pages/show_or_new?" + urlencode(
                        {"title": title.replace(" ", "_")}
                    )
                    if anchor:
                        path += "#" + quote(anchor)
                    output.append(self.link(label or target, path))
            elif search is not None:
                output.append(
                    self.link(search, "/posts?" + urlencode({"tags": search}))
                )
            elif entity is not None:
                output.append(
                    self.link("post #" + post_id, "/posts/" + post_id)
                )
            else:
                url = match.group().strip("<>")
                # Ordinary sentence punctuation is not part of a bare URL.
                suffix = ""
                if not match.group().startswith("<"):
                    url = url.rstrip(".,;!?")
                    while url.endswith(")") and url.count(")") > url.count(
                        "("
                    ):
                        url = url[:-1]
                    suffix = match.group()[len(url) :]
                output.append(self.link(url, url) + _escape(suffix))
            start = match.end()
        output.append(_escape(text[start:]))
        return "".join(output)

    def plain(self, text: str) -> str:
        lines = []
        for line in text.split("\n"):
            heading = re.match(r"^h([1-6])\.\s+(.*)$", line)
            bullet = re.match(r"^(\*+)\s+(.*)$", line)
            if heading:
                lines.append(
                    "#" * int(heading[1]) + " " + self.inline(heading[2])
                )
            elif bullet:
                lines.append(
                    "  " * (len(bullet[1]) - 1) + "- " + self.inline(bullet[2])
                )
            else:
                lines.append(self.inline(line))
        return "\n".join(lines)

    def parse(
        self, text: str, start: int = 0, closing: str = "", depth: int = 0
    ):
        if depth > 32:
            self.lossy = True
            return _escape(text[start:]), len(text), False
        output = []
        position = start
        for_match = _TOKEN.search(text, position)
        while for_match:
            match = for_match
            output.append(self.plain(text[position : match.start()]))
            token = match.group()
            position = match.end()
            if token.startswith("\\"):
                output.append(_escape(token[1:]))
            elif token.startswith("`"):
                end = re.search(
                    r"(?<!\\)" + re.escape(token) + r"(?!`)", text[position:]
                )
                if end:
                    body = text[position : position + end.start()]
                    output.append(token + body + token)
                    position += end.end()
                else:
                    output.append(_escape(token))
            else:
                is_close, tag, argument = re.fullmatch(
                    _TAG, token, re.I
                ).groups()
                tag = tag.lower()
                argument = (argument or "").lstrip("=,")
                if is_close:
                    if tag == closing:
                        return "".join(output), position, True
                    self.lossy = True
                    output.append(_escape(token))
                elif tag in ("code", "nodtext", "noparse", "nobbc"):
                    end = re.search(
                        r"\[/" + tag + r"\]", text[position:], re.I
                    )
                    if end:
                        body = text[position : position + end.start()]
                        output.append(
                            _fenced(body) if tag == "code" else _escape(body)
                        )
                        position += end.end()
                    else:
                        self.lossy = True
                        output.append(_escape(token))
                elif self.inkbunny and tag in (
                    "thumb",
                    "smallthumb",
                    "mediumthumb",
                    "largethumb",
                    "hugethumb",
                    "icon",
                    "iconname",
                    "name",
                ):
                    end = re.search(
                        r"\[/" + tag + r"\]", text[position:], re.I
                    )
                    if end:
                        body = text[position : position + end.start()]
                        replacement = self.inkbunny_embed(tag, body)
                        block_end = position + end.end()
                        if argument or replacement is None:
                            self.lossy = True
                            output.append(
                                _escape(text[match.start() : block_end])
                            )
                        else:
                            output.append(replacement)
                        position = block_end
                    else:
                        self.lossy = True
                        output.append(_escape(token))
                elif tag in (
                    "b",
                    "i",
                    "s",
                    "strike",
                    "quote",
                    "section",
                    "url",
                    "spoiler",
                    "small",
                    "u",
                    "sup",
                    "sub",
                    "color",
                    "center",
                ) or (self.inkbunny and tag in ("q", "t", "left", "right")):
                    body_start = position
                    body, position, found = self.parse(
                        text, position, tag, depth + 1
                    )
                    if not found:
                        self.lossy = True
                        output.append(_escape(token) + body)
                    elif tag in ("b", "i", "s", "strike") or (
                        self.inkbunny and tag == "t"
                    ):
                        marker = {
                            "b": "**",
                            "i": "*",
                            "s": "~~",
                            "strike": "~~",
                            "t": "**",
                        }[tag]
                        output.append(marker + body + marker)
                    elif tag == "quote" or (self.inkbunny and tag == "q"):
                        if argument:
                            if self.inkbunny and tag == "q":
                                body = _escape(argument) + " wrote:\n" + body
                            else:
                                self.lossy = True
                        output.append(
                            "\n\n"
                            + "\n".join(
                                "> " + line
                                for line in body.strip("\n").split("\n")
                            )
                            + "\n\n"
                        )
                    elif tag == "url":
                        raw_body = text[body_start : position - len("[/url]")]
                        output.append(
                            self.link(raw_body, argument or raw_body)
                        )
                    elif tag in ("spoiler", "small"):
                        output.append(
                            "[" + tag + "]" + body + "[/" + tag + "]"
                        )
                    else:
                        self.lossy = True
                        if tag == "section":
                            title = re.sub(r"^expanded=", "", argument)
                            output.append(
                                "\n\n"
                                + (
                                    "**" + self.inline(title) + "**\n\n"
                                    if title
                                    else ""
                                )
                                + body
                                + "\n\n"
                            )
                        else:
                            output.append(body)
                else:
                    self.lossy = True
                    output.append(_escape(token))
            for_match = _TOKEN.search(text, position)
        output.append(self.plain(text[position:]))
        return "".join(output), len(text), False


def to_markdown(text: Any, provider: str) -> Tuple[str, bool]:
    """Return converted Markdown and whether unsupported presentation was lost."""
    if not isinstance(text, str) or not text:
        return "", False
    converter = _Converter(provider)
    markdown, _position, _closed = converter.parse(
        text.replace("\r\n", "\n").replace("\r", "\n")
    )
    return markdown.strip(), converter.lossy


def plain_to_markdown(text: Any) -> str:
    """Keep plain post text literal while retaining HTTP(S) links and newlines."""
    if not isinstance(text, str):
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    result = []
    position = 0
    converter = _Converter("x")

    def literal(value):
        # Backslash escapes become brackets again before local DText wrappers
        # run. Entities keep plain X text from activating those wrappers.
        return (
            _escape(value)
            .replace("\\[", "&#91;")
            .replace("\\]", "&#93;")
            .replace("~", "&#126;")
        )

    for match in re.finditer(r"https?://[^\s<>]+", text):
        result.append(literal(text[position : match.start()]))
        url = match.group().rstrip(".,;!?")
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1]
        result.append(
            converter.link(url, url)
            .replace("\\[", "&#91;")
            .replace("\\]", "&#93;")
        )
        result.append(literal(match.group()[len(url) :]))
        position = match.end()
    result.append(literal(text[position:]))
    return "".join(result).strip()
