# SPDX-License-Identifier: GPL-3.0-or-later
"""Publication safeguards using synthetic identities and temporary files."""

from pathlib import Path
import io
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

import public_audit


def synthetic_path():
    return "/".join(("", "Users", "fixture-user", "checkout", "file.cpp"))


def zipped(name, payload):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, payload)
    return stream.getvalue()


class PublicAuditTests(unittest.TestCase):
    def test_private_paths_are_detected_without_printing_contents(self):
        for path in (synthetic_path(), "/".join(("", "home", "fixture-user", "source")),
                     "\\".join(("C:", "Users", "fixture-user", "file.cpp"))):
            findings = public_audit.audit_source_blob("docs/guide.md", path.encode())
            self.assertEqual(len(findings), 1)
            self.assertNotIn("fixture-user", repr(findings))

    def test_unchanged_public_upstream_examples_remain_allowed(self):
        upstream = (synthetic_path() + "\nPublic upstream example\n").encode()
        current = upstream + b"New general instructions\n"
        self.assertEqual(public_audit.audit_source_blob("docs/guide.md", current, upstream), [])
        self.assertTrue(public_audit.audit_source_blob("docs/guide.md", upstream.replace(b"fixture-user", b"new-user"), upstream))

    def test_secrets_are_detected(self):
        key = " ".join(("-----BEGIN", "OPENSSH", "PRIVATE", "KEY-----"))
        credentials = "https://" + "fixture-user:fixture-password@" + "example.test/path"
        for data in (key.encode(), ("ghp_" + "A" * 36).encode(), ("github_pat_" + "B" * 40).encode(), credentials.encode()):
            self.assertEqual(public_audit.audit_source_blob("settings.txt", data)[0]["problem"], "possible credential or private key")

    def test_new_contact_addresses_require_review_but_upstream_credits_survive(self):
        contact = ("fixture.person@" + "mail.invalid").encode()
        self.assertTrue(public_audit.audit_source_blob("notes.txt", contact))
        self.assertEqual(public_audit.audit_source_blob("AUTHORS", contact + b"\nNew credit without private contact", contact), [])

    def test_private_asset_additions_are_rejected(self):
        for name in ("Spilldata/data/game.bin", "captures/frame.png", "saves/player.pfl", ".env.local", "game.iso", "bundle.dmg"):
            self.assertTrue(public_audit.audit_source_blob(name, b"data"))
        self.assertTrue(public_audit.audit_source_blob("image.png", b"PNG\0bytes"))
        self.assertEqual(public_audit.audit_source_blob(".env.example", b"EXAMPLE_VALUE=replace-me"), [])

    def test_public_identity_requires_handle_and_noreply_for_both_fields(self):
        self.assertTrue(public_audit.public_identity("example-user", "1234+example-user@users.noreply.github.com"))
        self.assertTrue(public_audit.public_identity("example-user", "example-user@users.noreply.github.com"))
        self.assertTrue(public_audit.public_identity("GitHub", "noreply@github.com"))
        self.assertFalse(public_audit.public_identity("Example Person", "example-user@users.noreply.github.com"))
        self.assertFalse(public_audit.public_identity("example-user", "example-user@example.test"))

    def test_distribution_markers_cross_chunk_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runner"
            marker = synthetic_path().encode()
            for offset in (30, 31, 32):
                path.write_bytes(b"x" * offset + marker)
                with mock.patch.object(public_audit, "CHUNK_SIZE", 32), self.assertRaises(public_audit.PublicationError):
                    public_audit.assert_public_artifact(path)

    def test_distribution_rejects_utf16_and_custom_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runner"
            for encoding in ("utf-16-le", "utf-16-be"):
                for text in (synthetic_path(), "\\".join(("C:", "Users", "fixture-user", "file.cpp"))):
                    path.write_bytes(text.encode(encoding))
                    with self.assertRaises(public_audit.PublicationError):
                        public_audit.assert_public_artifact(path)
            path.write_bytes(b"/workspace/private-source/file.cpp")
            with self.assertRaises(public_audit.PublicationError):
                public_audit.assert_public_artifact(path, forbidden_roots=("/workspace/private-source",))

    def test_distribution_support_data_allowed_but_game_data_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Runner.app"
            app.mkdir()
            (app / "fonts.dat").write_bytes(b"public font bytes")
            public_audit.assert_public_artifact(app)
            (app / "game.bin").write_bytes(b"original resource")
            with self.assertRaises(public_audit.PublicationError):
                public_audit.assert_public_artifact(app)
            (app / "game.bin").unlink()
            (app / "metadata").symlink_to(app / "fonts.dat")
            with self.assertRaises(public_audit.PublicationError):
                public_audit.assert_public_artifact(app)

    def test_nested_compressed_private_paths_and_secrets_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "fonts.dat"
            for private in (synthetic_path().encode(), ("ghp_" + "A" * 36).encode()):
                archive.write_bytes(zipped("nested.zip", zipped("notes.txt", private)))
                self.assertNotIn(private, archive.read_bytes())
                with self.assertRaises(public_audit.PublicationError):
                    public_audit.assert_public_artifact(archive)

    def test_distributable_rejects_original_executables_and_signing_material(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("original.exe", "original.dll", "identity.p12", "identity.pfx", "identity.key"):
                file = Path(directory) / name
                file.write_bytes(b"synthetic fixture only")
                with self.subTest(name=name), self.assertRaises(public_audit.PublicationError):
                    public_audit.assert_public_artifact(file)

    def test_nested_original_player_resources_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "helpdialog.zip"
            archive.write_bytes(zipped("nested.zip", zipped("player.pfl", b"synthetic private profile")))
            with self.assertRaisesRegex(public_audit.PublicationError, "original game data"):
                public_audit.assert_public_artifact(archive)

    def test_zip_paths_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "helpdialog.zip"
            symlink = zipfile.ZipInfo("alias")
            symlink.create_system = 3
            symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
            for name in ("../outside.txt", "/absolute.txt", "folder\\outside.txt", "folder//file.txt", symlink):
                archive.write_bytes(zipped(name, b"synthetic data"))
                with self.subTest(path=str(name)), self.assertRaises(public_audit.PublicationError):
                    public_audit.assert_public_artifact(archive)

    def test_zip_expansion_recursion_and_entry_budgets_are_shared(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "helpdialog.zip"
            archive.write_bytes(zipped("text.txt", b"x" * 100))
            with mock.patch.object(public_audit, "MAX_EXPANDED_BYTES", 32), self.assertRaises(public_audit.PublicationError):
                public_audit.assert_public_artifact(archive)
            archive.write_bytes(zipped("nested.zip", zipped("text.txt", b"public")))
            with mock.patch.object(public_audit, "MAX_ARCHIVE_DEPTH", 1), self.assertRaises(public_audit.PublicationError):
                public_audit.assert_public_artifact(archive)
            with mock.patch.object(public_audit, "MAX_ARCHIVE_ENTRIES", 1), self.assertRaises(public_audit.PublicationError):
                public_audit.assert_public_artifact(archive)

    def test_history_retains_deleted_private_material_until_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()

            git("init", "-q")
            git("config", "user.name", "example-user")
            git("config", "user.email", "example-user@users.noreply.github.com")
            (root / "guide.md").write_text("Public baseline\n")
            git("add", ".")
            git("commit", "-qm", "Public baseline")
            baseline = git("rev-parse", "HEAD")
            (root / "guide.md").write_text(synthetic_path() + "\n")
            git("commit", "-qam", "Private addition")
            (root / "guide.md").write_text("Sanitized current text\n")
            git("commit", "-qam", "Remove private text")
            self.assertTrue(public_audit.audit_repository(root, baseline, check_commits=False)["passed"])
            self.assertFalse(public_audit.audit_repository(root, baseline)["passed"])

    def test_private_commit_message_is_detected_after_file_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-q")
            git("config", "user.name", "example-user")
            git("config", "user.email", "example-user@users.noreply.github.com")
            (root / "guide.md").write_text("Public baseline\n")
            git("add", ".")
            git("commit", "-qm", "Baseline")
            baseline = git("rev-parse", "HEAD")
            (root / "guide.md").write_text("Public addition\n")
            git("commit", "-qam", synthetic_path())
            findings = public_audit.audit_repository(root, baseline)["findings"]
            self.assertEqual(findings[0]["path"], "commit-message")

    def test_repository_metadata_check_does_not_echo_private_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-q")
            git("config", "user.name", "example-user")
            git("config", "user.email", "example-user@users.noreply.github.com")
            (root / "guide.md").write_text("Public baseline\n")
            git("add", ".")
            git("commit", "-qm", "Baseline")
            baseline = git("rev-parse", "HEAD")
            git("config", "user.name", "Fixture Person")
            git("config", "user.email", "fixture-person@example.test")
            (root / "guide.md").write_text("Public addition\n")
            git("commit", "-qam", "Addition")
            result = public_audit.audit_repository(root, baseline)
            self.assertFalse(result["passed"])
            self.assertNotIn("Fixture Person", repr(result))
            self.assertNotIn("example.test", repr(result))

    def test_merge_resolution_cannot_hide_private_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-q")
            git("config", "user.name", "example-user")
            git("config", "user.email", "example-user@users.noreply.github.com")
            (root / "guide.md").write_text("Baseline\n")
            git("add", ".")
            git("commit", "-qm", "Baseline")
            baseline = git("rev-parse", "HEAD")
            main = git("branch", "--show-current")
            git("checkout", "-qb", "other")
            (root / "guide.md").write_text("Other branch\n")
            git("commit", "-qam", "Other branch")
            git("checkout", "-q", main)
            (root / "guide.md").write_text("Main branch\n")
            git("commit", "-qam", "Main branch")
            merge = subprocess.run(["git", "merge", "other"], cwd=root, capture_output=True)
            self.assertEqual(merge.returncode, 1)
            (root / "guide.md").write_text(synthetic_path() + "\n")
            git("add", "guide.md")
            git("commit", "-qm", "Resolve merge")
            merge_commit = git("rev-parse", "HEAD")
            (root / "guide.md").write_text("Sanitized current text\n")
            git("commit", "-qam", "Cleanup")
            self.assertTrue(public_audit.audit_repository(root, baseline, check_commits=False)["passed"])
            findings = public_audit.audit_repository(root, baseline)["findings"]
            self.assertTrue(any(f["context"] == merge_commit and f.get("path") == "guide.md" for f in findings))


if __name__ == "__main__":
    unittest.main()
