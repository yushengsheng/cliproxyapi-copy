import unittest

from cliproxy_config.gui import App
from cliproxy_config.self_check import run_self_check
from cliproxy_config import VERSION


class RuntimeSelfCheckTests(unittest.TestCase):
    def test_native_credential_helper_and_migration_roundtrip(self):
        app = App(auto_prompt=False)
        app.withdraw()
        try:
            report = run_self_check(app)
        finally:
            app.destroy()
        self.assertEqual(report["version"], VERSION)
        for name in ("crypto", "migration", "credential_helper", "layout"):
            self.assertEqual(report[name], "ok")


if __name__ == "__main__":
    unittest.main()
