from unittest.mock import patch

import pytest

from szurubooru import api, db, model


@pytest.mark.parametrize(
    "input_value,canonical,label",
    [
        ("Arf", "safe", "Arf"),
        ("mErR?", "sketchy", "Merr?"),
        ("HUFF!", "unsafe", "Huff!"),
        ("safe", "safe", "Arf"),
        ("sketchy", "sketchy", "Merr?"),
        ("unsafe", "unsafe", "Huff!"),
    ],
)
def test_safety_alias_update_preserves_canonical_api_value(
    context_factory,
    config_injector,
    post_factory,
    user_factory,
    input_value,
    canonical,
    label,
):
    config_injector({"privileges": {"posts:edit:safety": "moderator"}})
    post = post_factory()
    db.session.add(post)
    db.session.flush()
    with patch("szurubooru.func.snapshots.modify"):
        result = api.post_api.update_post(
            context_factory(
                params={
                    "version": post.version,
                    "safety": input_value,
                    "fields": ["safety", "safetyLabel"],
                },
                user=user_factory(rank=model.User.RANK_MODERATOR),
            ),
            {"post_id": post.post_id},
        )
    assert result == {"safety": canonical, "safetyLabel": label}
    assert post.safety == canonical
