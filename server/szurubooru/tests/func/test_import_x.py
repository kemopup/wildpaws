from copy import deepcopy
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from szurubooru import db, errors, model
from szurubooru.func import import_x, post_imports


POST_ID = 2116645497310269464
POST_URL = "https://x.com/Author_One/status/%d" % POST_ID


def _payload(**updates):
    tweet = {
        "id": str(POST_ID),
        "text": "Artist's post text\n#not_a_tag @not_a_local_user [b]literal[/b]",
        "possibly_sensitive": True,
        "author": {
            "screen_name": "Author_One",
            "name": "Display Name Is Not A Tag",
            "description": "BIOGRAPHY_MUST_NOT_BE_IMPORTED",
            "avatar_url": "https://pbs.twimg.com/profile_images/avatar.jpg",
        },
        "media": {
            "photos": [
                {
                    "type": "photo",
                    "url": "https://pbs.twimg.com/media/PhotoA.jpg",
                    "width": 100,
                    "height": 100,
                },
                {
                    "type": "photo",
                    "url": "https://pbs.twimg.com/media/PhotoB?format=png&name=small",
                    "width": 200,
                    "height": 100,
                },
            ],
            "mosaic": {"url": "https://other.example/combined.jpg"},
        },
        "quote": {
            "text": "QUOTED_TEXT_MUST_NOT_BE_IMPORTED",
            "media": {
                "photos": [{"url": "https://pbs.twimg.com/media/Quote.jpg"}]
            },
        },
    }
    tweet.update(updates)
    return {"code": 200, "message": "OK", "tweet": tweet}


def test_x_import_credits_author_without_creating_tags_or_importing_biography():
    payload = _payload()
    unchanged = deepcopy(payload)
    result = import_x.normalize(payload, POST_ID)

    assert result["provider"] == "x"
    assert result["postUrl"] == POST_URL
    assert result["author"]["username"] == "Author_One"
    assert result["author"]["url"] == "https://x.com/Author_One"
    assert result["tags"] == {"artist": [], "character": []}
    assert "Author" in result["description"]
    assert "https://x.com/Author_One" in result["description"]
    assert "Artist's post text" in result["description"]
    assert "BIOGRAPHY_MUST_NOT_BE_IMPORTED" not in result["description"]
    assert "QUOTED_TEXT_MUST_NOT_BE_IMPORTED" not in result["description"]
    assert "**literal**" not in result["description"]
    assert "not_a_tag" not in result["tags"]["artist"]
    assert payload == unchanged
    assert db.session.query(model.Post).count() == 0
    assert db.session.query(model.Tag).count() == 0


def test_x_import_returns_individual_original_photos_without_mosaic_or_quote_media():
    result = import_x.normalize(_payload(), POST_ID)
    assert len(result["media"]) == 2
    assert result["contentUrl"] == result["media"][0]["contentUrl"]
    assert result["mimeType"] == "image/jpeg"
    for index, item in enumerate(result["media"], 1):
        parsed = urlsplit(item["contentUrl"])
        assert parsed.scheme == "https"
        assert parsed.hostname == "pbs.twimg.com"
        assert parsed.path.startswith("/media/Photo")
        assert parse_qs(parsed.query).get("name") == ["orig"]
        assert item["postUrl"] == POST_URL + "/photo/%d" % index
    assert result["media"][1]["mimeType"] == "image/png"


def test_explicit_photo_selector_imports_only_requested_photo_and_preserves_its_index():
    result = import_x.normalize(_payload(), POST_ID, photo_index=2)
    assert len(result["media"]) == 1
    assert result["postUrl"] == POST_URL + "/photo/2"
    assert result["media"][0]["postUrl"] == POST_URL + "/photo/2"
    assert "PhotoB" in result["contentUrl"]
    assert result["mimeType"] == "image/png"


@pytest.mark.parametrize("photo_index", [0, -1, 3, 5])
def test_invalid_or_missing_photo_selector_is_not_silently_replaced(
    photo_index,
):
    with pytest.raises(ValueError):
        import_x.normalize(_payload(), POST_ID, photo_index=photo_index)


@pytest.mark.parametrize("sensitive", [False, True, None])
def test_x_sensitive_flag_is_not_treated_as_proof_that_a_post_is_safe(
    sensitive,
):
    result = import_x.normalize(
        _payload(possibly_sensitive=sensitive), POST_ID
    )
    assert result["safety"] == "unsafe"
    assert result["warnings"]


