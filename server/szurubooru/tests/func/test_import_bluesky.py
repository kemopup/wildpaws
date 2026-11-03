import base64
import hashlib
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from szurubooru import db, model
from szurubooru.func import import_bluesky


DID = "did:plc:ewvi7nxzyoun6zhxrhs64oiz"
OTHER_DID = "did:plc:aaaaaaaaaaaaaaaaaaaaaaaa"
RKEY = "3kz5bcdefgh2k"
PDS = "https://test.us-east.host.bsky.network"
POST_URL = "https://bsky.app/profile/%s/post/%s" % (DID, RKEY)


def _cid(value):
    raw = b"\x01\x55\x12\x20" + hashlib.sha256(value.encode()).digest()
    return "b" + base64.b32encode(raw).decode().lower().rstrip("=")


CID_ONE = _cid("first image")
CID_TWO = _cid("second image")


def _blob(cid=CID_ONE, mime="image/jpeg", **updates):
    value = {
        "$type": "blob",
        "ref": {"$link": cid},
        "mimeType": mime,
        "size": 1000,
    }
    value.update(updates)
    return value


def _thumb(cid=CID_ONE, did=DID):
    return "https://cdn.bsky.app/img/feed_thumbnail/plain/%s/%s@jpeg" % (
        did,
        cid,
    )


def _image_embed(cids=None, gallery=False):
    cids = cids or [CID_ONE]
    kind = "app.bsky.embed.gallery" if gallery else "app.bsky.embed.images"
    key = "items" if gallery else "images"
    items, views = [], []
    for cid in cids:
        item = {"image": _blob(cid), "alt": "Do not replace the post text"}
        if gallery:
            item["$type"] = kind + "#image"
        items.append(item)
        views.append({"thumbnail" if gallery else "thumb": _thumb(cid)})
    return {"$type": kind, key: items}, {"$type": kind + "#view", key: views}


def _payload():
    embed, view = _image_embed()
    return {
        "posts": [
            {
                "uri": "at://%s/app.bsky.feed.post/%s" % (DID, RKEY),
                "author": {
                    "did": DID,
                    "handle": "author.bsky.social",
                    "displayName": "Not a tag",
                    "description": "PRIVATE_BIO_NOT_IMPORTED",
                },
                "record": {
                    "$type": "app.bsky.feed.post",
                    "text": "Post text #not_a_tag @not_local [b]literal[/b]",
                    "embed": embed,
                },
                "embed": view,
                "replyCount": 999,
            }
        ]
    }


def _normalize(payload=None, **kwargs):
    return import_bluesky.normalize(
        payload or _payload(), DID, RKEY, PDS, **kwargs
    )


def test_bluesky_preserves_only_original_post_text_credit_and_original_blob():
    payload = _payload()
    payload["posts"][0]["tags"] = {
        "artist": ["NOT_AN_ARTIST"],
        "character": ["NOT_A_CHARACTER"],
    }
    unchanged = deepcopy(payload)
    result = _normalize(payload)
    assert result["provider"] == "bluesky"
    assert result["postUrl"] == POST_URL
    assert result["sources"] == [POST_URL]
    assert result["author"] == {
        "username": "author.bsky.social",
        "url": "https://bsky.app/profile/" + DID,
    }
    parsed = urlsplit(result["contentUrl"])
    assert parsed.hostname == urlsplit(PDS).hostname
    assert parsed.path == "/xrpc/com.atproto.sync.getBlob"
    assert parse_qs(parsed.query) == {"did": [DID], "cid": [CID_ONE]}
    assert result["mimeType"] == "image/jpeg"
    assert result["previewUrl"] == _thumb()
    assert result["media"][0]["postUrl"] == POST_URL + "#media-1"
    assert result["tags"] == {"artist": [], "character": []}
    assert "Author:" in result["description"]
    assert "author.bsky.social" in result["description"]
    assert "Post text" in result["description"]
    assert "**literal**" not in result["description"]
    assert "PRIVATE_BIO_NOT_IMPORTED" not in result["description"]
    assert result["safety"] == "unsafe"
    assert "rating" in result["warnings"][0]
    assert payload == unchanged
    assert db.session.query(model.Post).count() == 0
    assert db.session.query(model.Tag).count() == 0


