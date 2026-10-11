import io
import struct
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.files import router as files_router
from app.services.file_store import UPLOADS_DIR, FileStore, set_store


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


def make_client(workspace: str) -> TestClient:
    app = FastAPI()
    app.include_router(files_router, prefix="/api/v1")
    set_store(FileStore(workspace))
    return TestClient(app)


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = make_client(self.tmp.name)

    def tearDown(self):
        set_store(None)
        self.tmp.cleanup()

    def post(self, content: bytes, filename: str, content_type="application/octet-stream"):
        return self.client.post(
            "/api/v1/files/upload",
            files={"file": (filename, io.BytesIO(content), content_type)},
        )

    def test_upload_returns_record_with_urls(self):
        r = self.post(png(), "shot.png", "image/png")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["name"], "shot.png")
        self.assertEqual(body["mime_type"], "image/png")
        self.assertEqual(body["download_url"], f"/api/v1/files/{body['id']}")
        self.assertEqual(body["preview_url"], f"/api/v1/files/{body['id']}/preview")

    def test_empty_upload_rejected(self):
        r = self.post(b"", "empty.png", "image/png")
        self.assertEqual(r.status_code, 400)
        self.assertIn("empty", r.json()["detail"])

    def test_oversize_upload_rejected(self):
        with patch("app.routers.files.settings") as s:
            s.max_file_size_mb = 0
            s.allowed_image_types = ["image/png"]
            s.allowed_file_types = ["*"]
            s.image_max_pixels = 3_686_400
            s.image_size_multiple = 32
            s.max_image_size_mb = 0
            r = self.post(png(), "x.png", "image/png")
        self.assertEqual(r.status_code, 413)

    def test_disallowed_image_type_rejected(self):
        gif = b"GIF89a" + struct.pack("<HH", 64, 64) + b"\x00" * 32
        with patch("app.routers.files.settings") as s:
            s.max_file_size_mb = 10
            s.allowed_image_types = ["image/png", "image/jpeg"]
            s.allowed_file_types = ["*"]
            s.image_max_pixels = 3_686_400
            s.image_size_multiple = 32
            s.max_image_size_mb = 10
            r = self.post(gif, "anim.gif", "image/gif")
        self.assertEqual(r.status_code, 415)
        self.assertIn("image/gif", r.json()["detail"])

    def test_image_over_pixel_budget_rejected(self):
        with patch("app.routers.files.settings") as s:
            s.max_file_size_mb = 10
            s.allowed_image_types = ["image/png"]
            s.allowed_file_types = ["*"]
            s.image_max_pixels = 1000
            s.image_size_multiple = 32
            s.max_image_size_mb = 10
            r = self.post(png(320, 256), "big.png", "image/png")
        self.assertEqual(r.status_code, 422)
        self.assertIn("pixels", r.json()["detail"])

    def test_non_image_with_disallowed_extension_rejected(self):
        with patch("app.routers.files.settings") as s:
            s.max_file_size_mb = 10
            s.allowed_image_types = ["image/png"]
            s.allowed_file_types = [".txt", ".csv"]
            s.image_max_pixels = 3_686_400
            s.image_size_multiple = 32
            s.max_image_size_mb = 10
            r = self.post(b"payload", "malware.exe")
        self.assertEqual(r.status_code, 415)

    def test_non_image_with_allowed_extension_passes(self):
        with patch("app.routers.files.settings") as s:
            s.max_file_size_mb = 10
            s.allowed_image_types = ["image/png"]
            s.allowed_file_types = [".txt"]
            s.image_max_pixels = 3_686_400
            s.image_size_multiple = 32
            s.max_image_size_mb = 10
            r = self.post(b"hello", "notes.txt", "text/plain")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["path"].startswith(UPLOADS_DIR))


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = make_client(self.tmp.name)
        self.rec = self.client.post(
            "/api/v1/files/upload",
            files={"file": ("a.png", io.BytesIO(png()), "image/png")},
        ).json()

    def tearDown(self):
        set_store(None)
        self.tmp.cleanup()

    def test_download_roundtrip(self):
        r = self.client.get(self.rec["download_url"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, png())
        self.assertIn("attachment", r.headers["content-disposition"])

    def test_preview_is_inline_for_images(self):
        r = self.client.get(self.rec["preview_url"])
        self.assertEqual(r.status_code, 200)
        self.assertIn("inline", r.headers["content-disposition"])
        self.assertEqual(r.headers["content-type"], "image/png")

    def test_unknown_id_is_404(self):
        self.assertEqual(self.client.get("/api/v1/files/deadbeef").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/files/deadbeef/preview").status_code, 404)

    def test_delete_removes_file(self):
        r = self.client.delete(f"/api/v1/files/{self.rec['id']}")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["deleted"])
        self.assertEqual(self.client.get(self.rec["download_url"]).status_code, 404)

    def test_delete_unknown_is_404(self):
        self.assertEqual(self.client.delete("/api/v1/files/nope").status_code, 404)

    def test_list_shows_uploaded_files(self):
        self.client.post(
            "/api/v1/files/upload",
            files={"file": ("b.txt", io.BytesIO(b"hi"), "text/plain")},
        )
        r = self.client.get("/api/v1/files")
        self.assertEqual(r.status_code, 200)
        names = [f["name"] for f in r.json()["files"]]
        self.assertIn("a.png", names)
        self.assertIn("b.txt", names)


class WorkspaceRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = make_client(self.tmp.name)

    def tearDown(self):
        set_store(None)
        self.tmp.cleanup()

    def test_list_root(self):
        r = self.client.get("/api/v1/workspace/list", params={"path": "."})
        self.assertEqual(r.status_code, 200)
        names = [e["name"] for e in r.json()["entries"]]
        self.assertIn("uploads", names)
        self.assertIn("outputs", names)

    def test_read_text_file(self):
        self.client.post(
            "/api/v1/files/upload",
            files={"file": ("n.txt", io.BytesIO(b"body text"), "text/plain")},
        )
        r = self.client.get("/api/v1/workspace/read", params={"path": "uploads/n.txt"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["content"], "body text")

    def test_read_missing_file_is_400(self):
        r = self.client.get("/api/v1/workspace/read", params={"path": "nope.txt"})
        self.assertEqual(r.status_code, 400)

    def test_traversal_is_rejected(self):
        for bad in ("../../etc/passwd", "/etc/passwd"):
            r = self.client.get("/api/v1/workspace/read", params={"path": bad})
            self.assertEqual(r.status_code, 400, bad)

    def test_delete_file_via_workspace(self):
        rec = self.client.post(
            "/api/v1/files/upload",
            files={"file": ("d.txt", io.BytesIO(b"x"), "text/plain")},
        ).json()
        r = self.client.delete("/api/v1/workspace", params={"path": rec["path"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["kind"], "file")

    def test_delete_empty_directory(self):
        import os

        os.makedirs(os.path.join(self.tmp.name, "empty"), exist_ok=True)
        r = self.client.delete("/api/v1/workspace", params={"path": "empty"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["kind"], "directory")

    def test_delete_nonempty_directory_rejected(self):
        self.client.post(
            "/api/v1/files/upload",
            files={"file": ("k.txt", io.BytesIO(b"x"), "text/plain")},
        )
        r = self.client.delete("/api/v1/workspace", params={"path": "uploads"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("not empty", r.json()["detail"])

    def test_delete_nonempty_directory_recursive(self):
        self.client.post(
            "/api/v1/files/upload",
            files={"file": ("gone.txt", io.BytesIO(b"x"), "text/plain")},
        )
        r = self.client.delete(
            "/api/v1/workspace", params={"path": "uploads", "recursive": "true"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["kind"], "directory")
        listing = self.client.get("/api/v1/workspace/list", params={"path": "."})
        names = [e["name"] for e in listing.json()["entries"]]
        self.assertNotIn("uploads", names)

    def test_bulk_delete_removes_mixed_paths(self):
        import os

        os.makedirs(os.path.join(self.tmp.name, "dir"), exist_ok=True)
        up = self.client.post(
            "/api/v1/files/upload",
            files={"file": ("bulk.txt", io.BytesIO(b"x"), "text/plain")},
        )
        self.assertEqual(up.status_code, 200)
        stored = up.json()["path"]
        r = self.client.post(
            "/api/v1/workspace/delete",
            json={"paths": [stored, "dir", "nope.txt"], "recursive": True},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["deleted_count"], 2)
        self.assertEqual(body["failed_count"], 1)
        self.assertEqual(body["failed"][0]["path"], "nope.txt")

    def test_bulk_delete_rejects_empty_list(self):
        r = self.client.post("/api/v1/workspace/delete", json={"paths": []})
        self.assertEqual(r.status_code, 400)
        self.assertIn("no paths", r.json()["detail"])

    def test_bulk_delete_all_failed_is_400(self):
        r = self.client.post(
            "/api/v1/workspace/delete",
            json={"paths": ["missing-a", "missing-b"], "recursive": True},
        )
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