@pytest.mark.parametrize(
    "url,mime_type",
    [
        ("https://pbs.twimg.com/media/PhotoA.jpg:large", "image/jpeg"),
        ("https://pbs.twimg.com/media/PhotoA.jpeg?name=small", "image/jpeg"),
        (
            "https://pbs.twimg.com/media/PhotoA?format=webp&name=thumb",
            "image/webp",
        ),
        (
            "https://pbs.twimg.com/media/PhotoA.png?token=discard_me&name=small",
            "image/png",
        ),
    ],
)
def test_x_photos_request_original_quality_and_remove_unrelated_parameters(
    url, mime_type
):
    payload = _payload(media={"photos": [{"type": "photo", "url": url}]})
    result = import_x.normalize(payload, POST_ID)
    parsed = urlsplit(result["contentUrl"])
    assert parse_qs(parsed.query).get("name") == ["orig"]
    assert set(parse_qs(parsed.query)) <= {"name", "format"}
    assert ":large" not in parsed.path
    assert result["mimeType"] == mime_type


@pytest.mark.parametrize(
    "url",
    [
        "http://pbs.twimg.com/media/PhotoA.jpg",
        "https://pbs.twimg.com.evil.test/media/PhotoA.jpg",
        "https://evil.test/media/PhotoA.jpg",
        "https://user:password@pbs.twimg.com/media/PhotoA.jpg",
        "https://pbs.twimg.com:444/media/PhotoA.jpg",
        "https://pbs.twimg.com/profile_images/PhotoA.jpg",
        "https://pbs.twimg.com/media/../PhotoA.jpg",
        "https://pbs.twimg.com/media/%2e%2e/PhotoA.jpg",
        "https://pbs.twimg.com/media/PhotoA.mp4",
        "https://pbs.twimg.com/media/PhotoA?format=html",
        "javascript:alert(1)",
    ],
)
def test_x_photo_urls_must_be_actual_photos_on_the_expected_cdn(url):
    with pytest.raises(ValueError):
        import_x.normalize(
            _payload(media={"photos": [{"type": "photo", "url": url}]}),
            POST_ID,
        )


@pytest.mark.parametrize("code", [401, 404, 500])
def test_fx_api_unavailable_or_protected_post_is_an_error(code):
    payload = _payload()
    payload["code"] = code
    with pytest.raises(ValueError):
        import_x.normalize(payload, POST_ID)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "not metadata",
        {},
        {"code": 200},
        {"code": 200, "tweet": None},
        {"code": 200, "tweet": []},
    ],
)
def test_malformed_fx_metadata_fails_with_a_normalization_error(payload):
    with pytest.raises(ValueError):
        import_x.normalize(payload, POST_ID)


@pytest.mark.parametrize(
    "media",
    [
        None,
        {},
        {"photos": []},
        {"mosaic": {"url": "https://pbs.twimg.com/media/Combined.jpg"}},
    ],
)
def test_no_attached_photo_does_not_fall_back_to_avatar_quote_mosaic_or_video(
    media,
):
    with pytest.raises(ValueError):
        import_x.normalize(_payload(media=media), POST_ID)


def _video(kind="video", name="VideoA"):
    return {
        "type": kind,
        "format": "video/mp4",
        "url": "https://video.twimg.com/ext_tw_video/123/pu/vid/avc1/640x360/%s.mp4?tag=12"
        % name,
        "thumbnail_url": "https://pbs.twimg.com/ext_tw_video_thumb/123/pu/img/%s.jpg"
        % name,
        "width": 640,
        "height": 360,
    }


def test_media_all_preserves_mixed_photo_video_gif_order_without_duplicate_fallback_arrays():
    photo = _payload()["tweet"]["media"]["photos"][0]
    video = _video()
    gif = _video("gif", "GifA")
    media = {
        "all": [photo, video, gif],
        "photos": [photo],
        "videos": [video, gif],
    }
    result = import_x.normalize(_payload(media=media), POST_ID)
    assert len(result["media"]) == 3
    assert [item["mimeType"] for item in result["media"]] == [
        "image/jpeg",
        "video/mp4",
        "video/mp4",
    ]
    assert result["media"][0]["postUrl"] == POST_URL + "/photo/1"
    assert result["media"][1]["postUrl"] == POST_URL + "/video/2"
    assert result["media"][2]["postUrl"] == POST_URL + "/video/3"
    assert "VideoA.mp4" in result["media"][1]["contentUrl"]
    assert "GifA.mp4" in result["media"][2]["contentUrl"]
    assert result["media"][1]["previewUrl"].startswith(
        "https://pbs.twimg.com/ext_tw_video_thumb/"
    )


@pytest.mark.parametrize("kind", ["video", "gif"])
def test_video_and_gif_only_posts_import_mp4_original_not_thumbnail(kind):
    item = _video(kind)
    result = import_x.normalize(_payload(media={"videos": [item]}), POST_ID)
    assert result["mimeType"] == "video/mp4"
    assert result["contentUrl"].startswith("https://video.twimg.com/")
    assert result["contentUrl"] != result["media"][0].get("previewUrl")
    assert len(result["media"]) == 1
    if kind == "gif":
        assert "loop" in result["media"][0]["flags"]


