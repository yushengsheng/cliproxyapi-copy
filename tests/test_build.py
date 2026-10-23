from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock

from scripts import build


class BuildTests(unittest.TestCase):
    def test_windows_executable_has_the_same_product_and_file_version(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(build, "ROOT", Path(temp)):
            path = build.windows_version_file()
            source = path.read_text(encoding='utf-8')
            self.assertIn("'FileVersion', '" + build.VERSION + "'", source)
            self.assertIn("'ProductVersion', '" + build.VERSION + "'", source)
            self.assertIn("CLIProxyAPI-Config.exe", source)
            compile(source, str(path), 'eval')

    def test_macos_minimum_matches_bundled_library_requirements(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            for name in ("python.dylib", "tkdnd.dylib"):
                (bundle / name).write_bytes(bytes.fromhex("cffaedfe"))
            (bundle / "readme.txt").write_text("fixture", encoding="utf-8")
            def inspect(command, **kwargs):
                if command[-1].endswith("tkdnd.dylib"):
                    return mock.Mock(stdout="cmd LC_BUILD_VERSION\nminos 14.0\n")
                return mock.Mock(stdout="cmd LC_VERSION_MIN_MACOSX\ncmdsize 16\nversion 10.15\n")
            with mock.patch.object(build.subprocess, "run", side_effect=inspect):
                self.assertEqual(build.minimum_macos_version(bundle), "14.0")

    def test_mac_archive_name_matches_requested_architecture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root / "dist/CLIProxyAPI-Config.app"
            info = bundle / "Contents/Info.plist"
            info.parent.mkdir(parents=True)
            info.write_bytes(plistlib.dumps({}))
            with mock.patch.object(build, "ROOT", root), \
                 mock.patch.object(build.sys, "platform", "darwin"), \
                 mock.patch.object(build.sys, "argv", ["build.py", "--target-arch", "universal2"]), \
                 mock.patch.object(build.platform, "machine", return_value="arm64"), \
                 mock.patch.object(build, "ensure_static_crypto"), \
                 mock.patch.object(build, "run") as run, mock.patch("builtins.print"):
                build.main()
            self.assertTrue(run.call_args.args[0][-1].endswith(f"macOS-universal2-{build.VERSION}.zip"))


if __name__ == "__main__":
    unittest.main()
