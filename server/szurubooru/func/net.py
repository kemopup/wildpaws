import json
import logging
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from threading import Thread
from typing import Any, Dict, List, Optional

from szurubooru import config, errors
from szurubooru.func import bluesky_network, mime

logger = logging.getLogger(__name__)
_dl_chunk_size = 2**15


class DownloadError(errors.ProcessingError):
    pass


class DownloadTooLargeError(DownloadError):
    pass


def get_import_user_agent() -> str:
    settings = config.config.get("post_import", {}) or {}
    return settings.get("user_agent") or (
        "Wildpaws/1.0 (https://github.com/kemopup/wildpaws)"
    )


def _get_download_user_agent(url: str) -> Optional[str]:
    if config.config.get("user_agent"):
        return config.config["user_agent"]
    try:
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
            and any(
                parsed.hostname == domain
                or (parsed.hostname or "").endswith("." + domain)
                for domain in (
                    "e621.net",
                    "e6ai.net",
                    "rule34.xxx",
                    "rule34.us",
                    "tbib.org",
                    "gelbooru.com",
                    "pbs.twimg.com",
                    "video.twimg.com",
                    "inkbunny.net",
                    "ib.metapix.net",
                    "furaffinity.net",
                    "cdn.bsky.app",
                    "video.bsky.app",
                )
            )
        ):
            return get_import_user_agent()
    except ValueError:
        pass
    return None


def _get_download_referer(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
        ):
            for domain in ("tbib.org", "gelbooru.com"):
                if parsed.hostname == domain or (
                    parsed.hostname or ""
                ).endswith("." + domain):
                    return "https://" + domain + "/"
            if parsed.hostname == "furaffinity.net" or (
                parsed.hostname or ""
            ).endswith(".furaffinity.net"):
                return "https://www.furaffinity.net/"
            if (
                parsed.hostname == "inkbunny.net"
                or any(
                    (parsed.hostname or "").endswith("." + domain)
                    for domain in ("inkbunny.net", "ib.metapix.net")
                )
                or parsed.hostname == "ib.metapix.net"
            ):
                return "https://inkbunny.net/"
    except ValueError:
        pass
    return url


def _download_bluesky_blob(url: str) -> bytes:
    return bluesky_network.download_blob(
        url,
        config.config["max_dl_filesize"],
        user_agent=_get_download_user_agent(url) or get_import_user_agent(),
    )


def download(url: str, use_video_downloader: bool = False) -> bytes:
    assert url
    if bluesky_network.is_blob_url(url):
        return _download_bluesky_blob(url)
    youtube_dl_error = None
    if use_video_downloader:
        try:
            url = _get_youtube_dl_content_url(url) or url
        except errors.ThirdPartyError as ex:
            youtube_dl_error = ex

    if bluesky_network.is_blob_url(url):
        return _download_bluesky_blob(url)

    request = urllib.request.Request(url)
    user_agent = _get_download_user_agent(url)
    if user_agent:
        request.add_header("User-Agent", user_agent)
    request.add_header("Referer", _get_download_referer(url))

    content_buffer = b""
    length_tally = 0
    try:
        with urllib.request.urlopen(request) as handle:
            while chunk := handle.read(_dl_chunk_size):
                length_tally += len(chunk)
                if length_tally > config.config["max_dl_filesize"]:
                    raise DownloadTooLargeError(
                        "Download target exceeds maximum. (%d)"
                        % (config.config["max_dl_filesize"]),
                        extra_fields={"URL": url},
                    )
                content_buffer += chunk
    except urllib.error.HTTPError as ex:
        raise DownloadError(
            "Download target returned HTTP %d. (%s)" % (ex.code, ex.reason),
            extra_fields={"URL": url},
        ) from ex

    if (
        youtube_dl_error
        and mime.get_mime_type(content_buffer) == "application/octet-stream"
    ):
        raise youtube_dl_error

    return content_buffer


def _get_youtube_dl_content_url(url: str) -> str:
    cmd = ["yt-dlp", "--format", "best", "--no-playlist"]
    if config.config["user_agent"]:
        cmd.extend(["--user-agent", config.config["user_agent"]])
    cmd.extend(["--get-url", url])
    try:
        return (
            subprocess.run(cmd, text=True, capture_output=True, check=True)
            .stdout.split("\n")[0]
            .strip()
        )
    except subprocess.CalledProcessError:
        raise errors.ThirdPartyError(
            "Could not extract content location from URL.",
            extra_fields={"URL": url},
        ) from None


def post_to_webhooks(payload: Dict[str, Any]) -> List[Thread]:
    threads = [
        Thread(target=_post_to_webhook, args=(webhook, payload), daemon=False)
        for webhook in (config.config["webhooks"] or [])
    ]
    for thread in threads:
        thread.start()
    return threads


def _post_to_webhook(webhook: str, payload: Dict[str, Any]) -> int:
    req = urllib.request.Request(webhook)
    req.data = json.dumps(
        payload,
        default=lambda x: x.isoformat("T") + "Z",
    ).encode("utf-8")
    req.add_header("Content-Type", "application/json")
    try:
        res = urllib.request.urlopen(req)
        if not 200 <= res.status <= 299:
            logger.warning(
                f"Webhook {webhook} returned {res.status} {res.reason}"
            )
        return res.status
    except urllib.error.URLError as ex:
        logger.warning(f"Unable to call webhook {webhook}: {ex}")
        return 400
