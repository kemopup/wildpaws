"""Read-only, provider-specific metadata import. Never imports general tags."""

import base64
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Tuple, Union

from szurubooru import config, errors
from szurubooru.func import (
    bluesky_network,
    images,
    import_bluesky,
    import_booru,
    import_description,
    import_furaffinity,
    import_inkbunny,
    import_x,
    net,
    tag_categories,
)


class ImportError(errors.ProcessingError):
    pass


_HOSTS = {
    "e621.net": "e621",
    "www.e621.net": "e621",
    "e6ai.net": "e6ai",
    "www.e6ai.net": "e6ai",
    "rule34.xxx": "rule34",
    "www.rule34.xxx": "rule34",
    "tbib.org": "tbib",
    "www.tbib.org": "tbib",
    "gelbooru.com": "gelbooru",
    "www.gelbooru.com": "gelbooru",
    "x.com": "x",
    "www.x.com": "x",
    "twitter.com": "x",
    "www.twitter.com": "x",
    "mobile.twitter.com": "x",
    "fxtwitter.com": "x",
    "www.fxtwitter.com": "x",
    "fixupx.com": "x",
    "www.fixupx.com": "x",
    "inkbunny.net": "inkbunny",
    "www.inkbunny.net": "inkbunny",
    "furaffinity.net": "furaffinity",
    "www.furaffinity.net": "furaffinity",
    "bsky.app": "bluesky",
    "www.bsky.app": "bluesky",
    "fxbsky.app": "bluesky",
    "www.fxbsky.app": "bluesky",
}
_DAPI_HOSTS = {
    "rule34": "rule34.xxx",
    "tbib": "tbib.org",
    "gelbooru": "gelbooru.com",
}
_LOCKS = {provider: threading.Lock() for provider in set(_HOSTS.values())}
_LAST_REQUEST = {provider: 0.0 for provider in _LOCKS}
_MAX_METADATA = 2 * 1024 * 1024
_INKBUNNY_SESSION_LOCK = threading.Lock()
_INKBUNNY_SESSION = {}