def test_media_selector_can_select_a_video_from_a_mixed_gallery():
    photo = _payload()["tweet"]["media"]["photos"][0]
    result = import_x.normalize(
        _payload(media={"all": [photo, _video()]}), POST_ID, photo_index=2
    )
    assert result["postUrl"] == POST_URL + "/video/2"
    assert result["mimeType"] == "video/mp4"
    assert len(result["media"]) == 1
    assert "VideoA.mp4" in result["contentUrl"]


def test_video_variants_choose_highest_bitrate_mp4_not_hls_or_low_resolution():
    video = _video()
    video["variants"] = [
        {
            "content_type": "application/x-mpegURL",
            "url": "https://video.twimg.com/ext_tw_video/123/pu/pl/stream.m3u8",
        },
        {
            "content_type": "video/mp4",
            "bitrate": 256000,
            "url": "https://video.twimg.com/ext_tw_video/123/pu/vid/Low.mp4",
        },
        {
            "content_type": "video/mp4",
            "bitrate": 2176000,
            "url": "https://video.twimg.com/ext_tw_video/123/pu/vid/Best.mp4",
        },
    ]
    result = import_x.normalize(_payload(media={"videos": [video]}), POST_ID)
    assert "Best.mp4" in result["contentUrl"]
    assert result["mimeType"] == "video/mp4"


@pytest.mark.parametrize(
    "url",
    [
        "http://video.twimg.com/ext_tw_video/123/pu/vid/VideoA.mp4",
        "https://video.twimg.com.evil.test/ext_tw_video/123/pu/vid/VideoA.mp4",
        "https://user:secret@video.twimg.com/ext_tw_video/123/pu/vid/VideoA.mp4",
        "https://evil.test/VideoA.mp4",
        "https://video.twimg.com/ext_tw_video/123/pu/pl/playlist.m3u8",
    ],
)
def test_video_original_url_must_be_a_trusted_mp4(url):
    video = _video()
    video["url"] = url
    with pytest.raises(ValueError):
        import_x.normalize(_payload(media={"videos": [video]}), POST_ID)


@pytest.mark.parametrize(
    "poster",
    [
        "https://evil.test/poster.jpg",
        "http://pbs.twimg.com/ext_tw_video_thumb/123/pu/img/Poster.jpg",
        "https://pbs.twimg.com/profile_images/Avatar.jpg",
        "data:image/svg+xml,untrusted",
    ],
)
def test_untrusted_poster_is_omitted_without_blocking_valid_original_video(
    poster,
):
    video = _video()
    video["thumbnail_url"] = poster
    result = import_x.normalize(_payload(media={"videos": [video]}), POST_ID)
    assert result["contentUrl"].startswith("https://video.twimg.com/")
    assert result["media"][0].get("previewUrl") in (None, "")


@pytest.mark.parametrize(
    "author", [{}, {"screen_name": ""}, {"screen_name": "not/a/handle"}]
)
def test_author_username_must_be_valid_before_generating_credit_links(author):
    with pytest.raises(ValueError):
        import_x.normalize(_payload(author=author), POST_ID)


def test_api_payload_must_belong_to_requested_status():
    with pytest.raises(ValueError):
        import_x.normalize(_payload(id="123"), POST_ID)


def test_x_post_text_is_plain_text_not_dtext_or_active_html():
    result = import_x.normalize(
        _payload(
            text="<script>alert(1)</script> [b]literal[/b] #tag @person\nhttps://artist.example/art"
        ),
        POST_ID,
    )
    description = result["description"]
    assert "<script>" not in description
    assert "alert" in description
    assert "**literal**" not in description
    assert "https://artist.example/art" in description
    assert "#tag" not in description or "\\#tag" in description
    assert "@person" not in description or "\\@person" in description


@pytest.mark.parametrize(
    "host",
    [
        "x.com",
        "www.x.com",
        "twitter.com",
        "www.twitter.com",
        "mobile.twitter.com",
        "fxtwitter.com",
        "fixupx.com",
    ],
)
def test_x_and_twitter_post_links_are_normalized_and_tracking_is_discarded(
    host,
):
    parsed = post_imports.parse_post_url(
        "https://%s/Author_One/status/%d?s=20&t=tracking#details"
        % (host, POST_ID)
    )
    assert parsed == ("x", POST_ID, POST_URL)


def test_photo_selector_in_x_url_is_preserved_while_query_is_removed():
    assert post_imports.parse_post_url(POST_URL + "/photo/2?s=20") == (
        "x",
        POST_ID,
        POST_URL + "/photo/2",
    )


