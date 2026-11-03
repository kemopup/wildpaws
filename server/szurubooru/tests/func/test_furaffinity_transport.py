import base64
import io
import json
import urllib.error
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from szurubooru import errors
from szurubooru.func import post_imports


POST_ID = 12345
POST_URL = "https://www.furaffinity.net/view/12345/"
ORIGINAL = (
    "https://d.furaffinity.net/art/owner/1700000000/1700000000.owner_video.mp4"
)
THUMBNAIL = "https://t.furaffinity.net/12345@600-1700000000.jpg"
COOKIE_A = "private-cookie-a-123456"
COOKIE_B = "private-cookie-b-654321"


@pytest.fixture(autouse=True)
def isolated_config(config_injector, monkeypatch):
    config_injector(
        {"post_import": {"furaffinity": {"a": COOKIE_A, "b": COOKIE_B}}}
    )

    def unexpected_network(*_args, **_kwargs):
        pytest.fail("Unexpected unmocked network request")

    monkeypatch.setattr(
        post_imports.urllib.request, "build_opener", unexpected_network
    )


def _payload():
    return {
        "id": POST_ID,
        "author": {
            "name": "Owner",
            "url": "https://www.furaffinity.net/user/owner/",
        },
        "description": "[b]Submission note[/b]",
        "file_url": ORIGINAL,
        "thumbnail_url": THUMBNAIL,
        "rating": "Mature",
        "tags": ["not_an_artist", "not_a_character"],
        "cookies": [{"name": "a", "value": COOKIE_A}],
    }


@pytest.fixture
def transport(monkeypatch):
    def make(body=None, failure=None):
        opener = MagicMock()
        if failure is not None:
            opener.open.side_effect = failure
        else:
            data = (
                json.dumps(_payload() if body is None else body).encode("utf8")
                if not isinstance(body, bytes)
                else body
            )
            opener.open.return_value = io.BytesIO(data)
        builder = MagicMock(return_value=opener)
        monkeypatch.setattr(
            post_imports.urllib.request, "build_opener", builder
        )
        return builder, opener

    return make


@pytest.mark.parametrize(
    "url",
    [
        POST_URL,
        "https://furaffinity.net/view/12345",
        "https://www.furaffinity.net/full/12345/?search=discard&a=discard#comments",
        "http://furaffinity.net/view/0012345/?b=discard",
    ],
)
def test_furaffinity_links_normalize_post_id_and_remove_unrelated_arguments(
    url,
):
    assert post_imports.parse_post_url(url) == (
        "furaffinity",
        POST_ID,
        POST_URL,
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://furaffinity.net.evil.test/view/12345/",
        "https://furaffinity.net@evil.test/view/12345/",
        "https://user:password@www.furaffinity.net/view/12345/",
        "https://www.furaffinity.net:443/view/12345/",
        "https://www.furaffinity.net:bad/view/12345/",
        "https://127.0.0.1/view/12345/",
        "ftp://www.furaffinity.net/view/12345/",
        "https://www.furaffinity.net/view/0/",
        "https://www.furaffinity.net/view/-1/",
        "https://www.furaffinity.net/view/12345/other/",
        "https://www.furaffinity.net/gallery/owner/",
        "https://www.furaffinity.net/view/?id=12345",
        "https://www.furaffinity.net/view/1234567890123/",
    ],
)
def test_invalid_furaffinity_links_do_not_contact_helper(url):
    with patch.object(post_imports, "_furaffinity_payload") as helper:
        with pytest.raises(errors.ValidationError):
            post_imports.preview(url)
    helper.assert_not_called()


