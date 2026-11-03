import io
import json
import threading
from copy import deepcopy
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest

from szurubooru import errors
from szurubooru.func import post_imports


SID_ONE = "FirstSession12345678901234567890"
SID_TWO = "SecondSession1234567890123456789"
POST_URL = "https://inkbunny.net/s/123"


@pytest.fixture(autouse=True)
def isolate_import_session(config_injector, monkeypatch):
    config_injector({"post_import": {}})
    monkeypatch.setattr(post_imports, "_INKBUNNY_SESSION", {})
    monkeypatch.setattr(
        post_imports, "_INKBUNNY_SESSION_LOCK", threading.Lock()
    )
    monkeypatch.setitem(post_imports._LOCKS, "inkbunny", threading.Lock())
    monkeypatch.setitem(post_imports._LAST_REQUEST, "inkbunny", 0.0)
    monkeypatch.setattr(post_imports.time, "sleep", lambda _seconds: None)

    def unexpected_network(*_args, **_kwargs):
        pytest.fail("The test attempted an unmocked network request.")

    monkeypatch.setattr(
        post_imports.urllib.request, "build_opener", unexpected_network
    )


@pytest.fixture
def transport(monkeypatch):
    def make(responses):
        responses = iter(responses)
        opener = MagicMock()

        def open_response(_request, timeout):
            assert timeout == 15
            return io.BytesIO(json.dumps(next(responses)).encode("utf8"))

        opener.open.side_effect = open_response
        monkeypatch.setattr(
            post_imports.urllib.request,
            "build_opener",
            lambda *_handlers: opener,
        )
        return opener

    return make


def _account(config_injector, **updates):
    settings = {"username": "PrivateAccount", "password": "private&p=secret"}
    settings.update(updates)
    config_injector(
        {
            "post_import": {
                "inkbunny": dict(settings, api_key="unused-inkbunny-key"),
                "e621": {"username": "foreign", "api_key": "foreign-key"},
                "rule34": {"user_id": "12345", "api_key": "foreign-key"},
            }
        }
    )


def _forms(opener):
    return [
        parse_qs(call.args[0].data.decode("utf8"))
        for call in opener.open.call_args_list
    ]


def _endpoints(opener):
    return [call.args[0].full_url for call in opener.open.call_args_list]


def _submission():
    return {
        "submissions": [
            {
                "submission_id": "123",
                "username": "Uploader_One",
                "description": "[b]Artist note[/b]",
                "public": "t",
                "hidden": "f",
                "deleted": "f",
                "rating_id": "0",
                "keywords": [
                    {"keyword_name": "must_not_import", "contributed": "f"}
                ],
                "files": [
                    {
                        "submission_id": "123",
                        "file_id": "22",
                        "submission_file_order": "1",
                        "file_url_full": "https://tx.ib.metapix.net/files/full/1/22_video.mp4",
                        "thumbnail_url_large_noncustom": "https://tx.ib.metapix.net/thumbnails/large/1/22_video.jpg",
                        "mimetype": "video/mp4",
                        "deleted": "f",
                    },
                    {
                        "submission_id": "123",
                        "file_id": "11",
                        "submission_file_order": "0",
                        "file_url_full": "https://tx.ib.metapix.net/files/full/1/11_image.png",
                        "mimetype": "image/png",
                        "deleted": "f",
                    },
                ],
            }
        ]
    }


def test_account_credentials_travel_only_in_fixed_https_login_post_body(
    config_injector, transport
):
    _account(config_injector)
    payload = {"submissions": []}
    opener = transport([{"sid": SID_ONE}, payload])

    assert post_imports._inkbunny_payload(123) == payload

    assert _endpoints(opener) == [
        "https://inkbunny.net/api_login.php",
        "https://inkbunny.net/api_submissions.php",
    ]
    forms = _forms(opener)
    assert forms[0] == {
        "username": ["PrivateAccount"],
        "password": ["private&p=secret"],
        "output_mode": ["json"],
    }
    assert forms[1] == {
        "sid": [SID_ONE],
        "submission_ids": ["123"],
        "show_description": ["yes"],
        "output_mode": ["json"],
    }
    for call in opener.open.call_args_list:
        request = call.args[0]
        assert request.get_method() == "POST"
        assert request.get_header("Authorization") is None
        assert request.get_header("Content-type") == (
            "application/x-www-form-urlencoded"
        )
        assert urlsplit(request.full_url).query == ""
        assert "private" not in repr(request.header_items()).lower()
        assert "foreign-key" not in repr(request.__dict__)
        assert "unused-inkbunny-key" not in repr(request.__dict__)


