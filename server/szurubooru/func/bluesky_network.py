"""Public Bluesky reads and DNS-pinned downloads of verified original blobs."""

import base64
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from szurubooru import errors


class BlueskyError(errors.ProcessingError):
    pass


_APPVIEW = "https://public.api.bsky.app"
_MAX_METADATA = 2 * 1024 * 1024
_TIMEOUT = 15
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST = 0.0
_DEFAULT_UA = "Wildpaws/1.0 (https://github.com/kemopup/wildpaws)"
_BLOB_PATH = "/xrpc/com.atproto.sync.getBlob"
_HANDLE_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"


def validate_actor(value):
    if isinstance(value, str) and value.startswith("did:"):
        return validate_did(value)
    if not isinstance(value, str) or len(value) > 253:
        raise ValueError("Enter a valid Bluesky handle or DID.")
    handle = value.lower()
    if not re.fullmatch(
        _HANDLE_LABEL + r"(?:\." + _HANDLE_LABEL + r")+", handle
    ) or not re.fullmatch(r"[a-z][a-z0-9-]*", handle.rsplit(".", 1)[1]):
        raise ValueError("Enter a valid Bluesky handle or DID.")
    if handle.endswith((".localhost", ".local", ".internal", ".arpa")):
        raise ValueError("Enter a public Bluesky handle.")
    return handle


def validate_did(value):
    if isinstance(value, str) and re.fullmatch(r"did:plc:[a-z2-7]{24}", value):
        return value
    if isinstance(value, str) and value.startswith("did:web:"):
        host = value[len("did:web:") :]
        if ":" not in host and "%" not in host:
            return "did:web:" + validate_actor(host)
    raise ValueError("This Bluesky account has an unsupported or invalid DID.")


def validate_rkey(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9._~:-]{1,512}", value)
        or value in (".", "..")
    ):
        raise ValueError("Enter a valid Bluesky post record key.")
    return value


def validate_cid(value):
    """Return the digest of a canonical CIDv1 raw SHA-256 media blob."""
    if not isinstance(value, str) or not re.fullmatch(r"b[a-z2-7]{58}", value):
        raise ValueError("Bluesky returned an unsupported media content ID.")
    try:
        encoded = value[1:].upper()
        raw = base64.b32decode(encoded + "=" * (-len(encoded) % 8))
    except (ValueError, base64.binascii.Error):
        raise ValueError(
            "Bluesky returned an invalid media content ID."
        ) from None
    if (
        len(raw) != 36
        or raw[:4] != b"\x01\x55\x12\x20"
        or "b" + base64.b32encode(raw).decode("ascii").lower().rstrip("=")
        != value
    ):
        raise ValueError("Bluesky returned an unsupported media content ID.")
    return raw[4:]


def _public_host(host):
    # DNS names only: literal IPs, alternate numerical forms and local names
    # are not account/PDS origins. DNS answers are checked separately below.
    return validate_actor(host)


def validate_pds_url(value):
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(
            ord(char) <= 32 or ord(char) > 126 or char in "\\'\"<>`"
            for char in value
        )
    ):
        raise ValueError("Bluesky returned an invalid data-server location.")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError(
            "Bluesky returned an unsupported data-server location."
        )
    return "https://" + _public_host(parsed.hostname or "")


def parse_post_url(url):
    try:
        if (
            not isinstance(url, str)
            or len(url) > 4096
            or any(ord(char) <= 32 or ord(char) == 127 for char in url.strip())
        ):
            raise ValueError()
        parsed = urlsplit(url.strip())
        if (
            parsed.scheme not in ("http", "https")
            or parsed.hostname
            not in ("bsky.app", "www.bsky.app", "fxbsky.app", "www.fxbsky.app")
            or parsed.username
            or parsed.password
            or parsed.port is not None
        ):
            raise ValueError()
        match = re.fullmatch(r"/profile/([^/]+)/post/([^/]+)/?", parsed.path)
        if not match:
            raise ValueError()
        actor = validate_actor(match.group(1))
        rkey = validate_rkey(match.group(2))
        index = None
        if parsed.fragment.startswith("media-"):
            if not re.fullmatch(r"media-(?:[1-9]|1[0-9]|20)", parsed.fragment):
                raise ValueError()
            index = int(parsed.fragment[6:])
        canonical = "https://bsky.app/profile/%s/post/%s" % (actor, rkey)
        if index is not None:
            canonical += "#media-%d" % index
        return actor, rkey, canonical, index
    except (ValueError, AttributeError):
        raise ValueError("Enter a valid Bluesky post link.") from None


