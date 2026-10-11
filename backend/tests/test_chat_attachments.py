import base64
import io
import struct
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.routers import chat as chat_mod
from app.routers.chat import (
    Attachment,
    ChatMessage,
    ChatRequest,
    _build_payload,
    _prepare_messages,
)
from app.services.file_store import FileStore, set_store


def png(width: int = 64, height: int = 64) -> bytes:
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


def store_with(files: dict[str, bytes]) -> dict[str, str]:
    """Store *files* and return {filename: file_id}."""
    tmp = tempfile.mkdtemp()
    store = FileStore(tmp)
    ids = {}
    for name, data in files.items():
        ids[name] = store.save(data, name).id
    set_store(store)
    return ids


class ValidationModelTests(unittest.TestCase):
    def test_attachment_only_turn_is_valid(self):
        msg = ChatMessage(role="user", content="", attachments=[Attachment(file_id="x")])
        self.assertEqual(msg.attachments[0].file_id, "x")

    def test_totally_empty_turn_is_rejected(self):
        with self.assertRaises(ValueError):
            ChatMessage(role="user", content="   ")

    def test_text_turn_still_valid(self):
        self.assertEqual(ChatMessage(role="user", content="hi").content, "hi")

    def test_unknown_api_rejected(self):
        with self.assertRaises(ValueError):
            ChatRequest(messages=[ChatMessage(role="user", content="hi")], api="bogus")


class PrepareMessagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ids = store_with(
            {
                "pic.png": png(),
                "notes.txt": b"file body",
                "big.png": png(320, 256),
            }
        )

    def test_text_only_turns_unchanged(self):
        body = ChatRequest(
            messages=[ChatMessage(role="user", content="hello")], api="chat"
        )
        out = _prepare_messages(body, "chat", True)
        self.assertEqual(out, [{"role": "user", "content": "hello"}])

    def test_image_only_turn_becomes_content_parts(self):
        img = Attachment(type="image", file_id=self.ids["pic.png"], name="pic.png")
        body = ChatRequest(
            messages=[ChatMessage(role="user", content="", attachments=[img])], api="chat"
        )
        out = _prepare_messages(body, "chat", True)
        parts = out[0]["content"]
        self.assertIsInstance(parts, list)
        self.assertEqual(parts[0]["type"], "image_url")
        self.assertTrue(parts[0]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_text_plus_image_keeps_text_first(self):
        img = Attachment(type="image", file_id=self.ids["pic.png"], name="pic.png")
        body = ChatRequest(
            messages=[ChatMessage(role="user", content="look", attachments=[img])],
            api="chat",
        )
        parts = _prepare_messages(body, "chat", True)[0]["content"]
        self.assertEqual(parts[0], {"type": "text", "text": "look"})
        self.assertEqual(parts[1]["type"], "image_url")

    def test_multiple_images(self):
        imgs = [
            Attachment(type="image", file_id=self.ids["pic.png"], name="a.png"),
            Attachment(type="image", file_id=self.ids["big.png"], name="b.png"),
        ]
        body = ChatRequest(
            messages=[ChatMessage(role="user", content="two", attachments=imgs)], api="chat"
        )
        parts = _prepare_messages(body, "chat", True)[0]["content"]
        self.assertEqual(len(parts), 3)
        self.assertEqual([p["type"] for p in parts[1:]], ["image_url", "image_url"])

    def test_messages_style_uses_anthropic_shape(self):
        img = Attachment(type="image", file_id=self.ids["pic.png"], name="pic.png")
        body = ChatRequest(
            messages=[ChatMessage(role="user", content="", attachments=[img])],
            api="messages",
        )
        parts = _prepare_messages(body, "messages", True)[0]["content"]
        self.assertEqual(parts[0]["type"], "image")
        self.assertEqual(parts[0]["source"]["type"], "base64")
        self.assertEqual(parts[0]["source"]["media_type"], "image/png")

    def test_responses_style_uses_input_image(self):
        img = Attachment(type="image", file_id=self.ids["pic.png"], name="pic.png")
        body = ChatRequest(
            messages=[ChatMessage(role="user", content="", attachments=[img])],
            api="responses",
        )
        parts = _prepare_messages(body, "responses", True)[0]["content"]
        self.assertEqual(parts[0]["type"], "input_image")
        self.assertIn("image_url", parts[0])

    def test_non_image_attachment_inlined_as_text(self):
        f = Attachment(type="file", file_id=self.ids["notes.txt"], name="notes.txt")
        body = ChatRequest(
            messages=[ChatMessage(role="user", content="read this", attachments=[f])],
            api="chat",
        )
        out = _prepare_messages(body, "chat", True)
        self.assertEqual(out[0]["content"], "read this\n\n[Attached file: notes.txt]\nfile body")

    def test_missing_file_id_raises_400(self):
        img = Attachment(type="image", file_id="nope", name="ghost.png")
        body = ChatRequest(
            messages=[ChatMessage(role="user", content="", attachments=[img])], api="chat"
        )
        with self.assertRaises(HTTPException) as ctx:
            _prepare_messages(body, "chat", True)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_vision_disabled_rejects_images(self):
        img = Attachment(type="image", file_id=self.ids["pic.png"], name="pic.png")
        body = ChatRequest(
            messages=[ChatMessage(role="user", content="", attachments=[img])], api="chat"
        )
        with self.assertRaises(HTTPException) as ctx:
            _prepare_messages(body, "chat", False)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("vision", ctx.exception.detail)

    def test_completions_style_rejects_images(self):
        img = Attachment(type="image", file_id=self.ids["pic.png"], name="pic.png")
        body = ChatRequest(
            messages=[ChatMessage(role="user", content="", attachments=[img])],
            api="completions",
        )
        with self.assertRaises(HTTPException) as ctx:
            _prepare_messages(body, "completions", True)
        self.assertEqual(ctx.exception.status_code, 422)


class BuildPayloadTests(unittest.TestCase):
    def test_chat_style(self):
        body = ChatRequest(
            messages=[ChatMessage(role="user", content="hi")], api="chat", stream=True
        )
        payload, headers = _build_payload("chat", body, "m1", [{"role": "user", "content": "hi"}])
        self.assertEqual(payload["model"], "m1")
        self.assertEqual(payload["messages"], [{"role": "user", "content": "hi"}])
        self.assertTrue(payload["stream"])
        self.assertEqual(headers, {})

    def test_chat_style_thinking_off(self):
        body = ChatRequest(
            messages=[ChatMessage(role="user", content="hi")], api="chat", thinking=False
        )
        payload, _ = _build_payload("chat", body, "m1", [{"role": "user", "content": "hi"}])
        self.assertEqual(payload["reasoning_effort"], "none")

    def test_messages_style_splits_system(self):
        msgs = [
            {"role": "system", "content": "be nice"},
            {"role": "user", "content": "hi"},
        ]
        body = ChatRequest(
            messages=[
                ChatMessage(role="system", content="be nice"),
                ChatMessage(role="user", content="hi"),
            ],
            api="messages",
        )
        payload, headers = _build_payload("messages", body, "m1", msgs)
        self.assertEqual(payload["system"], "be nice")
        self.assertEqual(len(payload["messages"]), 1)
        self.assertEqual(headers["anthropic-version"], chat_mod.ANTHROPIC_VERSION)

    def test_responses_style_uses_last_user_turn(self):
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": [{"type": "text", "text": "second"}]},
        ]
        body = ChatRequest(
            messages=[ChatMessage(role="user", content="x")], api="responses"
        )
        payload, _ = _build_payload("responses", body, "m1", msgs)
        self.assertEqual(payload["input"], [{"type": "text", "text": "second"}])

    def test_completions_style_uses_prompt(self):
        msgs = [{"role": "user", "content": "prompt text"}]
        body = ChatRequest(
            messages=[ChatMessage(role="user", content="x")], api="completions"
        )
        payload, _ = _build_payload("completions", body, "m1", msgs)
        self.assertEqual(payload["prompt"], "prompt text")

    def test_max_tokens_and_temperature_pass_through(self):
        body = ChatRequest(
            messages=[ChatMessage(role="user", content="x")],
            api="chat",
            max_tokens=512,
            temperature=0.3,
        )
        payload, _ = _build_payload("chat", body, "m", [{"role": "user", "content": "x"}])
        self.assertEqual(payload["max_tokens"], 512)
        self.assertEqual(payload["temperature"], 0.3)