@pytest.mark.parametrize("gallery,count", [(False, 4), (True, 10), (True, 20)])
def test_all_attached_images_and_new_galleries_preserve_display_order(
    gallery, count
):
    payload = _payload()
    cids = [_cid(str(i)) for i in range(count)]
    payload["posts"][0]["record"]["embed"], payload["posts"][0]["embed"] = (
        _image_embed(cids, gallery)
    )
    result = _normalize(payload)
    assert len(result["media"]) == count
    for index, (item, cid) in enumerate(zip(result["media"], cids), 1):
        assert parse_qs(urlsplit(item["contentUrl"]).query)["cid"] == [cid]
        assert item["postUrl"] == POST_URL + "#media-%d" % index
        assert item["previewUrl"] == _thumb(cid)


def test_explicit_media_selection_preserves_its_original_position():
    payload = _payload()
    payload["posts"][0]["record"]["embed"], payload["posts"][0]["embed"] = (
        _image_embed([CID_ONE, CID_TWO])
    )
    result = _normalize(payload, media_index=2)
    assert len(result["media"]) == 1
    assert result["postUrl"] == POST_URL + "#media-2"
    assert result["sources"] == [POST_URL]
    assert result["previewUrl"] == _thumb(CID_TWO)
    assert result["contentUrl"] == result["media"][0]["contentUrl"]


@pytest.mark.parametrize("index", [0, -1, 2, True, "1"])
def test_missing_or_invalid_media_selection_is_not_silently_replaced(index):
    with pytest.raises(ValueError):
        _normalize(media_index=index)


@pytest.mark.parametrize(
    "presentation,expected", [(None, []), ("default", []), ("gif", ["loop"])]
)
def test_video_and_gif_presentation_download_stored_file_not_stream(
    presentation, expected
):
    payload = _payload()
    video = {
        "$type": "app.bsky.embed.video",
        "video": _blob(CID_ONE, "video/mp4"),
        "captions": [{"file": _blob(CID_TWO, "text/vtt")}],
    }
    if presentation:
        video["presentation"] = presentation
    poster = "https://video.bsky.app/watch/%s/%s/thumbnail.jpg" % (
        DID,
        CID_ONE,
    )
    payload["posts"][0]["record"]["embed"] = video
    payload["posts"][0]["embed"] = {
        "$type": "app.bsky.embed.video#view",
        "cid": CID_ONE,
        "playlist": "https://video.bsky.app/DO_NOT_IMPORT.m3u8",
        "thumbnail": poster,
    }
    result = _normalize(payload)
    assert result["mimeType"] == "video/mp4"
    assert result["previewUrl"] == poster
    assert result["flags"] == expected
    assert result["media"][0]["flags"] == expected
    assert "m3u8" not in result["contentUrl"]
    assert parse_qs(urlsplit(result["contentUrl"]).query)["cid"] == [CID_ONE]
    assert CID_TWO not in result["contentUrl"]


def test_quoted_post_and_reply_media_are_excluded_from_record_with_media():
    payload = _payload()
    post = payload["posts"][0]
    own, own_view = _image_embed()
    quote, quote_view = _image_embed([CID_TWO])
    post["record"]["embed"] = {
        "$type": "app.bsky.embed.recordWithMedia",
        "media": own,
        "record": {"uri": "at://quote", "embed": quote},
    }
    post["embed"] = {
        "$type": "app.bsky.embed.recordWithMedia#view",
        "media": own_view,
        "record": {"text": "QUOTED_TEXT_NOT_IMPORTED", "embed": quote_view},
    }
    post["record"]["reply"] = {"root": {"uri": "at://reply", "embed": quote}}
    result = _normalize(payload)
    assert len(result["media"]) == 1
    assert CID_TWO not in result["contentUrl"]
    assert "QUOTED_TEXT_NOT_IMPORTED" not in result["description"]


@pytest.mark.parametrize(
    "kind",
    ["app.bsky.embed.external", "app.bsky.embed.record", "unknown.embed"],
)
def test_external_cards_and_quote_only_posts_do_not_become_uploads(kind):
    payload = _payload()
    payload["posts"][0]["record"]["embed"] = {
        "$type": kind,
        "external": {"thumb": _blob(), "uri": "https://evil.test"},
        "record": {"embed": _image_embed()[0]},
    }
    with pytest.raises(ValueError, match="no supported attached"):
        _normalize(payload)