def _addresses(host):
    host = _public_host(host)
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        result = []
        for family, _kind, _protocol, _name, sockaddr in answers:
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            address = ipaddress.ip_address(sockaddr[0])
            if (
                not address.is_global
                or address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_reserved
                or address.is_unspecified
            ):
                raise BlueskyError(
                    "Bluesky returned a non-public network location."
                )
            item = (family, str(address))
            if item not in result:
                result.append(item)
        if not result:
            raise OSError()
        return sorted(result, key=lambda item: item[0] != socket.AF_INET)
    except (OSError, ValueError):
        raise BlueskyError("Could not resolve the Bluesky service.") from None


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, family, timeout):
        super().__init__(
            host, timeout=timeout, context=ssl.create_default_context()
        )
        self._address = address
        self._family = family

    def connect(self):
        raw = socket.socket(self._family, socket.SOCK_STREAM)
        raw.settimeout(self.timeout)
        try:
            raw.connect((self._address, 443))
            self.sock = self._context.wrap_socket(
                raw, server_hostname=self.host
            )
        except Exception:
            raw.close()
            raise


def _http_error(status):
    if status in (400, 401, 403, 404, 410, 451):
        return (
            "This Bluesky post or media is unavailable through public access."
        )
    if status in (429, 503):
        return "Bluesky is busy or limiting requests. Please try again later."
    if 300 <= status < 400:
        return "Bluesky returned a redirect that cannot be used for importing."
    return "Bluesky could not return the requested data."


def _fetch_bytes(url, limit, user_agent=None):
    global _LAST_REQUEST
    parsed = urlsplit(url)
    origin = validate_pds_url("https://" + (parsed.netloc or ""))
    if (
        parsed.scheme != "https"
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise BlueskyError("Bluesky returned an unsupported network location.")
    host = urlsplit(origin).hostname
    addresses = _addresses(host)
    # A display-preview request may share this limiter with an original upload.
    # Let the upload wait for that request while bounding the total queue wait.
    if not _REQUEST_LOCK.acquire(timeout=_TIMEOUT):
        raise BlueskyError(
            "Another Bluesky request is in progress. Please try again shortly."
        )
    try:
        delay = 1.1 - (time.monotonic() - _LAST_REQUEST)
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST = time.monotonic()
        deadline = time.monotonic() + _TIMEOUT
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        for family, address in addresses:
            connection = None
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                connection = _PinnedHTTPSConnection(
                    host, address, family, remaining
                )
                connection.request(
                    "GET",
                    path,
                    headers={
                        "Host": host,
                        "User-Agent": user_agent or _DEFAULT_UA,
                        "Accept": "*/*",
                        "Accept-Encoding": "identity",
                    },
                )
                response = connection.getresponse()
                if response.status != 200:
                    raise BlueskyError(_http_error(response.status))
                length = response.getheader("Content-Length")
                if length is not None and (
                    not length.isdigit() or int(length) > limit
                ):
                    raise BlueskyError(
                        "The Bluesky response exceeds the allowed file size."
                    )
                content = bytearray()
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError()
                    chunk = response.read(min(32768, limit + 1 - len(content)))
                    if not chunk:
                        return bytes(content)
                    content.extend(chunk)
                    if len(content) > limit:
                        raise BlueskyError(
                            "The Bluesky response exceeds the allowed file size."
                        )
            except (OSError, http.client.HTTPException):
                # Try only other already-validated addresses of the same host.
                continue
            finally:
                if connection is not None:
                    connection.close()
        raise BlueskyError("Could not reach Bluesky. Please try again later.")
    finally:
        _REQUEST_LOCK.release()


def _json(url, user_agent=None):
    try:
        data = json.loads(
            _fetch_bytes(url, _MAX_METADATA, user_agent).decode("utf8")
        )
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, UnicodeError):
        raise BlueskyError("Bluesky did not return valid metadata.") from None


def _resolve_handle(handle, user_agent=None):
    data = _json(
        _APPVIEW
        + "/xrpc/com.atproto.identity.resolveHandle?"
        + urlencode({"handle": handle}),
        user_agent,
    )
    return validate_did(data.get("did"))


def _did_document(did, user_agent=None):
    did = validate_did(did)
    endpoint = (
        ("https://plc.directory/" + did)
        if did.startswith("did:plc:")
        else ("https://" + did[len("did:web:") :] + "/.well-known/did.json")
    )
    document = _json(endpoint, user_agent)
    if document.get("id") != did:
        raise BlueskyError("Bluesky returned a mismatched account identity.")
    return document


