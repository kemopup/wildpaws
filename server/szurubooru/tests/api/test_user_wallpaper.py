import pytest
import sqlalchemy as sa

from szurubooru import api, config, db, errors, model
from szurubooru.func import posts, users


@pytest.fixture(autouse=True)
def default_permissions(config_injector):
    settings = config._file_config("config.yaml.dist")
    settings.update({"secret": "wallpaper-test", "data_url": "/data"})
    config_injector(settings)


def update_wallpaper(context_factory, user, post_id, actor=None):
    return api.user_api.update_user(
        context_factory(
            params={
                "version": user.version,
                "wallpaperPostId": post_id,
                "fields": ["wallpaper", "version"],
            },
            user=actor or user,
        ),
        {"user_name": user.name},
    )


def test_set_and_clear_own_wallpaper(
    context_factory, user_factory, post_factory
):
    user = user_factory()
    post = post_factory()
    post.user = user
    db.session.add_all([user, post])
    db.session.flush()

    result = update_wallpaper(context_factory, user, post.post_id)
    assert result == {
        "version": 2,
        "wallpaper": {
            "postId": post.post_id,
            "contentUrl": posts.get_post_content_url(post),
            "safety": "safe",
            "safetyLabel": "Arf",
        },
    }
    assert user.wallpaper_post_id == post.post_id
    result = update_wallpaper(context_factory, user, None)
    assert result == {"version": 3, "wallpaper": None}
    assert user.wallpaper_post_id is None


@pytest.mark.parametrize(
    "post_type",
    [model.Post.TYPE_VIDEO, model.Post.TYPE_FLASH, model.Post.TYPE_ANIMATION],
)
def test_reject_nonstatic_posts(
    context_factory, user_factory, post_factory, post_type
):
    user = user_factory()
    original = post_factory()
    invalid = post_factory(type=post_type)
    user.wallpaper_post = original
    db.session.add_all([user, original, invalid])
    db.session.flush()
    with pytest.raises(users.InvalidWallpaperError):
        update_wallpaper(context_factory, user, invalid.post_id)
    assert user.version == 1
    assert user.wallpaper_post is original


@pytest.mark.parametrize("post_id", [0, -1, "not-an-id"])
def test_reject_invalid_ids(context_factory, user_factory, post_id):
    user = user_factory()
    db.session.add(user)
    db.session.flush()
    with pytest.raises(errors.InvalidParameterError):
        update_wallpaper(context_factory, user, post_id)
    assert user.version == 1
    assert user.wallpaper_post is None


def test_reject_missing_post(context_factory, user_factory):
    user = user_factory()
    db.session.add(user)
    db.session.flush()
    with pytest.raises(posts.PostNotFoundError):
        update_wallpaper(context_factory, user, 999999)
    assert user.version == 1


@pytest.mark.parametrize(
    "rank", [model.User.RANK_REGULAR, model.User.RANK_ADMINISTRATOR]
)
def test_cannot_change_other_users_wallpaper(
    context_factory, user_factory, post_factory, rank
):
    user = user_factory()
    actor = user_factory(rank=rank)
    post = post_factory()
    db.session.add_all([user, actor, post])
    db.session.flush()
    with pytest.raises(errors.AuthError):
        update_wallpaper(context_factory, user, post.post_id, actor=actor)
    assert user.version == 1
    assert user.wallpaper_post is None


def test_wallpaper_requires_own_edit_privilege(
    context_factory, user_factory, post_factory
):
    user = user_factory(rank=model.User.RANK_RESTRICTED)
    post = post_factory()
    db.session.add_all([user, post])
    db.session.flush()
    with pytest.raises(errors.AuthError):
        update_wallpaper(context_factory, user, post.post_id)
    assert user.version == 1


def test_post_view_privilege_required_but_clearing_still_allowed(
    context_factory, user_factory, post_factory
):
    user = user_factory()
    post = post_factory()
    user.wallpaper_post = post
    db.session.add_all([user, post])
    db.session.flush()
    config.config["privileges"]["posts:view"] = "moderator"
    with pytest.raises(errors.AuthError):
        update_wallpaper(context_factory, user, post.post_id)
    assert users.serialize_user(user, user, ["wallpaper"]) == {
        "wallpaper": None,
    }
    assert update_wallpaper(context_factory, user, None)["wallpaper"] is None


def test_wallpaper_private_even_from_administrators(
    user_factory, post_factory
):
    user = user_factory()
    actor = user_factory(rank=model.User.RANK_ADMINISTRATOR)
    post = post_factory()
    user.wallpaper_post = post
    db.session.add_all([user, actor, post])
    db.session.flush()
    assert users.serialize_user(user, actor, ["wallpaper"]) == {
        "wallpaper": False,
    }


def test_deleting_selected_post_restores_default(user_factory, post_factory):
    # The test fixture creates tables individually, so deferred constraints
    # need the same separate creation step that the migration performs.
    constraint = next(
        item
        for item in model.User.__table__.foreign_key_constraints
        if item.name == "fk_user_wallpaper_post_id"
    )
    db.session.execute(sa.schema.AddConstraint(constraint))
    user = user_factory()
    post = post_factory()
    user.wallpaper_post = post
    db.session.add_all([user, post])
    db.session.flush()
    db.session.delete(post)
    db.session.flush()
    db.session.expire_all()
    assert user.wallpaper_post_id is None
    assert users.serialize_user(user, user, ["wallpaper"]) == {
        "wallpaper": None,
    }


def test_replaced_post_content_no_longer_static_uses_default(
    user_factory, post_factory
):
    user = user_factory()
    post = post_factory(type=model.Post.TYPE_VIDEO)
    user.wallpaper_post = post
    db.session.add_all([user, post])
    db.session.flush()
    assert users.serialize_user(user, user, ["wallpaper"]) == {
        "wallpaper": None,
    }
