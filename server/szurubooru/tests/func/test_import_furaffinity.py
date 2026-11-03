from copy import deepcopy

import pytest

from szurubooru import db, model
from szurubooru.func import import_furaffinity


POST_ID = 12345
POST_URL = "https://www.furaffinity.net/view/12345/"
ORIGINAL = "https://d.furaffinity.net/art/author/1700000000/1700000000.author_original.png"
THUMBNAIL = "https://t.furaffinity.net/12345@600-1700000000.jpg"


def _payload(**updates):
    payload = {
        "id": POST_ID,
        "author": {
            "name": "Author_One",
            "url": "https://www.furaffinity.net/user/authorone",
        },
        "description": "[b]Artist notes[/b]\n[url=https://example.com/source]Original source[/url]",
        "file_url": ORIGINAL,
        "thumbnail_url": THUMBNAIL,
        "rating": "General",
        "tags": ["not_an_artist", "not_a_character", "do_not_import"],
        "species": "not_imported",
        "footer": "PROFILE_FOOTER_MUST_NOT_IMPORT",
        "comments": [{"text": "COMMENTS_MUST_NOT_IMPORT"}],
        "cookies": [{"name": "a", "value": "SECRET_COOKIE_MUST_NOT_IMPORT"}],
    }
    payload.update(updates)
    return payload


def test_furaffinity_import_keeps_original_and_credits_owner_without_guessing_tags():
    payload = _payload()
    unchanged = deepcopy(payload)
    result = import_furaffinity.normalize(payload, POST_ID)
    assert result["provider"] == "furaffinity"
    assert result["postUrl"] == POST_URL
    assert result["contentUrl"] == ORIGINAL
    assert result["mimeType"] == "image/png"
    assert result["previewUrl"] == THUMBNAIL
    assert result["media"] == [
        {
            "contentUrl": ORIGINAL,
            "mimeType": "image/png",
            "postUrl": POST_URL,
            "previewUrl": THUMBNAIL,
            "flags": [],
        }
    ]
    assert result["author"] == {
        "username": "Author_One",
        "url": "https://www.furaffinity.net/user/authorone/",
    }
    assert result["sources"] == [POST_URL]
    assert result["tags"] == {"artist": [], "character": []}
    assert "Submission owner: [Author\\_One]" in result["description"]
    assert "**Artist notes**" in result["description"]
    assert (
        "[Original source](https://example.com/source)"
        in result["description"]
    )
    assert result["safety"] == "safe"
    assert "No keywords were imported" in result["warnings"][0]
    for value in (
        "not_an_artist",
        "not_a_character",
        "do_not_import",
        "not_imported",
        "PROFILE_FOOTER_MUST_NOT_IMPORT",
        "COMMENTS_MUST_NOT_IMPORT",
        "SECRET_COOKIE_MUST_NOT_IMPORT",
    ):
        assert value not in repr(result)
    assert payload == unchanged
    assert db.session.query(model.Post).count() == 0
    assert db.session.query(model.Tag).count() == 0


@pytest.mark.parametrize(
    "extension,mime",
    [
        ("jpg", "image/jpeg"),
        ("jpeg", "image/jpeg"),
        ("png", "image/png"),
        ("gif", "image/gif"),
        ("webp", "image/webp"),
        ("bmp", "image/bmp"),
        ("avif", "image/avif"),
        ("heif", "image/heif"),
        ("heic", "image/heic"),
        ("mp4", "video/mp4"),
        ("webm", "video/webm"),
        ("mov", "video/quicktime"),
    ],
)
def test_original_supported_image_and_video_formats_never_use_preview_as_content(
    extension, mime
):
    original = ORIGINAL.rsplit(".", 1)[0] + "." + extension
    result = import_furaffinity.normalize(_payload(file_url=original), POST_ID)
    assert result["contentUrl"] == original
    assert result["mimeType"] == mime
    assert result["media"][0]["contentUrl"] != THUMBNAIL
    assert result["media"][0]["flags"] == []


@pytest.mark.parametrize(
    "rating,expected",
    [
        ("General", "safe"),
        ("Mature", "sketchy"),
        ("Adult", "unsafe"),
        (" ADULT ", "unsafe"),
        (None, "unsafe"),
        ("unrecognized", "unsafe"),
    ],
)
def test_ratings_map_conservatively(rating, expected):
    result = import_furaffinity.normalize(_payload(rating=rating), POST_ID)
    assert result["safety"] == expected
    assert any(
        "no recognized rating" in warning for warning in result["warnings"]
    ) == (rating in (None, "unrecognized"))


