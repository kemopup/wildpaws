import io
import urllib.error
from unittest.mock import patch

import pytest

from szurubooru.func import net


@pytest.fixture(autouse=True)
def inject_config(config_injector):
    config_injector(
        {
            "user_agent": None,
            "max_dl_filesize": 1024,
            "post_import": {
                "user_agent": "WildpawsTest/1.0 (by test_importer)",
                "e621": {
                    "username": "private-account",
                    "api_key": "secret-e621-key",
                },
                "e6ai": {
                    "username": "private-account",
                    "api_key": "secret-e6ai-key",
                },
                "rule34": {"user_id": "123", "api_key": "secret-rule34-key"},
                "gelbooru": {
                    "user_id": "456",
                    "api_key": "secret-gelbooru-key",
                },
            },
        }
    )


def _download_request(url, body=b"original\x00bytes\xff"):
    # BytesIO is a context manager and obeys the downloader's chunked reads.
    with patch.object(
        net.urllib.request, "urlopen", return_value=io.BytesIO(body)
    ) as opened:
        result = net.download(url)
    opened.assert_called_once()
    return opened.call_args[0][0], result


@pytest.mark.parametrize(
    "url",
    [
        "https://e621.net/data/original.png",
        "https://static1.e621.net/data/original.png",
        "https://e6ai.net/data/original.png",
        "https://static1.e6ai.net/data/original.png",
        "https://pbs.twimg.com/media/PhotoA?format=jpg&name=orig",
        "https://video.twimg.com/ext_tw_video/123/pu/vid/VideoA.mp4",
        "https://wimg.rule34.xxx/images/original.png",
        "https://rule34.us/images/original.png",
        "https://cdn.rule34.us:443/images/original.png",
    ],
)
def test_provider_downloads_identify_importer_without_sending_api_credentials(
    url,
):
    request, result = _download_request(url)
    assert (
        request.get_header("User-agent")
        == "WildpawsTest/1.0 (by test_importer)"
    )
    assert request.get_header("Referer") == url
    assert request.get_header("Authorization") is None
    assert request.full_url == url
    assert "secret-" not in str(request.header_items())
    assert "private-account" not in str(request.header_items())
    assert result == b"original\x00bytes\xff"


@pytest.mark.parametrize(
    "url",
    [
        "https://artist.example/art.png",
        "http://static1.e6ai.net/data/original.png",
        "https://e6ai.net.evil.test/data/original.png",
        "https://not-e6ai.net/data/original.png",
        "https://e6ai.net@evil.test/data/original.png",
        "https://user:password@static1.e6ai.net/data/original.png",
        "https://static1.e6ai.net:444/data/original.png",
    ],
)
def test_other_downloads_do_not_receive_provider_specific_headers(url):
    request, result = _download_request(url)
    assert request.get_header("User-agent") is None
    assert result == b"original\x00bytes\xff"


@pytest.mark.parametrize(
    "url",
    [
        "https://static1.e6ai.net/data/original.png",
        "https://artist.example/art.png",
    ],
)
def test_explicit_general_user_agent_takes_precedence(config_injector, url):
    config_injector(
        {
            "user_agent": "MyExistingDownloader/2.0",
            "max_dl_filesize": 1024,
            "post_import": {"user_agent": "ImporterOnly/1.0"},
        }
    )
    request, _result = _download_request(url)
    assert request.get_header("User-agent") == "MyExistingDownloader/2.0"


def test_unconfigured_importer_uses_descriptive_application_default(
    config_injector,
):
    config_injector({"user_agent": None, "max_dl_filesize": 1024})
    request, _result = _download_request(
        "https://static1.e6ai.net/data/original.png"
    )
    assert (
        request.get_header("User-agent")
        == "Wildpaws/1.0 (https://github.com/kemopup/wildpaws)"
    )


def test_oversized_provider_download_is_still_rejected(config_injector):
    config_injector({"user_agent": None, "max_dl_filesize": 4})
    with patch.object(
        net.urllib.request, "urlopen", return_value=io.BytesIO(b"12345")
    ) as opened:
        with pytest.raises(net.DownloadTooLargeError):
            net.download("https://static1.e6ai.net/data/original.png")
    opened.assert_called_once()


def test_provider_403_remains_a_download_error_without_retry():
    url = "https://static1.e6ai.net/data/original.png"
    failure = urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
    with patch.object(
        net.urllib.request, "urlopen", side_effect=failure
    ) as opened:
        with pytest.raises(net.DownloadError, match="HTTP 403"):
            net.download(url)
    opened.assert_called_once()


def test_resolved_provider_url_receives_importer_identity():
    destination = "https://static1.e6ai.net/data/original.png"
    with patch.object(
        net, "_get_youtube_dl_content_url", return_value=destination
    ), patch.object(
        net.urllib.request, "urlopen", return_value=io.BytesIO(b"original")
    ) as opened:
        assert (
            net.download(
                "https://artist.example/post/1", use_video_downloader=True
            )
            == b"original"
        )
    request = opened.call_args[0][0]
    assert request.full_url == destination
    assert (
        request.get_header("User-agent")
        == "WildpawsTest/1.0 (by test_importer)"
    )


@pytest.mark.parametrize(
    "url,referer",
    [
        ("https://tbib.org/images/original.png", "https://tbib.org/"),
        ("https://cdn.tbib.org/images/original.png", "https://tbib.org/"),
        ("https://gelbooru.com/images/original.png", "https://gelbooru.com/"),
        (
            "https://img3.gelbooru.com/images/original.png",
            "https://gelbooru.com/",
        ),
        (
            "https://inkbunny.net/files/full/original.png",
            "https://inkbunny.net/",
        ),
        (
            "https://tx.ib.metapix.net/files/full/original.mp4",
            "https://inkbunny.net/",
        ),
        (
            "https://d.furaffinity.net/art/owner/123/original.png",
            "https://www.furaffinity.net/",
        ),
        (
            "https://t.furaffinity.net/123@200.jpg",
            "https://www.furaffinity.net/",
        ),
    ],
)
def test_additional_providers_get_application_identity_and_site_referer(
    url, referer
):
    request, result = _download_request(url)
    assert (
        request.get_header("User-agent")
        == "WildpawsTest/1.0 (by test_importer)"
    )
    assert request.get_header("Referer") == referer
    assert request.get_header("Authorization") is None
    assert "api_key" not in request.full_url
    assert "secret-" not in str(request.header_items())
    assert result == b"original\x00bytes\xff"


@pytest.mark.parametrize(
    "url",
    [
        "https://tbib.org.evil.test/images/original.png",
        "https://gelbooru.com.evil.test/images/original.png",
        "https://not-tbib.org/images/original.png",
        "https://not-gelbooru.com/images/original.png",
        "https://inkbunny.net.evil.test/files/full/original.png",
        "https://ib.metapix.net.evil.test/files/full/original.png",
        "https://d.furaffinity.net.evil.test/art/owner/123/original.png",
    ],
)
def test_lookalike_domains_receive_no_provider_identity_or_site_referer(url):
    request, _result = _download_request(url)
    assert request.get_header("User-agent") is None
    assert request.get_header("Referer") == url
