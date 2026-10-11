import os
import shutil
import tempfile
import time
import unittest

from app.services.file_store import (
    INDEX_FILENAME,
    OUTPUTS_DIR,
    UPLOADS_DIR,
    FileStore,
    FileStoreError,
    sanitize_filename,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class SanitizeTests(unittest.TestCase):
    def test_keeps_a_normal_name(self):
        self.assertEqual(sanitize_filename("report.pdf"), "report.pdf")

    def test_strips_directory_components(self):
        self.assertEqual(sanitize_filename("../../etc/passwd"), "passwd")
        self.assertEqual(sanitize_filename("a/b/c/notes.txt"), "notes.txt")
        self.assertEqual(sanitize_filename(r"C:\Windows\System32\evil.exe"), "evil.exe")

    def test_strips_control_and_shell_characters(self):
        self.assertEqual(sanitize_filename('a"b*c?d<e>f|g.txt'), "abcdefg.txt")

    def test_no_hidden_files(self):
        self.assertEqual(sanitize_filename(".hidden"), "hidden")
        self.assertEqual(sanitize_filename(".."), "file")

    def test_empty_falls_back(self):
        self.assertEqual(sanitize_filename(""), "file")

    def test_long_names_are_capped(self):
        self.assertEqual(len(sanitize_filename("x" * 500)), 180)


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = FileStore(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_returns_record_and_writes_file(self):
        rec = self.store.save(PNG, "shot.png")
        self.assertEqual(rec.name, "shot.png")
        self.assertEqual(rec.size, len(PNG))
        self.assertEqual(rec.mime_type, "image/png")
        self.assertTrue(rec.path.startswith(UPLOADS_DIR))
        self.assertTrue(os.path.exists(os.path.join(self.store.root, rec.path)))

    def test_unique_names_do_not_collide(self):
        first = self.store.save(PNG, "same.png")
        second = self.store.save(PNG, "same.png")
        self.assertNotEqual(first.path, second.path)
        self.assertNotEqual(first.id, second.id)

    def test_rejects_empty_payload(self):
        with self.assertRaises(FileStoreError):
            self.store.save(b"", "empty.png")

    def test_rejects_oversize_before_touching_disk(self):
        store = FileStore(self.tmp.name, max_file_bytes=10)
        with self.assertRaises(FileStoreError):
            store.save(b"x" * 11, "nope.bin")
        self.assertEqual(store.list(), [])

    def test_rejects_oversize(self):
        store = FileStore(self.tmp.name, max_file_bytes=100)
        with self.assertRaises(FileStoreError):
            store.save(b"x" * 200, "big.bin")

    def test_rejects_traversal_in_filename(self):
        rec = self.store.save(PNG, "../../../etc/cron.d/evil")
        self.assertNotIn("..", rec.path)
        self.assertTrue(rec.path.startswith(UPLOADS_DIR))

    def test_sniffed_mime_wins_over_declared(self):
        rec = self.store.save(PNG, "mislabeled.txt", declared_mime="text/csv")
        self.assertEqual(rec.mime_type, "image/png")

    def test_declared_mime_used_when_magic_bytes_unknown(self):
        rec = self.store.save(b"a,b,c\n1,2,3\n", "data.dat", declared_mime="text/csv")
        self.assertEqual(rec.mime_type, "text/csv")

    def test_output_subdir_is_supported(self):
        rec = self.store.save(b"result", "out.txt", subdir=OUTPUTS_DIR)
        self.assertTrue(rec.path.startswith(OUTPUTS_DIR))


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = FileStore(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_bytes_roundtrip(self):
        rec = self.store.save(PNG, "a.png")
        self.assertEqual(self.store.read_bytes(rec.id), PNG)

    def test_unknown_id_raises(self):
        with self.assertRaises(FileStoreError):
            self.store.read_bytes("deadbeef")

    def test_read_text(self):
        rec = self.store.save("hello world".encode(), "greeting.txt")
        out = self.store.read_text(rec.path)
        self.assertEqual(out["content"], "hello world")
        self.assertEqual(out["encoding"], "utf-8")
        self.assertFalse(out["truncated"])

    def test_read_text_truncates_at_limit(self):
        store = FileStore(self.tmp.name, read_limit=10)
        rec = store.save(b"0123456789abcdefghij", "long.txt")
        out = store.read_text(rec.path)
        self.assertEqual(out["content"], "0123456789")
        self.assertTrue(out["truncated"])

    def test_binary_is_refused_as_text(self):
        self.store.save(b"\x00\x01\x02binary", "blob.dat")
        with self.assertRaises(FileStoreError):
            self.store.read_text("blob.dat")


class ConfinementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = FileStore(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_traversal_rejected(self):
        for bad in ("../etc/passwd", "/etc/passwd", "uploads/../../x", "..", "a/../../b"):
            with self.assertRaises(FileStoreError, msg=bad):
                self.store.resolve(bad)

    def test_windows_drive_path_rejected(self):
        with self.assertRaises(FileStoreError):
            self.store.resolve("C:\\Windows\\win.ini")

    def test_symlink_escape_rejected(self):
        outside = tempfile.mkdtemp()
        try:
            secret = os.path.join(outside, "secret.txt")
            with open(secret, "w") as fh:
                fh.write("secret")
            link = os.path.join(self.store.root, UPLOADS_DIR, "link.txt")
            os.symlink(secret, link)
            with self.assertRaises(FileStoreError):
                self.store.resolve(os.path.join(UPLOADS_DIR, "link.txt"))
        finally:
            os.unlink(link)
            shutil.rmtree(outside, ignore_errors=True)

    def test_legitimate_nested_path_allowed(self):
        os.makedirs(os.path.join(self.store.root, "notes", "deep"), exist_ok=True)
        resolved = self.store.resolve("notes/deep")
        self.assertTrue(resolved.startswith(self.store.root))


class DeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = FileStore(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_delete_removes_file_and_index_entry(self):
        rec = self.store.save(PNG, "gone.png")
        full = os.path.join(self.store.root, rec.path)
        self.assertTrue(self.store.delete(rec.id))
        self.assertFalse(os.path.exists(full))
        self.assertFalse(self.store.delete(rec.id))

    def test_delete_path_refuses_nonempty_directory(self):
        self.store.save(PNG, "keep.png")
        with self.assertRaises(FileStoreError) as ctx:
            self.store.delete_path(UPLOADS_DIR)
        self.assertIn("not empty", str(ctx.exception))

    def test_delete_path_removes_empty_directory(self):
        os.makedirs(os.path.join(self.store.root, "emptydir"), exist_ok=True)
        self.store.delete_path("emptydir")
        self.assertFalse(os.path.exists(os.path.join(self.store.root, "emptydir")))

    def test_delete_path_removes_directory_with_only_leftover_tmp_files(self):
        """A dir that *looks* empty may still hold the store's own .tmp-* files.

        Those are invisible to the user, so the emptiness check passes but
        ``os.rmdir`` would fail with ENOTEMPTY unless they are cleared first.
        """
        d = os.path.join(self.store.root, "ghostdir")
        os.makedirs(d, exist_ok=True)
        for name in (".tmp-aaa", ".tmp-bbb", INDEX_FILENAME):
            with open(os.path.join(d, name), "w") as fh:
                fh.write("x")
        self.store.delete_path("ghostdir")
        self.assertFalse(os.path.exists(d))

    def test_delete_path_refuses_workspace_root(self):
        with self.assertRaises(FileStoreError):
            self.store.delete_path(".")

    def test_delete_path_outside_workspace_rejected(self):
        with self.assertRaises(FileStoreError):
            self.store.delete_path("../outside.txt")

    def test_delete_path_recursive_removes_nonempty_directory(self):
        self.store.save(PNG, "doomed.png")
        os.makedirs(os.path.join(self.store.root, "tree", "branch"), exist_ok=True)
        with open(os.path.join(self.store.root, "tree", "branch", "leaf.txt"), "w") as fh:
            fh.write("leaf")
        action = self.store.delete_path("tree", recursive=True)
        self.assertEqual(action, "directory")
        self.assertFalse(os.path.exists(os.path.join(self.store.root, "tree")))

    def test_delete_path_recursive_purges_index_records(self):
        rec = self.store.save(b"data", "dropme.txt")
        self.store.delete_path(rec.path, recursive=True)
        self.assertNotIn(rec.id, self.store._records)
        self.assertFalse(os.path.exists(os.path.join(self.store.root, rec.path)))

    def test_delete_paths_reports_successes_and_failures(self):
        rec = self.store.save(b"one", "one.txt")
        os.makedirs(os.path.join(self.store.root, "emptydir"), exist_ok=True)
        result = self.store.delete_paths(
            [rec.path, "emptydir", "missing.txt"], recursive=True
        )
        self.assertEqual(result["deleted_count"], 2)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["failed"][0]["path"], "missing.txt")
        self.assertFalse(os.path.exists(os.path.join(self.store.root, rec.path)))
        self.assertFalse(os.path.exists(os.path.join(self.store.root, "emptydir")))

    def test_delete_paths_children_listed_with_parent(self):
        # Selecting a directory and its (visible) children must not report
        # the children as failures once the parent is gone.
        os.makedirs(os.path.join(self.store.root, "a", "b"), exist_ok=True)
        with open(os.path.join(self.store.root, "a", "b", "c.txt"), "w") as fh:
            fh.write("c")
        result = self.store.delete_paths(
            ["a", "a/b", "a/b/c.txt"], recursive=True
        )
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["deleted_count"], 3)
        self.assertFalse(os.path.exists(os.path.join(self.store.root, "a")))

    def test_delete_paths_rejects_empty_selection(self):
        result = self.store.delete_paths(["", "   "])
        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(result["failed_count"], 0)


class BrowseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = FileStore(self.tmp.name)
        self.store.save(PNG, "one.png")
        self.store.save(b"hello", "two.txt")
        os.makedirs(os.path.join(self.store.root, "uploads", "subdir"), exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_directories_before_files(self):
        out = self.store.browse(UPLOADS_DIR)
        entries = out["entries"]
        self.assertIn("subdir", [e["name"] for e in entries])
        first_file = next(i for i, e in enumerate(entries) if not e["is_dir"])
        last_dir = max(i for i, e in enumerate(entries) if e["is_dir"])
        self.assertLess(last_dir, first_file)
        self.assertTrue(out["entries"][0]["is_dir"])

    def test_index_file_is_hidden(self):
        out = self.store.browse(".")
        names = [e["name"] for e in out["entries"]]
        self.assertNotIn(INDEX_FILENAME, names)

    def test_files_carry_mime_and_size(self):
        out = self.store.browse(UPLOADS_DIR)
        entry = next(e for e in out["entries"] if e["name"].startswith("one"))
        self.assertEqual(entry["mime_type"], "image/png")
        self.assertEqual(entry["size"], len(PNG))

    def test_browse_missing_dir_raises(self):
        with self.assertRaises(FileStoreError):
            self.store.browse("nope")


class IndexTests(unittest.TestCase):
    def test_index_survives_reload(self):
        with tempfile.TemporaryDirectory() as ws:
            store = FileStore(ws)
            rec = store.save(PNG, "persist.png")
            reloaded = FileStore(ws)
            self.assertEqual(reloaded.require(rec.id).name, "persist.png")

    def test_corrupt_index_is_rebuilt_from_disk(self):
        with tempfile.TemporaryDirectory() as ws:
            store = FileStore(ws)
            store.save(PNG, "a.png")
            with open(os.path.join(ws, INDEX_FILENAME), "w") as fh:
                fh.write("{ this is not json")
            rebuilt = FileStore(ws)
            listed = rebuilt.list() if hasattr(rebuilt, "list") else None
            self.assertTrue(listed)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["name"], "a.png")

    def test_index_drops_records_whose_files_are_gone(self):
        with tempfile.TemporaryDirectory() as ws:
            store = FileStore(ws)
            rec = store.save(PNG, "temp.png")
            os.unlink(os.path.join(ws, rec.path))
            reloaded = FileStore(ws)
            with self.assertRaises(FileStoreError):
                reloaded.require(rec.id)


class SweepTests(unittest.TestCase):
    def test_old_files_are_removed(self):
        with tempfile.TemporaryDirectory() as ws:
            store = FileStore(ws, ttl_days=1)
            rec = store.save(PNG, "old.png")
            # Age the record beyond the TTL.
            store._records[rec.id].created_at = time.time() - 2 * 86400
            removed = store.sweep()
            self.assertEqual(removed, 1)
            self.assertFalse(os.path.exists(os.path.join(ws, rec.path)))

    def test_recent_files_survive(self):
        with tempfile.TemporaryDirectory() as ws:
            store = FileStore(ws, ttl_days=7)
            store.save(PNG, "fresh.png")
            self.assertEqual(store.sweep(), 0)

    def test_ttl_zero_disables_sweep(self):
        with tempfile.TemporaryDirectory() as ws:
            store = FileStore(ws, ttl_days=0)
            rec = store.save(PNG, "old.png")
            store._records[rec.id].created_at = time.time() - 365 * 86400
            self.assertEqual(store.sweep(), 0)


class AdoptTests(unittest.TestCase):
    def test_files_written_by_other_means_are_adopted(self):
        with tempfile.TemporaryDirectory() as ws:
            store = FileStore(ws)
            stray_dir = os.path.join(ws, OUTPUTS_DIR, "agent")
            os.makedirs(stray_dir, exist_ok=True)
            with open(os.path.join(stray_dir, "script.py"), "w") as fh:
                fh.write("print('hi')")
            added = store.adopt_existing()
            self.assertEqual(added, 1)
            paths = [r.path for r in store._records.values()]
            self.assertIn(os.path.join(OUTPUTS_DIR, "agent", "script.py"), paths)

    def test_adopt_is_idempotent(self):
        with tempfile.TemporaryDirectory() as ws:
            store = FileStore(ws)
            stray = os.path.join(ws, OUTPUTS_DIR, "x.txt")
            with open(stray, "w") as fh:
                fh.write("x")
            store.adopt_existing()
            self.assertEqual(store.adopt_existing(), 0)


if __name__ == "__main__":
    unittest.main()
