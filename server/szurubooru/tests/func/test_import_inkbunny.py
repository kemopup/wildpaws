from copy import deepcopy

import pytest

from szurubooru.func import import_inkbunny


POST_ID = 12345
POST_URL = "https://inkbunny.net/s/12345"
FILE_BASE = "https://tx.ib.metapix.net/files/full/123/"
THUMB_BASE = "https://tx.ib.metapix.net/thumbnails/large/123/"


def _file(file_id=100, order=0, **updates):
    item = {
        "submission_id": str(POST_ID),
        "file_id": str(file_id),
        "submission_file_order": str(order),
        "deleted": "f",
        "mimetype": "image/jpeg",
        "file_url_full": FILE_BASE + str(file_id) + "_owner_original.jpg",
        "file_url_screen": "https://screen.example/DO_NOT_IMPORT.jpg",
        "file_url_preview": "https://preview.example/DO_NOT_IMPORT.jpg",
        "thumbnail_url_large_noncustom": THUMB_BASE + str(file_id) + ".jpg",
        "thumbnail_url_large": THUMB_BASE + "custom_" + str(file_id) + ".jpg",
    }
    item.update(updates)
    return item


def _payload(**updates):
    post = {
        "submission_id": str(POST_ID),
        "username": "Owner_One",
        "user_id": "321",
        "description": "[b]Submission description[/b]\n[url=https://example.com/source]Source[/url]",
        "description_bbcode_parsed": "<script>DO_NOT_IMPORT</script>",
        "title": "A title",
        "rating_id": "0",
        "deleted": "f",
        "public": "t",
        "hidden": "f",
        "files": [_file(101, 1), _file()],
        "keywords": [
            {"keyword_id": "1", "keyword_name": "general_tag"},
            {"keyword_id": "2", "keyword_name": "character_claim"},
            {"keyword_id": "3", "keyword_name": "artist_claim"},
        ],
        "writing": "A story is not the submission description.",
        "user_icon_url_large": "https://avatar.example/DO_NOT_IMPORT.jpg",
    }
    post.update(updates)
    return {"sid": "DO_NOT_EXPOSE_SESSION", "submissions": [post]}


def test_inkbunny_imports_full_originals_in_display_order_without_keywords():
    payload = _payload()
    unchanged = deepcopy(payload)
    result = import_inkbunny.normalize(payload, POST_ID)

    assert result["provider"] == "inkbunny"
    assert result["postUrl"] == POST_URL
    assert result["contentUrl"] == FILE_BASE + "100_owner_original.jpg"
    assert result["mimeType"] == "image/jpeg"
    assert [item["postUrl"] for item in result["media"]] == [
        POST_URL + "-p1-",
        POST_URL + "-p2-",
    ]
    assert result["media"][1]["contentUrl"].endswith("101_owner_original.jpg")
    assert result["media"][0]["previewUrl"] == THUMB_BASE + "100.jpg"
    assert result["author"] == {
        "username": "Owner_One",
        "url": "https://inkbunny.net/Owner_One",
    }
    assert result["sources"] == [POST_URL]
    assert result["tags"] == {"artist": [], "character": []}
    assert "Owner\\_One" in result["description"]
    assert "**Submission description**" in result["description"]
    assert "[Source](https://example.com/source)" in result["description"]
    assert result["safety"] == "safe"
    for excluded in (
        "DO_NOT_EXPOSE_SESSION",
        "DO_NOT_IMPORT",
        "general_tag",
        "character_claim",
        "artist_claim",
        "A story is not",
    ):
        assert excluded not in repr(result)
    assert "No keywords were imported" in result["warnings"][0]
    assert payload == unchanged


def test_inkbunny_selected_page_keeps_actual_page_url_and_file():
    result = import_inkbunny.normalize(_payload(), POST_ID, page_index=2)
    assert result["postUrl"] == POST_URL + "-p2-"
    assert result["sources"] == [POST_URL + "-p2-"]
    assert len(result["media"]) == 1
    assert result["contentUrl"].endswith("101_owner_original.jpg")