def test_account_session_reused_without_overriding_account_rating_choices(
    config_injector, transport
):
    _account(config_injector)
    first, second = {"submissions": [1]}, {"submissions": [2]}
    opener = transport([{"sid": SID_ONE}, first, second])

    assert post_imports._inkbunny_payload(123) == first
    assert post_imports._inkbunny_payload(456) == second

    assert _endpoints(opener) == [
        "https://inkbunny.net/api_login.php",
        "https://inkbunny.net/api_submissions.php",
        "https://inkbunny.net/api_submissions.php",
    ]
    assert [form.get("sid") for form in _forms(opener)[1:]] == [
        [SID_ONE],
        [SID_ONE],
    ]


def test_session_tokens_are_opaque_and_urlencoded_in_post_body(
    config_injector, transport
):
    _account(config_injector)
    sid = "Opaque_Session-token.with+punctuation/=1234567890"
    opener = transport([{"sid": sid}, {"submissions": []}])
    post_imports._inkbunny_payload(123)
    assert _forms(opener)[1]["sid"] == [sid]
    assert all(urlsplit(url).query == "" for url in _endpoints(opener))


@pytest.mark.parametrize("error_code", [2, "2"])
def test_expired_session_refreshes_once_then_reuses_replacement(
    error_code, config_injector, transport
):
    _account(config_injector)
    payload = {"submissions": []}
    opener = transport(
        [
            {"sid": SID_ONE},
            payload,
            {"error_code": error_code},
            {"sid": SID_TWO},
            payload,
            payload,
        ]
    )

    assert post_imports._inkbunny_payload(123) == payload
    assert post_imports._inkbunny_payload(456) == payload
    assert post_imports._inkbunny_payload(789) == payload

    assert len(opener.open.call_args_list) == 6
    assert [form.get("sid") for form in _forms(opener)] == [
        None,
        [SID_ONE],
        [SID_ONE],
        None,
        [SID_TWO],
        [SID_TWO],
    ]


def test_repeated_rejected_session_stops_after_one_refresh(
    config_injector, transport
):
    _account(config_injector)
    opener = transport(
        [
            {"sid": SID_ONE},
            {"error_code": 2, "error_message": SID_ONE},
            {"sid": SID_TWO},
            {"error_code": 2, "error_message": SID_TWO},
        ]
    )

    with pytest.raises(post_imports.ImportError, match="session") as result:
        post_imports._inkbunny_payload(123)

    assert opener.open.call_count == 4
    assert SID_ONE not in str(result.value)
    assert SID_TWO not in str(result.value)
    assert post_imports._INKBUNNY_SESSION == {}


@pytest.mark.parametrize("error_code", [0, "0"])
def test_failed_login_explains_api_access_without_returning_provider_secrets(
    error_code, config_injector, transport
):
    _account(config_injector)
    opener = transport(
        [
            {
                "error_code": error_code,
                "error_message": "private&p=secret PrivateAccount foreign-key",
            }
        ]
    )

    with pytest.raises(post_imports.ImportError, match="API Access") as result:
        post_imports._inkbunny_payload(123)

    assert opener.open.call_count == 1
    for secret in ("private&p=secret", "PrivateAccount", "foreign-key"):
        assert secret not in str(result.value)
    assert post_imports._INKBUNNY_SESSION == {}


@pytest.mark.parametrize(
    "settings",
    [
        {"username": "member", "password": ""},
        {"username": "", "password": "secret"},
        {"username": 123, "password": "secret"},
        {"username": "member", "password": 123},
    ],
)
def test_incomplete_or_nonstring_credentials_fail_before_network(
    settings, config_injector
):
    _account(config_injector, **settings)
    with pytest.raises(post_imports.ImportError):
        post_imports._inkbunny_payload(123)


@pytest.mark.parametrize(
    "updates", [{"password": "replacement-secret"}, {"username": "NewMember"}]
)
def test_changed_account_settings_invalidate_cached_session(
    updates, config_injector, transport
):
    _account(config_injector)
    payload = {"submissions": []}
    opener = transport([{"sid": SID_ONE}, payload, {"sid": SID_TWO}, payload])
    post_imports._inkbunny_payload(123)

    _account(config_injector, **updates)
    post_imports._inkbunny_payload(123)

    assert _endpoints(opener).count("https://inkbunny.net/api_login.php") == 2
    assert _forms(opener)[-1]["sid"] == [SID_TWO]


