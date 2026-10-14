"""Native desktop UI; slow cryptography and network work stay off the UI thread."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import urllib.parse

from tkinterdnd2 import DND_FILES, TkinterDnD

from . import VERSION
from .client import application_list, resolve_client
from .config import (apply_connection, check_connection, export_connection,
                     read_bytes, read_connection, restore_backup, validate_connection)
from .crypto import ConfigError, MAX_FILE_BYTES, decrypt

TITLE = "CLIProxyAPI 配置助手"
PREVIEW_CHARACTERS = 512


def clipboard_file() -> str | None:
    if sys.platform == "darwin":
        try:
            result = subprocess.run(["/usr/bin/osascript", "-e", "POSIX path of (the clipboard as alias)"],
                                    capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    elif sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        user, shell = ctypes.windll.user32, ctypes.windll.shell32
        user.GetClipboardData.argtypes = [wintypes.UINT]
        user.GetClipboardData.restype = wintypes.HANDLE
        shell.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        shell.DragQueryFileW.restype = wintypes.UINT
        if user.OpenClipboard(None):
            try:
                handle = user.GetClipboardData(15)  # CF_HDROP
                if handle and shell.DragQueryFileW(handle, 0xFFFFFFFF, None, 0) == 1:
                    size = shell.DragQueryFileW(handle, 0, None, 0)
                    buffer = ctypes.create_unicode_buffer(size + 1)
                    shell.DragQueryFileW(handle, 0, buffer, size + 1)
                    return buffer.value
            finally:
                user.CloseClipboard()
    return None


class App(TkinterDnD.Tk):
    def __init__(self, auto_prompt=True):
        super().__init__()
        self.title(TITLE)
        self.geometry("860x760")
        self.minsize(800, 740)
        self.configure(background="#f4f6f8")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.busy = False
        self.widgets = []
        self.local_payload = None
        self.imported = None
        self.unlocked_password = None
        self.file_content = None
        self._editing = False
        self.directory = tk.StringVar()
        self.client_app = tk.StringVar(value="点击图标选择客户端")
        self.directory_hint = tk.StringVar(value="选择后自动读取本机线路")
        self.client_icon = None
        self.export_password = tk.StringVar()
        self.export_confirm = tk.StringVar()
        self.import_password = tk.StringVar()
        self.status = tk.StringVar(value="就绪 · 配置在本机处理")
        self._style()
        self._layout()
        self.directory.trace_add("write", self._directory_changed)
        self.import_password.trace_add("write", self._import_changed)
        self.bind("<Command-v>" if sys.platform == "darwin" else "<Control-v>", self._paste_shortcut)
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self._drop)
        if auto_prompt:
            self.after(100, self._choose_client)

    def _style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        font = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI"
        self.option_add("*Font", (font, 10))
        style.configure("TFrame", background="#f4f6f8")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f4f6f8", foreground="#1c2735", font=(font, 10))
        style.configure("Card.TLabel", background="#ffffff", font=(font, 10))
        style.configure("Muted.TLabel", foreground="#627082", font=(font, 9))
        style.configure("Title.TLabel", font=(font, 23, "bold"), foreground="#182633")
        style.configure("Heading.TLabel", font=(font, 13, "bold"))
        style.configure("TButton", padding=(14, 7), font=(font, 10), borderwidth=0)
        style.map("TButton", background=[("active", "#e5eaf0"), ("!disabled", "#eaf0f5")])
        style.configure("Primary.TButton", foreground="white")
        style.map("Primary.TButton", background=[("disabled", "#93aeb3"), ("active", "#176574"), ("!disabled", "#207789")],
                  foreground=[("disabled", "#eef3f4"), ("!disabled", "white")])
        style.configure("TEntry", padding=5)
        style.configure("TNotebook", background="#f4f6f8", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(24, 10), font=(font, 10))
        style.map("TNotebook.Tab", background=[("selected", "#ffffff"), ("!selected", "#e9eef3")],
                  foreground=[("selected", "#207789")])

    def _button(self, parent, title, command, primary=False):
        button = ttk.Button(parent, text=title, command=command,
                            style="Primary.TButton" if primary else "TButton")
        self.widgets.append(button)
        return button

    def _entry(self, parent, variable, password=False):
        entry = ttk.Entry(parent, textvariable=variable, show="•" if password else "")
        self.widgets.append(entry)
        return entry

    def _layout(self):
        shell = ttk.Frame(self, padding=(24, 10, 24, 10))
        shell.pack(fill="both", expand=True)
        header = ttk.Frame(shell)
        header.pack(fill="x")
        ttk.Label(header, text=TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="把当前线路带到另一台电脑", style="Muted.TLabel").pack(anchor="w", pady=(3, 10))
        folder = ttk.Frame(shell)
        folder.pack(fill="x", pady=(0, 10))
        ttk.Label(folder, text="要读取或应用配置的客户端").pack(anchor="w", pady=(0, 7))
        row = ttk.Frame(folder)
        row.pack(fill="x")
        self.client_icon_label = ttk.Label(row, text="◎", style="Heading.TLabel", width=4)
        self.client_icon_label.pack(side="left")
        ttk.Label(row, textvariable=self.client_app, style="Heading.TLabel").pack(side="left", fill="x", expand=True)
        self._button(row, "选择客户端", self._choose_client).pack(side="left", padx=(10, 0))
        ttk.Label(folder, textvariable=self.directory_hint, style="Muted.TLabel").pack(anchor="w", pady=(5, 0))
        self.notebook = ttk.Notebook(shell)
        self.notebook.pack(fill="both", expand=True)
        export_tab, import_tab = ttk.Frame(self.notebook, padding=14), ttk.Frame(self.notebook, padding=14)
        self.notebook.add(export_tab, text="导出本机配置")
        self.notebook.add(import_tab, text="导入并应用")
        self._export_tab(export_tab)
        self._import_tab(import_tab)
        footer = ttk.Frame(shell)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel", wraplength=570).pack(side="left")
        self._button(footer, "恢复加密备份", self._restore).pack(side="right")

    def _summary(self, parent):
        box = ttk.Frame(parent, style="Card.TFrame", padding=8)
        box.pack(fill="x", pady=(8, 8))
        fields = []
        for label in ("服务地址", "默认模型", "可选模型"):
            row = ttk.Frame(box, style="Card.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, width=11, style="Card.TLabel").pack(side="left", anchor="n")
            variable = tk.StringVar(value="尚未读取")
            ttk.Label(row, textvariable=variable, style="Card.TLabel", wraplength=570).pack(side="left", fill="x", expand=True)
            fields.append(variable)
        return fields

    def _export_tab(self, tab):
        row = ttk.Frame(tab)
        row.pack(fill="x")
        ttk.Label(row, text="读取当前正在使用的线路", style="Heading.TLabel").pack(side="left")
        self._button(row, "读取本机配置", self._read_local).pack(side="right")
        self.export_summary = self._summary(tab)
        ttk.Label(tab, text="设置加密密码").pack(anchor="w")
        self.export_entry = self._entry(tab, self.export_password, True)
        self.export_entry.pack(fill="x", pady=(6, 10))
        ttk.Label(tab, text="再次输入密码").pack(anchor="w")
        self.confirm_entry = self._entry(tab, self.export_confirm, True)
        self.confirm_entry.pack(fill="x", pady=(6, 8))
        show = tk.BooleanVar()
        toggle = ttk.Checkbutton(tab, text="显示密码", variable=show,
            command=lambda: [e.configure(show="" if show.get() else "•") for e in (self.export_entry, self.confirm_entry)])
        toggle.pack(anchor="w")
        self.widgets.append(toggle)
        ttk.Label(tab, text="至少 8 个字符，建议 12 个以上。请单独记住密码，程序无法找回。", style="Muted.TLabel").pack(anchor="w", pady=(8, 16))
        actions = ttk.Frame(tab)
        actions.pack(fill="x")
        self._button(actions, "加密并保存文件", lambda: self._export(False), True).pack(side="left")
        self._button(actions, "复制加密内容", lambda: self._export(True)).pack(side="left", padx=10)
        self.local_test = self._button(actions, "测试连接", lambda: self._test(self.local_payload))
        self.local_test.pack(side="right")
        self.local_test.configure(state="disabled")

    def _import_tab(self, tab):
        ttk.Label(tab, text="导入一份加密线路配置", style="Heading.TLabel").pack(anchor="w")
        self.drop_area = tk.Label(tab, text="将配置文件拖到这里", bg="#eaf3f4", fg="#207789",
                                  padx=20, pady=8, relief="flat")
        self.drop_area.pack(fill="x", pady=(8, 8))
        self.drop_area.drop_target_register(DND_FILES)
        self.drop_area.dnd_bind("<<Drop>>", self._drop)
        actions = ttk.Frame(tab)
        actions.pack(fill="x")
        self._button(actions, "选择配置文件", self._choose_import).pack(side="left")
        self._button(actions, "从剪贴板导入", self._paste).pack(side="left", padx=10)
        self._button(actions, "清空", self._clear_import).pack(side="right")
        self.file_label = tk.StringVar(value="也可以在下方粘贴加密内容")
        ttk.Label(tab, textvariable=self.file_label, style="Muted.TLabel", wraplength=720).pack(anchor="w", pady=(8, 5))
        self.import_text = tk.Text(tab, height=2, wrap="none", borderwidth=1, relief="solid",
                                   highlightthickness=0, padx=8, pady=8, undo=False)
        self.import_text.pack(fill="x")
        self.widgets.append(self.import_text)
        self.import_text.bind("<<Modified>>", self._text_changed)
        # Widget bindings run before Tk's Text class binding inserts the full clipboard.
        self.import_text.bind("<<Paste>>", self._paste_event)
        self.import_text.bind("<Command-v>" if sys.platform == "darwin" else "<Control-v>", self._paste_event)
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(10, 0))
        ttk.Label(row, text="解密密码").pack(side="left", padx=(0, 10))
        self.import_entry = self._entry(row, self.import_password, True)
        self.import_entry.pack(side="left", fill="x", expand=True)
        self._button(row, "解密配置", self._decrypt, True).pack(side="right", padx=(10, 0))
        self.import_summary = self._summary(tab)
        actions = ttk.Frame(tab)
        actions.pack(fill="x")
        self.apply_button = self._button(actions, "备份并应用", self._apply, True)
        self.apply_button.pack(side="left")
        self.apply_button.configure(state="disabled")
        self.import_test = self._button(actions, "测试连接", lambda: self._test(self.imported))
        self.import_test.pack(side="left", padx=10)
        self.import_test.configure(state="disabled")
        ttk.Label(tab, text="应用前请完全退出目标客户端，完成后重新打开。", style="Muted.TLabel").pack(anchor="w", pady=(10, 0))

    def _target(self):
        text = self.directory.get().strip()
        path = Path(text).expanduser()
        if not text or not path.is_absolute():
            raise ConfigError("请先选择 GPT / Codex 桌面客户端，程序会自动定位配置。")
        return path.absolute()

    def _choose_client(self):
        if self.busy:
            return
        self._run(application_list, self._show_client_list, "查找已安装的客户端…")

    def _show_client_list(self, applications):
        dialog = tk.Toplevel(self)
        dialog.title("选择客户端")
        dialog.geometry("560x310")
        dialog.resizable(False, False)
        if len(applications) > 3:
            dialog.geometry("560x" + str(310 + 110 * ((len(applications) - 1) // 3)))
        dialog.transient(self)
        body = ttk.Frame(dialog, padding=22)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="选择要配置的应用", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(body, text="点击应用图标即可，无需查找路径", style="Muted.TLabel").pack(anchor="w", pady=(5, 15))
        grid = ttk.Frame(body)
        grid.pack(fill="both", expand=True)
        dialog.icons = []
        def select(application, icon):
            dialog.destroy()
            try:
                self._set_client(application.path, application.name, icon)
                if self.notebook.index(self.notebook.select()) == 0:
                    self._read_local()
            except (ConfigError, OSError) as error:
                self._error(str(error))
        for index, application in enumerate(applications):
            icon = None
            if application.icon:
                try:
                    icon = tk.PhotoImage(master=self, data=application.icon)
                    dialog.icons.append(icon)
                except tk.TclError:
                    pass
            label = application.name
            if sum(row.name == application.name for row in applications) > 1:
                label += "\n" + application.path.parent.name
            button = ttk.Button(grid, text=label if icon else "◎\n" + label,
                                image=icon or "", compound="top", width=16,
                                command=lambda row=application, image=icon: select(row, image))
            button.grid(row=index // 3, column=index % 3, padx=(0, 12), pady=(0, 12), sticky="n")
        if not applications:
            ttk.Label(grid, text="未发现可配置的 GPT / Codex 桌面客户端。\n请先安装客户端，再点击刷新。",
                      style="Muted.TLabel").pack(anchor="w", pady=10)
        actions = ttk.Frame(body)
        actions.pack(fill="x")
        ttk.Button(actions, text="刷新列表", command=lambda: (dialog.destroy(), self._choose_client())).pack(side="left")
        ttk.Button(actions, text="关闭", command=dialog.destroy).pack(side="right")
        dialog.grab_set()
        dialog.focus_set()

    def _set_client(self, selected, name=None, icon=None):
        application, directory = resolve_client(Path(selected))
        self.client_app.set(name or application.stem)
        self.directory.set(str(directory))
        self.client_icon = icon
        self.client_icon_label.configure(image=icon or "", text="" if icon else "◎")
        self.directory_hint.set("已自动定位本机配置")
        self.status.set("已选择 " + (name or application.stem))

    def _directory_changed(self, *_):
        self.local_payload = None
        self.local_test.configure(state="disabled")
        for field in self.export_summary:
            field.set("尚未读取")

    def _show_summary(self, fields, payload):
        def shorten(value, limit):
            return value if len(value) <= limit else value[:limit - 1] + "…"
        for field, value in zip(fields, (shorten(payload["base_url"], 90), shorten(payload["model"], 64), str(len(payload["catalog"]["models"])) + " 个")):
            field.set(value)

    def _read_local(self):
        if self.busy:
            return
        try:
            target = self._target()
        except ConfigError as error:
            self._error(str(error)); return
        self._directory_changed()
        def done(payload):
            self.local_payload = payload
            self._show_summary(self.export_summary, payload)
            self.local_test.configure(state="normal")
            self.status.set("已读取本机线路 · API 密钥不在界面中显示")
        self._run(lambda: read_connection(target), done, "读取本机配置…")

    def _export(self, copy):
        try:
            target = self._target()
            password = self.export_password.get()
            if len(password) < 8:
                raise ConfigError("加密密码至少需要 8 个字符。")
            if password != self.export_confirm.get():
                raise ConfigError("两次输入的密码不一致。")
        except ConfigError as error:
            self._error(str(error)); return
        selected = None
        if not copy:
            selected = filedialog.asksaveasfilename(title="保存加密配置", defaultextension=".cproxy",
                initialfile="我的线路.cproxy", filetypes=[("加密线路配置", "*.cproxy")], parent=self)
            if not selected:
                return
        self._directory_changed()
        def worker():
            return export_connection(target, password, Path(selected) if selected else None)
        def done(result):
            payload, content = result
            self.local_payload = payload
            self._show_summary(self.export_summary, payload)
            self.local_test.configure(state="normal")
            if copy:
                self.clipboard_clear()
                self.clipboard_append(content)
                self.status.set("加密内容已复制 · 可到另一台电脑粘贴导入")
            else:
                self.status.set("已保存加密配置：" + selected)
        self._run(worker, done, "读取并加密配置…")

    def _choose_import(self):
        selected = filedialog.askopenfilename(title="选择加密线路配置", filetypes=[("加密线路配置", "*.cproxy"), ("所有文件", "*")], parent=self)
        if selected:
            self._load_file(selected)

    def _drop(self, event):
        if self.busy:
            return "break"
        try:
            paths = self.tk.splitlist(event.data)
            if len(paths) != 1:
                raise ConfigError("每次请导入一个配置文件。")
            if Path(paths[0]).suffix.lower() in (".app", ".exe"):
                self._set_client(paths[0])
            else:
                self._load_file(paths[0])
        except (ConfigError, tk.TclError) as error:
            self._error(str(error))
        return "break"

    def _load_file(self, selected):
        def done(content):
            self._clear_import()
            self.file_content = content
            self.file_label.set("已载入：" + Path(selected).name)
            self.status.set("配置文件已载入，请输入解密密码")
            self.notebook.select(1)
            self.import_entry.focus_set()
        self._run(lambda: read_bytes(Path(selected).expanduser().absolute(), MAX_FILE_BYTES), done, "载入加密文件…")

    def _paste_shortcut(self, event):
        if self.notebook.index(self.notebook.select()) == 1 and not isinstance(self.focus_get(), (tk.Entry, ttk.Entry)):
            self._paste()
            return "break"

    def _paste_event(self, _):
        self._paste()
        return "break"

    def _paste(self):
        if self.busy:
            return
        try:
            content = self.clipboard_get()
        except tk.TclError:
            content = ""
        if content.lstrip().startswith("{"):
            if len(content.encode("utf-8")) > MAX_FILE_BYTES:
                self._error("剪贴板内容超过大小限制。"); return
            self._clear_import()
            self.file_content = content
            self._editing = True
            self.import_text.insert("1.0", content[:PREVIEW_CHARACTERS])
            self.import_text.edit_modified(False)
            self._editing = False
            self.file_label.set("已粘贴完整加密内容（下方仅显示预览），请输入密码")
            self.notebook.select(1)
            self.import_entry.focus_set()
            return
        candidate = content.strip()
        try:
            if candidate.startswith("file://"):
                url = urllib.parse.urlsplit(candidate)
                candidate = urllib.parse.unquote(url.path) if not url.netloc or url.netloc == "localhost" else ""
                if sys.platform == "win32" and candidate.startswith("/"):
                    candidate = candidate[1:]
            path = Path(candidate) if len(candidate) <= 2048 and candidate else None
            if path and path.is_absolute() and path.is_file():
                self._load_file(str(path)); return
        except (OSError, ValueError):
            pass
        selected = clipboard_file()
        if selected:
            self._load_file(selected)
        else:
            self._error("剪贴板中没有加密配置内容或单个配置文件。")

    def _clear_import(self):
        self._editing = True
        self.import_text.delete("1.0", "end")
        self.import_text.edit_modified(False)
        self._editing = False
        self.file_content = None
        self.file_label.set("也可以在下方粘贴加密内容")
        self.import_password.set("")
        self._import_changed()

    def _text_changed(self, _):
        if self.import_text.edit_modified():
            self.import_text.edit_modified(False)
            if not self._editing:
                self.file_content = None
                self.file_label.set("使用下方输入的加密内容")
                self._import_changed()

    def _import_changed(self, *_):
        self.imported = None
        self.unlocked_password = None
        self.apply_button.configure(state="disabled")
        self.import_test.configure(state="disabled")
        for field in self.import_summary:
            field.set("尚未解密")

    def _decrypt(self):
        content = self.file_content if self.file_content is not None else self.import_text.get("1.0", "end").strip()
        password = self.import_password.get()
        if not content:
            self._error("请先选择文件、拖入文件或粘贴加密内容。"); return
        if not password:
            self._error("请输入解密密码。"); return
        def done(payload):
            self.imported = payload
            self.unlocked_password = password
            self._show_summary(self.import_summary, payload)
            self.apply_button.configure(state="normal")
            self.import_test.configure(state="normal")
            self.status.set("解密成功 · 可先测试连接，再应用到所选客户端")
        self._run(lambda: validate_connection(decrypt(content, password)), done, "验证密码并解密…")

    def _apply(self):
        if self.imported is None or not self.unlocked_password:
            return
        try:
            target = self._target()
        except ConfigError as error:
            self._error(str(error)); return
        payload, password = self.imported, self.unlocked_password
        def done(backup):
            self._directory_changed()
            self.status.set("已应用配置 · 请重新打开客户端")
            messagebox.showinfo("配置已应用", "请重新打开客户端，并新建会话使用这条线路。\n\n加密备份：\n" + str(backup)
                + "\n\n恢复时使用本次导入的解密密码。", parent=self)
        self._run(lambda: apply_connection(target, payload, password), done, "加密备份并应用配置…")

    def _restore(self):
        from tkinter.simpledialog import askstring
        try:
            target = self._target()
        except ConfigError as error:
            self._error(str(error)); return
        backup_dir = target / "cliproxy-config-backups"
        selected = filedialog.askopenfilename(title="选择原目录的加密恢复备份",
            initialdir=str(backup_dir if backup_dir.is_dir() else Path.home()),
            filetypes=[("加密恢复备份", "*.cproxybackup")], parent=self)
        if not selected:
            return
        password = askstring("恢复加密备份", "请输入生成这份备份时使用的密码。\n恢复前请完全退出客户端。", show="•", parent=self)
        if not password:
            return
        def done(backup):
            self._directory_changed()
            self.status.set("已恢复原配置 · 请重新打开客户端")
            messagebox.showinfo("已恢复", "原配置已恢复。恢复前的状态也已加密备份：\n" + str(backup), parent=self)
        self._run(lambda: restore_backup(target, read_bytes(Path(selected).absolute(), MAX_FILE_BYTES), password), done, "解密并恢复原配置…")

    def _test(self, payload):
        if payload is not None:
            self._run(lambda: check_connection(payload),
                      lambda count: self.status.set("连接成功 · 密钥有效，服务器提供 " + str(count) + " 个模型"),
                      "验证 HTTPS 接口、密钥及默认模型…")

    def _error(self, message):
        self.status.set("操作未完成")
        messagebox.showerror(TITLE, message, parent=self)

    def _run(self, worker, done, status):
        if self.busy:
            return
        self.busy = True
        states = [(widget, widget.cget("state")) for widget in self.widgets]
        for widget, _ in states:
            widget.configure(state="disabled")
        self.status.set(status)
        result = queue.Queue()
        def work():
            try:
                result.put((True, worker()))
            except ConfigError as error:
                result.put((False, str(error)))
            except PermissionError:
                result.put((False, "没有读取或写入权限，请检查所选目录及文件权限。"))
            except OSError:
                result.put((False, "文件操作失败，请检查路径、磁盘空间及目录是否可用。"))
            except Exception:
                result.put((False, "操作失败，配置未完成；如已生成备份，可使用恢复功能。"))
        def poll():
            try:
                success, value = result.get_nowait()
            except queue.Empty:
                self.after(80, poll)
                return
            self.busy = False
            for widget, state in states:
                widget.configure(state=state)
            if success:
                done(value)
            else:
                self._error(value)
        threading.Thread(target=work, daemon=True).start()
        self.after(80, poll)

    def _close(self):
        if self.busy:
            messagebox.showinfo(TITLE, "当前操作尚未完成，请等待后再关闭。", parent=self)
            return
        self.imported = self.local_payload = self.file_content = self.unlocked_password = None
        self.destroy()


def main():
    self_check = "--self-check" in sys.argv
    try:
        app = App(auto_prompt=not self_check)
    except (tk.TclError, RuntimeError):
        raise SystemExit("桌面组件未能启动。请使用自带运行环境的程序，或安装包含 Tk 的 Python 3.11 以上版本。") from None
    if self_check:
        import argparse
        from .self_check import run_self_check
        parser = argparse.ArgumentParser()
        parser.add_argument("--self-check", action="store_true")
        parser.add_argument("--self-check-report", type=Path)
        options = parser.parse_args()
        app.withdraw()
        failed = False
        try:
            report = run_self_check(app)
        except Exception as error:
            failed = True
            report = {"version": VERSION, "error": str(error)}
        finally:
            app.destroy()
        if options.self_check_report:
            options.self_check_report.parent.mkdir(parents=True, exist_ok=True)
            options.self_check_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        if failed:
            raise SystemExit(1)
        return
    app.deiconify()
    app.lift()
    app.focus_force()
    app.mainloop()