@pytest.mark.parametrize("page_index", [0, -1, 3, "2", True, 2.0])
def test_inkbunny_invalid_or_missing_page_does_not_select_a_different_file(
    page_index,
):
    with pytest.raises(ValueError, match="requested page"):
        import_inkbunny.normalize(_payload(), POST_ID, page_index=page_index)


@pytest.mark.parametrize(
    "mime,extension",
    [
        ("image/jpeg", "jpeg"),
        ("image/jpg", "jpg"),
        ("image/png", "png"),
        ("image/gif", "gif"),
        ("image/webp", "webp"),
        ("image/bmp", "bmp"),
        ("image/avif", "avif"),
        ("image/heif", "heif"),
        ("image/heic", "heic"),
        ("video/mp4", "mp4"),
        ("video/webm", "webm"),
        ("video/quicktime", "mov"),
    ],
)
def test_inkbunny_supported_images_and_videos_keep_original_mime_and_preview(
    mime,
    extension,
):
    item = _file(
        mimetype=mime, file_url_full=FILE_BASE + "original." + extension
    )
    result = import_inkbunny.normalize(_payload(files=[item]), POST_ID)
    assert result["contentUrl"] == FILE_BASE + "original." + extension
    assert result["mimeType"] == (
        "image/jpeg" if mime == "image/jpg" else mime
    )
    assert result["media"][0]["previewUrl"] == THUMB_BASE + "100.jpg"


def test_inkbunny_unsupported_mixed_files_fail_clearly_but_page_selection_works():
    payload = _payload(files=[_file(), _file(101, 1, mimetype="text/plain")])
    with pytest.raises(ValueError, match="unsupported file type"):
        import_inkbunny.normalize(payload, POST_ID)
    result = import_inkbunny.normalize(payload, POST_ID, page_index=1)
    assert len(result["media"]) == 1
    assert result["contentUrl"].endswith("100_owner_original.jpg")
    with pytest.raises(ValueError, match="unsupported file type"):
        import_inkbunny.normalize(payload, POST_ID, page_index=2)


@pytest.mark.parametrize(
    "value", [None, "", [], "application/pdf", "text/html"]
)
def test_inkbunny_unsupported_mime_is_not_inferred_from_url(value):
    with pytest.raises(ValueError, match="unsupported file type"):
        import_inkbunny.normalize(
            _payload(files=[_file(mimetype=value)]), POST_ID
        )


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "http://tx.ib.metapix.net/files/full/original.jpg",
        "https://tx.ib.metapix.net.evil.example/files/full/original.jpg",
        "https://evil.inkbunny.net.example/files/full/original.jpg",
        "https://127.0.0.1/files/full/original.jpg",
        "https://inkbunny.net@evil.example/files/full/original.jpg",
        "https://user:secret@tx.ib.metapix.net/files/full/original.jpg",
        "https://tx.ib.metapix.net:443/files/full/original.jpg",
        "https://tx.ib.metapix.net/files/full/original.jpg?sid=secret",
        "https://tx.ib.metapix.net/files/full/original.jpg#fragment",
        "https://tx.ib.metapix.net/files/screen/original.jpg",
        "https://tx.ib.metapix.net/files/preview/original.jpg",
        "https://tx.ib.metapix.net/files/full/../original.jpg",
        "https://tx.ib.metapix.net/files/full/%2e%2e/original.jpg",
        "https://tx.ib.metapix.net/files/full/%252e%252e/original.jpg",
        "https://tx.ib.metapix.net/files/full/original%0a.jpg",
        "https://tx.ib.metapix.net/files/full/original\\name.jpg",
        "https://tx.ib.metapix.net/files/full/original.html",
    ],
)
def test_inkbunny_rejects_unsafe_or_resized_original_urls(url):
    with pytest.raises(ValueError):
        import_inkbunny.normalize(
            _payload(files=[_file(file_url_full=url)]), POST_ID
        )


@pytest.mark.parametrize(
    "host",
    ["inkbunny.net", "nl.inkbunny.net", "ib.metapix.net", "tx.ib.metapix.net"],
)
def test_inkbunny_accepts_only_its_public_media_domains(host):
    url = "https://" + host + "/files/full/original.jpg"
    result = import_inkbunny.normalize(
        _payload(files=[_file(file_url_full=url)]), POST_ID
    )
    assert result["contentUrl"] == url


