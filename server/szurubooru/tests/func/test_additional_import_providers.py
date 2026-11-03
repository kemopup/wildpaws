import json
import urllib.error
import urllib.parse
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from szurubooru import db, errors, model
from szurubooru.func import import_booru, post_imports


@pytest.mark.parametrize(
    "url,provider,post_id,canonical",
    [
        (
            "https://tbib.org/index.php?page=post&s=view&id=28688424",
            "tbib",
            28688424,
            "https://tbib.org/index.php?page=post&s=view&id=28688424",
        ),
        (
            "https://www.tbib.org/index.php?page=post&s=view&id=123&tags=unwanted#comments",
            "tbib",
            123,
            "https://tbib.org/index.php?page=post&s=view&id=123",
        ),
        (
            "https://gelbooru.com/index.php?page=post&s=view&id=14362913&tags=raww_%28forgotten_island%29+",
            "gelbooru",
            14362913,
            "https://gelbooru.com/index.php?page=post&s=view&id=14362913",
        ),
        (
            "https://www.gelbooru.com/index.php?id=123&s=view&page=post#comments",
            "gelbooru",
            123,
            "https://gelbooru.com/index.php?page=post&s=view&id=123",
        ),
    ],
)
def test_additional_post_urls_are_canonicalized(
    url, provider, post_id, canonical
):
    assert post_imports.parse_post_url(url) == (provider, post_id, canonical)


@pytest.mark.parametrize("host", ["tbib.org", "gelbooru.com"])
@pytest.mark.parametrize(
    "pattern",
    [
        "http://{host}/index.php?page=post&s=view&id=123",
        "https://{host}.evil.test/index.php?page=post&s=view&id=123",
        "https://{host}@evil.test/index.php?page=post&s=view&id=123",
        "https://user:password@{host}/index.php?page=post&s=view&id=123",
        "https://{host}:444/index.php?page=post&s=view&id=123",
        "https://{host}/index.php?page=post&s=view&id=123&id=456",
        "https://{host}/index.php?page=dapi&s=post&q=index&id=123",
        "https://{host}/index.php?page=post&s=view&id=0",
    ],
)
def test_additional_provider_url_validation_precedes_network(host, pattern):
    with patch.object(post_imports, "_request_json") as request:
        with pytest.raises(errors.ValidationError):
            post_imports.preview(pattern.format(host=host))
    request.assert_not_called()


def _dapi_post(provider):
    domain = {"tbib": "tbib.org", "gelbooru": "gelbooru.com"}[provider]
    return {
        "id": 123,
        "file_url": "https://%s/images/original.png" % domain,
        "sample_url": "https://%s/samples/not_the_original.jpg" % domain,
        "preview_url": "https://%s/thumbnails/not_the_original.jpg" % domain,
        "source": "https://artist.example/art/123",
        "rating": "explicit",
        "description": '[b]Artist note[/b]\n"Portfolio":/index.php?page=account',
        "tags": "test_artist test_character never_import_general never_import_species never_import_meta never_import_copyright",
    }


def _tag_rows():
    return [
        {"name": "test_artist", "type": 1},
        {"name": "test_character", "type": "4"},
        {"name": "never_import_general", "type": 0},
        {"name": "never_import_species", "type": "0"},
        {"name": "never_import_meta", "type": 5},
        {"name": "never_import_copyright", "type": 3},
        {"name": "not_on_this_post", "type": 1},
    ]


@pytest.mark.parametrize("wrapped", [False, True])
def test_dapi_imports_original_sources_description_and_only_typed_post_tags(
    wrapped,
    config_injector,
):
    provider = "gelbooru"
    config_injector(
        {
            "post_import": {
                "gelbooru": {"user_id": "222", "api_key": "fixture-key"}
            }
        }
    )
    domain = {"tbib": "tbib.org", "gelbooru": "gelbooru.com"}[provider]
    post = _dapi_post(provider)
    unchanged = deepcopy(post)

    def request(provider_arg, url):
        assert provider_arg == provider
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parsed.query)
        assert parsed.scheme == "https"
        assert parsed.hostname == domain
        assert query["page"] == ["dapi"]
        assert query["q"] == ["index"]
        assert query["json"] == ["1"]
        assert "tags" not in query
        if query["s"] == ["post"]:
            assert query["id"] == ["123"]
            return {"post": [post]} if wrapped else [post]
        assert query["s"] == ["tag"]
        assert set(query["names"][0].split()) == set(post["tags"].split())
        return {"tag": _tag_rows()} if wrapped else _tag_rows()

    with patch.object(
        post_imports, "_request_json", side_effect=request
    ) as mocked:
        result = post_imports.preview(
            "https://%s/index.php?page=post&s=view&id=123&tags=ignored#comments"
            % domain
        )

    assert mocked.call_count == 2
    assert result["provider"] == provider
    assert (
        result["postUrl"]
        == "https://%s/index.php?page=post&s=view&id=123" % domain
    )
    assert result["contentUrl"] == post["file_url"]
    assert result["sources"] == [result["postUrl"], post["source"]]
    assert result["tags"] == {
        "artist": ["test_artist"],
        "character": ["test_character"],
    }
    assert result["safety"] == "unsafe"
    assert "**Artist note**" in result["description"]
    assert (
        "https://%s/index.php?page=account" % domain in result["description"]
    )
    assert post == unchanged
    assert db.session.query(model.Post).count() == 0
    assert db.session.query(model.Tag).count() == 0


