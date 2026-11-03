import base64
from copy import deepcopy
from unittest.mock import patch

import pytest

from szurubooru import db, errors, model
from szurubooru.func import post_imports


POST_URL = "https://gelbooru.com/index.php?page=post&s=view&id=123"
ORIGINAL_URL = "https://img4.gelbooru.com/images/ab/cd/original.jpg"
JPEG = b"\xff\xd8synthetic-thumbnail\xff\xd9"


def test_thumbnail_uses_validated_original_and_returns_separate_jpeg_preview():
    metadata = {
        "contentUrl": ORIGINAL_URL,
        "previewUrl": "https://img4.gelbooru.com/samples/sample.jpg",
        "source": POST_URL,
        "description": "Keep this description",
        "tags": {"artist": ["test_artist"], "character": []},
    }
    original_metadata = deepcopy(metadata)
    original_bytes = b"original-file-bytes\x00\xff"
    with patch.object(
        post_imports, "preview", return_value=metadata
    ) as preview, patch(
        "szurubooru.func.net.download", return_value=original_bytes
    ) as download, patch(
        "szurubooru.func.post_imports.images.Image"
    ) as image_type, patch(
        "szurubooru.func.files.save"
    ) as save_file, patch(
        "szurubooru.func.file_uploads.save"
    ) as save_upload:
        image = image_type.return_value
        image.to_jpeg.return_value = JPEG
        result = post_imports.thumbnail(
            POST_URL + "&tags=artist_name&api_key=not_forwarded#fragment"
        )

    preview.assert_called_once_with(POST_URL)
    assert download.call_count == 1
    assert download.call_args[0][0] == ORIGINAL_URL
    image_type.assert_called_once_with(original_bytes)
    image.resize_fill.assert_called_once_with(192, 128)
    image.to_jpeg.assert_called_once_with()
    assert result.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(result.split(",", 1)[1]) == JPEG
    assert metadata == original_metadata
    save_file.assert_not_called()
    save_upload.assert_not_called()
    assert db.session.query(model.Post).count() == 0
    assert db.session.query(model.Tag).count() == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/internal",
        "file:///etc/passwd",
        ORIGINAL_URL,
        "http://gelbooru.com/index.php?page=post&s=view&id=123",
        "https://gelbooru.com.evil.test/index.php?page=post&s=view&id=123",
        "https://user:password@gelbooru.com/index.php?page=post&s=view&id=123",
        "https://gelbooru.com/index.php?page=post&s=view&id=1&id=2",
        "https://e621.net/posts/123",
        "https://rule34.xxx/index.php?page=post&s=view&id=123",
    ],
)
def test_thumbnail_is_not_an_arbitrary_url_or_other_provider_proxy(url):
    with patch.object(post_imports, "preview") as preview, patch(
        "szurubooru.func.net.download"
    ) as download:
        with pytest.raises((errors.ValidationError, errors.ProcessingError)):
            post_imports.thumbnail(url)
    preview.assert_not_called()
    download.assert_not_called()


@pytest.mark.parametrize(
    "jpeg",
    [b"", b"<svg>not-a-jpeg</svg>", b"\xff\xd8" + b"x" * (256 * 1024)],
)
def test_thumbnail_rejects_invalid_or_oversized_generated_preview(jpeg):
    with patch.object(
        post_imports, "preview", return_value={"contentUrl": ORIGINAL_URL}
    ), patch("szurubooru.func.net.download", return_value=b"original"), patch(
        "szurubooru.func.post_imports.images.Image"
    ) as image_type:
        image_type.return_value.to_jpeg.return_value = jpeg
        with pytest.raises(errors.ProcessingError):
            post_imports.thumbnail(POST_URL)


def test_thumbnail_conversion_failure_does_not_return_original_or_create_post():
    with patch.object(
        post_imports, "preview", return_value={"contentUrl": ORIGINAL_URL}
    ), patch("szurubooru.func.net.download", return_value=b"original"), patch(
        "szurubooru.func.post_imports.images.Image",
        side_effect=errors.ProcessingError("Cannot decode this file"),
    ):
        with pytest.raises(errors.ProcessingError):
            post_imports.thumbnail(POST_URL)
    assert db.session.query(model.Post).count() == 0


def test_thumbnail_download_failure_is_not_silently_replaced_with_remote_url():
    with patch.object(
        post_imports, "preview", return_value={"contentUrl": ORIGINAL_URL}
    ), patch(
        "szurubooru.func.net.download",
        side_effect=errors.ProcessingError("Download failed"),
    ), patch(
        "szurubooru.func.post_imports.images.Image"
    ) as image_type:
        with pytest.raises(errors.ProcessingError):
            post_imports.thumbnail(POST_URL)
    image_type.assert_not_called()


def test_inkbunny_thumbnail_uses_selected_page_poster_and_never_downloads_video():
    post = "https://inkbunny.net/s/123-p2-"
    poster = "https://tx.ib.metapix.net/thumbnails/large/1/poster.jpg"
    metadata = {
        "contentUrl": "https://tx.ib.metapix.net/files/full/1/original.mp4",
        "mimeType": "video/mp4",
        "media": [{"previewUrl": poster}],
        "description": "Keep edited description",
    }
    unchanged = deepcopy(metadata)
    with patch.object(
        post_imports, "preview", return_value=metadata
    ) as preview, patch(
        "szurubooru.func.net.download", return_value=b"poster"
    ) as download, patch(
        "szurubooru.func.post_imports.images.Image"
    ) as image_type:
        image_type.return_value.to_jpeg.return_value = JPEG
        result = post_imports.thumbnail(post + "?sid=discarded")
    preview.assert_called_once_with(post)
    download.assert_called_once_with(poster)
    assert base64.b64decode(result.split(",", 1)[1]) == JPEG
    assert metadata == unchanged


@pytest.mark.parametrize(
    "poster",
    [
        None,
        "https://evil.test/poster.jpg",
        "https://tx.ib.metapix.net/private_thumbnails/1/poster.jpg?sid=secret",
        "https://tx.ib.metapix.net/files/full/1/original.mp4",
    ],
)
def test_inkbunny_thumbnail_does_not_proxy_private_or_arbitrary_media(poster):
    with patch.object(
        post_imports,
        "preview",
        return_value={"media": [{"previewUrl": poster}]},
    ), patch("szurubooru.func.net.download") as download:
        with pytest.raises(post_imports.ImportError):
            post_imports.thumbnail("https://inkbunny.net/s/123")
    download.assert_not_called()