def parse_post_url(url: str) -> Tuple[str, Union[int, str], str]:
    try:
        parsed = urllib.parse.urlsplit(url.strip())
        provider = _HOSTS.get(parsed.hostname or "")
        if provider == "bluesky":
            _actor, rkey, canonical, _index = bluesky_network.parse_post_url(
                url
            )
            return provider, rkey, canonical
        if (
            (
                parsed.scheme != "https"
                and not (
                    provider in ("x", "inkbunny", "furaffinity")
                    and parsed.scheme == "http"
                )
            )
            or not provider
            or parsed.username
            or parsed.password
            or parsed.port is not None
        ):
            raise ValueError()
        x_match = None
        if provider == "x":
            x_match = re.fullmatch(
                r"/(?:(?P<user>[A-Za-z0-9_]{1,15})/status|i/(?:web/)?status)/(?P<id>[0-9]{1,20})(?:/(?P<kind>photo|video)/(?P<index>[1-4]))?/?",
                parsed.path,
            )
            if not x_match:
                raise ValueError()
            post_id = x_match.group("id")
        elif provider == "furaffinity":
            match = re.fullmatch(r"/(?:view|full)/([0-9]+)/?", parsed.path)
            if not match:
                raise ValueError()
            post_id = match.group(1)
        elif provider == "inkbunny":
            page = None
            match = re.fullmatch(
                r"/s/([0-9]+)(?:-p([1-9][0-9]{0,3})-?)?/?", parsed.path
            )
            if match:
                post_id, page = match.groups()
            elif parsed.path == "/submissionview.php":
                query = urllib.parse.parse_qs(parsed.query)
                if len(query.get("id", [])) != 1:
                    raise ValueError()
                post_id = query["id"][0]
            else:
                raise ValueError()
            if parsed.fragment.startswith("p"):
                if not re.fullmatch(r"p[1-9][0-9]{0,3}", parsed.fragment):
                    raise ValueError()
                if page and page != parsed.fragment[1:]:
                    raise ValueError()
                page = parsed.fragment[1:]
        elif provider in _DAPI_HOSTS:
            query = urllib.parse.parse_qs(parsed.query)
            if (
                parsed.path != "/index.php"
                or query.get("page") != ["post"]
                or query.get("s") != ["view"]
                or len(query.get("id", [])) != 1
            ):
                raise ValueError()
            post_id = query["id"][0]
        else:
            match = re.fullmatch(
                r"/(?:posts/|post/show/)([0-9]+)(?:\.json)?/?", parsed.path
            )
            if not match:
                raise ValueError()
            post_id = match.group(1)
        if (
            not re.fullmatch(
                r"[0-9]{1,20}" if provider == "x" else r"[0-9]{1,12}", post_id
            )
            or int(post_id) <= 0
        ):
            raise ValueError()
    except (ValueError, AttributeError):
        raise errors.InvalidParameterError(
            "Enter an HTTPS post link from a supported site: e621, E6AI, Rule34, TBIB, Gelbooru, X/Twitter, Inkbunny, Fur Affinity, or Bluesky."
        ) from None
    post_id = int(post_id)
    if provider == "furaffinity":
        return (
            provider,
            post_id,
            "https://www.furaffinity.net/view/%d/" % post_id,
        )
    if provider == "inkbunny":
        canonical = "https://inkbunny.net/s/%d" % post_id
        if page:
            canonical += "-p%d-" % int(page)
        return provider, post_id, canonical
    if provider == "x":
        username = x_match.group("user") or "i"
        canonical = "https://x.com/%s/status/%d" % (username, post_id)
        if x_match.group("index"):
            canonical += (
                "/" + x_match.group("kind") + "/" + x_match.group("index")
            )
        return provider, post_id, canonical
    canonical = (
        "https://%s/index.php?page=post&s=view&id=%d"
        % (_DAPI_HOSTS[provider], post_id)
        if provider in _DAPI_HOSTS
        else "https://%s.net/posts/%d" % (provider, post_id)
    )
    return provider, post_id, canonical


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # API credentials must never follow a redirect to another endpoint.
        return None


def _request_data(
    provider: str,
    url: str,
    accept: str = "application/json",
    authenticate: bool = True,
    form: Any = None,
) -> bytes:
    # FxTwitter is a separate public API; never attach another provider's keys.
    if provider in ("x", "inkbunny"):
        authenticate = False
    settings = config.config.get("post_import", {}) or {}
    credentials = settings.get(provider, {}) or {}
    user_agent = net.get_import_user_agent()
    headers = {"User-Agent": user_agent, "Accept": accept}
    username = credentials.get("username")
    key = credentials.get("api_key")
    if authenticate and provider in ("rule34", "gelbooru"):
        user_id = credentials.get("user_id")
        if provider == "rule34" and (not user_id or not key):
            raise ImportError(
                "Rule34 import requires an API user ID and key configured by the site administrator."
            )
        if user_id or key:
            if not user_id or not key:
                raise ImportError(
                    "The site's %s import account configuration is incomplete."
                    % provider
                )
            url += "&" + urllib.parse.urlencode(
                {"user_id": user_id, "api_key": key}
            )
    elif authenticate and (username or key):
        if not username or not key:
            raise ImportError(
                "The site's %s import account configuration is incomplete."
                % provider
            )
        token = base64.b64encode(
            (str(username) + ":" + str(key)).encode("utf8")
        ).decode("ascii")
        headers["Authorization"] = "Basic " + token
    lock = _LOCKS[provider]
    if not lock.acquire(blocking=False):
        raise ImportError(
            "Another %s import is in progress. Please try again shortly."
            % provider
        )
    try:
        delay = 1.1 - (time.monotonic() - _LAST_REQUEST[provider])
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST[provider] = time.monotonic()
        body = None
        if form is not None:
            body = urllib.parse.urlencode(form).encode("utf8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=body, headers=headers)
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=15) as response:
            data = response.read(_MAX_METADATA + 1)
        if len(data) > _MAX_METADATA:
            raise ImportError("The provider returned too much metadata.")
        return data
    except urllib.error.HTTPError as ex:
        if provider == "x" and ex.code in (401, 403, 404):
            message = "This X post is private, deleted, or unavailable through FxTwitter."
        elif ex.code in (401, 403):
            message = "Access denied. Check the site's import API account and its access to this post."
        elif ex.code == 404:
            message = "This post was not found or is not available to the import account."
        elif ex.code in (429, 503):
            message = "The provider is busy or limiting requests. Please try again later."
        else:
            message = (
                "The provider could not return this post (HTTP %d)." % ex.code
            )
        raise ImportError(message) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ImportError(
            "Could not reach the provider. Please try again later."
        ) from None
    except (ValueError, UnicodeError):
        raise ImportError(
            "The provider did not return valid post metadata."
        ) from None
    finally:
        lock.release()


