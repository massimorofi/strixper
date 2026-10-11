"""Tests for the agent self-recovery plumbing."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.services.agent_recovery import (
    _arguments_are_valid,
    _closest,
    format_tool_error,
    max_turns_handler,
    sanitize_model_input,
)


def _call(call_id, name, arguments):
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }


def _output(call_id, output="ok"):
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def _model_input(items):
    return SimpleNamespace(
        model_data=SimpleNamespace(input=items, instructions="be helpful")
    )


class ArgumentsValidTests(unittest.TestCase):
    def test_valid_object(self):
        self.assertTrue(_arguments_are_valid('{"path": "a.txt"}'))

    def test_dict_passes(self):
        self.assertTrue(_arguments_are_valid({"a": 1}))

    def test_truncated_string_rejected(self):
        self.assertFalse(_arguments_are_valid('{"content": "cut off mid'))

    def test_empty_rejected(self):
        self.assertFalse(_arguments_are_valid(""))
        self.assertFalse(_arguments_are_valid(None))

    def test_non_object_json_rejected(self):
        self.assertFalse(_arguments_are_valid("[1, 2, 3]"))


class SanitizeModelInputTests(unittest.TestCase):
    def test_clean_history_untouched(self):
        items = [
            {"role": "user", "content": "hi"},
            _call("c1", "write_file", '{"path": "a.txt", "content": "x"}'),
            _output("c1"),
        ]
        out = sanitize_model_input(_model_input(list(items)))
        self.assertEqual(out.input, items)
        self.assertEqual(out.instructions, "be helpful")

    def test_truncated_call_repaired_and_answered(self):
        items = [
            {"role": "user", "content": "write a big file"},
            _call("c1", "write_file", '{"path": "a.txt", "content": "trunc'),
        ]
        out = sanitize_model_input(_model_input(items))
        self.assertEqual(len(out.input), 3)
        self.assertEqual(out.input[1]["arguments"], "{}")
        self.assertEqual(out.input[2]["type"], "function_call_output")
        self.assertEqual(out.input[2]["call_id"], "c1")
        self.assertIn("never ran", out.input[2]["output"])

    def test_dangling_call_closed(self):
        items = [
            {"role": "user", "content": "go"},
            _call("c1", "read_file", '{"path": "a.txt"}'),
        ]
        out = sanitize_model_input(_model_input(items))
        self.assertEqual(len(out.input), 3)
        self.assertEqual(out.input[2]["call_id"], "c1")
        self.assertIn("No result was recorded", out.input[2]["output"])

    def test_existing_output_not_duplicated(self):
        items = [
            _call("c1", "read_file", '{"path": "a.txt"}'),
            _output("c1", "contents"),
        ]
        out = sanitize_model_input(_model_input(items))
        self.assertEqual(len(out.input), 2)

    def test_multiple_truncated_calls(self):
        items = [
            _call("c1", "write_file", '{"content": "aaa'),
            _output("c1"),
            _call("c2", "write_file", '{"content": "bb'),
        ]
        out = sanitize_model_input(_model_input(items))
        # c1 repaired (already answered), c2 repaired + answered
        self.assertEqual(len(out.input), 4)
        self.assertEqual(out.input[0]["arguments"], "{}")
        self.assertEqual(out.input[3]["call_id"], "c2")

    def test_empty_input(self):
        out = sanitize_model_input(_model_input([]))
        self.assertEqual(out.input, [])

    def test_non_dict_items_pass_through(self):
        out = sanitize_model_input(_model_input(["plain string", 42]))
        self.assertEqual(out.input, ["plain string", 42])


class FormatToolErrorTests(unittest.TestCase):
    def _tools(self, *names):
        return [SimpleNamespace(name=n) for n in names]

    def test_not_found_lists_available_tools(self):
        args = SimpleNamespace(
            kind="tool_not_found",
            tool_name="write",
            run_context=SimpleNamespace(
                agent=SimpleNamespace(tools=self._tools("write_file", "read_file"))
            ),
        )
        msg = format_tool_error(args)
        self.assertIn("does not exist", msg)
        self.assertIn("write_file", msg)
        self.assertIn("read_file", msg)
        self.assertIn("Did you mean 'write_file'", msg)

    def test_other_kinds_return_empty(self):
        args = SimpleNamespace(kind="approval_rejected", tool_name="x", run_context=None)
        self.assertEqual(format_tool_error(args), "")

    def test_no_tools_no_crash(self):
        args = SimpleNamespace(
            kind="tool_not_found",
            tool_name="bogus",
            run_context=SimpleNamespace(agent=SimpleNamespace(tools=[])),
        )
        msg = format_tool_error(args)
        self.assertIn("(none)", msg)

    def test_broken_context_no_crash(self):
        args = SimpleNamespace(kind="tool_not_found", tool_name="bogus", run_context=None)
        self.assertIn("does not exist", format_tool_error(args))


class ClosestNameTests(unittest.TestCase):
    def test_substring_match(self):
        self.assertEqual(_closest("write", ["read_file", "write_file"]), "write_file")

    def test_prefix_match(self):
        self.assertEqual(_closest("run_sh", ["run_shell"]), "run_shell")

    def test_no_match(self):
        self.assertIsNone(_closest("zzz", ["read_file"]))


class MaxTurnsHandlerTests(unittest.TestCase):
    def test_uses_last_assistant_text(self):
        items = [
            SimpleNamespace(raw_item={"content": "first"}),
            SimpleNamespace(raw_item={"content": "the answer"}),
        ]
        args = SimpleNamespace(run_data=SimpleNamespace(new_items=items))
        result = max_turns_handler(args)
        self.assertEqual(result.final_output, "the answer")
        self.assertTrue(result.include_in_history)

    def test_content_parts_list(self):
        items = [
            SimpleNamespace(
                raw_item={"content": [{"text": "part one "}, {"text": "part two"}]}
            )
        ]
        args = SimpleNamespace(run_data=SimpleNamespace(new_items=items))
        self.assertEqual(
            max_turns_handler(args).final_output, "part one part two"
        )

    def test_no_text_gives_fallback(self):
        args = SimpleNamespace(run_data=SimpleNamespace(new_items=[]))
        self.assertIn("turn limit", max_turns_handler(args).final_output)

    def test_missing_run_data(self):
        result = max_turns_handler(SimpleNamespace(run_data=None))
        self.assertIn("turn limit", result.final_output)


class WriteFileSizeGuardTests(unittest.TestCase):
    """Exercise write_file through the SDK's own invocation path."""

    @staticmethod
    def _write(workspace, payload):
        from agents.tool_context import ToolContext

        from app.services.agent_exec_tools import build_exec_tools

        args = json.dumps(payload)
        ctx = ToolContext(
            context=None,
            tool_name="write_file",
            tool_call_id="c1",
            tool_arguments=args,
        )
        tools = {t.name: t for t in build_exec_tools(None, workspace)}
        return json.loads(asyncio.run(tools["write_file"].on_invoke_tool(ctx, args)))

    def test_oversized_write_refused(self):
        from app.services.agent_exec_tools import MAX_WRITE_CHARS

        with tempfile.TemporaryDirectory() as ws:
            result = self._write(ws, {
                "path": "big.txt",
                "content": "x" * (MAX_WRITE_CHARS + 1),
            })
            self.assertIn("error", result)
            self.assertIn("how_to_fix", result)
            self.assertFalse(Path(ws, "big.txt").exists())

    def test_normal_write_still_works(self):
        with tempfile.TemporaryDirectory() as ws:
            result = self._write(ws, {"path": "ok.txt", "content": "hello"})
            self.assertEqual(result["bytes"], 5)
            self.assertEqual(Path(ws, "ok.txt").read_text(), "hello")

    def test_chunked_write_completes(self):
        from app.services.agent_exec_tools import MAX_WRITE_CHARS

        with tempfile.TemporaryDirectory() as ws:
            chunk = "a" * MAX_WRITE_CHARS
            self._write(ws, {"path": "c.txt", "content": chunk})
            self._write(ws, {"path": "c.txt", "content": chunk, "append": True})
            self.assertEqual(
                Path(ws, "c.txt").read_text(), chunk + chunk
            )


