import asyncio
import json
import os
import struct
import tempfile
import unittest

from fastapi import HTTPException

from app.routers.agent import (
    Attachment,
    AgentChatMessage,
    AgentChatRequest,
    _agent_input,
    _attached_files_note,
    _uploaded_files_note,
    _validate_agent_images,
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
    store = FileStore(tempfile.mkdtemp())
    ids = {}
    for name, data in files.items():
        rec = store.save(data, name)
        ids[name] = rec.id
    set_store(store)
    return ids


class MessageModelTests(unittest.TestCase):
    def test_attachment_only_turn_allowed(self):
        m = AgentChatMessage(role="user", content="", attachments=[Attachment(file_id="x")])
        self.assertEqual(len(m.attachments), 1)

    def test_blank_turn_rejected(self):
        with self.assertRaises(ValueError):
            AgentChatMessage(role="user", content="   ")

    def test_text_turn_allowed(self):
        self.assertEqual(AgentChatMessage(role="user", content="hi").content, "hi")


class AgentInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ids = store_with(
            {"pic.png": png(), "doc.txt": b"document body", "wide.png": png(320, 256)}
        )

    def test_text_only_is_plain_string(self):
        body = AgentChatRequest(
            messages=[AgentChatMessage(role="user", content="hello")]
        )
        self.assertEqual(_agent_input(body), [{"role": "user", "content": "hello"}])

    def test_image_becomes_input_image(self):
        img = Attachment(
            type="image",
            file_id=self.ids["pic.png"],
            name="pic.png",
            workspace_path="uploads/pic.png",
        )
        body = AgentChatRequest(
            messages=[AgentChatMessage(role="user", content="see this", attachments=[img])]
        )
        parts = _agent_input(body)[0]["content"]
        self.assertIsInstance(parts, list)
        self.assertEqual(parts[0], {"type": "input_text", "text": "see this"})
        self.assertEqual(parts[1]["type"], "input_image")
        self.assertTrue(parts[1]["image_url"].startswith("data:image/png;base64,"))

    def test_declared_mime_ignored_when_magic_disagrees(self):
        img = Attachment(
            type="image",
            file_id=self.ids["pic.png"],
            name="tricky.txt",
            mime_type="text/csv",
            workspace_path="uploads/tricky.txt",
        )
        body = AgentChatRequest(
            messages=[AgentChatMessage(role="user", content="", attachments=[img])]
        )
        parts = _agent_input(body)[0]["content"]
        self.assertTrue(parts[0]["image_url"].startswith("data:image/png;base64,"))

    def test_files_produce_a_note_not_inline_bytes(self):
        f = Attachment(
            type="file",
            file_id=self.ids["doc.txt"],
            name="doc.txt",
            size=13,
            mime_type="text/plain",
            workspace_path="uploads/doc.txt",
        )
        body = AgentChatRequest(
            messages=[AgentChatMessage(role="user", content="read", attachments=[f])]
        )
        content = _agent_input(body)[0]["content"]
        self.assertIsInstance(content, str)
        self.assertIn("Attached files", content)
        self.assertIn("uploads/doc.txt", content)

    def test_mixed_images_and_files(self):
        img = Attachment(
            type="image", file_id=self.ids["pic.png"], name="pic.png",
            workspace_path="uploads/pic.png",
        )
        f = Attachment(
            type="file", file_id=self.ids["doc.txt"], name="doc.txt",
            mime_type="text/plain", workspace_path="uploads/doc.txt",
        )
        body = AgentChatRequest(
            messages=[AgentChatMessage(role="user", content="both", attachments=[img, f])]
        )
        parts = _agent_input(body)[0]["content"]
        self.assertEqual(parts[0]["type"], "input_text")
        self.assertIn("doc.txt", parts[0]["text"])
        self.assertEqual(parts[1]["type"], "input_image")

    def test_unknown_file_id_raises_400(self):
        img = Attachment(type="image", file_id="ghost", name="g.png")
        body = AgentChatRequest(
            messages=[AgentChatMessage(role="user", content="", attachments=[img])]
        )
        with self.assertRaises(HTTPException) as ctx:
            _agent_input(body)
        self.assertEqual(ctx.exception.status_code, 400)


class NoteTests(unittest.TestCase):
    def test_attached_files_note_lists_every_file(self):
        files = [
            Attachment(file_id="a", name="a.txt", mime_type="text/plain", size=2048,
                       workspace_path="uploads/a.txt"),
            Attachment(file_id="b", name="b.csv", mime_type="text/csv", size=512,
                       workspace_path="uploads/b.csv"),
        ]
        note = _attached_files_note(files)
        self.assertIn("a.txt", note)
        self.assertIn("b.csv", note)
        self.assertIn("2.0 KiB", note)

    def test_uploaded_note_is_empty_without_uploads(self):
        body = AgentChatRequest(
            messages=[AgentChatMessage(role="user", content="hi")]
        )
        self.assertEqual(_uploaded_files_note(body), "")

    def test_uploaded_note_covers_every_turn(self):
        img = Attachment(type="image", file_id="1", name="one.png", workspace_path="uploads/one.png")
        f = Attachment(type="file", file_id="2", name="two.txt", workspace_path="uploads/two.txt")
        body = AgentChatRequest(
            messages=[
                AgentChatMessage(role="user", content="a", attachments=[img]),
                AgentChatMessage(role="user", content="b", attachments=[f]),
            ]
        )
        note = _uploaded_files_note(body)
        self.assertIn("one.png", note)
        self.assertIn("two.txt", note)
        self.assertIn("image (also shown inline)", note)


class ValidateAgentImagesTests(unittest.TestCase):
    def setUp(self):
        self.ids = store_with({"ok.png": png(64, 64), "bad.png": png(100, 100)})

    def tearDown(self):
        set_store(None)

    def _body(self, names):
        return AgentChatRequest(
            messages=[
                AgentChatMessage(
                    role="user",
                    content="x",
                    attachments=[
                        Attachment(type="image", file_id=self.ids[n], name=n)
                        for n in names
                    ],
                )
            ]
        )

    def test_valid_image_passes(self):
        _validate_agent_images(self._body(["ok.png"]), True, 3_686_400, 32)

    def test_no_images_passes_even_without_vision(self):
        body = AgentChatRequest(
            messages=[AgentChatMessage(role="user", content="plain")]
        )
        _validate_agent_images(body, False, None, None)

    def test_vision_disabled_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            _validate_agent_images(self._body(["ok.png"]), False, None, None)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("vision", ctx.exception.detail)

    def test_bad_dimensions_rejected_with_filename(self):
        with self.assertRaises(HTTPException) as ctx:
            _validate_agent_images(self._body(["bad.png"]), True, 3_686_400, 32)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("bad.png", ctx.exception.detail)

    def test_too_many_images_rejected(self):
        from app.config import settings

        original = settings.max_images_per_message
        try:
            settings.max_images_per_message = 1
            with self.assertRaises(HTTPException) as ctx:
                _validate_agent_images(self._body(["ok.png", "ok.png"]), True, None, None)
            self.assertEqual(ctx.exception.status_code, 422)
            self.assertIn("at most 1", ctx.exception.detail)
        finally:
            settings.max_images_per_message = original


class DeletePathToolTests(unittest.TestCase):
    @staticmethod
    def _delete(workspace, path):
        from agents.tool_context import ToolContext

        from app.services.agent_exec_tools import build_exec_tools

        args = json.dumps({"path": path})
        ctx = ToolContext(
            context=None,
            tool_name="delete_path",
            tool_call_id="c1",
            tool_arguments=args,
        )
        tools = {t.name: t for t in build_exec_tools(None, workspace)}
        return json.loads(asyncio.run(tools["delete_path"].on_invoke_tool(ctx, args)))

    def test_tool_is_registered(self):
        from app.services.agent_exec_tools import build_exec_tools

        with tempfile.TemporaryDirectory() as ws:
            names = [t.name for t in build_exec_tools(None, ws)]
        self.assertIn("delete_path", names)

    def test_deletes_a_file(self):
        with tempfile.TemporaryDirectory() as ws:
            victim = os.path.join(ws, "gone.txt")
            with open(victim, "w") as fh:
                fh.write("bye")
            out = self._delete(ws, "gone.txt")
            self.assertEqual(out.get("deleted"), os.path.realpath(victim))
            self.assertFalse(os.path.exists(victim))

    def test_refuses_nonempty_directory(self):
        with tempfile.TemporaryDirectory() as ws:
            os.makedirs(os.path.join(ws, "stuff"))
            with open(os.path.join(ws, "stuff", "x.txt"), "w") as fh:
                fh.write("x")
            out = self._delete(ws, "stuff")
            self.assertIn("error", out)
            self.assertIn("not empty", out["error"])

    def test_deletes_empty_directory(self):
        with tempfile.TemporaryDirectory() as ws:
            os.makedirs(os.path.join(ws, "empty"))
            out = self._delete(ws, "empty")
            self.assertEqual(out.get("kind"), "empty_directory")

    def test_refuses_workspace_root(self):
        with tempfile.TemporaryDirectory() as ws:
            out = self._delete(ws, ".")
            self.assertIn("workspace root", out.get("error", ""))

    def test_refuses_outside_workspace(self):
        with tempfile.TemporaryDirectory() as ws:
            outside = os.path.join(os.path.dirname(ws), "definitely_outside.txt")
            with open(outside, "w") as fh:
                fh.write("x")
            try:
                out = self._delete(ws, outside)
                self.assertIn("outside the workspace", out.get("error", ""))
                self.assertTrue(os.path.exists(outside))
            finally:
                os.unlink(outside)

    def test_traversal_cannot_escape(self):
        with tempfile.TemporaryDirectory() as ws:
            outside = os.path.join(os.path.dirname(ws), "escape.txt")
            with open(outside, "w") as fh:
                fh.write("x")
            try:
                out = self._delete(ws, "../escape.txt")
                self.assertIn("error", out)
                self.assertTrue(os.path.exists(outside))
            finally:
                os.unlink(outside)


if __name__ == "__main__":
    unittest.main()