def _request_json(provider: str, url: str, form: Any = None) -> Any:
    data = (
        _request_data(provider, url)
        if form is None
        else _request_data(provider, url, form=form)
    )
    try:
        text = data.decode("utf8")
        if provider == "tbib" and text.lstrip().startswith("<"):
            if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
                raise ValueError()
            root = ET.fromstring(text)
            if root.tag != "posts":
                raise ValueError()
            return {
                "post": [dict(item.attrib) for item in root.findall("post")]
            }
        return json.loads(text)
    except (ValueError, UnicodeError, ET.ParseError):
        raise ImportError(
            "The provider did not return valid post metadata."
        ) from None


def _inkbunny_error(data):
    if not isinstance(data, dict):
        raise ImportError("Inkbunny did not return valid post metadata.")
    code = data.get("error_code")
    return str(code) if code is not None else None


def _raise_inkbunny_error(code):
    if code == "0":
        message = "Inkbunny login failed. Check the private YAML username and password, and enable API Access in the Inkbunny account settings."
    elif code in ("1", "2"):
        message = "The Inkbunny API session expired or was rejected. Please try again."
    elif code in ("8", "40"):
        message = "This Inkbunny submission is deleted, unavailable, or not accessible to the import account."
    else:
        message = "Inkbunny could not return this submission. Check the import account's access and rating settings."
    raise ImportError(message)


def _inkbunny_payload(post_id):
    # Only this fixed HTTPS API receives the login and SID, in POST bodies.
    settings = (config.config.get("post_import", {}) or {}).get(
        "inkbunny", {}
    ) or {}
    username = settings.get("username") or ""
    password = settings.get("password") or ""
    if not isinstance(username, str) or not isinstance(password, str):
        raise ImportError(
            "Inkbunny username and password must be quoted YAML strings."
        )
    username = username.strip()
    if (
        not username
        and password
        or username.lower() not in ("", "guest")
        and not password
    ):
        raise ImportError(
            "The site's Inkbunny username/password configuration is incomplete."
        )
    username = username or "guest"
    identity = hashlib.sha256(
        (username + "\0" + password).encode("utf8")
    ).digest()
    if not _INKBUNNY_SESSION_LOCK.acquire(blocking=False):
        raise ImportError(
            "Another Inkbunny import is in progress. Please try again shortly."
        )
    try:
        if _INKBUNNY_SESSION.get("identity") != identity:
            _INKBUNNY_SESSION.clear()
        for attempt in range(2):
            sid = _INKBUNNY_SESSION.get("sid")
            if not sid:
                form = {"username": username, "output_mode": "json"}
                if password:
                    form["password"] = password
                login = _request_json(
                    "inkbunny", "https://inkbunny.net/api_login.php", form
                )
                code = _inkbunny_error(login)
                if code is not None:
                    _raise_inkbunny_error(code)
                sid = login.get("sid")
                # The API defines SID as an opaque string, not an alphanumeric ID.
                if (
                    not isinstance(sid, str)
                    or not 16 <= len(sid) <= 1024
                    or any(ord(char) < 33 or ord(char) > 126 for char in sid)
                ):
                    raise ImportError(
                        "Inkbunny did not return a valid API session."
                    )
                if username.lower() == "guest":
                    ratings = _request_json(
                        "inkbunny",
                        "https://inkbunny.net/api_userrating.php",
                        {
                            "sid": sid,
                            "output_mode": "json",
                            **{"tag[%d]" % tag: "yes" for tag in range(2, 6)},
                        },
                    )
                    code = _inkbunny_error(ratings)
                    if code is not None:
                        _raise_inkbunny_error(code)
                _INKBUNNY_SESSION.update(identity=identity, sid=sid)
            data = _request_json(
                "inkbunny",
                "https://inkbunny.net/api_submissions.php",
                {
                    "sid": sid,
                    "submission_ids": str(post_id),
                    "show_description": "yes",
                    "output_mode": "json",
                },
            )
            code = _inkbunny_error(data)
            if code == "2":
                _INKBUNNY_SESSION.clear()
                if attempt == 0:
                    continue
            if code is not None:
                _raise_inkbunny_error(code)
            return data
    finally:
        _INKBUNNY_SESSION_LOCK.release()


