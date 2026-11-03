from typing import Dict

from szurubooru import errors, model, rest
from szurubooru.func import auth, post_imports


@rest.routes.post("/import-preview/?")
def preview_import(
    ctx: rest.Context, _params: Dict[str, str] = {}
) -> rest.Response:
    if ctx.user.rank == model.User.RANK_ANONYMOUS:
        raise errors.AuthError("Log in to import a post.")
    auth.verify_privilege(ctx.user, "uploads:create")
    return post_imports.preview(ctx.get_param_as_string("url"))


@rest.routes.post("/import-thumbnail/?")
def preview_thumbnail(
    ctx: rest.Context, _params: Dict[str, str] = {}
) -> rest.Response:
    if ctx.user.rank == model.User.RANK_ANONYMOUS:
        raise errors.AuthError("Log in to preview an imported post.")
    auth.verify_privilege(ctx.user, "uploads:create")
    return {
        "previewUrl": post_imports.thumbnail(ctx.get_param_as_string("url"))
    }
