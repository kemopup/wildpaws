"""Normalize Bluesky records; only the post's own stored media is imported."""

import html
import re
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlencode, urlsplit

from szurubooru.func import bluesky_network, import_description


_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_VIDEO_MIMES = {"video/mp4", "video/webm"}
_RESTRICTED_LABELS = {
    "!no-unauthenticated",
    "!hide",
    "!takedown",
    "!suspend",
}


def preview_url(value, did=None, cid=None):
    """Accept display-only Bluesky CDN images, never arbitrary URLs or streams."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or any(ord(c) <= 32 or ord(c) == 127 or c in "\"'<>`\\" for c in value)
    ):
        raise ValueError("Bluesky returned an invalid preview URL.")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Bluesky returned an unsupported preview location.")
    path = unquote(parsed.path)
    if "%" in path or re.search(r"(?:^|/)\.\.?(?:/|$)", path):
        raise ValueError("Bluesky returned an unsupported preview location.")
    if parsed.hostname == "cdn.bsky.app":
        match = re.fullmatch(
            r"/img/(?:feed_thumbnail|feed_fullsize)/plain/([^/]+)/([a-z2-7]+)(?:@(?:jpeg|png|webp))?",
            path,
        )
    elif parsed.hostname == "video.bsky.app":
        match = re.fullmatch(
            r"/watch/([^/]+)/([a-z2-7]+)/thumbnail\.(?:jpg|jpeg|png|webp)",
            path,
        )
    else:
        match = None
    if not match:
        raise ValueError("Bluesky returned an unsupported preview location.")
    actual_did, actual_cid = match.groups()
    bluesky_network.validate_did(actual_did)
    bluesky_network.validate_cid(actual_cid)
    if (did is not None and actual_did != did) or (
        cid is not None and actual_cid != cid
    ):
        raise ValueError("Bluesky returned a preview for different media.")
    return value


def _active_labels(post, record, author):
    result = set()
    for labels in (post.get("labels", []), author.get("labels", [])):
        if not isinstance(labels, list):
            raise ValueError("Bluesky returned invalid moderation metadata.")
        for label in labels:
            if not isinstance(label, dict) or not isinstance(
                label.get("val"), str
            ):
                raise ValueError(
                    "Bluesky returned invalid moderation metadata."
                )
            if label.get("neg") is True:
                continue
            if isinstance(label.get("exp"), str):
                try:
                    expiry = datetime.fromisoformat(
                        label["exp"].replace("Z", "+00:00")
                    )
                    if expiry.tzinfo and expiry <= datetime.now(timezone.utc):
                        continue
                except ValueError:
                    pass
            result.add(label["val"])
    self_labels = record.get("labels")
    if self_labels is not None:
        if not isinstance(self_labels, dict) or not isinstance(
            self_labels.get("values"), list
        ):
            raise ValueError("Bluesky returned invalid moderation metadata.")
        for label in self_labels["values"]:
            if not isinstance(label, dict) or not isinstance(
                label.get("val"), str
            ):
                raise ValueError(
                    "Bluesky returned invalid moderation metadata."
                )
            result.add(label["val"])
    if "!no-unauthenticated" in result:
        raise ValueError(
            "This Bluesky post is restricted to logged-in viewing. Only public-post imports are enabled."
        )
    if result & _RESTRICTED_LABELS:
        raise ValueError(
            "This Bluesky post is restricted or unavailable for public import."
        )
    return result


def _link(label, target):
    if (
        not isinstance(target, str)
        or len(target) > 4096
        or any(ord(c) <= 32 or c == "\\" for c in target)
    ):
        raise ValueError()
    parsed = urlsplit(target)
    if (
        parsed.scheme not in ("https", "http")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError()
    label = html.escape(label, quote=False)
    label = re.sub(
        r"[\\`*_{}\[\]|~#@+]", lambda m: "&#%d;" % ord(m.group()), label
    )
    return "[" + label + "](" + quote(target, safe=":/?#@!$&*+,;=%~_-.") + ")"


def _post_text(record):
    text = record.get("text")
    if not isinstance(text, str) or len(text) > 100000:
        raise ValueError("Bluesky did not supply valid post text.")
    try:
        data = text.encode("utf8")
    except UnicodeError:
        raise ValueError("Bluesky did not supply valid post text.") from None
    facets = record.get("facets", [])
    if not isinstance(facets, list):
        return import_description.plain_to_markdown(text), True
    ranges, simplified = [], False
    for facet in facets:
        try:
            index = facet["index"]
            start, end = index["byteStart"], index["byteEnd"]
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start < end <= len(data)
            ):
                raise ValueError()
            data[:start].decode("utf8")
            label = data[start:end].decode("utf8")
            target = None
            features = facet.get("features")
            if not isinstance(features, list):
                raise ValueError()
            for feature in features:
                if not isinstance(feature, dict):
                    raise ValueError()
                if feature.get("$type") == "app.bsky.richtext.facet#link":
                    target = feature.get("uri")
                    break
                if feature.get("$type") == "app.bsky.richtext.facet#mention":
                    mentioned_did = bluesky_network.validate_did(
                        feature.get("did")
                    )
                    target = "https://bsky.app/profile/" + mentioned_did
                    break
            if target is not None:
                ranges.append((start, end, _link(label, target)))
        except (ValueError, TypeError, KeyError, UnicodeError):
            simplified = True
    output, position = [], 0
    for start, end, replacement in sorted(ranges):
        if start < position:
            simplified = True
            continue
        output.append(_plain_text(data[position:start].decode("utf8")))
        output.append(replacement)
        position = end
    output.append(_plain_text(data[position:].decode("utf8")))
    return "".join(output), simplified


def _plain_text(text):
    # Keep spacing around rich-text facets; the generic converter strips it.
    if not text.strip():
        return text.replace("\r\n", "\n").replace("\r", "\n")
    leading = re.match(r"^\s*", text).group()
    trailing = re.search(r"\s*$", text).group()
    return leading + import_description.plain_to_markdown(text) + trailing


def _blob(value, kind):
    if not isinstance(value, dict) or value.get("$type") != "blob":
        raise ValueError("Bluesky did not supply an original media blob.")
    ref = value.get("ref")
    cid = ref.get("$link") if isinstance(ref, dict) else None
    bluesky_network.validate_cid(cid)
    mime = value.get("mimeType")
    if not isinstance(mime, str) or mime not in (
        _IMAGE_MIMES if kind == "image" else _VIDEO_MIMES
    ):
        raise ValueError(
            "This Bluesky post includes an unsupported image or video format."
        )
    if (
        type(value.get("size")) is not int
        or not 0 < value["size"] <= 2**53 - 1
    ):
        raise ValueError(
            "Bluesky did not supply valid original-file metadata."
        )
    return cid, mime


def _attachments(record, view):
    raw = record.get("embed")
    if (
        isinstance(raw, dict)
        and raw.get("$type") == "app.bsky.embed.recordWithMedia"
    ):
        raw = raw.get("media")
        view = (
            view.get("media", {})
            if isinstance(view, dict)
            and view.get("$type") == "app.bsky.embed.recordWithMedia#view"
            else {}
        )
    if not isinstance(raw, dict):
        raise ValueError(
            "This Bluesky post has no supported attached photos or videos."
        )
    kind = raw.get("$type")
    if kind in ("app.bsky.embed.images", "app.bsky.embed.gallery"):
        gallery = kind.endswith("gallery")
        key = "items" if gallery else "images"
        items = raw.get(key)
        if not isinstance(items, list) or not 1 <= len(items) <= (
            20 if gallery else 4
        ):
            raise ValueError("Bluesky returned invalid attached images.")
        previews = (
            view.get(key, [])
            if isinstance(view, dict) and view.get("$type") == kind + "#view"
            else []
        )
        if not isinstance(previews, list):
            previews = []
        entries = []
        for i, item in enumerate(items):
            if not isinstance(item, dict) or (
                gallery and item.get("$type") != kind + "#image"
            ):
                raise ValueError(
                    "This Bluesky gallery includes unsupported media."
                )
            preview = (
                previews[i]
                if i < len(previews) and isinstance(previews[i], dict)
                else {}
            )
            entries.append(
                (
                    item.get("image"),
                    "image",
                    preview.get("thumbnail" if gallery else "thumb")
                    or preview.get("fullsize"),
                    [],
                )
            )
        return entries
    if kind == "app.bsky.embed.video":
        preview = (
            view
            if isinstance(view, dict) and view.get("$type") == kind + "#view"
            else {}
        )
        return [
            (
                raw.get("video"),
                "video",
                preview.get("thumbnail"),
                ["loop"] if raw.get("presentation") == "gif" else [],
            )
        ]
    raise ValueError(
        "This Bluesky post has no supported attached photos or videos."
    )


def normalize(payload, did, rkey, pds_url, media_index=None):
    did = bluesky_network.validate_did(did)
    rkey = bluesky_network.validate_rkey(rkey)
    pds_url = bluesky_network.validate_pds_url(pds_url)
    uri = "at://%s/app.bsky.feed.post/%s" % (did, rkey)
    posts = payload.get("posts") if isinstance(payload, dict) else None
    if (
        not isinstance(posts, list)
        or len(posts) != 1
        or not isinstance(posts[0], dict)
        or posts[0].get("uri") != uri
    ):
        raise ValueError("Bluesky did not return the requested public post.")
    post = posts[0]
    author, record = post.get("author"), post.get("record")
    if (
        not isinstance(author, dict)
        or author.get("did") != did
        or not isinstance(record, dict)
        or record.get("$type") != "app.bsky.feed.post"
    ):
        raise ValueError("Bluesky did not return the requested public post.")
    labels = _active_labels(post, record, author)
    handle = author.get("handle")
    if not isinstance(handle, str) or handle == "handle.invalid":
        handle = did
    else:
        handle = bluesky_network.validate_actor(handle)
    author_url = "https://bsky.app/profile/" + did
    post_url = author_url + "/post/" + rkey
    text, simplified = _post_text(record)
    credit = _link("@" + handle if handle != did else did, author_url)
    description = "Author: " + credit + ("\n\n" + text if text else "")
    entries = _attachments(record, post.get("embed"))
    if media_index is not None and (
        type(media_index) is not int or not 1 <= media_index <= len(entries)
    ):
        raise ValueError(
            "The requested media item is not available in this Bluesky post."
        )
    media, seen = [], set()
    for i, (blob, kind, poster, flags) in enumerate(entries, 1):
        if media_index is not None and media_index != i:
            continue
        cid, mime = _blob(blob, kind)
        if cid in seen:
            continue
        seen.add(cid)
        item = {
            "contentUrl": pds_url
            + "/xrpc/com.atproto.sync.getBlob?"
            + urlencode({"did": did, "cid": cid}),
            "mimeType": mime,
            "postUrl": post_url + "#media-%d" % i,
            "flags": flags,
        }
        try:
            item["previewUrl"] = preview_url(poster, did, cid)
        except (ValueError, TypeError):
            pass
        media.append(item)
    warnings = [
        "Bluesky labels are not a compatible three-level rating. Review the rating before uploading."
    ]
    if labels:
        warnings.append(
            "Bluesky supplied moderation labels. Review the post and its rating before uploading."
        )
    if simplified:
        warnings.append(
            "Some Bluesky link formatting could not be converted. Review the description before uploading."
        )
    result = {
        "provider": "bluesky",
        "postUrl": post_url if media_index is None else media[0]["postUrl"],
        "contentUrl": media[0]["contentUrl"],
        "mimeType": media[0]["mimeType"],
        "media": media,
        "author": {"username": handle, "url": author_url},
        "sources": [post_url],
        "tags": {"artist": [], "character": []},
        "description": description,
        "safety": "unsafe",
        "warnings": warnings,
        "flags": media[0]["flags"],
    }
    if "previewUrl" in media[0]:
        result["previewUrl"] = media[0]["previewUrl"]
    return result
