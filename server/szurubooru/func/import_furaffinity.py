"""Normalize private FAAPI helper metadata without guessing tag categories."""

import re
from urllib.parse import quote, unquote, urlsplit

from szurubooru.func import import_description


_MIME_EXTENSIONS = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "avif": "image/avif",
    "heif": "image/heif",
    "heic": "image/heic",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mov": "video/quicktime",
}
_USERNAME = r"[A-Za-z0-9_.~\[\]^`-]{1,64}"


def _positive_id(value):
    if type(value) is int and 0 < value < 10**12:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value):
        number = int(value)
        if number > 0:
            return number
    raise ValueError("Fur Affinity returned an invalid submission ID.")


def _public_url(value, hosts):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or any(
            ord(char) <= 32 or ord(char) == 127 or char in "\"'<>`\\"
            for char in value
        )
    ):
        raise ValueError("Fur Affinity returned an invalid media URL.")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in hosts
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Fur Affinity returned an unsupported media location."
        )
    path = unquote(parsed.path)
    if (
        any(ord(char) < 32 or ord(char) == 127 for char in path)
        or "\\" in path
        or "%" in path
        or re.search(r"(?:^|/)\.\.?(?:/|$)", path)
    ):
        raise ValueError(
            "Fur Affinity returned an unsupported media location."
        )
    return value, path


def original_url(value):
    """Accept only the original download CDN, never the thumbnail CDN."""
    url, path = _public_url(value, ("d.furaffinity.net",))
    if not re.fullmatch(r"/art/[^/]+/[0-9]+/[^/]+", path):
        raise ValueError("Fur Affinity did not return an original file.")
    extension = path.rsplit(".", 1)[-1].lower()
    mime = _MIME_EXTENSIONS.get(extension)
    if not mime:
        raise ValueError(
            "This Fur Affinity submission does not contain a supported image or video."
        )
    return url, mime


def preview_url(value, post_id=None):
    """Validate a public thumbnail without accepting cookies or signed URLs."""
    url, path = _public_url(value, ("t.furaffinity.net",))
    match = re.fullmatch(
        r"/([0-9]{1,12})(?:@[A-Za-z0-9_-]+)?\.(?:jpg|jpeg|png|webp|gif)",
        path,
        re.I,
    )
    if not match or (
        post_id is not None and int(match.group(1)) != _positive_id(post_id)
    ):
        raise ValueError("Fur Affinity returned an unsupported thumbnail.")
    return url


def _owner(author):
    if not isinstance(author, dict):
        raise ValueError(
            "Fur Affinity did not supply a valid submission owner."
        )
    name = author.get("name")
    if not isinstance(name, str) or not re.fullmatch(_USERNAME, name):
        raise ValueError(
            "Fur Affinity did not supply a valid submission owner."
        )
    # Match FAAPI's username_url normalization, including legacy underscores.
    identifier = re.sub(r"[^a-z0-9.~`\[\]^\-]", "", name.lower())
    if not identifier:
        raise ValueError(
            "Fur Affinity did not supply a valid submission owner."
        )
    canonical = (
        "https://www.furaffinity.net/user/" + quote(identifier, safe="") + "/"
    )
    supplied = author.get("url")
    if supplied is not None:
        parsed = urlsplit(supplied) if isinstance(supplied, str) else None
        if not parsed or (
            parsed.scheme != "https"
            or parsed.hostname
            not in ("furaffinity.net", "www.furaffinity.net")
            or parsed.port is not None
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or unquote(parsed.path).rstrip("/") != "/user/" + identifier
        ):
            raise ValueError("Fur Affinity returned an invalid owner profile.")
    return name, canonical


def normalize(payload, post_id):
    post_id = _positive_id(post_id)
    if (
        not isinstance(payload, dict)
        or any(key in payload for key in ("error", "error_code", "detail"))
        or _positive_id(payload.get("id")) != post_id
    ):
        raise ValueError(
            "Fur Affinity did not return the requested submission."
        )
    if any(
        payload.get(key) not in (None, False, "f", "false", "0", 0)
        for key in ("deleted", "hidden")
    ) or payload.get("public") in (False, "f", "false", "0", 0):
        raise ValueError(
            "Deleted, private, or blocked Fur Affinity submissions cannot be imported."
        )
    name, owner_url = _owner(payload.get("author"))
    content_url, mime = original_url(payload.get("file_url"))
    post_url = "https://www.furaffinity.net/view/%d/" % post_id
    description, lossy = import_description.to_markdown(
        payload.get("description"), "furaffinity"
    )
    credit = (
        "Submission owner: ["
        + import_description.plain_to_markdown(name)
        + "]("
        + owner_url
        + ")"
    )
    description = credit + ("\n\n" + description if description else "")
    warnings = [
        "Fur Affinity does not categorize artist and character keywords. "
        "No keywords were imported; the submission owner is credited in the description."
    ]
    if lossy:
        warnings.append(
            "Some Fur Affinity description formatting could not be converted. "
            "Review the Markdown before uploading."
        )
    rating = {"general": "safe", "mature": "sketchy", "adult": "unsafe"}.get(
        str(payload.get("rating", "")).strip().lower()
    )
    if rating is None:
        rating = "unsafe"
        warnings.append(
            "Fur Affinity supplied no recognized rating. Review the rating before uploading."
        )
    media = {
        "contentUrl": content_url,
        "mimeType": mime,
        "postUrl": post_url,
        "flags": [],
    }
    result = {
        "provider": "furaffinity",
        "postUrl": post_url,
        "contentUrl": content_url,
        "mimeType": mime,
        "media": [media],
        "author": {"username": name, "url": owner_url},
        "sources": [post_url],
        "tags": {"artist": [], "character": []},
        "description": description,
        "safety": rating,
        "warnings": warnings,
    }
    if payload.get("thumbnail_url"):
        try:
            poster = preview_url(payload["thumbnail_url"], post_id)
            result["previewUrl"] = poster
            media["previewUrl"] = poster
        except (ValueError, TypeError):
            pass
    return result
