"""Retry behaviour for the chat endpoints.

The complaint these guard against: a long AI chat died on the first network
blip with a "network error" bubble. The chat path must keep trying -- five
retries, ten seconds apart -- before it gives up.
"""

import asyncio
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.chat import router as chat_router
from app.services.chat_retry import call_with_retry, is_retryable, retry_budget


class FakeState:
    def __init__(self, halogen):
        self.halogen = halogen
        self.model_name = "test-model"


def make_client(halogen) -> TestClient:
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/v1")
    app.state.rt = FakeState(halogen)
    return TestClient(app)


async def _noop(_):
    return None


class RetryBudgetTests(unittest.TestCase):
    def test_defaults_to_five_retries_ten_seconds_apart(self):
        attempts, pause = retry_budget()
        # "retry at least 5 times" == the first try plus five retries.
        self.assertEqual(attempts, 6)
        self.assertEqual(pause, 10.0)

    def test_environment_override(self):
        with patch.dict("os.environ", {"CHAT_RETRY_ATTEMPTS": "3"}):
            from app.config import Settings

            self.assertEqual(Settings().chat_retry_attempts, 3)

    def test_non_positive_falls_back(self):
        with patch.dict("os.environ", {"CHAT_RETRY_ATTEMPTS": "0"}):
            from app.config import Settings

            self.assertEqual(Settings().chat_retry_attempts, 6)


class IsRetryableTests(unittest.TestCase):
    def test_connection_errors_are_retryable(self):
        self.assertTrue(is_retryable(httpx.ConnectError("boom")))
        self.assertTrue(is_retryable(httpx.ReadError("boom")))
        self.assertTrue(is_retryable(httpx.RemoteProtocolError("boom")))
        self.assertTrue(is_retryable(httpx.ReadTimeout("boom")))

    def test_server_statuses_are_retryable(self):
        request = httpx.Request("POST", "http://engine/v1/chat/completions")
        for status in (408, 409, 425, 429, 500, 502, 503, 504):
            response = httpx.Response(status, request=request)
            exc = httpx.HTTPStatusError("x", request=request, response=response)
            self.assertTrue(is_retryable(exc), f"status {status} should retry")

    def test_client_errors_are_not_retryable(self):
        request = httpx.Request("POST", "http://engine/v1/chat/completions")
        for status in (400, 401, 403, 404, 422):
            response = httpx.Response(status, request=request)
            exc = httpx.HTTPStatusError("x", request=request, response=response)
            self.assertFalse(is_retryable(exc), f"status {status} should not retry")

    def test_cancellation_is_not_retryable(self):
        self.assertFalse(is_retryable(asyncio.CancelledError()))


class CallWithRetryTests(unittest.TestCase):
    def test_retries_until_success(self):
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ConnectError("down")
            return "ok"

        async def run():
            return await call_with_retry(flaky, pause=0)

        self.assertEqual(asyncio.run(run()), "ok")
        self.assertEqual(calls["n"], 3)

    def test_gives_up_after_the_budget(self):
        calls = {"n": 0}

        async def always_fails():
            calls["n"] += 1
            raise httpx.ConnectError("down")

        async def run():
            return await call_with_retry(always_fails, attempts=3, pause=0)

        with self.assertRaises(httpx.ConnectError):
            asyncio.run(run())
        self.assertEqual(calls["n"], 3)

    def test_non_retryable_fails_immediately(self):
        calls = {"n": 0}

        async def bad_request():
            calls["n"] += 1
            request = httpx.Request("POST", "http://engine/x")
            raise httpx.HTTPStatusError(
                "bad", request=request, response=httpx.Response(400, request=request)
            )

        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(call_with_retry(bad_request, attempts=6, pause=0))
        self.assertEqual(calls["n"], 1)

    def test_pause_between_attempts(self):
        slept = []

        async def record(_):
            slept.append(1)

        async def always_fails():
            raise httpx.ConnectError("down")

        async def run():
            try:
                await call_with_retry(always_fails, attempts=3, pause=10)
            except httpx.ConnectError:
                pass

        with patch("app.services.chat_retry.asyncio.sleep", side_effect=record):
            asyncio.run(run())
        # A pause after each of the first two failures, none after the last.
        self.assertEqual(len(slept), 2)


class StreamingChatRetryTests(unittest.TestCase):
    """End-to-end through POST /api/v1/chat."""

    def _body(self):
        return {"messages": [{"role": "user", "content": "hi"}], "api": "chat"}

    def test_transient_failure_is_retried_then_succeeds(self):
        calls = {"n": 0}

        class FlakyClient:
            base_url = "http://engine"

            async def get_health(self):
                return {}

            async def stream_sse(self, path, payload, headers=None):
                calls["n"] += 1
                if calls["n"] < 4:
                    raise httpx.ReadError("connection reset")
                yield {
                    "choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]
                }

            async def close(self):
                pass

        client = make_client(FlakyClient())
        with patch("app.routers.chat.asyncio.sleep", side_effect=_noop):
            resp = client.post("/api/v1/chat", json=self._body())

        self.assertEqual(resp.status_code, 200)
        self.assertIn("event: delta", resp.text)
        self.assertNotIn("event: error", resp.text)
        self.assertEqual(calls["n"], 4)

    def test_persistent_failure_reports_error_after_all_attempts(self):
        calls = {"n": 0}

        class DeadClient:
            base_url = "http://engine"

            async def get_health(self):
                return {}

            async def stream_sse(self, path, payload, headers=None):
                calls["n"] += 1
                raise httpx.ConnectError("connection refused")
                yield  # pragma: no cover - keeps this an async generator

            async def close(self):
                pass

        client = make_client(DeadClient())
        with patch("app.routers.chat.asyncio.sleep", side_effect=_noop):
            resp = client.post("/api/v1/chat", json=self._body())

        self.assertEqual(resp.status_code, 200)
        self.assertIn("event: error", resp.text)
        self.assertIn("6 attempts", resp.text)
        self.assertEqual(calls["n"], 6)

    def test_validation_error_is_not_retried(self):
        calls = {"n": 0}

        class RejectingClient:
            base_url = "http://engine"

            async def get_health(self):
                return {}

            async def stream_sse(self, path, payload, headers=None):
                calls["n"] += 1
                request = httpx.Request("POST", "http://engine" + path)
                raise httpx.HTTPStatusError(
                    "bad",
                    request=request,
                    response=httpx.Response(400, text="bad request", request=request),
                )
                yield  # pragma: no cover

            async def close(self):
                pass

        client = make_client(RejectingClient())
        resp = client.post("/api/v1/chat", json=self._body())
        self.assertIn("event: error", resp.text)
        self.assertEqual(calls["n"], 1)

    def test_failure_after_text_was_shown_is_not_retried(self):
        calls = {"n": 0}

        class HalfWayClient:
            base_url = "http://engine"

            async def get_health(self):
                return {}

            async def stream_sse(self, path, payload, headers=None):
                calls["n"] += 1
                yield {"choices": [{"delta": {"content": "partial"}}]}
                raise httpx.ReadError("connection reset")

            async def close(self):
                pass

        client = make_client(HalfWayClient())
        with patch("app.routers.chat.asyncio.sleep", side_effect=_noop):
            resp = client.post("/api/v1/chat", json=self._body())

        self.assertIn("event: delta", resp.text)
        self.assertIn("event: error", resp.text)
        # Replaying a request that already streamed text would duplicate the
        # answer, so the failure surfaces immediately.
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