@pytest.mark.parametrize(
    "label", ["!hide", "!no-unauthenticated", "!takedown", "!suspend"]
)
@pytest.mark.parametrize("location", ["post", "author", "self"])
def test_unavailable_or_anonymous_optout_labels_are_respected(label, location):
    payload = _payload()
    post = payload["posts"][0]
    if location == "post":
        post["labels"] = [{"val": label}]
    elif location == "author":
        post["author"]["labels"] = [{"val": label}]
    else:
        post["record"]["labels"] = {
            "$type": "com.atproto.label.defs#selfLabels",
            "values": [{"val": label}],
        }
    with pytest.raises(ValueError, match="restricted"):
        _normalize(payload)


@pytest.mark.parametrize(
    "label",
    [
        {"val": "!hide", "neg": True},
        {"val": "!hide", "exp": "2000-01-01T00:00:00Z"},
    ],
)
def test_negated_and_expired_labels_are_not_treated_as_current_restrictions(
    label,
):
    payload = _payload()
    payload["posts"][0]["labels"] = [label]
    assert _normalize(payload)["media"]


def test_adult_and_custom_labels_remain_review_metadata_not_tag_categories():
    payload = _payload()
    payload["posts"][0]["labels"] = [
        {"val": "porn"},
        {"val": "custom-not-an-artist"},
    ]
    result = _normalize(payload)
    assert result["safety"] == "unsafe"
    assert result["tags"] == {"artist": [], "character": []}
    assert any(
        "moderation labels" in warning for warning in result["warnings"]
    )
    assert "custom-not-an-artist" not in repr(result)


def test_rich_text_facets_use_utf8_offsets_and_preserve_spacing_and_display_text():
    payload = _payload()
    record = payload["posts"][0]["record"]
    text = "🐾 Visit example.com and @friend.bsky.social #manual"
    record["text"] = text
    record["facets"] = []
    for label, feature in [
        (
            "example.com",
            {
                "$type": "app.bsky.richtext.facet#link",
                "uri": "https://example.com/full/path",
            },
        ),
        (
            "@friend.bsky.social",
            {"$type": "app.bsky.richtext.facet#mention", "did": OTHER_DID},
        ),
        ("#manual", {"$type": "app.bsky.richtext.facet#tag", "tag": "manual"}),
    ]:
        start = text.index(label)
        byte_start = len(text[:start].encode())
        record["facets"].append(
            {
                "index": {
                    "byteStart": byte_start,
                    "byteEnd": byte_start + len(label.encode()),
                },
                "features": [feature],
            }
        )
    result = _normalize(payload)
    description = result["description"]
    assert (
        "🐾 Visit [example.com](https://example.com/full/path) and [&#64;friend.bsky.social]"
        in description
    )
    assert (
        "(https://bsky.app/profile/" + OTHER_DID + ") &#35;manual"
        in description
    )
    assert result["tags"] == {"artist": [], "character": []}


@pytest.mark.parametrize(
    "index",
    [
        {"byteStart": 1, "byteEnd": 2},
        {"byteStart": -1, "byteEnd": 2},
        {"byteStart": True, "byteEnd": 5},
        {"byteStart": 0, "byteEnd": 999},
    ],
)
def test_invalid_rich_text_ranges_preserve_literal_post_text_with_review_warning(
    index,
):
    payload = _payload()
    record = payload["posts"][0]["record"]
    record.update(
        text="🐾 full literal text",
        facets=[
            {
                "index": index,
                "features": [
                    {
                        "$type": "app.bsky.richtext.facet#link",
                        "uri": "https://example.com",
                    }
                ],
            }
        ],
    )
    result = _normalize(payload)
    assert "🐾 full literal text" in result["description"]
    assert any("link formatting" in warning for warning in result["warnings"])


@pytest.mark.parametrize(
    "target",
    [
        "javascript:alert(1)",
        "https://user:secret@example.com/",
        "https://example.com/\nsecret",
        "data:text/html,bad",
    ],
)
def test_unsafe_link_facets_do_not_activate_or_disclose_credentials(target):
    payload = _payload()
    payload["posts"][0]["record"].update(
        text="link",
        facets=[
            {
                "index": {"byteStart": 0, "byteEnd": 4},
                "features": [
                    {"$type": "app.bsky.richtext.facet#link", "uri": target}
                ],
            }
        ],
    )
    result = _normalize(payload)
    assert "\n\nlink" in result["description"]
    assert target not in result["description"]
    assert any("link formatting" in warning for warning in result["warnings"])


