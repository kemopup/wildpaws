from copy import deepcopy
import base64
import urllib.error
import urllib.parse
from unittest.mock import MagicMock, patch

import pytest

from szurubooru import db, errors, model
from szurubooru.func import post_imports


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "https://e621.net/posts/123",
            ("e621", 123, "https://e621.net/posts/123"),
        ),
        (
            "https://www.e621.net/posts/123.json?foo=bar#tags",
            ("e621", 123, "https://e621.net/posts/123"),
        ),
        (
            "https://e621.net/post/show/123",
            ("e621", 123, "https://e621.net/posts/123"),
        ),
        (
            "https://e6ai.net/posts/456",
            ("e6ai", 456, "https://e6ai.net/posts/456"),
        ),
        (
            "https://www.e6ai.net/post/show/456",
            ("e6ai", 456, "https://e6ai.net/posts/456"),
        ),
        (
            "https://rule34.xxx/index.php?page=post&s=view&id=789",
            (
                "rule34",
                789,
                "https://rule34.xxx/index.php?page=post&s=view&id=789",
            ),
        ),
    ],
)
def test_parse_supported_post_urls(url, expected):
    assert post_imports.parse_post_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://e621.net/posts/123",
        "https://e621.net.evil.test/posts/123",
        "https://e621.net@evil.test/posts/123",
        "https://user:password@e621.net/posts/123",
        "https://e621.net:443/posts/123",
        "https://127.0.0.1/posts/123",
        "https://e621.net/posts/0",
        "https://e621.net/posts/-123",
        "https://e621.net/posts/123/other",
        "https://e621.net/posts?tags=test",
        "https://rule34.xxx/index.php?page=post&s=view&id=1&id=2",
        "https://rule34.xxx/index.php?page=dapi&s=post&q=index&id=123",
        "https://rule34.xxx/index.php?page=post&s=view&id=abc",
        "file:///etc/passwd",
        "",
    ],
)
def test_reject_unsupported_or_ambiguous_import_urls_before_network(url):
    with patch.object(post_imports, "_request_json") as request:
        with pytest.raises(errors.ValidationError):
            post_imports.preview(url)
    request.assert_not_called()


def _e621_post(**updates):
    post = {
        "id": 123,
        "file": {"url": "https://static1.e621.net/data/ab/cd/original.png"},
        "preview": {"url": "https://static1.e621.net/data/preview.png"},
        "sample": {"url": "https://static1.e621.net/data/sample.jpg"},
        "tags": {
            "artist": ["test_artist"],
            "character": ["test_character"],
            "general": ["never_import_general"],
            "species": ["never_import_species"],
            "copyright": ["never_import_copyright"],
            "meta": ["never_import_meta"],
            "invalid": ["never_import_invalid"],
        },
        "sources": ["https://artist.example/art/1"],
        "rating": "q",
        "flags": {"deleted": False},
    }
    post.update(updates)
    return {"post": post}


def test_favorites_query_and_fragment_are_not_sent_to_provider_or_stored():
    data = _e621_post()
    with patch.object(
        post_imports, "_request_json", return_value=data
    ) as request:
        result = post_imports.preview(
            "https://e621.net/posts/123?q=fav%3AJessicaWolf#tags"
        )
    request.assert_called_once_with("e621", "https://e621.net/posts/123.json")
    assert result["postUrl"] == "https://e621.net/posts/123"
    assert all("JessicaWolf" not in source for source in result["sources"])


def test_preview_imports_only_artist_character_original_file_and_sources():
    data = _e621_post()
    unchanged = deepcopy(data)
    with patch.object(post_imports, "_request_json", return_value=data):
        result = post_imports.preview("https://e621.net/posts/123")

    assert result["provider"] == "e621"
    assert result["postUrl"] == "https://e621.net/posts/123"
    assert result["contentUrl"] == data["post"]["file"]["url"]
    assert result["sources"] == [
        "https://e621.net/posts/123",
        "https://artist.example/art/1",
    ]
    assert result["tags"] == {
        "artist": ["test_artist"],
        "character": ["test_character"],
    }
    assert result["safety"] == "sketchy"
    assert data == unchanged
    assert db.session.query(model.Post).count() == 0
    assert db.session.query(model.Tag).count() == 0