class ValidateImagesTests(unittest.TestCase):
    def setUp(self):
        self.ids = store_with({"ok.png": png(64, 64), "bad.png": png(100, 100)})

    def tearDown(self):
        set_store(None)

    def _req(self, names):
        atts = [
            Attachment(type="image", file_id=self.ids[n], name=n) for n in names
        ]
        return ChatRequest(
            messages=[ChatMessage(role="user", content="x", attachments=atts)]
        )

    def test_valid_passes(self):
        chat_mod._validate_request_images(self._req(["ok.png"]), True, 3_686_400, 32)

    def test_bad_multiple_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            chat_mod._validate_request_images(self._req(["bad.png"]), True, 3_686_400, 32)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("bad.png", ctx.exception.detail)

    def test_too_many_images_rejected(self):
        with patch("app.routers.chat.settings") as s:
            s.max_images_per_message = 1
            s.max_image_size_mb = 10
            s.allowed_image_types = ["image/png"]
            with self.assertRaises(HTTPException) as ctx:
                chat_mod._validate_request_images(
                    self._req(["ok.png", "ok.png"]), True, 3_686_400, 32
                )
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("at most 1", ctx.exception.detail)

    def test_vision_disabled_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            chat_mod._validate_request_images(self._req(["ok.png"]), False, None, None)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_completions_style_rejects_any_image(self):
        body = ChatRequest(
            messages=[
                ChatMessage(
                    role="user",
                    content="x",
                    attachments=[Attachment(type="image", file_id=self.ids["ok.png"])],
                )
            ],
            api="completions",
        )
        with self.assertRaises(HTTPException) as ctx:
            chat_mod._validate_request_images(body, True, None, None)
        self.assertEqual(ctx.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
