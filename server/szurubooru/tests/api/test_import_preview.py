from unittest.mock import patch

import pytest

from szurubooru import api, db, errors, model


@pytest.fixture(autouse=True)
def inject_config(config_injector):
    config_injector({"privileges": {"uploads:create": "regular"}})


def test_preview_requires_authenticated_uploader(
    context_factory, user_factory
):
    with patch("szurubooru.func.post_imports.preview") as preview:
        result = {"contentUrl": "https://static1.e621.net/data/original.png"}
        preview.return_value = result
        assert (
            api.import_api.preview_import(
                context_factory(
                    params={"url": "https://e621.net/posts/123"},
                    user=user_factory(rank=model.User.RANK_REGULAR),
                )
            )
            == result
        )
        preview.assert_called_once_with("https://e621.net/posts/123")
    assert db.session.query(model.Post).count() == 0
    assert db.session.query(model.Tag).count() == 0


@pytest.mark.parametrize(
    "rank", [model.User.RANK_ANONYMOUS, model.User.RANK_RESTRICTED]
)
def test_insufficient_rank_cannot_trigger_remote_requests(
    context_factory, user_factory, rank
):
    with patch("szurubooru.func.post_imports.preview") as preview:
        with pytest.raises(errors.AuthError):
            api.import_api.preview_import(
                context_factory(
                    params={"url": "https://e621.net/posts/123"},
                    user=user_factory(rank=rank),
                )
            )
        preview.assert_not_called()


def test_anonymous_preview_is_denied_even_if_upload_privilege_is_public(
    context_factory, user_factory, config_injector
):
    config_injector({"privileges": {"uploads:create": "anonymous"}})
    with patch("szurubooru.func.post_imports.preview") as preview:
        with pytest.raises(errors.AuthError):
            api.import_api.preview_import(
                context_factory(
                    params={"url": "https://e621.net/posts/123"},
                    user=user_factory(rank=model.User.RANK_ANONYMOUS),
                )
            )
        preview.assert_not_called()


def test_missing_url_is_rejected_before_remote_request(
    context_factory, user_factory
):
    with patch("szurubooru.func.post_imports.preview") as preview:
        with pytest.raises(errors.MissingRequiredParameterError):
            api.import_api.preview_import(context_factory(user=user_factory()))
        preview.assert_not_called()