def _pds(document, did):
    services = document.get("service")
    if isinstance(services, list):
        for service in services:
            if (
                isinstance(service, dict)
                and service.get("id") in ("#atproto_pds", did + "#atproto_pds")
                and service.get("type") == "AtprotoPersonalDataServer"
            ):
                return validate_pds_url(service.get("serviceEndpoint"))
    raise BlueskyError(
        "Bluesky did not supply a data server for this account."
    )


def resolve_pds(did, user_agent=None):
    try:
        did = validate_did(did)
        return _verified_pds(_did_document(did, user_agent), did)
    except ValueError:
        raise BlueskyError(
            "Bluesky returned an invalid account or data server."
        ) from None


def _verified_pds(document, did):
    origin = _pds(document, did)
    _addresses(urlsplit(origin).hostname)
    return origin


def _claimed_handle(document):
    values = document.get("alsoKnownAs")
    if isinstance(values, list):
        for value in values:
            if isinstance(value, str) and value.startswith("at://"):
                try:
                    handle = validate_actor(value[5:])
                    if not handle.startswith("did:"):
                        return handle
                except ValueError:
                    pass
    return None


def fetch_metadata(actor, rkey, user_agent=None):
    try:
        actor = validate_actor(actor)
        rkey = validate_rkey(rkey)
        did = (
            actor
            if actor.startswith("did:")
            else _resolve_handle(actor, user_agent)
        )
        uri = "at://%s/app.bsky.feed.post/%s" % (did, rkey)
        payload = _json(
            _APPVIEW
            + "/xrpc/app.bsky.feed.getPosts?"
            + urlencode({"uris": uri}),
            user_agent,
        )
        posts = payload.get("posts")
        if (
            not isinstance(posts, list)
            or len(posts) != 1
            or not isinstance(posts[0], dict)
        ):
            raise BlueskyError(
                "This Bluesky post is deleted, private, or unavailable through public access."
            )
        post = posts[0]
        if (
            post.get("uri") != uri
            or not isinstance(post.get("author"), dict)
            or post["author"].get("did") != did
        ):
            raise BlueskyError("Bluesky did not return the requested post.")
        document = _did_document(did, user_agent)
        if not actor.startswith("did:") and _claimed_handle(document) != actor:
            raise BlueskyError(
                "The Bluesky handle no longer matches this account. Use its current post link."
            )
        return payload, did, _verified_pds(document, did)
    except ValueError:
        raise BlueskyError(
            "Bluesky returned an invalid post or account identity."
        ) from None


def is_blob_url(url):
    try:
        return (
            isinstance(url, str)
            and urlsplit(url).path.rstrip("/") == _BLOB_PATH
        )
    except ValueError:
        return isinstance(url, str) and _BLOB_PATH in url


def _blob_url_parts(url):
    if (
        not isinstance(url, str)
        or len(url) > 4096
        or any(
            ord(char) <= 32 or ord(char) > 126 or char in "\\'\"<>`"
            for char in url
        )
    ):
        raise ValueError()
    parsed = urlsplit(url)
    origin = validate_pds_url("%s://%s" % (parsed.scheme, parsed.netloc))
    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    if (
        parsed.path != _BLOB_PATH
        or parsed.fragment
        or set(query) != {"did", "cid"}
        or any(len(value) != 1 for value in query.values())
    ):
        raise ValueError()
    did, cid = validate_did(query["did"][0]), query["cid"][0]
    digest = validate_cid(cid)
    return origin, did, cid, digest


def download_blob(url, max_size, user_agent=None):
    try:
        origin, did, cid, digest = _blob_url_parts(url)
        limit = int(max_size)
        if limit <= 0:
            raise ValueError()
        if resolve_pds(did, user_agent) != origin:
            raise BlueskyError(
                "The Bluesky media location no longer matches its account's data server."
            )
        canonical = (
            origin + _BLOB_PATH + "?" + urlencode({"did": did, "cid": cid})
        )
        content = _fetch_bytes(canonical, limit, user_agent)
        if hashlib.sha256(content).digest() != digest:
            raise BlueskyError(
                "The downloaded Bluesky file does not match its original content ID."
            )
        return content
    except (ValueError, OverflowError, TypeError):
        raise BlueskyError(
            "The Bluesky original-file URL is invalid."
        ) from None