def _request_booru_details(provider, post_url, post_tag_names=None):
    data = _request_data(provider, post_url, "text/html", authenticate=False)
    try:
        return import_booru.parse_details(
            data.decode("utf8"), post_url, post_tag_names
        )
    except (ValueError, UnicodeError):
        raise ImportError(
            "The provider did not return recognizable public post metadata."
        ) from None


def _furaffinity_payload(post_id):
    """Send FA cookies only to our private helper, never a public API mirror."""
    settings = (config.config.get("post_import", {}) or {}).get(
        "furaffinity", {}
    ) or {}
    if not isinstance(settings, dict):
        raise ImportError(
            "Fur Affinity import account configuration is invalid."
        )
    cookies = []
    for name in ("a", "b"):
        value = settings.get(name)
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 4096
            or any(
                ord(char) <= 32 or ord(char) > 126 or char in ';,"\\'
                for char in value
            )
        ):
            raise ImportError(
                "Fur Affinity import needs valid a and b session-cookie values in the private YAML configuration."
            )
        cookies.append({"name": name, "value": value})
    body = json.dumps({"cookies": cookies, "bbcode": True}).encode("utf8")
    request = urllib.request.Request(
        "http://fa-import:8080/submission/%d/" % post_id,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        # A configured global HTTP proxy must not receive private cookies.
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )
        with opener.open(request, timeout=50) as response:
            data = response.read(_MAX_METADATA + 1)
        if len(data) > _MAX_METADATA:
            raise ImportError(
                "The Fur Affinity helper returned too much metadata."
            )
        return json.loads(data.decode("utf8"))
    except urllib.error.HTTPError as ex:
        code = None
        try:
            data = json.loads(ex.read(4096).decode("utf8"))
            code = data.get("error") if isinstance(data, dict) else None
        except (ValueError, UnicodeError, OSError):
            pass
        if code == "theme_required":
            message = "Set the Fur Affinity account to the Modern theme, then try again."
        elif ex.code in (401, 403) or code == "auth_required":
            message = "Fur Affinity rejected the session. Replace the a and b cookies in the private YAML from an active login, then restart the API."
        elif ex.code == 404 or code == "unavailable":
            message = "This Fur Affinity submission is deleted, private, or unavailable to the import account."
        elif ex.code in (429, 503) or code == "busy":
            message = "Fur Affinity is busy or limiting requests. Please wait before trying again."
        else:
            message = "Fur Affinity could not return this submission. Please try again later."
        raise ImportError(message) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ImportError(
            "The private Fur Affinity import helper is unavailable. Start the helper service and try again."
        ) from None
    except (ValueError, UnicodeError):
        raise ImportError(
            "The Fur Affinity helper did not return valid post metadata."
        ) from None


