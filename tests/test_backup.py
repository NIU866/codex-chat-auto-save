import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_codex_chats as backup


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-backup-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        self.output = self.root / "saved"
        self.sessions = self.source / "sessions" / "2026" / "10" / "28"
        self.sessions.mkdir(parents=True)
        self.sid = "12345678-1234-1234-1234-123456789abc"
        self.session = self.sessions / ("rollout-2026-10-28T12-00-00-" + self.sid + ".jsonl")
        self.rows = [
            {"type": "session_meta", "payload": {"id": self.sid, "cwd": "example-project"}},
            self.message("developer", "PRIVATE_INTERNAL_INSTRUCTION"),
            {"type": "response_item", "payload": {"type": "reasoning", "summary": [{"text": "PRIVATE_REASONING"}]}},
            self.message("assistant", "PRIVATE_ANALYSIS", channel="analysis"),
            self.message("user", "请继续项目"),
            self.message("assistant", "进度已保存", phase="final_answer"),
        ]
        self.session.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in self.rows) + "\n", encoding="utf-8")
        (self.source / "auth.json").write_text("PRIVATE_CREDENTIAL", encoding="utf-8")
        (self.source / "config.toml").write_text("PRIVATE_CONFIGURATION", encoding="utf-8")
        (self.source / "session_index.jsonl").write_text(json.dumps({"id": self.sid, "thread_name": "示例聊天"}, ensure_ascii=False) + "\n", encoding="utf-8")
        archived = self.source / "archived_sessions"
        archived.mkdir()
        (archived / "old.jsonl.zst").write_bytes(b"compressed-fixture")
        self.args = argparse.Namespace(source=self.source, output=self.output, keep=2, force=False)

    @staticmethod
    def message(role, text, **extra):
        return {"type": "response_item", "payload": {"type": "message", "role": role,
                "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}], **extra}}

    def save(self):
        with redirect_stdout(io.StringIO()):
            return backup.backup(self.args)

    def test_backup_preserves_bytes_and_hashes_without_credentials(self):
        result = self.save()
        with zipfile.ZipFile(result["archive"]) as bundle:
            self.assertIsNone(bundle.testzip())
            names = bundle.namelist()
            self.assertNotIn("auth.json", names)
            self.assertNotIn("config.toml", names)
            self.assertEqual(bundle.read(self.session.relative_to(self.source).as_posix()), self.session.read_bytes())
            self.assertEqual(bundle.read("archived_sessions/old.jsonl.zst"), b"compressed-fixture")
            manifest = json.loads(bundle.read("backup_manifest.json"))
            for name, info in manifest["files"].items():
                content = bundle.read(name)
                self.assertEqual(info["size"], len(content))
                self.assertEqual(info["sha256"], hashlib.sha256(content).hexdigest())

    def test_readable_export_contains_only_visible_messages(self):
        self.save()
        exports = list((self.output / "latest" / "聊天文本").glob("*.md"))
        self.assertEqual(len(exports), 1)
        content = exports[0].read_text(encoding="utf-8")
        self.assertIn("请继续项目", content)
        self.assertIn("进度已保存", content)
        self.assertNotIn("PRIVATE_", content)

    def test_unchanged_source_does_not_create_duplicate(self):
        first = self.save()
        second = self.save()
        self.assertFalse(second["changed"])
        self.assertEqual(first["archive"], second["archive"])
        self.assertEqual(len(list((self.output / "snapshots").glob("*.zip"))), 1)

    def test_retention_keeps_unrelated_files(self):
        self.save()
        unrelated = self.output / "snapshots" / "keep-this.zip"
        unrelated.write_bytes(b"unrelated")
        self.args.force = True
        for _ in range(3):
            self.save()
        self.assertTrue(unrelated.exists())
        self.assertEqual(len(list((self.output / "snapshots").glob("codex-chats-*.zip"))), 2)

    def test_incomplete_line_is_preserved_and_reported(self):
        with self.session.open("ab") as handle:
            handle.write(b'{"type":')
        result = self.save()
        self.assertTrue(result["notes"])
        with zipfile.ZipFile(result["archive"]) as bundle:
            self.assertEqual(bundle.read(self.session.relative_to(self.source).as_posix()), self.session.read_bytes())
        self.assertIn("未完整", (self.output / "latest" / "项目接续索引.md").read_text(encoding="utf-8"))

    def test_paths_cannot_escape_backup_directory(self):
        with self.assertRaises(ValueError):
            backup.safe_child(self.output, "../outside")
        self.assertEqual(backup.safe_child(self.output, "inside/file"), self.output / "inside/file")

    def test_second_process_cannot_take_the_same_lock(self):
        with backup.RunLock(self.output / ".backup.lock"):
            process = subprocess.run([sys.executable, str(Path(backup.__file__)), "--source", str(self.source), "--output", str(self.output)],
                                     capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(process.returncode, 1)
        self.assertIn("已有一个备份程序", process.stderr)


if __name__ == "__main__":
    unittest.main()
