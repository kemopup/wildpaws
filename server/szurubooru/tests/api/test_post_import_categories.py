from unittest.mock import patch

import pytest

from szurubooru import api, db, errors, model
from szurubooru.func import cache, posts, tags


@pytest.fixture(autouse=True)
def inject_config(config_injector):
    cache.purge()
    config_injector(
        {
            "tag_name_regex": r"^\S+$",
            "privileges": {
                "posts:create:identified": "regular",
                "uploads:use_downloader": "power",
                "tags:create": "regular",
            },
        }
    )


def _set_test_content(post, _content):
    # Keep real post/tag creation and persistence; avoid unrelated media work.
    post.type = model.Post.TYPE_IMAGE
    post.mime_type = "image/png"
    post.checksum = "test-content"


def _create_post(context_factory, user_factory, category_map, tag_names):
    ctx = context_factory(
        params={
            "safety": "sketchy",
            "source": "https://e621.net/posts/123\nhttps://artist.example/art/1",
            "tags": tag_names,
            "tagCategories": category_map,
            "fields": ["id", "safety", "source", "tags"],
        },
        files={"content": posts.EMPTY_PIXEL},
        user=user_factory(),
    )
    with patch.object(
        posts, "update_post_content", side_effect=_set_test_content
    ), patch("szurubooru.func.snapshots.create"), patch.object(
        posts, "generate_alternate_formats", return_value=[]
    ):
        return api.post_api.create_post(ctx)


def test_real_post_creation_categorizes_new_tags_and_preserves_existing_tag_category(
    context_factory, user_factory, tag_category_factory, tag_factory
):
    default = tag_category_factory(name="default", default=True)
    artist = tag_category_factory(name="artist")
    character = tag_category_factory(name="character")
    existing = tag_factory(names=["existing_tag"], category=default)
    db.session.add_all([artist, character, existing])
    db.session.flush()
    result = _create_post(
        context_factory,
        user_factory,
        {
            "artist": ["new_artist", "existing_tag"],
            "character": ["new_character", "removed_character"],
        },
        ["new_artist", "new_character", "existing_tag", "manual_tag"],
    )
    assert result["safety"] == "sketchy"
    assert (
        result["source"]
        == "https://e621.net/posts/123\nhttps://artist.example/art/1"
    )
    assert tags.get_tag_by_name("new_artist").category is artist
    assert tags.get_tag_by_name("new_character").category is character
    assert tags.get_tag_by_name("manual_tag").category is default
    assert existing.category is default
    assert tags.try_get_tag_by_name("removed_character") is None
    post = posts.get_post_by_id(result["id"])
    assert {tag.names[0].name for tag in post.tags} == {
        "new_artist",
        "new_character",
        "existing_tag",
        "manual_tag",
    }


@pytest.mark.parametrize(
    "category_map", [{"general": ["new_tag"]}, {"artist": "new_tag"}, []]
)
def test_post_creation_rejects_invalid_import_category_maps(
    context_factory, user_factory, tag_category_factory, category_map
):
    db.session.add(tag_category_factory(name="default", default=True))
    db.session.flush()
    with pytest.raises(errors.ValidationError):
        _create_post(context_factory, user_factory, category_map, ["new_tag"])


def test_import_categories_do_not_bypass_tag_creation_privilege(
    context_factory, user_factory, tag_category_factory, config_injector
):
    config_injector(
        {
            "tag_name_regex": r"^\S+$",
            "privileges": {
                "posts:create:identified": "regular",
                "uploads:use_downloader": "power",
                "tags:create": "moderator",
            },
        }
    )
    db.session.add(tag_category_factory(name="default", default=True))
    db.session.flush()
    with pytest.raises(errors.AuthError):
        _create_post(
            context_factory, user_factory, {"artist": ["new_tag"]}, ["new_tag"]
        )