def _parse_tbib_details(text, post_url, post_tag_names):
    return import_booru.parse_details(text, post_url, post_tag_names)


def _request_tbib_details(post_url, post_tag_names):
    return _request_booru_details("tbib", post_url, post_tag_names)


def _gelbooru_tags(post_url, names):
    result = {"artist": [], "character": []}
    for offset in range(0, len(names), 100):
        requested = names[offset : offset + 100]
        endpoint = "https://gelbooru.com/index.php?" + urllib.parse.urlencode(
            {
                "page": "dapi",
                "s": "tag",
                "q": "index",
                "json": 1,
                "limit": 100,
                "names": " ".join(requested),
            }
        )
        data = _request_json("gelbooru", endpoint)
        items = data.get("tag", []) if isinstance(data, dict) else data
        if isinstance(items, dict):
            items = [items]
        classified = set()
        if isinstance(items, list):
            for item in items:
                if (
                    not isinstance(item, dict)
                    or item.get("name") not in requested
                ):
                    continue
                kind = str(item.get("type", ""))
                if not kind.isdigit():
                    continue
                classified.add(item["name"])
                category = {"1": "artist", "4": "character"}.get(kind)
                if category:
                    result[category].append(item["name"])
        if classified != set(requested):
            raise ImportError(
                "Gelbooru did not supply complete tag categories; no unfiltered tags were imported."
            )
    return result


def _http_url(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or len(value) > 4096
        or any(ord(c) < 32 for c in value)
    ):
        return False
    try:
        url = urllib.parse.urlsplit(value)
        return bool(
            url.scheme in ("http", "https")
            and url.hostname
            and not url.username
            and not url.password
            and url.port in (None, 80, 443)
        )
    except ValueError:
        return False


def _original_url(provider: str, value: Any) -> str:
    if not _http_url(value):
        raise ImportError(
            "The original file is unavailable. The post may require an account, be restricted, or be deleted."
        )
    url = urllib.parse.urlsplit(value)
    domains = {
        "e621": ("e621.net",),
        "e6ai": ("e6ai.net",),
        "rule34": ("rule34.xxx", "rule34.us"),
        "tbib": ("tbib.org",),
        "gelbooru": ("gelbooru.com",),
    }[provider]
    if url.scheme != "https" or not any(
        url.hostname == d or url.hostname.endswith("." + d) for d in domains
    ):
        raise ImportError(
            "The provider returned an unsupported original-file location."
        )
    return value


