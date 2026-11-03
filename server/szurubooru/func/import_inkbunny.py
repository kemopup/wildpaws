"""Normalize Inkbunny submission metadata without importing unclassified tags."""

import re
from urllib.parse import quote, unquote, urlsplit

from szurubooru.func import import_description


_MIME_EXTENSIONS = {
    "image/jpeg": ("jpg", "jpeg"),
    "image/png": ("png",),
    "image/gif": ("gif",),
    "image/webp": ("webp",),
    "image/bmp": ("bmp",),
    "image/avif": ("avif",),
    "image/heif": ("heif",),
    "image/heic": ("heic",),
    "video/mp4": ("mp4",),
    "video/webm": ("webm",),
    "video/quicktime": ("mov",),
}


def _positive_id(value):
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value):
        number = int(value)
        if number > 0:
            return number
    raise ValueError("Inkbunny returned an invalid submission or file ID.")


def _boolean(value, default=False):
    if value is None:
        return default
    if value is True or value in ("t", "true", "1", 1):
        return True
    if value is False or value in ("f", "false", "0", 0):
        return False
    raise ValueError("Inkbunny returned an invalid visibility flag.")


def _media_url(value, original=False):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or any(
            ord(char) <= 32 or ord(char) == 127 or char in "\"'<>`\\"
            for char in value
        )
    ):
        raise ValueError("Inkbunny returned an invalid media URL.")
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or not any(
            host == domain or host.endswith("." + domain)
            for domain in ("inkbunny.net", "ib.metapix.net")
        )
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Inkbunny returned an unsupported media location.")
    path = unquote(parsed.path)
    if (
        "\\" in path
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or re.search(r"(?:^|/)\.\.?(?:/|$)", path)
        or "%" in path
        or not path.startswith(
            ("/files/full/",)
            if original
            else ("/thumbnails/", "/files/preview/")
        )
    ):
        raise ValueError("Inkbunny returned an unsupported media location.")
    return value, path


def preview_url(value):
    """Validate a public, token-free thumbnail before downloading it."""
    url, path = _media_url(value)
    if not path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        raise ValueError("Inkbunny returned an unsupported thumbnail.")
    return url


def _original(item):
    mime = item.get("mimetype")
    if mime == "image/jpg":
        mime = "image/jpeg"
    if not isinstance(mime, str) or mime not in _MIME_EXTENSIONS:
        raise ValueError(
            "This Inkbunny submission includes an unsupported file type. "
            "Import a supported image or video page using its page link."
        )
    url, path = _media_url(item.get("file_url_full"), original=True)
    extension = path.rsplit(".", 1)[-1].lower()
    if extension not in _MIME_EXTENSIONS[mime]:
        raise ValueError("Inkbunny returned an unsupported original file.")
    return url, mime


def _preview(item):
    for suffix in (
        "large_noncustom",
        "medium_noncustom",
        "huge_noncustom",
        "large",
        "medium",
        "huge",
    ):
        try:
            return preview_url(item.get("thumbnail_url_" + suffix))
        except (ValueError, TypeError):
            pass
    return None


def normalize(payload, post_id, page_index=None):
    post_id = _positive_id(post_id)
    if not isinstance(payload, dict) or "error_code" in payload:
        raise ValueError("Inkbunny did not return accessible post metadata.")
    submissions = payload.get("submissions")
    if not isinstance(submissions, list) or len(submissions) != 1:
        raise ValueError(
            "This Inkbunny submission is unavailable to the import session."
        )
    submission = submissions[0]
    if (
        not isinstance(submission, dict)
        or _positive_id(submission.get("submission_id")) != post_id
    ):
        raise ValueError("Inkbunny did not return the requested submission.")
    if (
        _boolean(submission.get("deleted"))
        or _boolean(submission.get("hidden"))
        or not _boolean(submission.get("public"), default=True)
    ):
        raise ValueError(
            "Deleted, private, or blocked Inkbunny submissions cannot be imported."
        )
    username = submission.get("username")
    if not isinstance(username, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,64}", username
    ):
        raise ValueError("Inkbunny did not supply a valid submission owner.")
    post_url = "https://inkbunny.net/s/%d" % post_id
    owner_url = "https://inkbunny.net/" + quote(username, safe="")
    credit = (
        "Submission owner: ["
        + import_description.plain_to_markdown(username)
        + "]("
        + owner_url
        + ")"
    )
    text, lossy = import_description.to_markdown(
        submission.get("description"), "inkbunny"
    )
    description = credit + ("\n\n" + text if text else "")
    files = submission.get("files")
    if not isinstance(files, list) or not files or len(files) > 100:
        raise ValueError("Inkbunny did not return supported attached files.")
    ordered = {}
    file_ids = set()
    for item in files:
        if (
            not isinstance(item, dict)
            or _positive_id(item.get("submission_id")) != post_id
            or _boolean(item.get("deleted"))
        ):
            raise ValueError("Inkbunny returned an unavailable attached file.")
        file_id = _positive_id(item.get("file_id"))
        order = item.get("submission_file_order")
        if isinstance(order, str) and re.fullmatch(r"[0-9]{1,5}", order):
            order = int(order)
        if (
            type(order) is not int
            or order < 0
            or order > 10000
            or order in ordered
            or file_id in file_ids
        ):
            raise ValueError(
                "Inkbunny returned an invalid file display order."
            )
        ordered[order] = item
        file_ids.add(file_id)
    if page_index is not None:
        if type(page_index) is not int or page_index - 1 not in ordered:
            raise ValueError(
                "The requested page is not available in this Inkbunny submission."
            )
        ordered = {page_index - 1: ordered[page_index - 1]}
    media = []
    for order, item in sorted(ordered.items()):
        content_url, mime = _original(item)
        entry = {
            "contentUrl": content_url,
            "mimeType": mime,
            "postUrl": post_url + "-p%d-" % (order + 1),
            "flags": [],
        }
        poster = _preview(item)
        if poster:
            entry["previewUrl"] = poster
        media.append(entry)
    canonical = media[0]["postUrl"] if page_index is not None else post_url
    warnings = [
        "Inkbunny does not categorize artist and character keywords. "
        "No keywords were imported; the submission owner is credited in the description."
    ]
    if lossy:
        warnings.append(
            "Some Inkbunny description formatting could not be converted. "
            "Review the Markdown before uploading."
        )
    rating = {"0": "safe", "1": "sketchy", "2": "unsafe"}.get(
        str(submission.get("rating_id"))
    )
    if rating is None:
        rating = "unsafe"
        warnings.append(
            "Inkbunny supplied no recognized rating. Review the rating before uploading."
        )
    return {
        "provider": "inkbunny",
        "postUrl": canonical,
        "contentUrl": media[0]["contentUrl"],
        "mimeType": media[0]["mimeType"],
        "media": media,
        "author": {"username": username, "url": owner_url},
        "sources": [canonical],
        "tags": {"artist": [], "character": []},
        "description": description,
        "safety": rating,
        "warnings": warnings,
    }
