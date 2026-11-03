from unittest.mock import patch

import pytest

from szurubooru import api, db, errors, model


POST_URL = "https://gelbooru.com/index.php?page=post&s=view&id=123"


@pytest.fixture(autouse=True)
def inject_config(config_injector):
    config_injector({"privileges": {"uploads:create": "regular"}})


def test_thumbnail_endpoint_requires_uploader_and_returns_only_preview_data(
    context_factory, user_factory
):
    image = "data:image/jpeg;base64,/9j/2Q=="
    with patch(
        "szurubooru.func.post_imports.thumbnail", return_value=image
    ) as thumbnail:
        result = api.import_api.preview_thumbnail(
            context_factory(
                params={"url": POST_URL},
                user=user_factory(rank=model.User.RANK_REGULAR),
            )
        )
    assert result == {"previewUrl": image}
    thumbnail.assert_called_once_with(POST_URL)
    assert db.session.query(model.Post).count() == 0
    assert db.session.query(model.Tag).count() == 0


@pytest.mark.parametrize(
    "rank", [model.User.RANK_ANONYMOUS, model.User.RANK_RESTRICTED]
)
def test_thumbnail_insufficient_rank_cannot_fetch_media(
    rank, context_factory, user_factory
):
    with patch("szurubooru.func.post_imports.thumbnail") as thumbnail:
        with pytest.raises(errors.AuthError):
            api.import_api.preview_thumbnail(
                context_factory(
                    params={"url": POST_URL}, user=user_factory(rank=rank)
                )
            )
    thumbnail.assert_not_called()


def test_anonymous_thumbnail_is_denied_even_if_upload_privilege_is_public(
    context_factory, user_factory, config_injector
):
    config_injector({"privileges": {"uploads:create": "anonymous"}})
    with patch("szurubooru.func.post_imports.thumbnail") as thumbnail:
        with pytest.raises(errors.AuthError):
            api.import_api.preview_thumbnail(
                context_factory(
                    params={"url": POST_URL},
                    user=user_factory(rank=model.User.RANK_ANONYMOUS),
                )
            )
    thumbnail.assert_not_called()


def test_thumbnail_missing_url_is_rejected_before_fetch(
    context_factory, user_factory
):
    with patch("szurubooru.func.post_imports.thumbnail") as thumbnail:
        with pytest.raises(errors.MissingRequiredParameterError):
            api.import_api.preview_thumbnail(
                context_factory(user=user_factory())
            )
    thumbnail.assert_not_called()