def test_untyped_lookup_never_falls_back_to_importing_flat_tags(
    config_injector,
):
    provider = "gelbooru"
    config_injector(
        {
            "post_import": {
                "gelbooru": {"user_id": "222", "api_key": "fixture-key"}
            }
        }
    )
    domain = {"tbib": "tbib.org", "gelbooru": "gelbooru.com"}[provider]
    with patch.object(
        post_imports,
        "_request_json",
        side_effect=[[_dapi_post(provider)], [{"name": "test_artist"}]],
    ):
        with pytest.raises(errors.ProcessingError):
            post_imports.preview(
                "https://%s/index.php?page=post&s=view&id=123" % domain
            )


def test_missing_original_is_not_replaced_by_sample_or_preview(
    config_injector,
):
    provider = "gelbooru"
    config_injector(
        {
            "post_import": {
                "gelbooru": {"user_id": "222", "api_key": "fixture-key"}
            }
        }
    )
    domain = {"tbib": "tbib.org", "gelbooru": "gelbooru.com"}[provider]
    post = _dapi_post(provider)
    post["file_url"] = None
    with patch.object(
        post_imports,
        "_request_json",
        side_effect=[[post], _tag_rows()],
    ):
        with pytest.raises(errors.ProcessingError):
            post_imports.preview(
                "https://%s/index.php?page=post&s=view&id=123" % domain
            )


def _mock_opener():
    opener = MagicMock()
    response = opener.open.return_value.__enter__.return_value
    response.read.return_value = json.dumps({"post": []}).encode("utf8")
    return opener


def test_dapi_credentials_belong_to_the_requested_provider(
    config_injector, monkeypatch
):
    provider = "gelbooru"
    domain = {"tbib": "tbib.org", "gelbooru": "gelbooru.com"}[provider]
    config_injector(
        {
            "post_import": {
                "rule34": {"user_id": "111", "api_key": "other-secret"},
                provider: {"user_id": "222", "api_key": "provider-secret"},
            }
        }
    )
    monkeypatch.setitem(post_imports._LAST_REQUEST, provider, 0.0)
    opener = _mock_opener()
    with patch.object(
        post_imports.urllib.request, "build_opener", return_value=opener
    ):
        post_imports._request_json(
            provider, "https://%s/index.php?page=dapi&s=post&q=index" % domain
        )
    request = opener.open.call_args[0][0]
    query = urllib.parse.parse_qs(
        urllib.parse.urlsplit(request.full_url).query
    )
    assert query["user_id"] == ["222"]
    assert query["api_key"] == ["provider-secret"]
    assert "other-secret" not in request.full_url
    assert request.get_header("Authorization") is None


def test_dapi_can_attempt_public_metadata_without_blank_credentials(
    config_injector, monkeypatch
):
    provider = "gelbooru"
    domain = {"tbib": "tbib.org", "gelbooru": "gelbooru.com"}[provider]
    config_injector({"post_import": {}})
    monkeypatch.setitem(post_imports._LAST_REQUEST, provider, 0.0)
    opener = _mock_opener()
    with patch.object(
        post_imports.urllib.request, "build_opener", return_value=opener
    ):
        post_imports._request_json(
            provider, "https://%s/index.php?page=dapi&s=post&q=index" % domain
        )
    query = urllib.parse.parse_qs(
        urllib.parse.urlsplit(opener.open.call_args[0][0].full_url).query,
        keep_blank_values=True,
    )
    assert "user_id" not in query
    assert "api_key" not in query