@pytest.mark.parametrize("settings", [{}, {"username": "", "password": ""}])
def test_unconfigured_account_uses_guest_session_and_sets_guest_ratings_once(
    settings, config_injector, transport
):
    config_injector({"post_import": {"inkbunny": settings}})
    payload = {"submissions": []}
    opener = transport([{"sid": SID_ONE}, {"sid": SID_ONE}, payload, payload])

    post_imports._inkbunny_payload(123)
    post_imports._inkbunny_payload(456)

    assert _endpoints(opener) == [
        "https://inkbunny.net/api_login.php",
        "https://inkbunny.net/api_userrating.php",
        "https://inkbunny.net/api_submissions.php",
        "https://inkbunny.net/api_submissions.php",
    ]
    assert _forms(opener)[0] == {
        "username": ["guest"],
        "output_mode": ["json"],
    }
    assert _forms(opener)[1] == {
        "sid": [SID_ONE],
        "output_mode": ["json"],
        **{"tag[%d]" % tag: ["yes"] for tag in range(2, 6)},
    }


@pytest.mark.parametrize(
    "url,canonical",
    [
        (POST_URL, POST_URL),
        (" http://www.inkbunny.net/s/000123/?ref=gallery#comments ", POST_URL),
        (POST_URL + "-p2-?ref=gallery", POST_URL + "-p2-"),
        (POST_URL + "-p2/#p2", POST_URL + "-p2-"),
        (POST_URL + "?ref=gallery#p2", POST_URL + "-p2-"),
        (
            "https://inkbunny.net/submissionview.php?id=123&ref=gallery#p2",
            POST_URL + "-p2-",
        ),
    ],
)
def test_submission_links_normalize_without_gallery_query_information(
    url, canonical
):
    assert post_imports.parse_post_url(url) == ("inkbunny", 123, canonical)


@pytest.mark.parametrize(
    "url",
    [
        "https://inkbunny.net.evil.test/s/123",
        "https://inkbunny.net@evil.test/s/123",
        "https://user:secret@inkbunny.net/s/123",
        "https://inkbunny.net:443/s/123",
        "https://inkbunny.net:invalid/s/123",
        "https://127.0.0.1/s/123",
        "https://inkbunny.net/s/0",
        "https://inkbunny.net/s/-123",
        "https://inkbunny.net/s/123/other",
        "https://inkbunny.net/s/123-p0-",
        "https://inkbunny.net/s/123-p2-#p3",
        "https://inkbunny.net/s/123#p0",
        "https://inkbunny.net/s/123#p2extra",
        "https://inkbunny.net/submissionview.php?id=1&id=2",
        "https://inkbunny.net/submissionview.php?id=123&id=123",
        "https://inkbunny.net/submissionview.php?id=abc",
        "https://inkbunny.net/submissionview.php",
    ],
)
def test_unsafe_or_ambiguous_submission_links_fail_before_network(url):
    with pytest.raises(errors.ValidationError):
        post_imports.preview(url)


@pytest.mark.parametrize("selected_page", [None, 2])
def test_preview_combines_session_metadata_and_canonical_page_selection(
    selected_page, config_injector, transport
):
    _account(config_injector)
    payload = _submission()
    unchanged = deepcopy(payload)
    transport([{"sid": SID_ONE}, payload])
    url = POST_URL + ("#p2" if selected_page else "?ref=gallery#comments")

    result = post_imports.preview(url)

    assert result["provider"] == "inkbunny"
    assert result["postUrl"] == POST_URL + ("-p2-" if selected_page else "")
    assert result["tags"] == {"artist": [], "character": []}
    assert result["safety"] == "safe"
    assert "Uploader\\_One" in result["description"]
    assert "**Artist note**" in result["description"]
    assert "must_not_import" not in result["description"]
    assert "sid=" not in repr(result)
    assert "private&p=secret" not in repr(result)
    if selected_page:
        assert len(result["media"]) == 1
        assert result["mimeType"] == "video/mp4"
        assert result["contentUrl"].endswith("22_video.mp4")
        assert result["media"][0]["postUrl"] == POST_URL + "-p2-"
        assert result["media"][0]["previewUrl"].endswith("22_video.jpg")
    else:
        assert [item["mimeType"] for item in result["media"]] == [
            "image/png",
            "video/mp4",
        ]
        assert result["contentUrl"].endswith("11_image.png")
    assert payload == unchanged