def test_inkbunny_missing_full_original_does_not_fall_back_to_screen_or_preview():
    item = _file()
    del item["file_url_full"]
    with pytest.raises(ValueError):
        import_inkbunny.normalize(_payload(files=[item]), POST_ID)


def test_inkbunny_invalid_poster_is_ignored_without_changing_original():
    item = _file(
        thumbnail_url_large_noncustom="https://evil.example/poster.jpg"
    )
    del item["thumbnail_url_large"]
    result = import_inkbunny.normalize(_payload(files=[item]), POST_ID)
    assert "previewUrl" not in result["media"][0]
    assert result["contentUrl"] == item["file_url_full"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("deleted", "t"),
        ("deleted", True),
        ("hidden", "t"),
        ("hidden", True),
        ("public", "f"),
        ("public", False),
        ("deleted", "unknown"),
        ("hidden", "unknown"),
        ("public", "unknown"),
    ],
)
def test_inkbunny_rejects_inaccessible_or_invalid_visibility_flags(
    field, value
):
    with pytest.raises(ValueError):
        import_inkbunny.normalize(_payload(**{field: value}), POST_ID)


def test_inkbunny_guest_blocked_flag_does_not_override_authorized_session_access():
    result = import_inkbunny.normalize(_payload(guest_block="t"), POST_ID)
    assert result["contentUrl"]


@pytest.mark.parametrize(
    "rating,expected",
    [
        (0, "safe"),
        ("1", "sketchy"),
        (2, "unsafe"),
        (None, "unsafe"),
        ("other", "unsafe"),
    ],
)
def test_inkbunny_maps_ratings_conservatively(rating, expected):
    result = import_inkbunny.normalize(_payload(rating_id=rating), POST_ID)
    assert result["safety"] == expected
    assert any(
        "no recognized rating" in warning for warning in result["warnings"]
    ) == (rating in (None, "other"))


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"error_code": 0, "error_message": "secret"},
        {"submissions": []},
        {"submissions": [None]},
    ],
)
def test_inkbunny_rejects_missing_and_error_payloads_without_exposing_messages(
    payload,
):
    with pytest.raises(ValueError) as error:
        import_inkbunny.normalize(payload, POST_ID)
    assert "secret" not in str(error.value)


def test_inkbunny_rejects_wrong_submission_or_file_parent_ids():
    with pytest.raises(ValueError):
        import_inkbunny.normalize(_payload(submission_id="999"), POST_ID)
    with pytest.raises(ValueError):
        import_inkbunny.normalize(
            _payload(files=[_file(submission_id="999")]), POST_ID
        )


@pytest.mark.parametrize(
    "files",
    [
        None,
        [],
        [None],
        [_file(deleted="t")],
        [_file(file_id=None)],
        [_file(), _file(101, 0)],
        [_file(), _file(100, 1)],
        [_file(submission_file_order=-1)],
        [_file(submission_file_order=True)],
    ],
)
def test_inkbunny_rejects_invalid_or_deleted_file_records(files):
    with pytest.raises(ValueError):
        import_inkbunny.normalize(_payload(files=files), POST_ID)


def test_inkbunny_unsupported_description_formatting_is_reported_and_html_escaped():
    result = import_inkbunny.normalize(
        _payload(description="[center]Text[/center]<script>alert(1)</script>"),
        POST_ID,
    )
    assert "Text" in result["description"]
    assert "<script>" not in result["description"]
    assert "&lt;script&gt;" in result["description"]
    assert any(
        "formatting could not be converted" in warning
        for warning in result["warnings"]
    )


@pytest.mark.parametrize("character", [" ", "'", '"', "<", ">", "`", "\\"])
def test_inkbunny_rejects_media_urls_that_could_escape_html_or_css(character):
    with pytest.raises(ValueError):
        import_inkbunny.normalize(
            _payload(
                files=[
                    _file(
                        file_url_full="https://tx.ib.metapix.net/files/full/image"
                        + character
                        + ".jpg"
                    )
                ]
            ),
            POST_ID,
        )
