import base64
import hashlib
import io
import json
from copy import deepcopy
from unittest.mock import patch
from urllib.parse import urlencode

import pytest

from szurubooru import db, errors, model
from szurubooru.func import bluesky_network, net, post_imports


DID = "did:plc:abcdefghijklmnopqrstuvwx"
RKEY = "3lfixturepost"
HANDLE = "artist.example"
PDS = "https://account.us-east.host.bsky.network"
POST = "https://bsky.app/profile/%s/post/%s" % (DID, RKEY)
HANDLE_POST = "https://bsky.app/profile/%s/post/%s" % (HANDLE, RKEY)
ORIGINAL_BYTES = b"unchanged-original-file\x00\xff"
CID = "b" + base64.b32encode(
    b"\x01\x55\x12\x20" + hashlib.sha256(ORIGINAL_BYTES).digest()
).decode("ascii").lower().rstrip("=")
BLOB = (
    PDS
    + "/xrpc/com.atproto.sync.getBlob?"
    + urlencode({"did": DID, "cid": CID})
)
POSTER = "https://cdn.bsky.app/img/feed_thumbnail/plain/%s/%s@jpeg" % (
    DID,
    CID,
)
JPEG = b"\xff\xd8synthetic-preview\xff\xd9"


@pytest.fixture(autouse=True)
def private_credentials_are_not_transport_inputs(config_injector):
    config_injector(
        {
            "user_agent": None,
            "max_dl_filesize": 1024,
            "post_import": {
                "user_agent": "WildpawsTest/1.0 (public Bluesky import)",
                "bluesky": {
                    "username": "unused-account",
                    "api_key": "unused-bluesky-secret",
                },
                "furaffinity": {
                    "a": "unused-cookie-a",
                    "b": "unused-cookie-b",
                },
                "e621": {
                    "username": "foreign-account",
                    "api_key": "foreign-secret",
                },
            },
        }
    )


def _payload():
    return {
        "posts": [
            {
                "uri": "at://%s/app.bsky.feed.post/%s" % (DID, RKEY),
                "author": {"did": DID, "handle": HANDLE},
                "record": {
                    "$type": "app.bsky.feed.post",
                    "text": "Original post text #manual_tag",
                    "createdAt": "2026-10-31T12:00:00Z",
                    "embed": {
                        "$type": "app.bsky.embed.images",
                        "images": [
                            {
                                "alt": "Image alt text",
                                "image": {
                                    "$type": "blob",
                                    "ref": {"$link": CID},
                                    "mimeType": "image/png",
                                    "size": len(ORIGINAL_BYTES),
                                },
                            }
                        ],
                    },
                },
                "embed": {
                    "$type": "app.bsky.embed.images#view",
                    "images": [
                        {
                            "thumb": POSTER,
                            "fullsize": POSTER.replace(
                                "feed_thumbnail", "feed_fullsize"
                            ),
                            "alt": "Image alt text",
                        }
                    ],
                },
                "labels": [],
            }
        ],
        "api_key": "foreign-secret",
    }


@pytest.mark.parametrize(
    "url,canonical",
    [
        (HANDLE_POST + "?q=discarded#comments", HANDLE_POST),
        (
            "http://www.bsky.app/profile/Artist.Example/post/" + RKEY,
            HANDLE_POST,
        ),
        (POST + "?api_key=discarded#media-2", POST + "#media-2"),
    ],
)
def test_global_import_parser_preserves_bluesky_selectors_and_strips_search_data(
    url, canonical
):
    assert post_imports.parse_post_url(url) == ("bluesky", RKEY, canonical)


@pytest.mark.parametrize(
    "url",
    [
        "https://bsky.app.evil.test/profile/artist.example/post/" + RKEY,
        POST + "#media-0",
    ],
)
def test_invalid_bluesky_import_does_not_request_public_or_private_provider_data(
    url,
):
    with patch.object(
        bluesky_network, "fetch_metadata"
    ) as fetch, patch.object(post_imports, "_request_json") as generic:
        with pytest.raises(errors.ValidationError):
            post_imports.preview(url)
    fetch.assert_not_called()
    generic.assert_not_called()


def test_bluesky_preview_delegates_public_identity_and_selected_media_without_credentials():
    payload = _payload()
    normalized = {"provider": "bluesky", "contentUrl": BLOB}
    with patch.object(
        bluesky_network, "fetch_metadata", return_value=(payload, DID, PDS)
    ) as fetch, patch.object(
        post_imports.import_bluesky, "normalize", return_value=normalized
    ) as normalize, patch.object(
        post_imports, "_request_json"
    ) as generic:
        result = post_imports.preview(
            HANDLE_POST + "?password=discarded#media-2"
        )
    assert result == normalized
    fetch.assert_called_once_with(
        HANDLE, RKEY, user_agent="WildpawsTest/1.0 (public Bluesky import)"
    )
    normalize.assert_called_once_with(payload, DID, RKEY, PDS, 2)
    generic.assert_not_called()
    assert "secret" not in repr(fetch.call_args)
    assert "cookie" not in repr(fetch.call_args)


def test_bluesky_preview_integration_keeps_stored_original_without_creating_tags_or_posts():
    payload = _payload()
    unchanged = deepcopy(payload)
    with patch.object(
        bluesky_network, "fetch_metadata", return_value=(payload, DID, PDS)
    ), patch.object(post_imports, "_request_json") as generic:
        result = post_imports.preview(HANDLE_POST)
    assert result["provider"] == "bluesky"
    assert result["postUrl"] == POST
    assert result["contentUrl"] == BLOB
    assert result["mimeType"] == "image/png"
    assert result["tags"] == {"artist": [], "character": []}
    assert "Original post text" in result["description"]
    assert HANDLE in result["description"]
    assert "foreign-secret" not in json.dumps(result)
    assert payload == unchanged
    generic.assert_not_called()
    assert db.session.query(model.Post).count() == 0
    assert db.session.query(model.Tag).count() == 0


