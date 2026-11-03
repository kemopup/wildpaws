import re

import pytest

from szurubooru.func import import_description


@pytest.mark.parametrize(
    "tag", ["thumb", "smallthumb", "mediumthumb", "largethumb", "hugethumb"]
)
def test_inkbunny_submission_embeds_become_links_without_fetching_images(tag):
    markdown, lossy = import_description.to_markdown(
        "Before [{0}]3972391[/{0}] after".format(tag), "inkbunny"
    )
    assert (
        markdown
        == "Before [Inkbunny submission 3972391](https://inkbunny.net/s/3972391) after"
    )
    assert "![" not in markdown
    assert lossy is False


@pytest.mark.parametrize("shortcut", ["S", "M", "L", "H", "h"])
def test_inkbunny_submission_shortcuts_keep_selected_pages(shortcut):
    markdown, lossy = import_description.to_markdown(
        "#" + shortcut + "3973895,2", "inkbunny"
    )
    assert (
        markdown
        == "[Inkbunny submission 3973895, page 2](https://inkbunny.net/s/3973895-p2-)"
    )
    assert lossy is False


def test_inkbunny_bracket_embed_keeps_explicit_page_and_normalizes_integer_ids():
    markdown, lossy = import_description.to_markdown(
        "[hugethumb] 003973895, 2 [/hugethumb]", "inkbunny"
    )
    assert "https://inkbunny.net/s/3973895-p2-" in markdown
    assert lossy is False


@pytest.mark.parametrize("tag", ["icon", "iconname", "name"])
def test_inkbunny_profile_embeds_become_profile_links(tag):
    markdown, lossy = import_description.to_markdown(
        "[{0}]Artist_One[/{0}]".format(tag), "inkbunny"
    )
    assert markdown == "[Artist\\_One](https://inkbunny.net/Artist_One)"
    assert lossy is False


@pytest.mark.parametrize("shortcut", ["@Artist_One", "ib!Artist_One"])
def test_inkbunny_profile_shortcuts_become_source_profile_links(shortcut):
    markdown, lossy = import_description.to_markdown(shortcut, "inkbunny")
    assert markdown == "[Artist\\_One](https://inkbunny.net/Artist_One)"
    assert lossy is False


def test_inkbunny_common_formatting_preserves_title_and_quote_attribution():
    markdown, lossy = import_description.to_markdown(
        "[t]Title[/t]\n[q=Artist_One]Quoted [b]text[/b][/q]", "inkbunny"
    )
    assert "**Title**" in markdown
    assert "> Artist\\_One wrote:\n> Quoted **text**" in markdown
    assert lossy is False


def test_inkbunny_relative_links_use_inkbunny_origin():
    markdown, lossy = import_description.to_markdown(
        "[url=/s/3973895-p2-]Second page[/url]", "inkbunny"
    )
    assert markdown == "[Second page](https://inkbunny.net/s/3973895-p2-)"
    assert lossy is False


@pytest.mark.parametrize("wrapper", ["code", "noparse", "nobbc"])
def test_inkbunny_embeds_inside_literal_blocks_are_not_converted(wrapper):
    markdown, _lossy = import_description.to_markdown(
        "[{0}][hugethumb]123[/hugethumb] @Artist #H123[/{0}]".format(wrapper),
        "inkbunny",
    )
    assert "](https://inkbunny.net" not in markdown


def test_inkbunny_embeds_inside_inline_code_remain_literal():
    markdown, _lossy = import_description.to_markdown(
        "`[hugethumb]123[/hugethumb] @Artist #H123`", "inkbunny"
    )
    assert markdown == "`[hugethumb]123[/hugethumb] @Artist #H123`"


@pytest.mark.parametrize(
    "body",
    ["0", "-1", "123,0", "123,-1", "javascript:alert(1)", "123?sid=secret"],
)
def test_invalid_submission_embed_cannot_supply_a_link_or_active_html(body):
    markdown, lossy = import_description.to_markdown(
        "[hugethumb]" + body + "[/hugethumb]", "inkbunny"
    )
    assert "](https://inkbunny.net/s/" not in markdown
    assert "<script>" not in markdown
    assert lossy is True


def test_inkbunny_valid_embed_does_not_make_adjacent_html_active():
    markdown, _lossy = import_description.to_markdown(
        "[hugethumb]123[/hugethumb]<script>alert(1)</script>", "inkbunny"
    )
    assert "[Inkbunny submission 123](https://inkbunny.net/s/123)" in markdown
    assert "<script>" not in markdown
    assert "&lt;script&gt;" in markdown


@pytest.mark.parametrize(
    "body",
    [
        "evil.example/path",
        "user?sid=secret",
        "<img src=x onerror=alert(1)>",
        "Artist](javascript:alert(1))",
        "@Artist",
    ],
)
def test_invalid_profile_embed_cannot_change_target_or_create_active_markup(
    body,
):
    markdown, lossy = import_description.to_markdown(
        "[icon]" + body + "[/icon]", "inkbunny"
    )
    assert "](https://inkbunny.net/" not in markdown
    assert not re.search(r"(?<!\\)\]\(javascript:", markdown)
    assert "<img" not in markdown
    assert lossy is True


def test_unknown_inkbunny_presentation_remains_readable_and_warns():
    markdown, lossy = import_description.to_markdown(
        "[unknown]Keep text[/unknown]", "inkbunny"
    )
    assert "Keep text" in markdown
    assert lossy is True


@pytest.mark.parametrize(
    "provider", ["e621", "e6ai", "rule34", "tbib", "gelbooru"]
)
def test_inkbunny_specific_tags_and_shortcuts_do_not_change_other_providers(
    provider,
):
    markdown, lossy = import_description.to_markdown(
        "[hugethumb]123[/hugethumb] [icon]Artist[/icon] @Artist #H123 ib!Artist",
        provider,
    )
    assert "https://inkbunny.net" not in markdown
    assert "\\[hugethumb\\]123\\[/hugethumb\\]" in markdown
    assert "\\[icon\\]Artist\\[/icon\\]" in markdown
    assert "&#64;Artist" in markdown
    assert lossy is True


def test_inkbunny_profile_shortcut_does_not_rewrite_email_addresses():
    markdown, lossy = import_description.to_markdown(
        "artist@example.com", "inkbunny"
    )
    assert markdown == "artist&#64;example.com"
    assert lossy is False
