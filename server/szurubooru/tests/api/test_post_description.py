from unittest.mock import patch

import pytest

from szurubooru import api, db, model
from szurubooru.func import posts, snapshots


@pytest.fixture(autouse=True)
def inject_config(config_injector):
    config_injector(
        {
            "privileges": {
                "posts:create:identified": model.User.RANK_REGULAR,
                "posts:edit:description": model.User.RANK_REGULAR,
                "posts:edit:source": model.User.RANK_REGULAR,
                "posts:view": model.User.RANK_ANONYMOUS,
                "uploads:use_downloader": model.User.RANK_REGULAR,
            },
        }
    )


@pytest.mark.parametrize(
    "description",
    [
        "# Artist notes\n\n**Bold**, *italic*, and [a link](https://example.com).",
        "Unicode: 狼 🐾\n\n" + "A long paragraph. " * 1500,
        '<script>alert("literal source")</script>\n\nKeep the Markdown source.',
    ],
    ids=["markdown", "long-unicode", "raw-html"],
)
def test_description_survives_database_round_trip(
    context_factory, post_factory, user_factory, description
):
    post = post_factory()
    db.session.add(post)
    db.session.flush()
    post_id = post.post_id
    with patch("szurubooru.func.snapshots.modify") as modify:
        result = api.post_api.update_post(
            context_factory(
                params={
                    "version": 1,
                    "description": description,
                    "fields": ["id", "version", "description"],
                },
                user=user_factory(rank=model.User.RANK_REGULAR),
            ),
            {"post_id": post_id},
        )
    assert result == {"id": post_id, "version": 2, "description": description}
    modify.assert_called_once()
    db.session.expire_all()
    assert posts.get_post_by_id(post_id).description == description
    assert snapshots.get_post_snapshot(post)["description"] == description
    result = api.post_api.get_post(
        context_factory(
            params={"fields": ["description"]},
            user=user_factory(rank=model.User.RANK_ANONYMOUS),
        ),
        {"post_id": post_id},
    )
    assert result == {"description": description}


@pytest.mark.parametrize("description", ["", None])
def test_description_can_be_cleared(
    context_factory, post_factory, user_factory, description
):
    post = post_factory()
    post.description = "Old description"
    db.session.add(post)
    db.session.flush()
    with patch("szurubooru.func.snapshots.modify"):
        result = api.post_api.update_post(
            context_factory(
                params={
                    "version": 1,
                    "description": description,
                    "fields": ["description"],
                },
                user=user_factory(rank=model.User.RANK_REGULAR),
            ),
            {"post_id": post.post_id},
        )
    assert result == {"description": ""}
    assert post.description is None


def test_other_edits_preserve_description(
    context_factory, post_factory, user_factory
):
    post = post_factory()
    post.description = "Existing **description**"
    db.session.add(post)
    db.session.flush()
    with patch("szurubooru.func.snapshots.modify"):
        result = api.post_api.update_post(
            context_factory(
                params={
                    "version": 1,
                    "source": "https://example.com",
                    "fields": ["description"],
                },
                user=user_factory(rank=model.User.RANK_REGULAR),
            ),
            {"post_id": post.post_id},
        )
    assert result == {"description": "Existing **description**"}


def test_creating_post_with_description(
    context_factory, post_factory, user_factory
):
    post = post_factory()
    description = "New post\n\n**Artist notes** 🐾"
    with patch(
        "szurubooru.func.posts.create_post", return_value=(post, [])
    ), patch("szurubooru.func.snapshots.create"), patch(
        "szurubooru.func.posts.generate_alternate_formats", return_value=[]
    ):
        result = api.post_api.create_post(
            context_factory(
                params={
                    "safety": "safe",
                    "description": description,
                    "fields": ["description"],
                },
                files={"content": b"unused"},
                user=user_factory(rank=model.User.RANK_REGULAR),
            )
        )
    assert result == {"description": description}
    db.session.expire_all()
    assert posts.get_post_by_id(post.post_id).description == description


def test_existing_posts_have_empty_description(post_factory):
    post = post_factory()
    assert posts.serialize_post(post, None, options=["description"]) == {
        "description": "",
    }