@pytest.mark.parametrize(
    "settings",
    [
        None,
        {},
        {"a": COOKIE_A},
        {"b": COOKIE_B},
        {"a": "", "b": COOKIE_B},
        {"a": 12345, "b": COOKIE_B},
        {"a": COOKIE_A, "b": True},
        {"a": COOKIE_A + ";other=secret", "b": COOKIE_B},
        {"a": COOKIE_A + "\r\nCookie: secret", "b": COOKIE_B},
        {"a": "contains space", "b": COOKIE_B},
        {"a": "ü", "b": COOKIE_B},
        {"a": "a" * 4097, "b": COOKIE_B},
        "not-a-mapping",
        ["invalid"],
    ],
)
def test_missing_or_malformed_cookie_yaml_is_rejected_before_transport(
    settings, config_injector
):
    config_injector({"post_import": {"furaffinity": settings}})
    with pytest.raises(post_imports.ImportError) as result:
        post_imports._furaffinity_payload(POST_ID)
    assert COOKIE_A not in str(result.value)
    assert COOKIE_B not in str(result.value)


def test_private_cookies_travel_only_in_json_body_to_fixed_local_helper(
    transport, config_injector
):
    config_injector(
        {
            "post_import": {
                "furaffinity": {
                    "a": COOKIE_A,
                    "b": COOKIE_B,
                    "url": "https://public-mirror.example",
                    "api_key": "unused-key",
                },
                "e621": {
                    "username": "foreign-user",
                    "api_key": "foreign-secret",
                },
            }
        }
    )
    builder, opener = transport()
    result = post_imports._furaffinity_payload(POST_ID)
    request = opener.open.call_args.args[0]
    assert request.full_url == "http://fa-import:8080/submission/12345/"
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Cookie") is None
    assert request.get_header("Authorization") is None
    assert json.loads(request.data.decode("utf8")) == {
        "cookies": [
            {"name": "a", "value": COOKIE_A},
            {"name": "b", "value": COOKIE_B},
        ],
        "bbcode": True,
    }
    assert "foreign-secret" not in repr(request.__dict__)
    assert "unused-key" not in repr(request.__dict__)
    assert COOKIE_A not in request.full_url
    assert COOKIE_B not in repr(request.header_items())
    assert builder.call_args.args[0].proxies == {}
    assert isinstance(builder.call_args.args[1], post_imports._NoRedirect)
    assert (
        builder.call_args.args[1].redirect_request(
            request, None, 307, "Redirect", {}, "https://public-mirror.example"
        )
        is None
    )
    assert 1 <= opener.open.call_args.kwargs["timeout"] <= 60
    assert result["id"] == POST_ID


@pytest.mark.parametrize(
    "status,error,expected",
    [
        (401, "auth_required", "Replace the a and b cookies"),
        (403, "provider_error", "Replace the a and b cookies"),
        (404, "unavailable", "deleted, private, or unavailable"),
        (429, "busy", "busy or limiting"),
        (503, "provider_error", "busy or limiting"),
        (422, "theme_required", "Modern theme"),
        (500, "provider_error", "could not return"),
        (307, "redirect", "could not return"),
    ],
)
def test_helper_errors_are_actionable_and_do_not_expose_cookies(
    status, error, expected, transport
):
    body = json.dumps({"error": error, "message": COOKIE_A + COOKIE_B}).encode(
        "utf8"
    )
    failure = urllib.error.HTTPError(
        "http://fa-import:8080/submission/12345/",
        status,
        COOKIE_A,
        {},
        io.BytesIO(body),
    )
    _builder, opener = transport(failure=failure)
    with pytest.raises(post_imports.ImportError, match=expected) as result:
        post_imports._furaffinity_payload(POST_ID)
    assert COOKIE_A not in str(result.value)
    assert COOKIE_B not in str(result.value)
    opener.open.assert_called_once()


@pytest.mark.parametrize(
    "body", [b"not-json " + COOKIE_A.encode(), b"\xff\xfe", b"[unclosed"]
)
def test_invalid_json_response_is_a_fixed_nonsecret_error(body, transport):
    transport(body=body)
    with pytest.raises(
        post_imports.ImportError, match="valid post metadata"
    ) as result:
        post_imports._furaffinity_payload(POST_ID)
    assert COOKIE_A not in str(result.value)