def test_video_selector_in_x_url_is_preserved_while_query_is_removed():
    assert post_imports.parse_post_url(POST_URL + "/video/2?s=20") == (
        "x",
        POST_ID,
        POST_URL + "/video/2",
    )


@pytest.mark.parametrize("path", ["/i/status/", "/i/web/status/"])
def test_x_id_based_share_links_resolve_without_a_claimed_author(path):
    provider, post_id, canonical = post_imports.parse_post_url(
        "https://x.com" + path + str(POST_ID) + "?s=20"
    )
    assert provider == "x"
    assert post_id == POST_ID
    assert canonical.startswith("https://x.com/")
    assert str(POST_ID) in canonical
    assert "?" not in canonical


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com.evil.test/Author_One/status/%d" % POST_ID,
        "https://x.com@evil.test/Author_One/status/%d" % POST_ID,
        "https://user:secret@x.com/Author_One/status/%d" % POST_ID,
        "https://x.com:444/Author_One/status/%d" % POST_ID,
        "https://x.com/Author_One/status/0",
        "https://x.com/Author_One/status/-1",
        "https://x.com/Author_One/status/%d/photo/0" % POST_ID,
        "https://x.com/Author_One/status/%d/photo/5" % POST_ID,
        "https://x.com/Author_One/status/%d/other" % POST_ID,
    ],
)
def test_invalid_x_links_fail_before_requesting_fx_api(url):
    with patch.object(post_imports, "_request_json") as request:
        with pytest.raises(errors.ValidationError):
            post_imports.preview(url)
    request.assert_not_called()


def test_plain_http_x_link_is_upgraded_without_an_insecure_network_request():
    with patch.object(
        post_imports, "_request_json", return_value=_payload()
    ) as request:
        result = post_imports.preview(
            "http://x.com/Author_One/status/%d?s=20" % POST_ID
        )
    request.assert_called_once_with(
        "x", "https://api.fxtwitter.com/status/%d" % POST_ID
    )
    assert result["postUrl"] == POST_URL


def test_plain_x_text_cannot_activate_local_description_wrappers():
    result = import_x.normalize(
        _payload(
            text="[spoiler]literal[/spoiler] [search]literal[/search] ~~literal~~"
        ),
        POST_ID,
    )
    assert "[spoiler]" not in result["description"]
    assert "[search]" not in result["description"]
    assert "~~literal~~" not in result["description"]
    assert "literal" in result["description"]


def test_explicit_media_index_is_not_shifted_by_duplicate_file_urls():
    payload = _payload()
    photos = payload["tweet"]["media"]["photos"]
    photos.insert(1, deepcopy(photos[0]))
    result = import_x.normalize(payload, POST_ID, photo_index=3)
    assert "PhotoB" in result["contentUrl"]
    assert result["postUrl"].endswith("/photo/3")


def test_x_preview_uses_fixed_fx_endpoint_without_forwarding_query_or_author_claim():
    with patch.object(
        post_imports, "_request_json", return_value=_payload()
    ) as request:
        result = post_imports.preview(
            "https://x.com/Other_Account/status/%d/photo/2?api_key=not_a_key&t=tracking"
            % POST_ID
        )
    request.assert_called_once_with(
        "x", "https://api.fxtwitter.com/status/%d" % POST_ID
    )
    assert result["postUrl"] == POST_URL + "/photo/2"
    assert result["author"]["username"] == "Author_One"
    assert result["tags"] == {"artist": [], "character": []}
    assert "not_a_key" not in repr(result)
    assert db.session.query(model.Post).count() == 0
    assert db.session.query(model.Tag).count() == 0


def test_fx_metadata_request_never_sends_configured_provider_credentials(
    config_injector, monkeypatch
):
    config_injector(
        {
            "post_import": {
                "x": {
                    "username": "private-account",
                    "user_id": "123",
                    "api_key": "private-api-key",
                },
                "e621": {
                    "username": "other-account",
                    "api_key": "other-api-key",
                },
            }
        }
    )
    monkeypatch.setitem(post_imports._LAST_REQUEST, "x", 0.0)
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.read.return_value = (
        b'{"code":200}'
    )
    endpoint = "https://api.fxtwitter.com/status/%d" % POST_ID
    with patch.object(
        post_imports.urllib.request, "build_opener", return_value=opener
    ):
        assert post_imports._request_json("x", endpoint) == {"code": 200}
    request = opener.open.call_args[0][0]
    assert request.full_url == endpoint
    assert request.get_header("Authorization") is None
    assert "private-" not in repr(request.header_items())
    assert "other-api-key" not in repr(request.header_items())
