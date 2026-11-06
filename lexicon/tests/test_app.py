import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as lexicon_app


def upload(client, *files):
    data = {"files": [(io.BytesIO(body), name) for name, body in files]}
    return client.post("/api/documents", data=data, content_type="multipart/form-data")


class UploadApiTests(unittest.TestCase):
    def setUp(self):
        self.client = lexicon_app.app.test_client()

    def test_starts_empty_and_search_is_safe(self):
        self.assertEqual(self.client.get("/api/stats").get_json()["documents"], 0)
        self.assertEqual(self.client.get("/api/search?q=moon").get_json()["total"], 0)

    def test_upload_search_remove(self):
        r = upload(self.client,
                   ("moon.txt", b"Moon notes\nThe moon pulls the tides."),
                   ("bees.md", b"# Bees\nBees make honey."))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sorted(r.get_json()["added"]), ["bees.md", "moon.txt"])
        res = self.client.get("/api/search?q=hon*").get_json()
        self.assertEqual([h["source"] for h in res["hits"]], ["bees.md"])
        self.assertEqual(self.client.get("/api/doc/0").status_code, 200)

        r = self.client.delete("/api/documents/1")          # remove bees.md (id 1)
        self.assertEqual(r.get_json()["stats"]["documents"], 1)
        self.assertEqual(self.client.get("/api/search?q=honey").get_json()["total"], 0)
        self.assertEqual(self.client.get("/api/doc/1").status_code, 404)

        self.client.delete("/api/documents")
        self.assertEqual(self.client.get("/api/stats").get_json()["documents"], 0)

    def test_same_name_replaces(self):
        upload(self.client, ("a.txt", b"One\nalpha"))
        upload(self.client, ("a.txt", b"One\nbeta"))
        self.assertEqual(self.client.get("/api/stats").get_json()["documents"], 1)
        self.assertEqual(self.client.get("/api/search?q=beta").get_json()["total"], 1)
        self.assertEqual(self.client.get("/api/search?q=alpha").get_json()["total"], 0)

    def test_rejections(self):
        r = upload(self.client, ("evil.exe", b"MZ"), ("empty.txt", b"  \n"), ("bad.pdf", b"nope"))
        self.assertEqual(r.status_code, 422)
        self.assertEqual(len(r.get_json()["rejected"]), 3)
        self.assertEqual(self.client.post("/api/documents").status_code, 400)

    def test_mixed_batch_keeps_good_files(self):
        r = upload(self.client, ("ok.txt", b"Fine\nwords here"), ("evil.exe", b"MZ"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["added"], ["ok.txt"])
        self.assertEqual(len(r.get_json()["rejected"]), 1)

    def test_sessions_are_isolated(self):
        other = lexicon_app.app.test_client()
        upload(self.client, ("mine.txt", b"Mine\nsecret words"))
        self.assertEqual(other.get("/api/search?q=secret").get_json()["total"], 0)
        self.assertEqual(self.client.get("/api/search?q=secret").get_json()["total"], 1)

    def test_path_in_filename_is_stripped(self):
        r = upload(self.client, ("../../etc/passwd.txt", b"Title\nbody text"))
        self.assertEqual(r.get_json()["added"], ["passwd.txt"])


if __name__ == "__main__":
    unittest.main()
