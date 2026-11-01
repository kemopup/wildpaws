from unittest.mock import patch

import pytest

from szurubooru import api, config, db, errors, model


@pytest.fixture(autouse=True)
def default_permissions(config_injector):
    config_injector(config._file_config("config.yaml.dist"))


@pytest.mark.parametrize(
    "rank,ownership,allowed",
    [
        (model.User.RANK_REGULAR, "own", True),
        (model.User.RANK_POWER, "own", True),
        (model.User.RANK_RESTRICTED, "own", False),
        (model.User.RANK_REGULAR, "other", False),
        (model.User.RANK_POWER, "other", False),
        (model.User.RANK_MODERATOR, "other", True),
        (model.User.RANK_ADMINISTRATOR, "other", True),
        (model.User.RANK_MODERATOR, "own", True),
        (model.User.RANK_REGULAR, "anonymous", False),
        (model.User.RANK_ANONYMOUS, "anonymous", False),
        (model.User.RANK_MODERATOR, "anonymous", True),
        (model.User.RANK_ADMINISTRATOR, "anonymous", True),
    ],
)
def test_description_ownership_permissions(
    context_factory, post_factory, user_factory, rank, ownership, allowed
):
    actor = user_factory(rank=rank)
    post = post_factory()
    if ownership == "own":
        post.user = actor
    elif ownership == "other":
        post.user = user_factory()
    post.description = "Original description"
    db.session.add(post)
    if rank != model.User.RANK_ANONYMOUS:
        db.session.add(actor)
    db.session.flush()
    ctx = context_factory(
        params={
            "version": 1,
            "description": "Updated **description**",
            "fields": ["description"],
        },
        user=actor,
    )
    with patch("szurubooru.func.snapshots.modify"):
        if allowed:
            assert api.post_api.update_post(
                ctx, {"post_id": post.post_id}
            ) == {
                "description": "Updated **description**",
            }
        else:
            with pytest.raises(errors.AuthError):
                api.post_api.update_post(ctx, {"post_id": post.post_id})
            assert post.description == "Original description"
            assert post.version == 1


@pytest.mark.parametrize(
    "rank", [model.User.RANK_REGULAR, model.User.RANK_POWER]
)
@pytest.mark.parametrize(
    "params,files",
    [
        ({"source": "https://example.com/changed"}, {}),
        ({"safety": "sketchy"}, {}),
        ({"relations": []}, {}),
        ({"flags": ["loop"]}, {}),
        ({"contentUrl": "https://example.com/changed.png"}, {}),
        ({}, {"content": b"unused"}),
        ({}, {"thumbnail": b"unused"}),
    ],
)
def test_other_post_fields_require_moderation(
    context_factory, post_factory, user_factory, rank, params, files
):
    actor = user_factory(rank=rank)
    post = post_factory()
    post.user = actor
    db.session.add(post)
    db.session.flush()
    with pytest.raises(errors.AuthError):
        api.post_api.update_post(
            context_factory(
                params={"version": 1, **params},
                files=files,
                user=actor,
            ),
            {"post_id": post.post_id},
        )


def test_regular_users_can_still_edit_tags_and_notes_on_others_posts(
    context_factory, post_factory, user_factory, tag_category_factory
):
    post = post_factory()
    post.user = user_factory()
    post.description = "Uploader's description"
    db.session.add(post)
    actor = user_factory()
    db.session.add(actor)
    db.session.add(tag_category_factory(default=True))
    db.session.flush()
    with patch("szurubooru.func.snapshots.modify"):
        result = api.post_api.update_post(
            context_factory(
                params={
                    "version": 1,
                    "tags": [],
                    "notes": [],
                    "fields": ["description"],
                },
                user=actor,
            ),
            {"post_id": post.post_id},
        )
    assert result == {"description": "Uploader's description"}
