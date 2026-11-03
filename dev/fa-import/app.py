"""Private, metadata-only FAAPI adapter for Wildpaws's import server.

There is no public API proxy, login endpoint, file downloader, or cookie store.
The Compose service must remain unpublished on its private Docker network.
"""

import hashlib
import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import faapi
import requests
from faapi import exceptions


MAX_REQUEST = 16 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
NETWORK_TIMEOUT = 20
_NETWORK_LOCK = threading.Lock()
_LAST_NETWORK_REQUEST = 0.0


class InvalidRequest(Exception):
    pass


class RestrictedSession(requests.Session):
    """Send the FA cookies only to fixed, read-only Fur Affinity pages."""

    def request(self, method, url, **kwargs):
        global _LAST_NETWORK_REQUEST
        parsed = urlsplit(url)
        if (
            method.upper() != "GET"
            or parsed.scheme != "https"
            or parsed.hostname != "www.furaffinity.net"
            or parsed.username
            or parsed.password
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
            or kwargs.get("params")
            or not re.fullmatch(
                r"/robots\.txt|/view/[1-9][0-9]{0,11}/?", parsed.path
            )
        ):
            raise requests.RequestException("Unsupported provider location")
        kwargs.update(
            allow_redirects=False, timeout=NETWORK_TIMEOUT, stream=True
        )
        self.headers["User-Agent"] = (
            "Wildpaws/1.0 (https://github.com/kemopup/wildpaws; faapi/%s)"
            % faapi.__version__
        )
        # Includes robots.txt and changed-cookie sessions, not only FAAPI.get.
        with _NETWORK_LOCK:
            delay = 1.1 - (time.monotonic() - _LAST_NETWORK_REQUEST)
            if delay > 0:
                time.sleep(delay)
            _LAST_NETWORK_REQUEST = time.monotonic()
            response = super().request(method, url, **kwargs)
        try:
            response.raise_for_status()
            if response.status_code != 200:
                raise requests.RequestException("Provider redirect rejected")
            data = bytearray()
            for chunk in response.iter_content(16384):
                data.extend(chunk)
                if len(data) > MAX_RESPONSE:
                    raise requests.RequestException(
                        "Provider response too large"
                    )
            response._content = bytes(data)
            response._content_consumed = True
            return response
        finally:
            response.close()


def validate_body(body):
    if not isinstance(body, dict) or set(body) != {"cookies", "bbcode"}:
        raise InvalidRequest()
    if body["bbcode"] is not True:
        raise InvalidRequest()
    cookies = body["cookies"]
    if not isinstance(cookies, list) or len(cookies) != 2:
        raise InvalidRequest()
    result = {}
    for cookie in cookies:
        if not isinstance(cookie, dict) or set(cookie) != {"name", "value"}:
            raise InvalidRequest()
        name, value = cookie["name"], cookie["value"]
        if (
            name not in ("a", "b")
            or name in result
            or not isinstance(value, str)
            or not 1 <= len(value) <= 4096
            or any(ord(c) < 33 or ord(c) > 126 or c in ';,"\\' for c in value)
        ):
            raise InvalidRequest()
        result[name] = value
    return [{"name": name, "value": result[name]} for name in ("a", "b")]


def failure(code, message):
    return {"error": code, "message": message}