def test_oversized_metadata_is_rejected_before_json_decode(transport):
    transport(body=b"x" * (post_imports._MAX_METADATA + 1))
    with pytest.raises(post_imports.ImportError, match="too much metadata"):
        post_imports._furaffinity_payload(POST_ID)


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError(COOKIE_A),
        TimeoutError(COOKIE_B),
        OSError(COOKIE_A),
    ],
)
def test_unavailable_helper_has_fixed_actionable_error_without_credentials(
    failure, transport
):
    transport(failure=failure)
    with pytest.raises(
        post_imports.ImportError, match="helper is unavailable"
    ) as result:
        post_imports._furaffinity_payload(POST_ID)
    assert COOKIE_A not in str(result.value)
    assert COOKIE_B not in str(result.value)


def test_preview_uses_normalized_metadata_and_never_exposes_cookies_or_uncategorized_tags(
    transport,
):
    _builder, opener = transport()
    result = post_imports.preview(
        "http://furaffinity.net/full/12345/?a=not_forwarded#comments"
    )
    assert result["postUrl"] == POST_URL
    assert result["contentUrl"] == ORIGINAL
    assert result["mimeType"] == "video/mp4"
    assert result["tags"] == {"artist": [], "character": []}
    assert (
        "Submission owner: [Owner](https://www.furaffinity.net/user/owner/)"
        in result["description"]
    )
    assert "**Submission note**" in result["description"]
    assert result["safety"] == "sketchy"
    for excluded in (
        COOKIE_A,
        COOKIE_B,
        "not_an_artist",
        "not_a_character",
        "not_forwarded",
    ):
        assert excluded not in repr(result)
    assert (
        opener.open.call_args.args[0].full_url
        == "http://fa-import:8080/submission/12345/"
    )


@pytest.mark.parametrize(
    "body", [[], "string", {"error": "auth_required", "message": COOKIE_A}]
)
def test_non_post_json_is_rejected_by_preview_without_leaking_response(
    body, transport
):
    transport(body=body)
    with pytest.raises(post_imports.ImportError) as result:
        post_imports.preview(POST_URL)
    assert COOKIE_A not in str(result.value)


def test_thumbnail_downloads_only_validated_poster_without_modifying_original_video():
    metadata = {
        "contentUrl": ORIGINAL,
        "mimeType": "video/mp4",
        "previewUrl": THUMBNAIL,
        "description": "Keep edited description",
    }
    unchanged = deepcopy(metadata)
    jpeg = b"\xff\xd8synthetic-preview\xff\xd9"
    with patch.object(
        post_imports, "preview", return_value=metadata
    ) as preview, patch(
        "szurubooru.func.net.download", return_value=b"poster-bytes"
    ) as download, patch(
        "szurubooru.func.post_imports.images.Image"
    ) as image_type:
        image_type.return_value.to_jpeg.return_value = jpeg
        result = post_imports.thumbnail(
            "https://furaffinity.net/full/12345/?b=discarded"
        )
    preview.assert_called_once_with(POST_URL)
    download.assert_called_once_with(THUMBNAIL)
    assert base64.b64decode(result.split(",", 1)[1]) == jpeg
    assert metadata == unchanged
    image_type.assert_called_once_with(b"poster-bytes")
    image_type.return_value.resize_fill.assert_called_once_with(192, 128)


@pytest.mark.parametrize(
    "poster",
    [
        None,
        ORIGINAL,
        "https://evil.test/12345.jpg",
        THUMBNAIL + "?a=secret",
        "https://t.furaffinity.net:443/12345.jpg",
    ],
)
def test_thumbnail_does_not_proxy_arbitrary_private_or_original_video_urls(
    poster,
):
    with patch.object(
        post_imports,
        "preview",
        return_value={"previewUrl": poster, "contentUrl": ORIGINAL},
    ), patch("szurubooru.func.net.download") as download:
        with pytest.raises(post_imports.ImportError):
            post_imports.thumbnail(POST_URL)
    download.assert_not_called()
