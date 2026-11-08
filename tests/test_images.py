import io

import pytest
from PIL import Image

from awning.images import MAX_PIXELS, ImageRejected, prepare_image


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, "PNG")
    return buffer.getvalue()


def test_prepare_image_downscales() -> None:
    prepared = prepare_image(_png(4000, 3000))
    assert (prepared.width, prepared.height) == (1568, 1176)
    assert prepared.media_type == "image/jpeg"
    assert Image.open(io.BytesIO(prepared.data)).format == "JPEG"


def test_prepare_image_does_not_upscale() -> None:
    prepared = prepare_image(_png(800, 600))
    assert (prepared.width, prepared.height) == (800, 600)


def test_prepare_image_respects_exif_orientation() -> None:
    image = Image.new("RGB", (600, 300), "white")
    exif = Image.Exif()
    exif[0x0112] = 6
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif)
    prepared = prepare_image(buffer.getvalue())
    assert (prepared.width, prepared.height) == (300, 600)


def test_prepare_image_rejects_pdf() -> None:
    with pytest.raises(ImageRejected) as excinfo:
        prepare_image(b"%PDF-1.7\n" + b"0" * 1000)
    assert excinfo.value.reason == "not_an_image"


def test_prepare_image_rejects_tiny() -> None:
    with pytest.raises(ImageRejected) as excinfo:
        prepare_image(_png(1000, 150))
    assert excinfo.value.reason == "too_small"


def test_prepare_image_rejects_too_large() -> None:
    with pytest.raises(ImageRejected) as excinfo:
        prepare_image(b"0" * (15 * 1024 * 1024 + 1))
    assert excinfo.value.reason == "too_large"


def test_prepare_image_rejects_huge_declared_dimensions() -> None:
    with pytest.raises(ImageRejected) as excinfo:
        prepare_image(_png(8000, 6000))
    assert excinfo.value.reason == "too_large"


def test_prepare_image_accepts_heic() -> None:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), "red").save(buffer, "HEIF")
    prepared = prepare_image(buffer.getvalue())
    assert (prepared.width, prepared.height) == (400, 300)
    assert prepared.media_type == "image/jpeg"


def test_prepare_image_rejects_corrupt_heic() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), "red").save(buffer, "HEIF")
    data = buffer.getvalue()
    with pytest.raises(ImageRejected) as excinfo:
        prepare_image(data[:40] + b"\x00" * 100)
    assert excinfo.value.reason == "not_an_image"


def test_prepare_image_rejects_over_25_megapixels() -> None:
    assert MAX_PIXELS == 25_000_000
    buffer = io.BytesIO()
    Image.new("1", (5100, 5000)).save(buffer, "PNG")
    with pytest.raises(ImageRejected) as excinfo:
        prepare_image(buffer.getvalue())
    assert excinfo.value.reason == "too_large"
