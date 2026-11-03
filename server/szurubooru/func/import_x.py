"""Normalize FxTwitter metadata; quoted/external media and hashtags are excluded."""

import re
from urllib.parse import parse_qs, urlencode, urlsplit

from szurubooru.func import import_description


def _split_media_url(value, host):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        raise ValueError("FxTwitter returned an invalid media URL.")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != host
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("FxTwitter returned an unsupported media location.")
    return parsed


def _photo(value):
    parsed = _split_media_url(value, "pbs.twimg.com")
    match = re.fullmatch(
        r"/media/([A-Za-z0-9_-]+)(?:\.(jpg|jpeg|png|webp))?(?::(?:orig|large|medium|small|thumb))?",
        parsed.path,
        re.I,
    )
    if not match:
        raise ValueError("FxTwitter did not return an original photo.")
    query = parse_qs(parsed.query)
    formats = query.get("format", [match.group(2) or "jpg"])
    if len(formats) != 1 or formats[0].lower() not in (
        "jpg",
        "jpeg",
        "png",
        "webp",
    ):
        raise ValueError("The X photo format is not supported.")
    format = formats[0].lower()
    return (
        "https://pbs.twimg.com/media/"
        + match.group(1)
        + "?"
        + urlencode({"format": format, "name": "orig"}),
        "image/" + ("jpeg" if format in ("jpg", "jpeg") else format),
    )


def _video_url(value):
    parsed = _split_media_url(value, "video.twimg.com")
    if (
        not parsed.path.startswith(
            ("/ext_tw_video/", "/amplify_video/", "/tweet_video/")
        )
        or not parsed.path.lower().endswith((".mp4", ".webm"))
        or re.search(r"(?:^|/)\.\.?(?:/|$)|%2e|%2f", parsed.path, re.I)
    ):
        raise ValueError(
            "FxTwitter did not return a supported direct video file."
        )
    # Preserve only the public CDN's variant parameter, never unrelated tracking.
    query = parse_qs(parsed.query)
    suffix = (
        "?" + urlencode({"tag": query["tag"][0]})
        if len(query.get("tag", [])) == 1
        else ""
    )
    return "https://video.twimg.com" + parsed.path + suffix


def _video(item):
    candidates = []
    variants = []
    for key in ("variants", "formats"):
        if isinstance(item.get(key), list):
            variants.extend(item[key])
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        format = (
            variant.get("content_type")
            or variant.get("format")
            or variant.get("container")
        )
        if format not in ("video/mp4", "mp4") or variant.get("codec") in (
            "hevc",
            "h265",
        ):
            continue
        try:
            url = _video_url(variant.get("url"))
            bitrate = float(variant.get("bitrate") or 0)
        except (ValueError, TypeError):
            continue
        if urlsplit(url).path.lower().endswith(".mp4"):
            candidates.append((bitrate, url))
    if candidates:
        url = max(candidates, key=lambda candidate: candidate[0])[1]
    else:
        url = _video_url(item.get("url"))
        format = item.get("format")
        if format and format not in ("video/mp4", "video/webm"):
            raise ValueError("FxTwitter returned an unsupported video format.")
    mime = (
        "video/webm"
        if urlsplit(url).path.lower().endswith(".webm")
        else "video/mp4"
    )
    return url, mime


def _poster(value):
    try:
        parsed = _split_media_url(value, "pbs.twimg.com")
        if re.fullmatch(
            r"/(?:ext_tw_video_thumb|amplify_video_thumb|tweet_video_thumb)/[A-Za-z0-9_./-]+\.(?:jpg|jpeg|png|webp)",
            parsed.path,
            re.I,
        ) and not re.search(r"(?:^|/)\.\.?(?:/|$)", parsed.path):
            return "https://pbs.twimg.com" + parsed.path
    except (ValueError, TypeError):
        pass
    return None


def normalize(payload, post_id, photo_index=None):
    if not isinstance(payload, dict) or payload.get("code") != 200:
        code = payload.get("code") if isinstance(payload, dict) else None
        if not isinstance(code, int):
            code = None
        messages = {
            401: "This X post is protected or unavailable to FxTwitter.",
            404: "This X post was not found by FxTwitter.",
            500: "FxTwitter could not fetch this post. Please try again later.",
        }
        raise ValueError(
            messages.get(code, "FxTwitter did not return valid post metadata.")
        )
    tweet = payload.get("tweet")
    if not isinstance(tweet, dict) or str(tweet.get("id")) != str(post_id):
        raise ValueError("FxTwitter did not return the requested X post.")
    author = tweet.get("author") or {}
    username = author.get("screen_name") if isinstance(author, dict) else None
    if not isinstance(username, str) or not re.fullmatch(
        r"[A-Za-z0-9_]{1,15}", username
    ):
        raise ValueError("FxTwitter did not supply a valid post author.")
    post_url = "https://x.com/%s/status/%s" % (username, post_id)
    author_url = "https://x.com/" + username
    credit = (
        "Author: ["
        + import_description.plain_to_markdown("@" + username)
        + "]("
        + author_url
        + ")"
    )
    text = import_description.plain_to_markdown(tweet.get("text"))
    description = credit + ("\n\n" + text if text else "")
    raw = tweet.get("media") or {}
    if not isinstance(raw, dict):
        raise ValueError("FxTwitter did not return attached media.")
    items = raw.get("all")
    if not isinstance(items, list) or not items:
        items = []
        for key, default in (("photos", "photo"), ("videos", "video")):
            if isinstance(raw.get(key), list):
                items.extend(
                    dict(item, type=item.get("type", default))
                    for item in raw[key]
                    if isinstance(item, dict)
                )
    if not items or len(items) > 16:
        raise ValueError(
            "This X post has no supported attached photos or videos."
        )
    media = []
    by_position = {}
    seen = set()
    for position, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError("FxTwitter returned invalid attached media.")
        media_type = item.get("type")
        if media_type == "photo":
            content_url, mime = _photo(item.get("url"))
        elif media_type in ("video", "gif"):
            content_url, mime = _video(item)
        else:
            raise ValueError(
                "This X post includes unsupported attached media."
            )
        result = {
            "contentUrl": content_url,
            "mimeType": mime,
            "postUrl": post_url
            + ("/photo/" if media_type == "photo" else "/video/")
            + str(position),
            "flags": ["loop"] if media_type == "gif" else [],
        }
        if media_type in ("video", "gif"):
            poster = _poster(item.get("thumbnail_url"))
            if poster:
                result["previewUrl"] = poster
        by_position[position] = result
        if content_url in seen:
            continue
        seen.add(content_url)
        media.append(result)
    if photo_index is not None:
        if type(photo_index) is not int or photo_index not in by_position:
            raise ValueError(
                "The requested media item is not available in this X post."
            )
        media = [by_position[photo_index]]
    canonical = media[0]["postUrl"] if photo_index is not None else post_url
    return {
        "provider": "x",
        "postUrl": canonical,
        "contentUrl": media[0]["contentUrl"],
        "mimeType": media[0]["mimeType"],
        "media": media,
        "author": {"username": username, "url": author_url},
        "sources": [canonical],
        "tags": {"artist": [], "character": []},
        "description": description,
        "safety": "unsafe",
        "warnings": [
            "X does not provide a compatible safety rating. Review the rating before uploading."
        ],
    }
