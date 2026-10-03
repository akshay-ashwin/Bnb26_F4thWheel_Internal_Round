"""The no-CR check must catch CR bytes in working-tree and staged files, and ignore binaries."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from fd import envfile, repo
from fd.lint import find_cr_files
from fd.secrets_cmd import generate


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class CrCheck(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _git(self.root, "init", "-q", "-b", "main")
        # Keep git from converting anything, so the CR bytes we write are the CR bytes we get.
        _git(self.root, "config", "--local", "core.autocrlf", "false")

    def write(self, name: str, data: bytes) -> None:
        (self.root / name).write_bytes(data)
        _git(self.root, "add", name)

    def test_lf_files_pass(self) -> None:
        self.write("a.txt", b"one\ntwo\n")
        self.assertEqual(find_cr_files(self.root, staged=False), [])
        self.assertEqual(find_cr_files(self.root, staged=True), [])

    def test_crlf_file_is_reported_in_both_modes(self) -> None:
        self.write("bad.sql", b"select 1;\r\nselect 2;\r\n")
        self.assertEqual(find_cr_files(self.root, staged=False), ["bad.sql"])
        self.assertEqual(find_cr_files(self.root, staged=True), ["bad.sql"])

    def test_binary_files_are_ignored(self) -> None:
        self.write("blob.bin", b"\x00\x01\r\n\x02")
        self.write("pic.png", b"\x89PNG\r\n\x1a\n")
        self.assertEqual(find_cr_files(self.root, staged=False), [])

    def test_this_repository_has_no_cr_bytes(self) -> None:
        self.assertEqual(find_cr_files(repo.repo_root(), staged=False), [])


class SecretsGenerator(unittest.TestCase):
    def test_env_example_parses_and_secrets_are_marked(self) -> None:
        text = (repo.repo_root() / ".env.example").read_text(encoding="utf-8")
        entries = {e.key: e for e in envfile.parse_example(text)}
        secret_keys = {k for k, e in entries.items() if e.secret}
        self.assertEqual(
            secret_keys,
            {
                "POSTGRES_PASSWORD",
                "DATABASE_URL",
                "PHONE_PEPPER",
                "SESSION_SECRET",
                "TOKEN_SIGNING_KEY",
                "ADMIN_KEY",
                "SIM_TELEMETRY_KEY",
            },
        )
        # Every variable the plan lists must be documented here.
        for key in ("APP_ENV", "SIM_MODE", "DATABASE_URL", "REDIS_URL", "TOKEN_KEY_ID"):
            self.assertIn(key, entries)

    def test_generate_writes_lf_secrets_and_refuses_to_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            example = (repo.repo_root() / ".env.example").read_bytes()
            (root / ".env.example").write_bytes(example)

            self.assertEqual(generate(force=False, root=root), 0)
            raw = (root / ".env").read_bytes()
            self.assertNotIn(b"\r", raw)
            values = envfile.read_values(root / ".env")
            for key in ("POSTGRES_PASSWORD", "PHONE_PEPPER", "SESSION_SECRET", "ADMIN_KEY"):
                self.assertRegex(values[key], r"^[0-9a-f]{64}$")
            # ${NAME} references are expanded, so the URL carries the generated password.
            self.assertIn(values["POSTGRES_PASSWORD"], values["DATABASE_URL"])
            self.assertNotIn("${", values["DATABASE_URL"])

            before = (root / ".env").read_bytes()
            self.assertEqual(generate(force=False, root=root), 1)
            self.assertEqual((root / ".env").read_bytes(), before)
            self.assertEqual(generate(force=True, root=root), 0)
            self.assertNotEqual((root / ".env").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
