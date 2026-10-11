import base64
import struct
import unittest

from app.services import vision_helper
from app.services.vision_helper import ImageValidationError


def png(width: int, height: int) -> bytes:
    """Minimal but structurally valid PNG header for the given size."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", len(ihdr))
        + b"IHDR"
        + ihdr
        + b"\x00\x00\x00\x00"
        + b"IEND"
        + b"\x00\x00\x00\x00"
    )


def gif(width: int, height: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00" * 64


def webp_lossy(width: int, height: int) -> bytes:
    """VP8 (lossy) chunk: 3-byte frame tag, start code, then 14-bit W/H."""
    payload = (
        b"\x00\x00\x00"
        + b"\x9d\x01\x2a"
        + struct.pack("<HH", width, height)
        + b"\x00" * 32
    )
    return (
        b"RIFF"
        + struct.pack("<I", 4 + 8 + len(payload))
        + b"WEBP"
        + b"VP8 "
        + struct.pack("<I", len(payload))
        + payload
    )


def jpeg(width: int, height: int) -> bytes:
    """SOI + JFIF APP0 + SOF0 carrying the frame size, plus component specs."""
    components = b"".join(struct.pack(">BHH", i + 1, 1, 1) + bytes([64 + i]) for i in range(3))
    sof = struct.pack(">HBHHB", 8 + 3 * 3, 8, height, width, 3) + components
    return (
        b"\xff\xd8"
        + b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        + b"\xff\xc0"
        + sof
        + b"\xff\xd9"
    )


class SniffTests(unittest.TestCase):
    def test_recognises_each_supported_format(self):
        self.assertEqual(vision_helper.sniff_mime(png(64, 64)), "image/png")
        self.assertEqual(vision_helper.sniff_mime(gif(64, 64)), "image/gif")
        self.assertEqual(vision_helper.sniff_mime(jpeg(64, 64)), "image/jpeg")
        self.assertEqual(vision_helper.sniff_mime(webp_lossy(64, 64)), "image/webp")

    def test_rejects_non_images(self):
        self.assertIsNone(vision_helper.sniff_mime(b"just some plain text"))
        self.assertIsNone(vision_helper.sniff_mime(b""))
        self.assertIsNone(vision_helper.sniff_mime(b"\x89PNG"))

    def test_riff_that_is_not_webp_is_rejected(self):
        self.assertIsNone(
            vision_helper.sniff_mime(b"RIFF" + b"\x00" * 4 + b"AVI " + b"x" * 32)
        )


class SizeParsingTests(unittest.TestCase):
    def test_png(self):
        self.assertEqual(vision_helper.read_image_size(png(320, 256)), (320, 256))

    def test_gif(self):
        self.assertEqual(vision_helper.read_image_size(gif(128, 256)), (128, 256))

    def test_jpeg(self):
        self.assertEqual(vision_helper.read_image_size(jpeg(640, 480)), (640, 480))

    def test_webp(self):
        self.assertEqual(vision_helper.read_image_size(webp_lossy(256, 128)), (256, 128))

    def test_truncated_png_raises(self):
        with self.assertRaises(ImageValidationError):
            vision_helper.read_image_size(png(64, 64)[:16])

    def test_png_without_ihdr_raises(self):
        bogus = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        with self.assertRaises(ImageValidationError):
            vision_helper.read_image_size(bogus)


class ValidationTests(unittest.TestCase):
    def test_accepts_a_compliant_image(self):
        mime, w, h = vision_helper.validate_image(
            png(320, 256),
            allowed_types=["image/png"],
            max_pixels=3_686_400,
            size_multiple=32,
            max_bytes=10 * 1024 * 1024,
        )
        self.assertEqual((mime, w, h), ("image/png", 320, 256))

    def test_rejects_over_pixel_budget(self):
        with self.assertRaises(ImageValidationError) as ctx:
            vision_helper.validate_image(png(3008, 3008), max_pixels=3_686_400)
        self.assertIn("pixels", str(ctx.exception))

    def test_rejects_non_multiple_dimensions(self):
        with self.assertRaises(ImageValidationError) as ctx:
            vision_helper.validate_image(png(100, 100), size_multiple=32)
        self.assertIn("multiples of 32", str(ctx.exception))

    def test_rejects_oversized_bytes(self):
        with self.assertRaises(ImageValidationError) as ctx:
            vision_helper.validate_image(png(64, 64), max_bytes=16)
        self.assertIn("limit", str(ctx.exception))

    def test_rejects_disallowed_type(self):
        with self.assertRaises(ImageValidationError) as ctx:
            vision_helper.validate_image(gif(64, 64), allowed_types=["image/png"])
        self.assertIn("image/gif", str(ctx.exception))

    def test_rejects_empty(self):
        with self.assertRaises(ImageValidationError):
            vision_helper.validate_image(b"")


class PartBuilderTests(unittest.TestCase):
    def test_data_url_roundtrips(self):
        data = png(64, 64)
        url = vision_helper.to_data_url(data, "image/png")
        self.assertTrue(url.startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(url.split(",", 1)[1]), data)

    def test_openai_part(self):
        part = vision_helper.openai_image_part(b"x" * 32, "image/png")
        self.assertEqual(part["type"], "image_url")
        self.assertIn("url", part["image_url"])

    def test_anthropic_part(self):
        part = vision_helper.anthropic_image_part(b"x" * 32, "image/jpeg")
        self.assertEqual(part["type"], "image")
        self.assertEqual(part["source"]["type"], "base64")
        self.assertEqual(part["source"]["media_type"], "image/jpeg")

    def test_responses_part(self):
        part = vision_helper.responses_image_part(b"x" * 32, "image/png")
        self.assertEqual(part["type"], "input_image")
        self.assertIn("image_url", part)


class HealthParsingTests(unittest.TestCase):
    def test_reads_advertised_vision_block(self):
        health = {"vision": {"enabled": True, "max_pixels": 3686400, "size_multiple": 32}}
        self.assertTrue(vision_helper.vision_enabled(health))
        self.assertEqual(vision_helper.vision_constraints(health), (3686400, 32))

    def test_disabled_vision(self):
        health = {"vision": {"enabled": False}}
        self.assertFalse(vision_helper.vision_enabled(health))

    def test_missing_block_is_tolerated(self):
        self.assertFalse(vision_helper.vision_enabled({}))
        self.assertEqual(vision_helper.vision_constraints(None), (None, None))


if __name__ == "__main__":
    unittest.main()