def test_unavailable_public_post_is_not_retried_through_other_import_paths():
    with patch.object(
        bluesky_network,
        "fetch_metadata",
        side_effect=bluesky_network.BlueskyError("Public post unavailable"),
    ), patch.object(post_imports, "_request_json") as generic, patch.object(
        net, "download"
    ) as download:
        with pytest.raises(errors.ProcessingError, match="unavailable"):
            post_imports.preview(HANDLE_POST)
    generic.assert_not_called()
    download.assert_not_called()


def test_bluesky_thumbnail_resolves_selected_post_and_downloads_only_its_validated_poster():
    selected = POST + "#media-2"
    metadata = {
        "contentUrl": BLOB,
        "mimeType": "video/mp4",
        "media": [{"previewUrl": POSTER}],
        "description": "Keep description intact",
    }
    unchanged = deepcopy(metadata)
    with patch.object(
        post_imports, "preview", return_value=metadata
    ) as preview, patch.object(
        net, "download", return_value=b"poster"
    ) as download, patch.object(
        post_imports.images, "Image"
    ) as image_type:
        image_type.return_value.to_jpeg.return_value = JPEG
        result = post_imports.thumbnail(POST + "?q=discarded#media-2")
    preview.assert_called_once_with(selected)
    download.assert_called_once_with(POSTER)
    assert base64.b64decode(result.split(",", 1)[1]) == JPEG
    assert metadata == unchanged
    assert db.session.query(model.Post).count() == 0


@pytest.mark.parametrize(
    "poster",
    [
        None,
        BLOB,
        "https://video.bsky.app/watch/%s/%s/playlist.m3u8" % (DID, CID),
        "https://cdn.bsky.app.evil.test/thumbnail.jpg",
    ],
)
def test_bluesky_thumbnail_never_downloads_arbitrary_urls_original_blobs_or_hls(
    poster,
):
    with patch.object(
        post_imports,
        "preview",
        return_value={"media": [{"previewUrl": poster}]},
    ), patch.object(net, "download") as download:
        with pytest.raises(errors.ProcessingError):
            post_imports.thumbnail(POST)
    download.assert_not_called()


@pytest.mark.parametrize("general_agent", [None, "GeneralDownloader/2.0"])
def test_original_blob_download_is_guarded_before_video_tools_and_returns_identical_bytes(
    general_agent, config_injector
):
    config_injector(
        {
            "user_agent": general_agent,
            "max_dl_filesize": 12345,
            "post_import": {"user_agent": "ImporterOnly/1.0"},
        }
    )
    with patch.object(
        bluesky_network, "download_blob", return_value=ORIGINAL_BYTES
    ) as guarded, patch.object(
        net, "_get_youtube_dl_content_url"
    ) as video_tool, patch.object(
        net.urllib.request, "urlopen"
    ) as generic:
        result = net.download(BLOB, use_video_downloader=True)
    assert result == ORIGINAL_BYTES
    guarded.assert_called_once_with(
        BLOB, 12345, user_agent=general_agent or "ImporterOnly/1.0"
    )
    video_tool.assert_not_called()
    generic.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/xrpc/com.atproto.sync.getBlob?did=" + DID,
        PDS + "/xrpc/com.atproto.sync.getBlob?did=" + DID + "&cid=invalid",
    ],
)
def test_rejected_blob_like_url_never_falls_back_to_generic_downloader_or_ytdlp(
    url,
):
    with patch.object(
        bluesky_network,
        "download_blob",
        side_effect=bluesky_network.BlueskyError("Invalid original-file URL"),
    ) as guarded, patch.object(
        net, "_get_youtube_dl_content_url"
    ) as video_tool, patch.object(
        net.urllib.request, "urlopen"
    ) as generic:
        with pytest.raises(errors.ProcessingError, match="Invalid"):
            net.download(url, use_video_downloader=True)
    guarded.assert_called_once()
    video_tool.assert_not_called()
    generic.assert_not_called()


def test_non_bluesky_download_keeps_existing_generic_download_behavior():
    with patch.object(
        bluesky_network, "download_blob"
    ) as guarded, patch.object(
        net.urllib.request, "urlopen", return_value=io.BytesIO(ORIGINAL_BYTES)
    ) as generic:
        result = net.download("https://artist.example/original.png")
    assert result == ORIGINAL_BYTES
    generic.assert_called_once()
    guarded.assert_not_called()


@pytest.mark.parametrize("rejected", [False, True])
def test_resolved_blob_still_uses_pds_and_content_verification(rejected):
    with patch.object(
        net, "_get_youtube_dl_content_url", return_value=BLOB
    ) as resolved, patch.object(
        bluesky_network, "download_blob", return_value=ORIGINAL_BYTES
    ) as guarded, patch.object(
        net.urllib.request, "urlopen"
    ) as generic:
        if rejected:
            guarded.side_effect = bluesky_network.BlueskyError(
                "Invalid original-file URL"
            )
            with pytest.raises(errors.ProcessingError, match="Invalid"):
                net.download(
                    "https://artist.example/video", use_video_downloader=True
                )
        else:
            assert (
                net.download(
                    "https://artist.example/video", use_video_downloader=True
                )
                == ORIGINAL_BYTES
            )
    resolved.assert_called_once()
    guarded.assert_called_once()
    generic.assert_not_called()
