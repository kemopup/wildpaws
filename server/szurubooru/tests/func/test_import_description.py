import re

import pytest

from szurubooru.func import import_description


@pytest.mark.parametrize("value", [None, "", 123, {}, []])
def test_empty_or_missing_description_is_empty_markdown(value):
    assert import_description.to_markdown(value, "e621") == ("", False)


def test_common_dtext_formatting_becomes_markdown():
    markdown, lossy = import_description.to_markdown(
        "h2. Artwork\r\n\r\n[b]Bold[/b] [i]italic[/i] [s]old[/s]", "e621"
    )
    assert "## Artwork" in markdown
    assert "**Bold**" in markdown
    assert "*italic*" in markdown
    assert "~~old~~" in markdown
    assert "\r" not in markdown
    assert lossy is False


def test_nested_formatting_keeps_both_levels():
    markdown, lossy = import_description.to_markdown(
        "[b]outer [i]inner[/i] text[/b]", "e6ai"
    )
    assert markdown == "**outer *inner* text**"
    assert lossy is False


def test_quotes_and_nested_dtext_lists_are_markdown_blocks():
    markdown, _lossy = import_description.to_markdown(
        "[quote]First line\nSecond line[/quote]\n\n" "* One\n** Child\n* Two",
        "e621",
    )
    assert "> First line" in markdown
    assert "> Second line" in markdown
    lines = markdown.splitlines()
    assert any(re.fullmatch(r"[-*] One", line) for line in lines)
    assert any(re.fullmatch(r" {2,}[-*] Child", line) for line in lines)


def test_code_blocks_preserve_literal_dtext_and_embedded_fences():
    body = "[b]not bold[/b]\n```\n<script>literal</script>"
    markdown, lossy = import_description.to_markdown(
        "[code]" + body + "[/code]", "e621"
    )
    assert body in markdown
    assert markdown.splitlines()[0].startswith("````")
    assert markdown.splitlines()[-1] == markdown.splitlines()[0]
    assert lossy is False


def test_inline_code_is_not_converted_as_dtext():
    markdown, _lossy = import_description.to_markdown(
        "Use `[b]literal[/b]` here.", "e621"
    )
    assert "`[b]literal[/b]`" in markdown
    assert "**literal**" not in markdown


@pytest.mark.parametrize("wrapper", ["nodtext", "noparse", "nobbc"])
def test_no_parse_blocks_preserve_literal_instructions(wrapper):
    markdown, _lossy = import_description.to_markdown(
        "[{0}][b]literal[/b] *stars* _underscores_[/{0}]".format(wrapper),
        "e621",
    )
    assert "**literal**" not in markdown
    assert "\\[b\\]literal\\[/b\\]" in markdown
    assert "\\*stars\\*" in markdown
    assert "\\_underscores\\_" in markdown


@pytest.mark.parametrize("provider", ["e621", "e6ai"])
def test_dtext_links_keep_source_site_and_link_titles(provider):
    markdown, lossy = import_description.to_markdown(
        '"Artist":https://artist.example/art '
        '"Users":/users '
        "[[blue_eyes|Blue eyes]] {{blue_eyes -cat}} post #123 thumb #456",
        provider,
    )
    assert "[Artist](https://artist.example/art)" in markdown
    assert "[Users](https://%s.net/users)" % provider in markdown
    assert "[Blue eyes](https://%s.net/wiki_pages/" % provider in markdown
    assert "title=blue_eyes" in markdown
    assert "https://%s.net/posts?tags=" % provider in markdown
    assert "blue_eyes" in markdown
    assert "https://%s.net/posts/123" % provider in markdown
    assert "https://%s.net/posts/456" % provider in markdown
    assert lossy is False


def test_bracketed_link_url_keeps_parentheses_inside_destination():
    markdown, _lossy = import_description.to_markdown(
        '"Artist":[https://artist.example/art_(test)]', "e621"
    )
    assert "[Artist](" in markdown
    assert "https://artist.example/art_" in markdown
    assert "test" in markdown
    assert "\\[https" not in markdown


@pytest.mark.parametrize(
    "text",
    [
        '"Click":javascript:alert(1)',
        "[url=javascript:alert(1)]Click[/url]",
        "[url=data:text/html,attack]Click[/url]",
    ],
)
def test_untrusted_link_schemes_never_become_clickable_markdown(text):
    markdown, _lossy = import_description.to_markdown(text, "e621")
    assert "Click" in markdown
    assert not re.search(r"(?<!\\)\]\(\s*<?(?:javascript|data):", markdown)


def test_raw_html_is_rendered_as_text_instead_of_active_tags():
    markdown, _lossy = import_description.to_markdown(
        '<script>alert(1)</script><img src="x" onerror="alert(2)">', "e621"
    )
    assert "<script" not in markdown
    assert "<img" not in markdown
    assert "alert" in markdown


@pytest.mark.parametrize("markup", ["u", "sup", "sub", "color=red"])
def test_unsupported_formatting_preserves_text_and_reports_loss(markup):
    closing = markup.split("=")[0]
    markdown, lossy = import_description.to_markdown(
        "[%s]Keep this text[/%s]" % (markup, closing), "e621"
    )
    assert "Keep this text" in markdown
    assert lossy is True


def test_plain_underscores_do_not_accidentally_become_markdown_emphasis():
    markdown, lossy = import_description.to_markdown(
        "artist_name and character_name", "e621"
    )
    assert "artist\\_name" in markdown
    assert "character\\_name" in markdown
    assert lossy is False
