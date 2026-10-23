import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts import local_app


class LocalSourceLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "源码 $' 中文 空格"
        (self.root / 'scripts').mkdir(parents=True)
        (self.root / 'scripts/local_app.py').write_bytes(Path(local_app.__file__).read_bytes())
        self.app = local_app.create_launcher(self.root)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.report = self.base / 'source report.json'

    def tearDown(self):
        self.temp.cleanup()

    def prepare_runtime(self):
        executable = self.root / '.venv/bin/python'
        executable.parent.mkdir(parents=True)
        executable.symlink_to(sys.executable)

    def write_source(self, marker):
        (self.root / 'launch.py').write_text(
            'import json, os, sys\nfrom pathlib import Path\n'
            'report = Path(sys.argv[sys.argv.index("--self-check-report") + 1])\n'
            'report.write_text(json.dumps({"marker": ' + repr(marker) + ', '
            '"frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable, '
            '"source": __file__, "cwd": os.getcwd(), "argv": sys.argv[1:]}), encoding="utf-8")\n',
            encoding='utf-8')

    def launch(self, *extra):
        return subprocess.run([str(self.app / 'Contents/MacOS/launcher'), '--self-check',
            '--self-check-report', str(self.report), *extra], cwd=self.base,
            env=dict(os.environ, HOME=str(self.home)), capture_output=True, text=True, timeout=15)

    def test_app_is_only_a_small_launcher_not_a_program_package(self):
        with mock.patch.object(local_app.subprocess, 'run', side_effect=AssertionError('must not build')):
            local_app.create_launcher(self.root)
        contents = {path.relative_to(self.app).as_posix() for path in self.app.rglob('*') if path.is_file()}
        self.assertEqual(contents, {'Contents/Info.plist', 'Contents/MacOS/launcher'})
        self.assertLess(sum(path.stat().st_size for path in self.app.rglob('*') if path.is_file()), 8192)
        info = plistlib.loads((self.app / 'Contents/Info.plist').read_bytes())
        self.assertEqual(info['CFBundleIdentifier'], local_app.BUNDLE_ID)
        script = (self.app / 'Contents/MacOS/launcher').read_text(encoding='utf-8')
        self.assertNotIn(str(self.root), script)
        self.assertNotIn('PyInstaller', script)
        self.assertIn('.venv/bin/python', script)
        self.assertTrue(os.access(self.app / 'Contents/MacOS/launcher', os.X_OK))

    @unittest.skipUnless(os.name == 'posix', 'macOS source launch uses a POSIX executable')
    def test_same_app_reads_modified_source_without_regenerating(self):
        self.prepare_runtime()
        self.write_source('first')
        before = {path: path.read_bytes() for path in self.app.rglob('*') if path.is_file()}
        result = self.launch('argument with spaces', '$(must-not-run)')
        self.assertEqual(result.returncode, 0, result.stderr)
        first = json.loads(self.report.read_text(encoding='utf-8'))
        self.assertEqual(first['marker'], 'first')
        self.assertFalse(first['frozen'])
        self.assertEqual(first['source'], str(self.root / 'launch.py'))
        self.assertEqual(first['cwd'], str(self.root))
        self.assertEqual(first['executable'], str(self.root / '.venv/bin/python'))
        self.assertEqual(first['argv'][-2:], ['argument with spaces', '$(must-not-run)'])
        self.write_source('second')
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(json.loads(self.report.read_text(encoding='utf-8'))['marker'], 'second')
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    @unittest.skipUnless(os.name == 'posix', 'macOS source launch uses a POSIX executable')
    def test_whole_project_can_move_without_recreating_the_app(self):
        self.prepare_runtime()
        self.write_source('moved')
        destination = self.base / '移动 后的目录'
        self.root.rename(destination)
        self.root = destination
        self.app = destination / local_app.APP_NAME
        self.assertEqual(self.launch().returncode, 0)
        report = json.loads(self.report.read_text(encoding='utf-8'))
        self.assertEqual(report['cwd'], str(destination))
        self.assertEqual(report['source'], str(destination / 'launch.py'))

    @unittest.skipUnless(os.name == 'posix', 'macOS source launch uses a POSIX executable')
    def test_missing_runtime_is_reported_and_does_not_start_another_python(self):
        self.write_source('must not run')
        self.assertNotEqual(self.launch().returncode, 0)
        self.assertFalse(self.report.exists())
        log = self.home / 'Library/Logs/CLIProxyAPI-Config/local-launcher.log'
        self.assertIn('.venv/bin/python', log.read_text(encoding='utf-8'))
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(os.name == 'posix', 'macOS source launch uses a POSIX executable')
    def test_source_startup_error_has_an_explicit_failure_and_log(self):
        self.prepare_runtime()
        (self.root / 'launch.py').write_text('raise RuntimeError("synthetic startup failure")\n', encoding='utf-8')
        self.assertNotEqual(self.launch().returncode, 0)
        log = self.home / 'Library/Logs/CLIProxyAPI-Config/local-launcher.log'
        self.assertIn('RuntimeError: synthetic startup failure', log.read_text(encoding='utf-8'))

    def test_normal_launch_failure_shows_a_native_error_dialog(self):
        with mock.patch.object(local_app.runpy, 'run_path', side_effect=RuntimeError('fixture')), \
             mock.patch.object(local_app.os, 'chdir'), mock.patch.object(local_app.sys, 'path', []), \
             mock.patch.object(local_app.sys, 'argv', []), mock.patch.object(local_app.traceback, 'print_exc'), \
             mock.patch.object(local_app.subprocess, 'run') as notify, self.assertRaises(SystemExit):
            local_app.run_source(self.root, [])
        self.assertEqual(notify.call_args.args[0][0], '/usr/bin/osascript')
        self.assertIn('local-launcher.log', notify.call_args.args[0][-1])

    def test_replacement_failure_restores_the_existing_app(self):
        original = (self.app / 'Contents/Info.plist').read_bytes()
        rename = Path.rename
        def fail_staged(path, destination):
            if path.name == local_app.APP_NAME and path.parent != self.root:
                raise OSError('synthetic install failure')
            return rename(path, destination)
        with mock.patch.object(Path, 'rename', fail_staged), self.assertRaises(OSError):
            local_app.create_launcher(self.root)
        self.assertEqual((self.app / 'Contents/Info.plist').read_bytes(), original)
        self.assertFalse(list(self.root.glob('.local-launcher-*')))


if __name__ == '__main__':
    unittest.main()