@pytest.mark.parametrize(
    "poster",
    [
        "https://evil.test/thumb.jpg",
        "http://cdn.bsky.app/image.jpg",
        "https://user:secret@cdn.bsky.app/image.jpg",
        "https://cdn.bsky.app.evil.test/image.jpg",
        "https://cdn.bsky.app/img/avatar/plain/x/y@jpeg",
        "https://video.bsky.app/watch/x/y/playlist.m3u8",
        _thumb(CID_TWO),
        _thumb(CID_ONE, OTHER_DID),
    ],
)
def test_invalid_or_other_media_previews_are_discarded_without_changing_original(
    poster,
):
    payload = _payload()
    payload["posts"][0]["embed"]["images"][0]["thumb"] = poster
    result = _normalize(payload)
    assert "previewUrl" not in result
    assert "previewUrl" not in result["media"][0]
    assert parse_qs(urlsplit(result["contentUrl"]).query)["cid"] == [CID_ONE]


@pytest.mark.parametrize(
    "mime", ["image/jpeg", "image/png", "image/gif", "image/webp"]
)
def test_supported_images_use_blob_mime_instead_of_extensionless_url(mime):
    payload = _payload()
    payload["posts"][0]["record"]["embed"]["images"][0]["image"][
        "mimeType"
    ] = mime
    assert _normalize(payload)["mimeType"] == mime


@pytest.mark.parametrize("preview_kind", ["feed_thumbnail", "feed_fullsize"])
def test_extensionless_cdn_previews_preserve_bound_media_and_blob_mime(
    preview_kind,
):
    payload = _payload()
    poster = "https://cdn.bsky.app/img/%s/plain/%s/%s" % (
        preview_kind,
        DID,
        CID_ONE,
    )
    payload["posts"][0]["embed"]["images"][0]["thumb"] = poster
    payload["posts"][0]["record"]["embed"]["images"][0]["image"][
        "mimeType"
    ] = "image/png"
    result = _normalize(payload)
    assert result["previewUrl"] == poster
    assert result["media"][0]["previewUrl"] == poster
    assert result["mimeType"] == "image/png"
    assert parse_qs(urlsplit(result["contentUrl"]).query) == {
        "did": [DID],
        "cid": [CID_ONE],
    }
    with pytest.raises(ValueError):
        import_bluesky.preview_url(poster, OTHER_DID, CID_ONE)
    with pytest.raises(ValueError):
        import_bluesky.preview_url(poster, DID, CID_TWO)


@pytest.mark.parametrize(
    "blob",
    [
        None,
        {},
        _blob(size=True),
        _blob(size=0),
        _blob(size="100"),
        _blob(mime="image/svg+xml"),
        _blob(mime="text/html"),
        _blob(cid="bafynotraw"),
        _blob(ref={"$link": "javascript:bad"}),
    ],
)
def test_invalid_original_blob_metadata_is_rejected_without_sample_fallback(
    blob,
):
    payload = _payload()
    payload["posts"][0]["record"]["embed"]["images"][0]["image"] = blob
    with pytest.raises(ValueError):
        _normalize(payload)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"posts": []},
        {"posts": [None]},
        {"posts": [{"uri": "at://wrong"}]},
    ],
)
def test_wrong_or_unavailable_post_payload_is_rejected(payload):
    with pytest.raises(ValueError):
        import_bluesky.normalize(payload, DID, RKEY, PDS)


def test_mismatched_author_did_is_rejected():
    payload = _payload()
    payload["posts"][0]["author"]["did"] = OTHER_DID
    with pytest.raises(ValueError):
        _normalize(payload)


def test_missing_handle_credits_stable_did_instead_of_guessing_from_display_name():
    payload = _payload()
    payload["posts"][0]["author"]["handle"] = "handle.invalid"
    result = _normalize(payload)
    assert result["author"]["username"] == DID
    assert "Not a tag" not in result["description"]