@pytest.mark.parametrize("provider", ["e621", "e6ai"])
def test_preview_description_is_markdown_without_importing_other_tags(
    provider,
):
    data = _e621_post(
        file={"url": "https://static1.%s.net/data/original.png" % provider},
        description='[b]Artist note[/b]\n"Portfolio":https://artist.example/art',
    )
    with patch.object(post_imports, "_request_json", return_value=data):
        result = post_imports.preview("https://%s.net/posts/123" % provider)
    assert "**Artist note**" in result["description"]
    assert "[Portfolio](https://artist.example/art)" in result["description"]
    assert result["tags"] == {
        "artist": ["test_artist"],
        "character": ["test_character"],
    }


def test_preview_missing_description_is_empty_and_never_uses_comments():
    data = _e621_post(comments=[{"body": "Never import this comment"}])
    with patch.object(post_imports, "_request_json", return_value=data):
        result = post_imports.preview("https://e621.net/posts/123")
    assert result["description"] == ""


def test_preview_warns_when_description_formatting_cannot_be_preserved():
    data = _e621_post(description="[color=red]A colored note[/color]")
    with patch.object(post_imports, "_request_json", return_value=data):
        result = post_imports.preview("https://e621.net/posts/123")
    assert "A colored note" in result["description"]
    assert any(
        "description" in warning.lower() for warning in result["warnings"]
    )


@pytest.mark.parametrize(
    "rating,safety", [("s", "safe"), ("q", "sketchy"), ("e", "unsafe")]
)
def test_maps_remote_rating_without_defaulting_explicit_posts_to_safe(
    rating, safety
):
    with patch.object(
        post_imports, "_request_json", return_value=_e621_post(rating=rating)
    ):
        assert (
            post_imports.preview("https://e621.net/posts/123")["safety"]
            == safety
        )


def test_missing_artist_category_does_not_copy_e6ai_general_tags_as_artists():
    data = _e621_post(
        id=456,
        file={"url": "https://static1.e6ai.net/data/original.png"},
        tags={
            "character": ["test_character"],
            "general": ["never_import_general"],
        },
    )
    with patch.object(post_imports, "_request_json", return_value=data):
        result = post_imports.preview("https://e6ai.net/posts/456")
    assert result["tags"] == {"artist": [], "character": ["test_character"]}


@pytest.mark.parametrize(
    "updates",
    [
        {"file": {"url": None}},
        {"file": {}},
        {"flags": {"deleted": True}},
        {"file": {"url": "https://127.0.0.1/private.png"}},
        {"file": {"url": "https://static1.e621.net.evil.test/data/x.png"}},
        {"file": {"url": "http://static1.e621.net/data/x.png"}},
    ],
)
def test_refuses_unavailable_or_invalid_remote_files_and_ratings(updates):
    with patch.object(
        post_imports, "_request_json", return_value=_e621_post(**updates)
    ):
        with pytest.raises(errors.ProcessingError):
            post_imports.preview("https://e621.net/posts/123")


def test_sources_are_deduplicated_and_non_web_sources_are_ignored():
    sources = [
        "https://e621.net/posts/123",
        "https://artist.example/art/1",
        "https://artist.example/art/1",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "data:text/html,untrusted",
    ]
    with patch.object(
        post_imports, "_request_json", return_value=_e621_post(sources=sources)
    ):
        result = post_imports.preview("https://e621.net/posts/123")
    assert result["sources"] == [
        "https://e621.net/posts/123",
        "https://artist.example/art/1",
    ]