if __name__ == "__main__":
    unittest.main()


class ImageTokenEstimateTests(unittest.TestCase):
    """Base64 images must not be costed as if they were prose."""

    def test_image_payload_does_not_inflate_estimate(self):
        from app.services.agent_recovery import (
            IMAGE_TOKEN_ESTIMATE,
            MAX_CONTEXT_TOKENS,
            _estimate_items_tokens,
        )

        big_b64 = "A" * (5 * 1024 * 1024)
        items = [
            {"role": "system", "content": "sys"},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "look"},
                    {"type": "input_image", "image_url": f"data:image/png;base64,{big_b64}"},
                ],
            },
        ]
        est = _estimate_items_tokens(items)
        self.assertLess(est, MAX_CONTEXT_TOKENS)
        self.assertGreater(est, IMAGE_TOKEN_ESTIMATE)

    def test_plain_history_estimate_unchanged(self):
        from app.services.agent_recovery import CHARS_PER_TOKEN, _estimate_items_tokens

        items = [{"role": "user", "content": "x" * 400}]
        expected = len(json.dumps(items[0], ensure_ascii=False)) // CHARS_PER_TOKEN
        self.assertEqual(_estimate_items_tokens(items), expected)

    def test_many_images_still_eventually_truncate(self):
        from app.services.agent_recovery import (
            IMAGE_TOKEN_ESTIMATE,
            MAX_CONTEXT_TOKENS,
            _estimate_items_tokens,
        )

        per_image = MAX_CONTEXT_TOKENS // IMAGE_TOKEN_ESTIMATE + 1
        items = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "t"},
                    {"type": "input_image", "image_url": "data:image/png;base64," + "A" * 100},
                ],
            }
            for _ in range(per_image)
        ]
        self.assertGreater(_estimate_items_tokens(items), MAX_CONTEXT_TOKENS)