def test_dapi_auth_error_does_not_leak_secret_or_return_provider_body(
    config_injector, monkeypatch
):
    provider = "gelbooru"
    domain = {"tbib": "tbib.org", "gelbooru": "gelbooru.com"}[provider]
    config_injector(
        {
            "post_import": {
                provider: {"user_id": "222", "api_key": "provider-secret"}
            }
        }
    )
    monkeypatch.setitem(post_imports._LAST_REQUEST, provider, 0.0)
    opener = _mock_opener()
    opener.open.side_effect = urllib.error.HTTPError(
        "https://%s/?api_key=provider-secret" % domain,
        403,
        "provider-secret rejected by upstream",
        {},
        None,
    )
    with patch.object(
        post_imports.urllib.request, "build_opener", return_value=opener
    ):
        with pytest.raises(errors.ProcessingError) as exc:
            post_imports._request_json(
                provider,
                "https://%s/index.php?page=dapi&s=post&q=index" % domain,
            )
    assert "provider-secret" not in str(exc.value)
    assert "api_key" not in str(exc.value)
    opener.open.assert_called_once()


def test_tbib_xml_metadata_is_read_without_treating_flat_tags_as_categories(
    config_injector, monkeypatch
):
    config_injector({"post_import": {}})
    monkeypatch.setitem(post_imports._LAST_REQUEST, "tbib", 0.0)
    opener = _mock_opener()
    response = opener.open.return_value.__enter__.return_value
    response.read.return_value = (
        b'<posts count="1"><post id="123" directory="55" '
        b'image="original.png" rating="e" '
        b'tags="test_artist test_character never_import_general" /></posts>'
    )
    with patch.object(
        post_imports.urllib.request, "build_opener", return_value=opener
    ):
        result = post_imports._request_json(
            "tbib",
            "https://tbib.org/index.php?page=dapi&s=post&q=index&id=123",
        )
    assert result["post"][0]["id"] == "123"
    assert (
        result["post"][0]["tags"]
        == "test_artist test_character never_import_general"
    )
    assert "artist" not in result["post"][0]
    assert opener.open.call_args[0][0].get_header("Authorization") is None


def test_tbib_preview_uses_one_post_details_lookup_not_unfiltered_tag_queries():
    canonical = "https://tbib.org/index.php?page=post&s=view&id=123"
    original = "https://tbib.org/images/55/original.png"
    flat_tags = "test_artist test_character never_import_general"
    post = {
        "id": "123",
        "image": "original.png",
        "directory": "55",
        "rating": "e",
        "tags": flat_tags,
    }
    details = {
        "contentUrl": original,
        "sources": ["https://artist.example/art/123"],
        "tags": {"artist": ["test_artist"], "character": ["test_character"]},
    }
    with patch.object(
        post_imports, "_request_json", return_value={"post": [post]}
    ) as request, patch.object(
        post_imports, "_request_tbib_details", return_value=details
    ) as fetch_details:
        result = post_imports.preview(canonical + "&tags=ignored#comments")
    request.assert_called_once()
    fetch_details.assert_called_once()
    assert fetch_details.call_args[0][0] == canonical
    assert set(fetch_details.call_args[0][1]) == set(flat_tags.split())
    assert result["contentUrl"] == original
    assert result["sources"] == [canonical, "https://artist.example/art/123"]
    assert result["tags"] == details["tags"]
    assert result["safety"] == "unsafe"
    assert db.session.query(model.Post).count() == 0
    assert db.session.query(model.Tag).count() == 0


def test_tbib_missing_typed_categories_does_not_import_flat_general_tags():
    post = {
        "id": "123",
        "image": "original.png",
        "directory": "55",
        "rating": "s",
        "tags": "never_import_general",
    }
    details = {
        "contentUrl": "https://tbib.org/images/55/original.png",
        "sources": [],
        "tags": {"artist": [], "character": []},
    }
    with patch.object(
        post_imports, "_request_json", return_value={"post": [post]}
    ), patch.object(
        post_imports, "_request_tbib_details", return_value=details
    ):
        result = post_imports.preview(
            "https://tbib.org/index.php?page=post&s=view&id=123"
        )
    assert result["tags"] == {"artist": [], "character": []}


def test_tbib_xml_entity_declarations_are_rejected_before_expansion(
    config_injector, monkeypatch
):
    config_injector({"post_import": {}})
    monkeypatch.setitem(post_imports._LAST_REQUEST, "tbib", 0.0)
    opener = _mock_opener()
    response = opener.open.return_value.__enter__.return_value
    response.read.return_value = (
        b'<!DOCTYPE posts [<!ENTITY a "nested">]>'
        b'<posts><post id="123" tags="&a;" /></posts>'
    )
    with patch.object(
        post_imports.urllib.request, "build_opener", return_value=opener
    ):
        with pytest.raises(errors.ProcessingError):
            post_imports._request_json(
                "tbib",
                "https://tbib.org/index.php?page=dapi&s=post&q=index&id=123",
            )