def test_long_sources_warn_without_silently_dropping_provenance():
    sources = [
        "https://artist.example/art/" + str(i) + "x" * 200 for i in range(12)
    ]
    with patch.object(
        post_imports, "_request_json", return_value=_e621_post(sources=sources)
    ):
        result = post_imports.preview("https://e621.net/posts/123")
    assert result["sources"][1:] == sources
    assert any("2,048" in warning for warning in result["warnings"])


def test_unrecognized_rating_defaults_to_unsafe_and_requires_review():
    with patch.object(
        post_imports,
        "_request_json",
        return_value=_e621_post(rating="unknown"),
    ):
        result = post_imports.preview("https://e621.net/posts/123")
    assert result["safety"] == "unsafe"
    assert any("rating" in warning.lower() for warning in result["warnings"])


def test_imported_tag_names_are_unique_valid_names_without_other_categories():
    tags = {
        "artist": ["test_artist", "TEST_ARTIST", "bad name", None, "x" * 129],
        "character": ["valid_character", "bad\ntag", "bad\x00tag"],
        "general": ["excluded_general"],
    }
    with patch.object(
        post_imports, "_request_json", return_value=_e621_post(tags=tags)
    ):
        result = post_imports.preview("https://e621.net/posts/123")
    assert result["tags"] == {
        "artist": ["test_artist"],
        "character": ["valid_character"],
    }


@pytest.mark.parametrize("wrapped", [False, True])
def test_rule34_uses_tag_info_artist_character_only(wrapped):
    post = {
        "id": 789,
        "file_url": "https://wimg.rule34.xxx/images/original.jpg",
        "sample_url": "https://wimg.rule34.xxx/samples/sample.jpg",
        "source": "https://artist.example/art/1",
        "rating": "explicit",
        "tags": "excluded_general excluded_species excluded_meta",
        "tag_info": [
            {"tag": "test_artist", "type": "artist", "count": 2},
            {"tag": "test_character", "type": "character", "count": 5},
            {"tag": "excluded_general", "type": "general", "count": 9},
            {"tag": "excluded_numeric", "type": 0, "count": 9},
        ],
    }
    payload = {"post": [post]} if wrapped else [post]
    with patch.object(
        post_imports, "_request_json", return_value=payload
    ) as request:
        result = post_imports.preview(
            "https://rule34.xxx/index.php?page=post&s=view&id=789"
        )
    assert result["contentUrl"] == post["file_url"]
    assert result["tags"] == {
        "artist": ["test_artist"],
        "character": ["test_character"],
    }
    assert result["safety"] == "unsafe"
    assert result["sources"] == [result["postUrl"], post["source"]]
    provider, endpoint = request.call_args[0]
    assert provider == "rule34"
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(endpoint).query)[
        "fields"
    ] == ["tag_info"]


def test_rule34_missing_tag_types_fails_instead_of_falling_back_to_all_tags():
    payload = [
        {
            "id": 789,
            "file_url": "https://wimg.rule34.xxx/images/original.jpg",
            "tags": "must_not_import",
        }
    ]
    with patch.object(post_imports, "_request_json", return_value=payload):
        with pytest.raises(errors.ProcessingError, match="categorized tags"):
            post_imports.preview(
                "https://rule34.xxx/index.php?page=post&s=view&id=789"
            )


@pytest.mark.parametrize("include_description", [False, True])
def test_rule34_description_is_optional_and_does_not_use_comments(
    include_description,
):
    post = {
        "id": 789,
        "file_url": "https://wimg.rule34.xxx/images/original.jpg",
        "rating": "explicit",
        "tag_info": [],
        "comments": [{"body": "Never import this comment"}],
    }
    if include_description:
        post["description"] = "[b]A creator note[/b]"
    with patch.object(post_imports, "_request_json", return_value=[post]):
        result = post_imports.preview(
            "https://rule34.xxx/index.php?page=post&s=view&id=789"
        )
    if include_description:
        assert result["description"] == "**A creator note**"
    else:
        assert result["description"] == ""
        assert any(
            "description" in warning.lower() for warning in result["warnings"]
        )


