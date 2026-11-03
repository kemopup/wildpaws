import http.client
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from faapi import exceptions

import app


def body(a="first-secret-cookie", b="second-secret-cookie"):
    return {
        "cookies": [{"name": "a", "value": a}, {"name": "b", "value": b}],
        "bbcode": True,
    }


def submission(**updates):
    values = dict(
        id=123,
        author=SimpleNamespace(
            name="Artist_One",
            url="https://www.furaffinity.net/user/artistone/",
        ),
        description_bbcode="[b]Artist note[/b]",
        description="<b>Never return HTML</b>",
        file_url="https://d.furaffinity.net/art/artistone/1/123.original.png",
        thumbnail_url="https://t.furaffinity.net/123@200-1.jpg",
        rating="general",
        tags=["never_import"],
        comments=["private-comment"],
        footer="private-footer",
    )
    values.update(updates)
    return SimpleNamespace(**values)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.api = MagicMock()
        self.api.submission.return_value = (submission(), None)
        self.factory = MagicMock(return_value=self.api)
        self.service = app.ImportService(self.factory)

    def test_metadata_is_whitelisted_and_media_is_not_downloaded(self):
        status, result = self.service.submission(123, body())
        self.assertEqual(status, 200)
        self.assertEqual(
            set(result),
            {
                "id",
                "author",
                "description",
                "file_url",
                "thumbnail_url",
                "rating",
            },
        )
        self.assertEqual(result["description"], "[b]Artist note[/b]")
        self.assertEqual(set(result["author"]), {"name", "url"})
        self.api.submission.assert_called_once_with(123, get_file=False)
        self.api.submission_file.assert_not_called()
        self.assertTrue(self.api.raise_for_unauthorized)
        self.assertEqual(self.api.timeout, app.NETWORK_TIMEOUT)
        self.factory.assert_called_once_with(
            body()["cookies"], session_class=app.RestrictedSession
        )
        output = json.dumps(result)
        for forbidden in (
            "secret-cookie",
            "Never return HTML",
            "private-comment",
            "private-footer",
            "never_import",
        ):
            self.assertNotIn(forbidden, output)

    def test_same_cookies_reuse_session_and_changed_cookies_close_old_session(
        self,
    ):
        self.service.submission(123, body())
        self.service.submission(123, body())
        self.assertEqual(self.factory.call_count, 1)
        self.service.submission(123, body(a="replacement-cookie"))
        self.assertEqual(self.factory.call_count, 2)
        self.api.session.close.assert_called_once()

    def test_cookie_order_does_not_create_another_session(self):
        self.service.submission(123, body())
        request = body()
        request["cookies"].reverse()
        self.service.submission(123, request)
        self.assertEqual(self.factory.call_count, 1)

    def test_invalid_cookie_input_never_contacts_provider(self):
        cases = [
            None,
            {},
            dict(body(), url="https://evil.test"),
            dict(body(), bbcode=False),
        ]
        cases += [
            {"cookies": cookies, "bbcode": True}
            for cookies in [
                [],
                body()["cookies"][:1],
                body()["cookies"] * 2,
                [{"name": "a", "value": "one"}, {"name": "a", "value": "two"}],
                [
                    {"name": "other", "value": "one"},
                    {"name": "b", "value": "two"},
                ],
                [
                    {"name": "a", "value": "one", "domain": "evil.test"},
                    {"name": "b", "value": "two"},
                ],
            ]
        ]
        cases += [
            body(a=value)
            for value in (
                None,
                1,
                "",
                "x" * 4097,
                "a;b",
                "a\nb",
                "a\rb",
                'a"b',
                "a b",
                "a\\b",
            )
        ]
        for request in cases:
            with self.subTest(request=request):
                self.assertEqual(self.service.submission(123, request)[0], 400)
        self.factory.assert_not_called()

    def test_invalid_ids_never_contact_provider(self):
        for value in (0, -1, "123", True, 1000000000000, "https://evil.test"):
            self.assertEqual(self.service.submission(value, body())[0], 400)
        self.factory.assert_not_called()

    def test_busy_request_returns_without_second_provider_call(self):
        self.service.lock.acquire()
        try:
            self.assertEqual(self.service.submission(123, body())[0], 429)
        finally:
            self.service.lock.release()
        self.factory.assert_not_called()

    def test_expired_cookie_error_is_clear_and_contains_no_exception_or_cookie(
        self,
    ):
        self.api.submission.side_effect = exceptions.Unauthorized(
            "first-secret-cookie private-provider-response"
        )
        status, result = self.service.submission(123, body())
        self.assertEqual(status, 401)
        self.assertEqual(result["error"], "auth_required")
        self.assertIn("private YAML cookies", result["message"])
        self.assertNotIn("first-secret-cookie", json.dumps(result))
        self.assertNotIn("private-provider-response", json.dumps(result))
        self.assertIsNone(self.service.api)
        self.api.session.close.assert_called_once()

    def test_provider_errors_are_sanitized(self):
        for error, status, code in [
            (exceptions.NotFound("cookie"), 404, "unavailable"),
            (exceptions.DisabledAccount("cookie"), 404, "unavailable"),
            (exceptions.ClassicTheme("cookie"), 422, "theme_required"),
            (exceptions.DisallowedPath("cookie"), 502, "provider_error"),
            (requests.Timeout("cookie"), 502, "provider_error"),
            (RuntimeError("cookie"), 502, "provider_error"),
        ]:
            with self.subTest(error=type(error)):
                self.service.cooldown_until = 0
                self.api.submission.side_effect = error
                actual_status, result = self.service.submission(123, body())
                self.assertEqual(
                    (actual_status, result["error"]), (status, code)
                )
                self.assertNotIn('"cookie"', json.dumps(result))

    def test_http_errors_are_mapped_without_returning_response_body(self):
        for provider_status, expected_status in (
            (401, 401),
            (403, 503),
            (404, 404),
            (429, 429),
            (500, 502),
        ):
            with self.subTest(status=provider_status):
                self.service.cooldown_until = 0
                response = requests.Response()
                response.status_code = provider_status
                response._content = b"first-secret-cookie"
                self.api.submission.side_effect = requests.HTTPError(
                    "first-secret-cookie", response=response
                )
                status, result = self.service.submission(123, body())
                self.assertEqual(status, expected_status)
                self.assertNotIn("first-secret-cookie", json.dumps(result))

    def test_wrong_submission_id_is_unavailable(self):
        self.api.submission.return_value = (submission(id=456), None)
        self.assertEqual(self.service.submission(123, body())[0], 404)

    def test_mitigation_response_does_not_claim_expired_cookies_or_retry(self):
        response = requests.Response()
        response.status_code = 403
        self.api.submission.side_effect = requests.HTTPError(
            "private", response=response
        )
        with patch.object(app.time, "monotonic", return_value=100):
            status, result = self.service.submission(123, body())
            self.assertEqual((status, result["error"]), (503, "busy"))
            self.assertNotIn("expired", result["message"])
            self.assertEqual(self.service.cooldown_until, 160)
            self.assertEqual(self.service.submission(123, body())[0], 429)
        self.assertIsNotNone(self.service.api)
        self.api.session.close.assert_not_called()
        self.api.submission.assert_called_once()

    def test_provider_rate_limit_respects_retry_after_without_repeating_request(
        self,
    ):
        response = requests.Response()
        response.status_code = 429
        response.headers["Retry-After"] = "30"
        self.api.submission.side_effect = requests.HTTPError(response=response)
        with patch.object(app.time, "monotonic", return_value=100):
            self.assertEqual(self.service.submission(123, body())[0], 429)
            self.assertEqual(self.service.cooldown_until, 130)
            self.assertEqual(self.service.submission(123, body())[0], 429)
        self.api.submission.assert_called_once()

    def test_provider_timeouts_back_off_exponentially_without_immediate_retry(
        self,
    ):
        self.api.submission.side_effect = requests.Timeout("private message")
        with patch.object(app.time, "monotonic", return_value=100):
            self.assertEqual(self.service.submission(123, body())[0], 502)
            self.assertEqual(self.service.cooldown_until, 102)
            self.assertEqual(self.service.submission(123, body())[0], 429)
        with patch.object(app.time, "monotonic", return_value=103):
            self.assertEqual(self.service.submission(123, body())[0], 502)
            self.assertEqual(self.service.cooldown_until, 107)
        self.assertEqual(self.api.submission.call_count, 2)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch.object(app.time, "sleep").start()
        patch.object(app, "_LAST_NETWORK_REQUEST", 0).start()
        self.addCleanup(patch.stopall)

    def test_network_only_targets_fixed_https_provider_pages(self):
        for url in (
            "https://evil.test/view/123",
            "https://www.furaffinity.net.evil.test/view/123",
            "http://www.furaffinity.net/view/123",
            "https://furaffinity.net/view/123",
            "https://user:password@www.furaffinity.net/view/123",
            "https://www.furaffinity.net:443/view/123",
            "https://www.furaffinity.net/view/123?cookie=x",
            "https://www.furaffinity.net/view/123#fragment",
            "https://www.furaffinity.net/logout/",
            "https://d.furaffinity.net/art/123.png",
            "https://www.furaffinity.net/view/0",
        ):
            with self.subTest(url=url), patch.object(
                requests.Session, "request"
            ) as request:
                with self.assertRaises(requests.RequestException):
                    app.RestrictedSession().get(url)
                request.assert_not_called()

    def response(self, data=b"User-agent: *\nCrawl-delay: 1\n", status=200):
        response = requests.Response()
        response.status_code = status
        response._content = data
        response._content_consumed = True
        response.close = MagicMock()
        return response

    def test_robots_and_submission_have_timeouts_and_cannot_follow_redirects(
        self,
    ):
        for path in ("/robots.txt", "/view/123", "/view/123/"):
            response = self.response()
            with patch.object(
                requests.Session, "request", return_value=response
            ) as request:
                actual = app.RestrictedSession().get(
                    "https://www.furaffinity.net" + path, timeout=None
                )
            self.assertEqual(actual.content, response.content)
            self.assertEqual(
                request.call_args.kwargs["timeout"], app.NETWORK_TIMEOUT
            )
            self.assertFalse(request.call_args.kwargs["allow_redirects"])
            self.assertTrue(request.call_args.kwargs["stream"])
            response.close.assert_called_once()

    def test_redirect_response_is_not_parsed_or_followed(self):
        response = self.response(status=302)
        with patch.object(
            requests.Session, "request", return_value=response
        ) as request:
            with self.assertRaises(requests.RequestException):
                app.RestrictedSession().get(
                    "https://www.furaffinity.net/view/123"
                )
        request.assert_called_once()
        response.close.assert_called_once()

    def test_large_provider_pages_are_rejected(self):
        response = self.response(b"x" * (app.MAX_RESPONSE + 1))
        with patch.object(requests.Session, "request", return_value=response):
            with self.assertRaises(requests.RequestException):
                app.RestrictedSession().get(
                    "https://www.furaffinity.net/view/123"
                )
        response.close.assert_called_once()

    def test_new_sessions_share_one_provider_request_delay_including_robots(
        self,
    ):
        response = self.response()
        with patch.object(
            requests.Session, "request", return_value=response
        ), patch.object(app.time, "monotonic", return_value=100):
            app.RestrictedSession().get(
                "https://www.furaffinity.net/robots.txt"
            )
            app.RestrictedSession().get("https://www.furaffinity.net/view/123")
        self.sleep.assert_called_once()
        self.assertAlmostEqual(self.sleep.call_args.args[0], 1.1)

    def test_actual_pinned_faapi_obeys_robots_before_fetching_submission(self):
        robots = self.response(
            b"User-agent: *\nDisallow: /view/\nCrawl-delay: 2\n"
        )
        with patch.object(
            requests.Session, "request", return_value=robots
        ) as request:
            service = app.ImportService()
            status, result = service.submission(123, body())
        self.assertEqual((status, result["error"]), (502, "provider_error"))
        request.assert_called_once()
        self.assertEqual(
            request.call_args.args[1], "https://www.furaffinity.net/robots.txt"
        )
        self.assertEqual(service.api.crawl_delay, 2)
        self.assertIn("github.com/kemopup/wildpaws", service.api.user_agent)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = MagicMock()
        cls.handler = type(
            "TestHandler", (app.ImportHandler,), {"service": cls.service}
        )
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), cls.handler)
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.service.reset_mock()
        self.service.submission.return_value = (200, {"id": 123})

    def request(self, method, path, data=None, headers=None):
        connection = http.client.HTTPConnection(
            *self.server.server_address, timeout=2
        )
        connection.request(method, path, body=data, headers=headers or {})
        response = connection.getresponse()
        result = (
            response.status,
            json.loads(response.read()),
            dict(response.getheaders()),
        )
        connection.close()
        return result

    def test_health_never_contacts_provider(self):
        status, result, headers = self.request("GET", "/health")
        self.assertEqual((status, result), (200, {"status": "ok"}))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.service.submission.assert_not_called()

    def test_submission_endpoint_passes_only_id_and_parsed_body(self):
        status, result, _ = self.request(
            "POST",
            "/submission/123/",
            json.dumps(body()),
            {"Content-Type": "application/json"},
        )
        self.assertEqual((status, result), (200, {"id": 123}))
        self.service.submission.assert_called_once_with(123, body())

    def test_unknown_or_arbitrary_url_endpoints_do_not_contact_provider(self):
        for path in (
            "/submission/123/?url=https://evil.test",
            "/submission/0/",
            "/logout/",
            "/submission/123",
            "/submission/https://evil.test/",
        ):
            self.assertEqual(self.request("POST", path)[0], 404)
        self.service.submission.assert_not_called()

    def test_invalid_or_oversized_request_is_sanitized(self):
        for data, headers in (
            ("first-secret-cookie", {"Content-Type": "application/json"}),
            ("{}", {"Content-Type": "text/plain"}),
            (
                "x" * (app.MAX_REQUEST + 1),
                {"Content-Type": "application/json"},
            ),
        ):
            status, result, _ = self.request(
                "POST", "/submission/123/", data, headers
            )
            self.assertEqual(status, 400)
            self.assertNotIn("first-secret-cookie", json.dumps(result))
        self.service.submission.assert_not_called()


if __name__ == "__main__":
    unittest.main()