@pytest.mark.parametrize(
    "provider,host,sidebar",
    [
        ("tbib", "tbib.org", "tag-sidebar"),
        ("gelbooru", "gelbooru.com", "tag-list"),
    ],
)
def test_public_post_parser_uses_sidebar_types_original_link_and_source_only(
    provider, host, sidebar
):
    canonical = "https://%s/index.php?page=post&s=view&id=123" % host
    html = """
        <ul id="{sidebar}">
          <li class="tag-type-artist"><a href="/index.php?page=wiki&amp;s=list&amp;search=test_artist">?</a>
            <a href="/index.php?page=post&amp;s=list&amp;tags=test_artist">Different Display Label</a></li>
          <li class="tag-type-character"><a href="/index.php?page=post&amp;s=list&amp;tags=test_character">Character Display Label</a></li>
          <li class="tag-type-general"><a href="/index.php?page=post&amp;s=list&amp;tags=never_import_general">Never Import General</a></li>
          <li class="tag-type-copyright"><a href="/index.php?page=post&amp;s=list&amp;tags=never_import_copyright">Never Import Copyright</a></li>
        </ul>
        <ul id="comments">
          <li class="tag-type-artist"><a href="/index.php?page=post&amp;s=list&amp;tags=not_a_real_post_tag">Unrelated</a></li>
        </ul>
        <ul id="stats">
          <li>Source: <a href="https://artist.example/art/123">Artist source</a></li>
          <li>Rating: General</li>
        </ul>
        <a href="https://unrelated.example/not_a_source">Comment link</a>
        <img id="image" src="https://{host}/samples/preview.jpg" />
        <a href="https://{host}/images/original.png">Original image</a>
        """.format(
        sidebar=sidebar, host=host
    )
    result = import_booru.parse_details(
        html,
        canonical,
        [
            "test_artist",
            "test_character",
            "never_import_general",
            "never_import_copyright",
        ],
    )
    assert result["contentUrl"] == "https://%s/images/original.png" % host
    assert result["sources"] == ["https://artist.example/art/123"]
    assert result["tags"] == {
        "artist": ["test_artist"],
        "character": ["test_character"],
    }
    assert result["rating"].lower() == "general"


def test_public_parser_never_uses_sample_when_original_link_is_missing():
    html = '<ul id="tag-sidebar"></ul><li>Rating: General</li><img id="image" src="https://tbib.org/samples/preview.jpg" />'
    try:
        result = import_booru.parse_details(
            html, "https://tbib.org/index.php?page=post&s=view&id=123", []
        )
    except (errors.ProcessingError, ValueError):
        return
    assert not result.get("contentUrl")


def test_tbib_details_filter_out_typed_tags_not_present_in_xml_post_metadata():
    html = """<ul id="tag-sidebar">
        <li class="tag-type-artist"><a href="/index.php?page=post&amp;s=list&amp;tags=not_in_post">Unrelated artist</a></li>
        <li class="tag-type-character"><a href="/index.php?page=post&amp;s=list&amp;tags=test_character">Character</a></li>
        </ul><li>Rating: General</li><a href="https://tbib.org/images/original.png">Original image</a>"""
    result = post_imports._parse_tbib_details(
        html,
        "https://tbib.org/index.php?page=post&s=view&id=123",
        ["test_character", "never_import_general"],
    )
    assert result["tags"] == {"artist": [], "character": ["test_character"]}


def test_gelbooru_public_preview_uses_no_api_account_or_flat_tags(
    config_injector,
):
    config_injector({"post_import": {}})
    canonical = "https://gelbooru.com/index.php?page=post&s=view&id=123"
    details = {
        "contentUrl": "https://img3.gelbooru.com/images/original.png",
        "sources": ["https://artist.example/art/123"],
        "tags": {"artist": ["test_artist"], "character": ["test_character"]},
        "rating": "General",
    }
    with patch.object(
        post_imports, "_request_booru_details", return_value=details
    ) as page, patch.object(post_imports, "_request_json") as api:
        result = post_imports.preview(canonical + "&tags=ignored")
    api.assert_not_called()
    page.assert_called_once_with("gelbooru", canonical)
    assert result["tags"] == details["tags"]
    assert result["contentUrl"] == details["contentUrl"]
    assert result["sources"] == [canonical, "https://artist.example/art/123"]
    assert result["safety"] == "safe"
