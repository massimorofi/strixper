"""Image inspection helpers for chat attachments.

Pure stdlib: the deployment image has no Pillow, and the only things we
need from an image are its real type and its pixel dimensions. Both can be
had by reading the header, which is also cheaper than decoding.

Why sniff instead of trusting ``Content-Type``: a browser sends whatever
the OS/extension claims. A file named ``.png`` that is really HTML would
be forwarded to a vision model as an image. Magic bytes are the truth;
the client header is a hint we verify against them.
"""

from __future__ import annotations

import base64
import struct
from typing import Optional

# Magic-byte signatures, longest prefix first so PNG/GIF are not shadowed.
_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # refined below
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"BM", "image/bmp"),
]

IMAGE_MIME_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")

# A JPEG needs more than SOI: the fourth byte must not be a stray 0x00 and
# the file must be non-trivial. 12 bytes is the smallest header we accept.
_MIN_IMAGE_BYTES = 12


class ImageValidationError(ValueError):
    """Raised when an image cannot be used with the loaded engine."""


def sniff_mime(data: bytes) -> Optional[str]:
    """Return the real MIME type of *data*, or ``None`` if unrecognised."""
    if not data or len(data) < _MIN_IMAGE_BYTES:
        return None

    for magic, mime in _SIGNATURES:
        if data.startswith(magic):
            if mime == "image/webp":
                # RIFF....WEBP -- the form type lives at offset 8.
                if len(data) >= 12 and data[8:12] == b"WEBP":
                    return "image/webp"
                return None
            if mime == "image/bmp":
                # "BM" is only 2 bytes and shows up in text by accident.
                return "image/bmp" if len(data) >= 54 else None
            return mime

    # Some producers emit JPEG without the trailing 0xff in the SOI run.
    if data.startswith(b"\xff\xd8") and len(data) > 3 and data[2] != 0x00:
        return "image/jpeg"
    return None


def is_image(data: bytes) -> bool:
    return sniff_mime(data) is not None


def read_image_size(data: bytes, mime: Optional[str] = None) -> tuple[int, int]:
    """Return ``(width, height)`` parsed from the image header.

    Raises ``ImageValidationError`` if the header is malformed.
    """
    mime = mime or sniff_mime(data)
    try:
        if mime == "image/png":
            # IHDR is the first chunk: 8-byte sig, 4 len, "IHDR", then
            # width(4) height(4) at offset 16.
            if len(data) < 24 or data[12:16] != b"IHDR":
                raise ValueError("missing PNG IHDR chunk")
            width, height = struct.unpack(">II", data[16:24])
        elif mime == "image/gif":
            width, height = struct.unpack("<HH", data[6:10])
        elif mime == "image/jpeg":
            width, height = _jpeg_size(data)
        elif mime == "image/webp":
            width, height = _webp_size(data)
        elif mime == "image/bmp":
            width, height = struct.unpack("<ii", data[18:26])
        else:
            raise ValueError(f"unsupported image type: {mime!r}")
    except ImageValidationError:
        raise
    except (struct.error, IndexError, ValueError) as exc:
        raise ImageValidationError(f"could not read image dimensions: {exc}") from exc

    if width <= 0 or height <= 0:
        raise ImageValidationError("image has zero dimensions")
    return int(width), int(height)


def _jpeg_size(data: bytes) -> tuple[int, int]:
    """Walk JPEG markers until an SOFn carrying the frame size."""
    pos = 2  # skip SOI
    limit = len(data)
    # A JPEG is a chain of markers; bail out rather than spin if it is junk.
    for _ in range(1000):
        if pos + 4 > limit:
            break
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        # Standalone markers (RST, SOI, EOI, TEM) carry no length field.
        if marker in (0xD8, 0xD9) or (0xD0 <= marker <= 0xD7) or marker == 0x01:
            pos += 2
            continue
        if pos + 8 > limit:
            break
        length = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
        # SOF0..SOF15 except DHT(C4)/JPGA(C8)/DAC(CC).
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[pos + 5 : pos + 9])
            return width, height
        pos += 2 + length
    raise ImageValidationError("no JPEG start-of-frame marker found")


