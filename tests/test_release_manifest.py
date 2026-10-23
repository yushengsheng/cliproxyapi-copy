from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import release_manifest


class ReleaseManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "dist"
        self.directory.mkdir()
        (self.root / "CHANGELOG.md").write_text("## " + release_manifest.VERSION + "（待发布）\n\n- Fixture change\n", encoding="utf-8")
        self.reports = {}
        for target, system, architecture in release_manifest.TARGETS:
            archive = self.directory / f"CLIProxyAPI-Config-{target}-{release_manifest.VERSION}.zip"
            archive.write_bytes(target.encode())
            report = {"version": release_manifest.VERSION, "archive": archive.name,
                      "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "archive_crc": "ok",
                      "private_files": "none", "licenses": "ok", "source_match": "ok", "browser_capture": "ok",
                      "source_revision": os.environ.get("GITHUB_SHA"), "minimum_system_version": "14.0",
                      "runtime": {"version": release_manifest.VERSION, "platform": system,
                          "architecture": "AMD64" if system == "Windows" else architecture, "frozen": True,
                          **{key: "ok" for key in ("crypto", "migration", "credential_helper", "agents", "layout")}}}
            self.reports[archive] = report
        self.save_reports()

    def save_reports(self):
        for archive, report in self.reports.items():
            archive.with_suffix(".check.json").write_text(json.dumps(report), encoding="utf-8")

    def test_complete_native_reports_generate_checksums_and_notes(self):
        self.assertEqual(release_manifest.prepare_release(self.directory, self.root), release_manifest.VERSION)
        self.assertEqual(len((self.directory / "SHA256SUMS").read_text().splitlines()), 3)
        notes = (self.root / "release-notes.md").read_text(encoding="utf-8")
        self.assertIn("Fixture change", notes)
        self.assertIn("WebView2", notes)

    def test_missing_or_extra_platform_and_modified_package_stop_release(self):
        archive = next(iter(self.reports))
        content = archive.read_bytes()
        archive.unlink()
        with self.assertRaises(RuntimeError):
            release_manifest.prepare_release(self.directory, self.root)
        archive.write_bytes(content + b"changed")
        with self.assertRaises(RuntimeError):
            release_manifest.prepare_release(self.directory, self.root)
        archive.write_bytes(content)
        (self.directory / "unexpected.zip").write_bytes(b"unexpected")
        with self.assertRaises(RuntimeError):
            release_manifest.prepare_release(self.directory, self.root)

    def test_incomplete_or_mismatched_reports_stop_before_generating_release_files(self):
        archive = next(iter(self.reports))
        original = deepcopy(self.reports[archive])
        changes = [lambda r: r.update(browser_capture="failed"), lambda r: r.update(licenses="missing"),
                   lambda r: r.update(source_match="failed"), lambda r: r.update(archive="wrong.zip"),
                   lambda r: r.update(private_files="found"), lambda r: r.update(minimum_system_version=None),
                   lambda r: r["runtime"].update(frozen=False), lambda r: r["runtime"].update(agents="failed"),
                   lambda r: r["runtime"].update(version="old"), lambda r: r["runtime"].update(architecture="x86_64"),
                   lambda r: r["runtime"].update(platform="Windows")]
        for change in changes:
            with self.subTest(change=change):
                self.reports[archive] = deepcopy(original)
                change(self.reports[archive])
                self.save_reports()
                with self.assertRaises(RuntimeError):
                    release_manifest.prepare_release(self.directory, self.root)
                self.assertFalse((self.root / "release-notes.md").exists())
                self.assertFalse((self.directory / "SHA256SUMS").exists())

    def test_development_versions_missing_changelog_and_wrong_revision_stop_release(self):
        with mock.patch.object(release_manifest, "VERSION", "0.2.0-dev"), self.assertRaises(RuntimeError):
            release_manifest.prepare_release(self.directory, self.root)
        with mock.patch.dict(os.environ, {"GITHUB_SHA": "different-source"}), self.assertRaises(RuntimeError):
            release_manifest.prepare_release(self.directory, self.root)
        (self.root / "CHANGELOG.md").write_text("## 0.0.0\n\nOld notes\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            release_manifest.prepare_release(self.directory, self.root)


if __name__ == "__main__":
    unittest.main()
