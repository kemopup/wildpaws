"""Read public post metadata without loading embedded images or comments."""

import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit


_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class _PostParser(HTMLParser):
    def __init__(self, post_url, post_tag_names):
        super().__init__(convert_charrefs=True)
        self.post_url = post_url
        self.allowed = (
            set(post_tag_names) if post_tag_names is not None else None
        )
        self.stack = []
        self.file_url = None
        self.sources = []
        self.seen_sources = False
        self.rating = None
        self.tags = {"artist": [], "character": []}
        self.saw_sidebar = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in _VOID:
            return
        # HTML permits a following list item to close its predecessor.
        if tag == "li" and self.stack and self.stack[-1]["tag"] == "li":
            self.handle_endtag("li")
        node = {"tag": tag, "attrs": attrs, "text": [], "links": []}
        if tag == "ul" and attrs.get("id") in ("tag-sidebar", "tag-list"):
            self.saw_sidebar = True
        if tag == "li":
            scope = any(
                n["tag"] == "ul"
                and n["attrs"].get("id") in ("tag-sidebar", "tag-list")
                for n in self.stack
            )
            classes = set(attrs.get("class", "").split())
            node["category"] = next(
                (
                    c
                    for c in ("artist", "character")
                    if scope and "tag-type-" + c in classes
                ),
                None,
            )
        self.stack.append(node)

    def handle_data(self, data):
        for node in self.stack:
            if node["tag"] in ("li", "a"):
                node["text"].append(data)

    def handle_endtag(self, tag):
        index = next(
            (
                i
                for i in range(len(self.stack) - 1, -1, -1)
                if self.stack[i]["tag"] == tag
            ),
            None,
        )
        if index is None:
            return
        closing = self.stack[index:]
        self.stack = self.stack[:index]
        for node in reversed(closing):
            text = "".join(node["text"]).strip()
            if node["tag"] == "a":
                target = urljoin(self.post_url, node["attrs"].get("href", ""))
                if text.lower() == "original image" and self.file_url is None:
                    parsed = urlsplit(target)
                    self.file_url = urlunsplit(
                        (
                            parsed.scheme,
                            parsed.netloc,
                            re.sub(r"/{2,}", "/", parsed.path),
                            parsed.query,
                            "",
                        )
                    )
                for parent in self.stack + closing:
                    if parent["tag"] == "li":
                        parent["links"].append(target)
            elif node["tag"] == "li":
                if (
                    re.match(r"^Source\s*:", text, re.I)
                    and not self.seen_sources
                ):
                    self.sources = node["links"]
                    self.seen_sources = True
                match = re.match(r"^Rating\s*:\s*([a-z]+)", text, re.I)
                if match and self.rating is None:
                    self.rating = match.group(1).lower()
                category = node.get("category")
                if category:
                    for target in node["links"]:
                        parsed = urlsplit(target)
                        query = parse_qs(parsed.query)
                        if (
                            parsed.hostname != urlsplit(self.post_url).hostname
                            or query.get("page") != ["post"]
                            or query.get("s") != ["list"]
                        ):
                            continue
                        names = query.get("tags", [])
                        if len(names) != 1 or re.search(r"\s", names[0]):
                            continue
                        name = names[0]
                        if (
                            self.allowed is not None
                            and name not in self.allowed
                        ):
                            continue
                        if name not in self.tags[category]:
                            self.tags[category].append(name)


def parse_details(text, post_url, post_tag_names=None):
    parser = _PostParser(post_url, post_tag_names)
    parser.feed(text)
    parser.close()
    if not parser.file_url or not parser.saw_sidebar or not parser.rating:
        raise ValueError("Unrecognized public post metadata")
    if "/images/" not in urlsplit(parser.file_url).path:
        raise ValueError("Original-file link is not an original image")
    return {
        "contentUrl": parser.file_url,
        "sources": parser.sources,
        "tags": parser.tags,
        "rating": parser.rating,
    }
