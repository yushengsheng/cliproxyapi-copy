from __future__ import annotations

from pathlib import Path
import tempfile
import time
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from cliproxy_config.crypto import decrypt, encrypt
from cliproxy_config.config import apply_connection
from cliproxy_config.gui import App, PREVIEW_CHARACTERS
from cliproxy_config.crypto import MAX_FILE_BYTES
from test_config import PASSWORD, connection


class DesktopFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.app = App(auto_prompt=False)
        self.app.withdraw()
        self.app.directory.set(str(self.root / "client"))
        self.error = mock.patch("cliproxy_config.gui.messagebox.showerror").start()
        self.info = mock.patch("cliproxy_config.gui.messagebox.showinfo").start()
        self.content = encrypt(connection(), PASSWORD)

    def tearDown(self):
        self.app.destroy()
        mock.patch.stopall()
        self.temp.cleanup()

    def complete(self):
        deadline = time.monotonic() + 8
        while self.app.busy and time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.01)
        self.app.update()
        self.assertFalse(self.app.busy, "界面后台操作超时")

    def unlock(self):
        with mock.patch.object(self.app, "clipboard_get", return_value=self.content):
            self.app._paste()
        self.app.import_password.set(PASSWORD)
        self.app._decrypt()
        self.complete()

    def test_paste_unlock_apply_and_invalidate_on_password_change(self):
        self.unlock()
        self.assertEqual(self.app.imported, connection())
        self.assertEqual(str(self.app.apply_button.cget("state")), "normal")
        self.app._apply()
        self.complete()
        self.assertTrue((self.root / "client" / "config.toml").is_file())
        self.app.import_password.set("changed")
        self.assertIsNone(self.app.imported)
        self.assertEqual(str(self.app.apply_button.cget("state")), "disabled")
        self.error.assert_not_called()

    def test_drop_filename_with_spaces_and_nonascii_then_wrong_password(self):
        path = self.root / "中文 空格'线路.cproxy"
        path.write_text(self.content)
        self.app._drop(SimpleNamespace(data="{" + str(path) + "}"))
        self.complete()
        self.assertEqual(self.app.file_content, self.content.encode())
        self.app.import_password.set("wrong-password")
        self.app._decrypt()
        self.complete()
        self.error.assert_called_once()
        self.assertIsNone(self.app.imported)
        self.assertFalse((self.root / "client").exists())
        self.app.import_password.set(PASSWORD)
        self.app._decrypt()
        self.complete()
        self.assertEqual(self.app.imported, connection())

    def test_editing_text_disables_previously_unlocked_configuration(self):
        self.unlock()
        self.app.import_text.insert("end", "tamper")
        self.app.update()
        self.assertIsNone(self.app.imported)
        self.assertEqual(str(self.app.apply_button.cget("state")), "disabled")

    def test_file_clipboard_import_and_multi_file_drop(self):
        path = self.root / "copy.cproxy"
        path.write_text(self.content)
        with mock.patch.object(self.app, "clipboard_get", return_value=""), mock.patch("cliproxy_config.gui.clipboard_file", return_value=str(path)):
            self.app._paste()
        self.complete()
        self.assertEqual(self.app.file_content, self.content.encode())
        self.app._drop(SimpleNamespace(data="{" + str(path) + "} {" + str(path) + "}"))
        self.error.assert_called_once()

    def test_export_copies_encrypted_text_without_touching_clipboard(self):
        self.app.export_password.set(PASSWORD)
        self.app.export_confirm.set(PASSWORD)
        with mock.patch("cliproxy_config.gui.export_connection", return_value=(connection(), self.content)), \
             mock.patch.object(self.app, "clipboard_clear") as clear, \
             mock.patch.object(self.app, "clipboard_append") as append:
            self.app._export(True)
            self.complete()
            clear.assert_called_once()
            self.assertEqual(decrypt(append.call_args.args[0], PASSWORD), connection())

    def test_file_export_rejects_client_config_and_saves_a_usable_encrypted_file(self):
        directory = self.root / "client"
        apply_connection(directory, connection(), PASSWORD)
        nested = directory / "nested"
        nested.mkdir()
        original = (directory / "config.toml").read_bytes()
        self.app.export_password.set(PASSWORD)
        self.app.export_confirm.set(PASSWORD)
        with mock.patch("cliproxy_config.gui.filedialog.asksaveasfilename", return_value=str(nested / ".." / "config.toml")):
            self.app._export(False)
            self.complete()
        self.error.assert_called_once()
        self.assertEqual((directory / "config.toml").read_bytes(), original)
        destination = self.root / "导出线路.cproxy"
        with mock.patch("cliproxy_config.gui.filedialog.asksaveasfilename", return_value=str(destination)):
            self.app._export(False)
            self.complete()
        self.assertEqual(decrypt(destination.read_bytes(), PASSWORD), connection())
        self.assertEqual(self.app.local_payload, connection())

    def test_failed_refresh_clears_previously_read_connection(self):
        with mock.patch("cliproxy_config.gui.read_connection", return_value=connection()):
            self.app._read_local()
            self.complete()
        self.assertEqual(self.app.local_payload, connection())
        with mock.patch("cliproxy_config.gui.read_connection", side_effect=OSError("fixture")):
            self.app._read_local()
            self.complete()
        self.assertIsNone(self.app.local_payload)
        self.assertEqual(str(self.app.local_test.cget("state")), "disabled")
        self.assertTrue(all(field.get() == "尚未读取" for field in self.app.export_summary))
        self.error.assert_called_once()

    def test_apply_invalidates_previously_read_connection(self):
        self.unlock()
        self.app.local_payload = connection()
        self.app.local_test.configure(state="normal")
        self.app._apply()
        self.complete()
        self.assertIsNone(self.app.local_payload)
        self.assertEqual(str(self.app.local_test.cget("state")), "disabled")
        self.error.assert_not_called()

    def test_malformed_clipboard_file_url_preserves_previous_input(self):
        self.unlock()
        with mock.patch.object(self.app, "clipboard_get", return_value="file://["), \
             mock.patch("cliproxy_config.gui.clipboard_file", return_value=None):
            self.app._paste()
        self.assertEqual(self.app.file_content, self.content)
        self.assertEqual(self.app.imported, connection())
        self.error.assert_called_once()

    def test_default_window_fits_all_controls(self):
        self.app.update_idletasks()
        self.assertLessEqual(self.app.winfo_reqheight(), self.app.minsize()[1])

    def test_client_picker_uses_an_application_list_instead_of_a_file_dialog(self):
        from cliproxy_config.client import ClientApplication
        from tkinter import ttk
        application = self.root / "自定义安装位置" / "ChatGPT.app"
        candidate = ClientApplication(application, "ChatGPT")
        with mock.patch("cliproxy_config.gui.application_list", return_value=[candidate]), \
             mock.patch("cliproxy_config.gui.filedialog.askopenfilename") as file_dialog, \
             mock.patch("cliproxy_config.gui.resolve_client", return_value=(application, self.root / "client")), \
             mock.patch.object(self.app, "_read_local") as read:
            self.app._choose_client()
            self.complete()
            dialog = next(widget for widget in self.app.winfo_children() if widget.winfo_class() == "Toplevel")
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            button = next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Button) and "ChatGPT" in widget.cget("text"))
            button.invoke()
            self.assertEqual(self.app.client_app.get(), "ChatGPT")
            self.assertEqual(self.app.directory.get(), str(self.root / "client"))
            read.assert_called_once()
            file_dialog.assert_not_called()

    def test_large_clipboard_keeps_full_content_and_renders_a_bounded_preview(self):
        payload = connection()
        payload["catalog"]["models"][0]["description"] = "fixture-only-" * 160000
        content = encrypt(payload, PASSWORD)
        self.assertGreater(len(content), 2 * 1024 * 1024)
        self.app.deiconify()
        self.app.notebook.select(1)
        self.app.update()
        ticks = []
        self.app.after(0, lambda: ticks.append(True))
        started = time.monotonic()
        with mock.patch.object(self.app, "clipboard_get", return_value=content):
            self.app._paste()
            self.app.update()
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertTrue(ticks, "粘贴后界面事件循环必须继续运行")
        self.assertEqual(self.app.file_content, content)
        self.assertLessEqual(len(self.app.import_text.get("1.0", "end-1c")), PREVIEW_CHARACTERS)
        self.app.import_password.set(PASSWORD)
        self.app._decrypt()
        self.complete()
        self.assertEqual(self.app.imported, payload)
        self.error.assert_not_called()

    def test_paste_virtual_event_is_intercepted_before_text_class_insertion(self):
        content = '{"format":"cliproxy-config","data":"' + "fixture" * 150000 + '"}'
        self.app.deiconify()
        self.app.notebook.select(1)
        self.app.update()
        with mock.patch.object(self.app, "clipboard_get", return_value=content):
            self.app.import_text.event_generate("<<Paste>>")
            self.app.update()
        self.assertEqual(self.app.file_content, content)
        self.assertEqual(self.app.import_text.get("1.0", "end-1c"), content[:PREVIEW_CHARACTERS])

    def test_keyboard_paste_is_intercepted_before_text_class_insertion(self):
        content = '{"format":"cliproxy-config","data":"' + "fixture" * 150000 + '"}'
        self.app.deiconify()
        self.app.notebook.select(1)
        self.app.import_text.focus_force()
        self.app.update()
        with mock.patch.object(self.app, "clipboard_get", return_value=content):
            self.app.import_text.event_generate("<Command-v>" if sys.platform == "darwin" else "<Control-v>")
            self.app.update()
        self.assertEqual(self.app.file_content, content)
        self.assertEqual(self.app.import_text.get("1.0", "end-1c"), content[:PREVIEW_CHARACTERS])

    def test_oversized_paste_is_rejected_without_discarding_previous_input(self):
        self.unlock()
        old_content = self.app.file_content
        with mock.patch.object(self.app, "clipboard_get", return_value="{" + "x" * MAX_FILE_BYTES):
            self.app._paste()
        self.error.assert_called_once()
        self.assertEqual(self.app.file_content, old_content)
        self.assertEqual(self.app.imported, connection())


if __name__ == "__main__":
    unittest.main()