def _tag_names(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for name in value:
        if (
            isinstance(name, str)
            and 0 < len(name) <= 128
            and not re.search(r"\s|[\x00-\x1f\x7f]", name)
            and name.lower() not in seen
        ):
            result.append(name)
            seen.add(name.lower())
    if len(result) > 200:
        raise ImportError(
            "The post has too many artist or character tags to import."
        )
    return result


def preview(url: str) -> Dict[str, Any]:
    provider, post_id, post_url = parse_post_url(url)
    if provider == "bluesky":
        actor, rkey, _canonical, index = bluesky_network.parse_post_url(
            post_url
        )
        try:
            payload, did, pds_url = bluesky_network.fetch_metadata(
                actor, rkey, user_agent=net.get_import_user_agent()
            )
            return import_bluesky.normalize(payload, did, rkey, pds_url, index)
        except (ValueError, TypeError) as ex:
            raise ImportError(str(ex)) from None
    if provider == "furaffinity":
        try:
            return import_furaffinity.normalize(
                _furaffinity_payload(post_id), post_id
            )
        except (ValueError, TypeError) as ex:
            raise ImportError(str(ex)) from None
    if provider == "inkbunny":
        data = _inkbunny_payload(post_id)
        selector = re.search(r"-p([0-9]+)-$", post_url)
        try:
            return import_inkbunny.normalize(
                data, post_id, int(selector.group(1)) if selector else None
            )
        except (ValueError, TypeError) as ex:
            raise ImportError(str(ex)) from None
    if provider == "x":
        data = _request_json(
            "x", "https://api.fxtwitter.com/status/%d" % post_id
        )
        selector = re.search(r"/(?:photo|video)/([1-4])$", post_url)
        try:
            return import_x.normalize(
                data, post_id, int(selector.group(1)) if selector else None
            )
        except (ValueError, TypeError) as ex:
            raise ImportError(str(ex)) from None
    credentials = (config.config.get("post_import", {}) or {}).get(
        provider, {}
    ) or {}
    public_details = None
    if provider == "gelbooru" and not (
        credentials.get("user_id") or credentials.get("api_key")
    ):
        public_details = _request_booru_details(provider, post_url)
    endpoint = (
        "https://api.rule34.xxx/index.php?page=dapi&s=post&q=index&id=%d&json=1&fields=tag_info"
        % post_id
        if provider == "rule34"
        else (
            "https://%s/index.php?page=dapi&s=post&q=index&id=%d&json=1"
            % (_DAPI_HOSTS[provider], post_id)
            if provider in _DAPI_HOSTS
            else "https://%s.net/posts/%d.json" % (provider, post_id)
        )
    )
    data = (
        _request_json(provider, endpoint)
        if public_details is None
        else public_details
    )
    if public_details is not None:
        post = {"id": post_id, "rating": public_details["rating"]}
    elif provider in _DAPI_HOSTS:
        if isinstance(data, dict):
            data = data.get("post")
        post = data[0] if isinstance(data, list) and len(data) == 1 else data
    else:
        post = data.get("post") if isinstance(data, dict) else None
    if not isinstance(post, dict) or str(post.get("id")) != str(post_id):
        raise ImportError("The provider did not return the requested post.")
    flags = post.get("flags") or {}
    if post.get("is_deleted") or (
        isinstance(flags, dict) and flags.get("deleted")
    ):
        raise ImportError("Deleted posts cannot be imported.")
    warnings = []
    if provider in ("tbib", "gelbooru"):
        flat_tags = (
            _tag_names(post.get("tags", "").split())
            if isinstance(post.get("tags", ""), str)
            else []
        )
        details = public_details
        if provider == "tbib":
            details = _request_tbib_details(post_url, flat_tags)
        if details is not None:
            file_url = details["contentUrl"]
            raw_sources = details.get("sources", [])
            tag_map = {
                category: _tag_names(details.get("tags", {}).get(category))
                for category in ("artist", "character")
            }
        else:
            file_url = post.get("file_url")
            raw_sources = post.get("source", "")
            tag_map = _gelbooru_tags(post_url, flat_tags)
    elif provider == "rule34":
        file_url = post.get("file_url")
        raw_sources = [post.get("source", "")]
        info = post.get("tag_info")
        if not isinstance(info, list):
            raise ImportError(
                "Rule34 did not supply categorized tags; no unfiltered tags were imported."
            )
        tag_map = {
            category: _tag_names(
                [
                    item.get("tag")
                    for item in info
                    if isinstance(item, dict)
                    and str(item.get("type", "")).lower() == category
                ]
            )
            for category in ("artist", "character")
        }
    else:
        file = post.get("file") or {}
        file_url = file.get("url") if isinstance(file, dict) else None
        raw_sources = post.get("sources", [])
        tags = post.get("tags") or {}
        if not isinstance(tags, dict):
            raise ImportError("The provider did not supply categorized tags.")
        tag_map = {
            category: _tag_names(tags.get(category))
            for category in ("artist", "character")
        }
        if provider == "e6ai" and not tag_map["artist"]:
            warnings.append(
                "E6AI does not supply artist tags. Director and contributor tags were not imported."
            )
    content_url = _original_url(provider, file_url)
    rating = {
        "s": "safe",
        "g": "safe",
        "safe": "safe",
        "general": "safe",
        "q": "sketchy",
        "questionable": "sketchy",
        "e": "unsafe",
        "explicit": "unsafe",
    }.get(str(post.get("rating", "")).lower())
    if rating is None:
        rating = "unsafe"
        warnings.append(
            "The provider supplied no recognized rating. Review the rating before uploading."
        )
    sources = [post_url]
    if isinstance(raw_sources, str):
        raw_sources = raw_sources.splitlines()
    if isinstance(raw_sources, list):
        for source in raw_sources[:64]:
            if _http_url(source) and source not in sources:
                sources.append(source)
    for category, names in tag_map.items():
        if names and not tag_categories.try_get_category_by_name(category):
            warnings.append(
                "The local %s category does not exist. New tags will use the default category."
                % category
            )
    if len("\n".join(sources)) > 2048:
        warnings.append(
            "Sources exceed the 2,048-character limit. Shorten the sources before uploading."
        )
    description, simplified = import_description.to_markdown(
        post.get("description"), provider
    )
    if simplified:
        warnings.append(
            "Some description formatting was simplified during Markdown conversion. Review the description before uploading."
        )
    if provider in _DAPI_HOSTS and not description:
        warnings.append(
            "%s did not supply a post description. Comments were not imported."
            % {"rule34": "Rule34", "tbib": "TBIB", "gelbooru": "Gelbooru"}[
                provider
            ]
        )
    return {
        "provider": provider,
        "postUrl": post_url,
        "contentUrl": content_url,
        "sources": sources,
        "tags": tag_map,
        "safety": rating,
        "description": description,
        "warnings": warnings,
    }


def apply_tag_categories(new_tags: List, category_map: Any) -> None:
    if not isinstance(category_map, dict) or set(category_map) - {
        "artist",
        "character",
    }:
        raise errors.InvalidParameterError(
            "tagCategories may contain only artist and character lists."
        )
    seen = set()
    for category, names in category_map.items():
        if (
            not isinstance(names, list)
            or any(not isinstance(n, str) for n in names)
            or len(names) > 200
        ):
            raise errors.InvalidParameterError(
                "tagCategories must contain lists of tag names."
            )
        selected = {name.lower() for name in names}
        if seen & selected:
            raise errors.InvalidParameterError(
                "A tag cannot be both artist and character."
            )
        seen.update(selected)
        local_category = tag_categories.try_get_category_by_name(category)
        if local_category:
            for tag in new_tags:
                if any(name.name.lower() in selected for name in tag.names):
                    tag.category = local_category


def thumbnail(url: str) -> str:
    """Return a display-only JPEG; the upload continues to use its original URL."""
    provider, _post_id, canonical = parse_post_url(url)
    if provider not in ("gelbooru", "inkbunny", "furaffinity", "bluesky"):
        raise errors.InvalidParameterError(
            "Server previews currently support Gelbooru, Inkbunny, Fur Affinity, and Bluesky post links only."
        )
    metadata = preview(canonical)
    if provider == "bluesky":
        media = metadata.get("media") or []
        try:
            download_url = import_bluesky.preview_url(
                media[0].get("previewUrl")
            )
        except (ValueError, IndexError, AttributeError):
            raise ImportError(
                "Bluesky did not supply an accessible preview for this media item."
            ) from None
    elif provider == "furaffinity":
        try:
            download_url = import_furaffinity.preview_url(
                metadata.get("previewUrl")
            )
        except (ValueError, TypeError):
            raise ImportError(
                "Fur Affinity did not supply an accessible image preview."
            ) from None
    elif provider == "inkbunny":
        media = metadata.get("media") or []
        try:
            download_url = import_inkbunny.preview_url(
                media[0].get("previewUrl")
            )
        except (ValueError, IndexError, AttributeError):
            raise ImportError(
                "Inkbunny did not supply an accessible image preview for this page."
            ) from None
    else:
        download_url = _original_url(provider, metadata.get("contentUrl"))
    content = net.download(download_url)
    image = images.Image(content)
    image.resize_fill(192, 128)
    jpeg = image.to_jpeg()
    if not jpeg.startswith(b"\xff\xd8") or len(jpeg) > 256 * 1024:
        raise ImportError(
            "Could not create a small JPEG preview for this post."
        )
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