class ImportService:
    def __init__(self, api_factory=faapi.FAAPI):
        self.api_factory = api_factory
        self.lock = threading.Lock()
        self.identity = None
        self.api = None

        self.cooldown_until = 0.0
        self.backoff = 0

    def pause(self, retry_after=None):
        self.backoff = min(max(2, self.backoff * 2), 300)
        delay = self.backoff
        if isinstance(retry_after, str) and retry_after.isdigit():
            delay = max(delay, min(int(retry_after), 86400))
        self.cooldown_until = time.monotonic() + delay

    def clear(self):
        if self.api is not None:
            self.api.session.close()
        self.api = None
        self.identity = None

    def submission(self, submission_id, body):
        try:
            cookies = validate_body(body)
        except (InvalidRequest, TypeError):
            return 400, failure("provider_error", "Invalid import request.")
        if (
            type(submission_id) is not int
            or not 1 <= submission_id <= 999999999999
        ):
            return 400, failure("provider_error", "Invalid submission ID.")
        if not self.lock.acquire(blocking=False):
            return 429, failure(
                "busy", "Another Fur Affinity import is in progress."
            )
        try:
            if time.monotonic() < self.cooldown_until:
                return 429, failure(
                    "busy",
                    "Fur Affinity is cooling down after an error. Try again later.",
                )
            identity = hashlib.sha256(
                json.dumps(cookies).encode("utf8")
            ).digest()
            if self.identity != identity:
                self.clear()
                self.api = self.api_factory(
                    cookies, session_class=RestrictedSession
                )
                self.api.timeout = NETWORK_TIMEOUT
                self.api.raise_for_unauthorized = True
                self.identity = identity
            submission, _unused_file = self.api.submission(
                submission_id, get_file=False
            )
            if submission.id != submission_id:
                return 404, failure(
                    "unavailable",
                    "This Fur Affinity submission is unavailable.",
                )
            result = {
                "id": submission.id,
                "author": {
                    "name": submission.author.name,
                    "url": submission.author.url,
                },
                "description": submission.description_bbcode,
                "file_url": submission.file_url,
                "thumbnail_url": submission.thumbnail_url,
                "rating": submission.rating,
            }
            if len(json.dumps(result).encode("utf8")) > MAX_RESPONSE:
                return 502, failure(
                    "provider_error", "The submission metadata is too large."
                )
            self.backoff = 0
            return 200, result
        except exceptions.Unauthorized:
            self.clear()
            return 401, failure(
                "auth_required",
                "Fur Affinity login expired or was rejected. Replace the private YAML cookies.",
            )
        except exceptions.ClassicTheme:
            return 422, failure(
                "theme_required",
                "Set the Fur Affinity account to the Modern theme, then try again.",
            )
        except (exceptions.NotFound, exceptions.DisabledAccount):
            return 404, failure(
                "unavailable",
                "This Fur Affinity submission is unavailable to the import account.",
            )
        except requests.HTTPError as error:
            status = (
                error.response.status_code
                if error.response is not None
                else None
            )
            if status == 401:
                self.clear()
                return 401, failure(
                    "auth_required",
                    "Fur Affinity login expired or was rejected. Check the private YAML cookies.",
                )
            if status == 403:
                self.pause(error.response.headers.get("Retry-After") or "60")
                return 503, failure(
                    "busy",
                    "Fur Affinity is blocking automated access. Wait and try again later.",
                )
            if status in (429, 503):
                self.pause(error.response.headers.get("Retry-After"))
                return 429, failure(
                    "busy",
                    "Fur Affinity is limiting requests. Try again later.",
                )
            if status == 404:
                return 404, failure(
                    "unavailable",
                    "This Fur Affinity submission is unavailable.",
                )
            return 502, failure(
                "provider_error",
                "Fur Affinity could not return this submission.",
            )
        except requests.RequestException:
            self.pause()
            return 502, failure(
                "provider_error",
                "Fur Affinity could not be reached. Try again later.",
            )
        except Exception:
            # Never include third-party exceptions, response bodies, or cookies.
            return 502, failure(
                "provider_error",
                "Could not read this submission through Fur Affinity's API adapter.",
            )
        finally:
            self.lock.release()


class ImportHandler(BaseHTTPRequestHandler):
    service = ImportService()

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, _format, *_args):
        pass

    def send_json(self, status, data):
        body = json.dumps(data).encode("utf8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if status in (429, 503):
            cooldown = getattr(self.service, "cooldown_until", 0)
            remaining = (
                max(1, int(cooldown - time.monotonic() + 0.999))
                if isinstance(cooldown, (int, float))
                else 5
            )
            self.send_header("Retry-After", str(remaining))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"status": "ok"})
        else:
            self.send_json(404, failure("unavailable", "Unknown endpoint."))

    def do_POST(self):
        match = re.fullmatch(r"/submission/([1-9][0-9]{0,11})/", self.path)
        if not match:
            self.send_json(404, failure("unavailable", "Unknown endpoint."))
            return
        try:
            if self.headers.get("Transfer-Encoding"):
                raise InvalidRequest()
            if (
                self.headers.get("Content-Type", "").split(";", 1)[0].strip()
                != "application/json"
            ):
                raise InvalidRequest()
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST:
                raise InvalidRequest()
            data = self.rfile.read(length)
            if len(data) != length:
                raise InvalidRequest()
            body = json.loads(data)
        except (ValueError, UnicodeError, InvalidRequest, socket.timeout):
            self.close_connection = True
            self.send_json(
                400, failure("provider_error", "Invalid import request.")
            )
            return
        status, response = self.service.submission(int(match[1]), body)
        self.send_json(status, response)


def main():
    server = ThreadingHTTPServer(("0.0.0.0", 8080), ImportHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