def _webp_size(data: bytes) -> tuple[int, int]:
    """Parse WebP container chunks (VP8 lossy / VP8L lossless / VP8X extended)."""
    pos = 12  # past "RIFF" + size + "WEBP"
    limit = len(data)
    while pos + 8 <= limit:
        fourcc = data[pos : pos + 4]
        chunk_len = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        if fourcc == b"VP8X":
            if pos + 20 > limit:
                break
            # canvas width/height are 24-bit minus-one values at offsets 4..10
            w_raw, h_raw = struct.unpack("<II", data[pos + 12 : pos + 20])[:2]
            return (w_raw & 0xFFFFFF) + 1, (h_raw & 0xFFFFFF) + 1
        if fourcc == b"VP8 ":
            if pos + 18 > limit:
                break
            w, h = struct.unpack("<HH", data[pos + 14 : pos + 18])
            return w & 0x3FFF, h & 0x3FFF
        if fourcc == b"VP8L":
            if pos + 13 > limit:
                break
            bits = struct.unpack("<I", data[pos + 8 : pos + 12])[0]
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        pos += 8 + chunk_len + (chunk_len & 1)
    raise ImageValidationError("no size-carrying WebP chunk found")


def validate_image(
    data: bytes,
    allowed_types: Optional[list[str]] = None,
    max_pixels: Optional[int] = None,
    size_multiple: Optional[int] = None,
    max_bytes: Optional[int] = None,
) -> tuple[str, int, int]:
    """Validate bytes against engine constraints.

    Returns ``(mime_type, width, height)``. Raises ``ImageValidationError``
    with a user-facing message for the first constraint that fails.
    """
    if not data:
        raise ImageValidationError("the uploaded file is empty")

    if max_bytes is not None and len(data) > max_bytes:
        raise ImageValidationError(
            f"image is {len(data) / 1_048_576:.1f} MB, the limit is "
            f"{max_bytes / 1_048_576:.1f} MB"
        )

    mime = sniff_mime(data)
    if mime is None:
        raise ImageValidationError(
            "the file is not a recognised image (checked PNG, JPEG, WebP, GIF)"
        )

    if allowed_types and mime not in allowed_types:
        raise ImageValidationError(
            f"{mime} images are not accepted; allowed: {', '.join(allowed_types)}"
        )

    width, height = read_image_size(data, mime)

    if max_pixels is not None and width * height > max_pixels:
        raise ImageValidationError(
            f"image is {width}x{height} ({width * height:,} pixels), the engine "
            f"accepts at most {max_pixels:,} pixels"
        )

    if size_multiple and size_multiple > 1:
        if width % size_multiple or height % size_multiple:
            raise ImageValidationError(
                f"image is {width}x{height}; the engine requires both dimensions "
                f"to be multiples of {size_multiple}"
            )

    return mime, width, height


def to_data_url(data: bytes, mime: str) -> str:
    """Build the ``data:`` URL form the engine expects."""
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def openai_image_part(data: bytes, mime: str) -> dict:
    """OpenAI chat-completions image content part."""
    return {"type": "image_url", "image_url": {"url": to_data_url(data, mime)}}


def anthropic_image_part(data: bytes, mime: str) -> dict:
    """Anthropic ``/v1/messages`` image content part."""
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": mime,
            "data": base64.b64encode(data).decode("ascii"),
        },
    }


def responses_image_part(data: bytes, mime: str) -> dict:
    """OpenAI ``/v1/responses`` input image part."""
    return {"type": "input_image", "image_url": to_data_url(data, mime)}


def vision_enabled(health: Optional[dict]) -> bool:
    """Read ``vision.enabled`` out of an engine /health payload."""
    if not isinstance(health, dict):
        return False
    vision = health.get("vision")
    if isinstance(vision, dict):
        return bool(vision.get("enabled"))
    return False


def vision_constraints(health: Optional[dict]) -> tuple[Optional[int], Optional[int]]:
    """Return ``(max_pixels, size_multiple)`` advertised by the engine."""
    if not isinstance(health, dict):
        return None, None
    vision = health.get("vision") if isinstance(health.get("vision"), dict) else {}
    max_pixels = vision.get("max_pixels")
    size_multiple = vision.get("size_multiple")
    return (
        int(max_pixels) if isinstance(max_pixels, (int, float)) else None,
        int(size_multiple) if isinstance(size_multiple, (int, float)) else None,
    )