def _mock_opener(data=b"{}"):
    opener = MagicMock()
    response = opener.open.return_value.__enter__.return_value
    response.read.return_value = data
    return opener, response


@pytest.mark.parametrize("provider", ["e621", "e6ai"])
def test_api_credentials_use_basic_auth_and_stay_out_of_request_url(
    provider, config_injector, monkeypatch
):
    config_injector(
        {
            "post_import": {
                provider: {"username": "import_user", "api_key": "secret-key"}
            }
        }
    )
    monkeypatch.setitem(post_imports._LAST_REQUEST, provider, 0.0)
    opener, response = _mock_opener(b'{"post": {"id": 123}}')
    url = "https://%s.net/posts/123.json" % provider
    with patch.object(
        post_imports.urllib.request, "build_opener", return_value=opener
    ):
        result = post_imports._request_json(provider, url)
    assert result == {"post": {"id": 123}}
    request = opener.open.call_args[0][0]
    assert request.full_url == url
    assert request.get_header("Authorization") == "Basic " + base64.b64encode(
        b"import_user:secret-key"
    ).decode("ascii")
    assert "Wildpaws" in request.get_header("User-agent")
    assert opener.open.call_args[1] == {"timeout": 15}
    response.read.assert_called_once_with(post_imports._MAX_METADATA + 1)


def test_rule34_missing_credentials_fails_before_network(config_injector):
    config_injector({"post_import": {}})
    with patch.object(post_imports.urllib.request, "build_opener") as build:
        with pytest.raises(
            errors.ProcessingError, match="API user ID and key"
        ):
            post_imports._request_json(
                "rule34", "https://api.rule34.xxx/index.php?page=dapi"
            )
    build.assert_not_called()


def test_rule34_uses_provider_specific_key_in_its_fixed_endpoint(
    config_injector, monkeypatch
):
    config_injector(
        {
            "post_import": {
                "rule34": {"user_id": "123", "api_key": "secret-key"}
            }
        }
    )
    monkeypatch.setitem(post_imports._LAST_REQUEST, "rule34", 0.0)
    opener, _response = _mock_opener()
    with patch.object(
        post_imports.urllib.request, "build_opener", return_value=opener
    ):
        post_imports._request_json(
            "rule34", "https://api.rule34.xxx/index.php?page=dapi"
        )
    request = opener.open.call_args[0][0]
    query = urllib.parse.parse_qs(
        urllib.parse.urlsplit(request.full_url).query
    )
    assert query["user_id"] == ["123"]
    assert query["api_key"] == ["secret-key"]
    assert request.get_header("Authorization") is None


@pytest.mark.parametrize("status", [301, 401, 403, 404, 429, 503])
def test_provider_errors_never_return_credentials_or_provider_body(
    status, config_injector, monkeypatch
):
    config_injector(
        {
            "post_import": {
                "rule34": {"user_id": "123", "api_key": "secret-key"}
            }
        }
    )
    monkeypatch.setitem(post_imports._LAST_REQUEST, "rule34", 0.0)
    opener, _response = _mock_opener()
    opener.open.side_effect = urllib.error.HTTPError(
        "https://api.rule34.xxx/?api_key=secret-key",
        status,
        "secret-key in provider error",
        {},
        None,
    )
    with patch.object(
        post_imports.urllib.request, "build_opener", return_value=opener
    ):
        with pytest.raises(errors.ProcessingError) as exc:
            post_imports._request_json(
                "rule34", "https://api.rule34.xxx/index.php?page=dapi"
            )
    assert "secret-key" not in str(exc.value)
    assert "api_key" not in str(exc.value)


