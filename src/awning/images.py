import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

register_heif_opener()

MAX_RAW_BYTES = 15 * 1024 * 1024
MAX_SIDE = 1568
MIN_SIDE = 200
MAX_PIXELS = 40_000_000
JPEG_QUALITY = 85


class ImageRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PreparedImage:
    data: bytes
    media_type: str
    width: int
    height: int


def prepare_image(raw: bytes) -> PreparedImage:
    if len(raw) > MAX_RAW_BYTES:
        raise ImageRejected("too_large")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            opened = Image.open(io.BytesIO(raw))
            if opened.width * opened.height > MAX_PIXELS:
                raise ImageRejected("too_large")
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except (
        UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError,
        Image.DecompressionBombError, Image.DecompressionBombWarning,
    ):
        raise ImageRejected("not_an_image") from None
    if min(image.size) < MIN_SIDE:
        raise ImageRejected("too_small")
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=JPEG_QUALITY)
    return PreparedImage(buffer.getvalue(), "image/jpeg", image.width, image.height)