@pytest.mark.parametrize(
    "url",
    [
        "http://d.furaffinity.net/art/author/1700000000/original.png",
        "https://d.furaffinity.net.evil.test/art/author/1700000000/original.png",
        "https://evil.test/art/author/1700000000/original.png",
        "https://127.0.0.1/art/author/1700000000/original.png",
        "https://d.furaffinity.net@evil.test/art/author/1700000000/original.png",
        "https://user:password@d.furaffinity.net/art/author/1700000000/original.png",
        "https://d.furaffinity.net:443/art/author/1700000000/original.png",
        "https://d.furaffinity.net:bad/art/author/1700000000/original.png",
        ORIGINAL + "?a=secret",
        ORIGINAL + "#b=secret",
        "https://d.furaffinity.net/art/author/1700000000/../original.png",
        "https://d.furaffinity.net/art/author/1700000000/%2e%2e/original.png",
        "https://d.furaffinity.net/art/author/1700000000/%252e%252e.png",
        "https://d.furaffinity.net/art/author/1700000000/%0aoriginal.png",
        "https://d.furaffinity.net/art/author/1700000000/image' onerror='x.png",
        'https://d.furaffinity.net/art/author/1700000000/image".png',
        "https://d.furaffinity.net/art/author/1700000000/image<svg>.png",
        "https://d.furaffinity.net/art/author/1700000000/image`x.png",
        "https://d.furaffinity.net/art/author/1700000000/image\\x.png",
        "https://d.furaffinity.net/art/author/1700000000/image\nx.png",
        "https://d.furaffinity.net/preview/original.png",
        "https://t.furaffinity.net/12345@600-1700000000.jpg",
        "https://d.furaffinity.net/art/author/1700000000/story.txt",
        "https://d.furaffinity.net/art/author/1700000000/vector.svg",
        "https://d.furaffinity.net/art/author/1700000000/file.swf",
        "data:image/png;base64,AA==",
        None,
    ],
)
def test_original_urls_reject_untrusted_private_or_unsupported_locations(url):
    with pytest.raises(ValueError) as error:
        import_furaffinity.normalize(_payload(file_url=url), POST_ID)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "https://evil.test/12345.jpg",
        "https://t.furaffinity.net.evil.test/12345.jpg",
        "http://t.furaffinity.net/12345.jpg",
        "https://user:password@t.furaffinity.net/12345.jpg",
        "https://t.furaffinity.net:443/12345.jpg",
        THUMBNAIL + "?a=secret",
        THUMBNAIL + "#b=secret",
        "https://t.furaffinity.net/54321@600-1700000000.jpg",
        "https://t.furaffinity.net/12345@600-1700000000.svg",
        "https://t.furaffinity.net/../12345.jpg",
        ORIGINAL,
    ],
)
def test_invalid_optional_preview_is_ignored_without_changing_original(url):
    result = import_furaffinity.normalize(_payload(thumbnail_url=url), POST_ID)
    assert "previewUrl" not in result
    assert "previewUrl" not in result["media"][0]
    assert result["contentUrl"] == ORIGINAL


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"error": "auth_required", "message": "SECRET"},
        _payload(id=54321),
        _payload(id=True),
    ],
)
def test_error_and_mismatched_payloads_are_rejected_without_exposing_messages(
    payload,
):
    with pytest.raises(ValueError) as error:
        import_furaffinity.normalize(payload, POST_ID)
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize(
    "author",
    [
        None,
        {},
        {"name": ""},
        {"name": "<script>alert(1)</script>"},
        {"name": "author", "url": "https://evil.test/user/author/"},
        {
            "name": "author",
            "url": "https://www.furaffinity.net/user/different/",
        },
        {
            "name": "author",
            "url": "https://www.furaffinity.net/user/author/?a=secret",
        },
        {
            "name": "author",
            "url": "https://user:password@www.furaffinity.net/user/author/",
        },
        {
            "name": "author",
            "url": "https://www.furaffinity.net:443/user/author/",
        },
    ],
)
def test_author_credit_requires_a_matching_safe_furaffinity_profile(author):
    with pytest.raises(ValueError):
        import_furaffinity.normalize(_payload(author=author), POST_ID)


def test_legacy_owner_identifier_characters_are_encoded_in_profile_link():
    result = import_furaffinity.normalize(
        _payload(
            author={
                "name": "Legacy_[User]~",
                "url": "https://www.furaffinity.net/user/legacy%5Buser%5D~/",
            }
        ),
        POST_ID,
    )
    assert (
        result["author"]["url"]
        == "https://www.furaffinity.net/user/legacy%5Buser%5D~/"
    )
    assert result["author"]["username"] == "Legacy_[User]~"


@pytest.mark.parametrize(
    "visibility", [{"deleted": True}, {"hidden": "t"}, {"public": False}]
)
def test_inaccessible_submissions_are_rejected(visibility):
    with pytest.raises(ValueError):
        import_furaffinity.normalize(_payload(**visibility), POST_ID)


def test_description_html_stays_escaped_and_unsupported_formatting_warns():
    result = import_furaffinity.normalize(
        _payload(
            description="[center]Notes[/center]<script>alert(1)</script>"
        ),
        POST_ID,
    )
    assert "<script>" not in result["description"]
    assert "&lt;script&gt;" in result["description"]
    assert any(
        "formatting could not be converted" in warning
        for warning in result["warnings"]
    )