def test_credentials_are_not_forwarded_through_redirects():
    request = post_imports.urllib.request.Request(
        "https://e621.net/posts/123.json",
        headers={"Authorization": "Basic secret"},
    )
    assert (
        post_imports._NoRedirect().redirect_request(
            request, None, 302, "Found", {}, "https://evil.example/steal"
        )
        is None
    )


@pytest.mark.parametrize("data", [b"not json", b"\xff", b"x" * 33])
def test_metadata_response_is_size_limited_and_must_be_json(
    data, config_injector, monkeypatch
):
    config_injector({"post_import": {}})
    monkeypatch.setitem(post_imports._LAST_REQUEST, "e621", 0.0)
    monkeypatch.setattr(post_imports, "_MAX_METADATA", 32)
    opener, response = _mock_opener(data)
    with patch.object(
        post_imports.urllib.request, "build_opener", return_value=opener
    ):
        with pytest.raises(errors.ProcessingError):
            post_imports._request_json(
                "e621", "https://e621.net/posts/123.json"
            )
    response.read.assert_called_once_with(33)


def test_api_rate_limit_waits_between_requests(config_injector, monkeypatch):
    config_injector({"post_import": {}})
    monkeypatch.setitem(post_imports._LAST_REQUEST, "e621", 100.0)
    opener, _response = _mock_opener()
    with patch.object(
        post_imports.time, "monotonic", return_value=100.2
    ), patch.object(post_imports.time, "sleep") as sleep, patch.object(
        post_imports.urllib.request, "build_opener", return_value=opener
    ):
        post_imports._request_json("e621", "https://e621.net/posts/123.json")
    assert sleep.call_args[0][0] == pytest.approx(0.9)


def test_category_assignment_uses_existing_case_insensitive_category_and_tag_aliases(
    tag_factory, tag_category_factory
):
    default = tag_category_factory(name="default", default=True)
    artist = tag_category_factory(name="Artist")
    character = tag_category_factory(name="Character")
    artist_tag = tag_factory(
        names=["artist_primary", "Artist_Alias"], category=default
    )
    character_tag = tag_factory(names=["char_primary"], category=default)
    untouched_tag = tag_factory(names=["manual_tag"], category=default)
    db.session.add_all(
        [artist, character, artist_tag, character_tag, untouched_tag]
    )
    db.session.flush()
    post_imports.apply_tag_categories(
        [artist_tag, character_tag, untouched_tag],
        {"artist": ["artist_alias"], "character": ["CHAR_PRIMARY"]},
    )
    assert artist_tag.category is artist
    assert character_tag.category is character
    assert untouched_tag.category is default


def test_category_assignment_does_not_touch_existing_tags_not_in_new_tags(
    tag_factory, tag_category_factory
):
    default = tag_category_factory(name="default", default=True)
    artist = tag_category_factory(name="artist")
    existing = tag_factory(names=["already_here"], category=default)
    new_tag = tag_factory(names=["new_artist"], category=default)
    db.session.add_all([artist, existing, new_tag])
    db.session.flush()
    post_imports.apply_tag_categories(
        [new_tag], {"artist": ["already_here", "new_artist"], "character": []}
    )
    assert existing.category is default
    assert new_tag.category is artist


def test_missing_local_category_keeps_default_without_creating_category(
    tag_factory, tag_category_factory
):
    default = tag_category_factory(name="default", default=True)
    tag = tag_factory(names=["new_artist"], category=default)
    db.session.add(tag)
    db.session.flush()
    post_imports.apply_tag_categories(
        [tag], {"artist": ["new_artist"], "character": []}
    )
    assert tag.category is default
    assert db.session.query(model.TagCategory).count() == 1


@pytest.mark.parametrize(
    "category_map",
    [
        {"general": ["not_allowed"]},
        {"artist": "not_a_list"},
        {"artist": [123]},
        {"character": [None]},
        [],
        "artist",
    ],
)
def test_invalid_category_maps_are_rejected(category_map):
    with pytest.raises(errors.ValidationError):
        post_imports.apply_tag_categories([], category_map)
