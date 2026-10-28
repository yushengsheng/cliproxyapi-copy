"""Native desktop workflow: browser source, selected models, native agent adapters."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import datetime as dt
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

from tkinterdnd2 import DND_FILES, DND_TEXT, TkinterDnD

from . import VERSION
from .agents import AGENTS, PI_PROVIDER, apply_agent, default_directory, prepare_import, preview_agent, restore_agent
from .config import (FILES, atomic_write, effective_model_settings, needs_codex_recovery,
                     read_bytes, validate_connection)
from .crypto import (ConfigError, MAX_FILE_BYTES, decode_document, encode_document, is_encrypted)
from .browser import BROWSERS, BrowserUnavailable
from .gateway import Gateway, fetch_models, make_connection, management_url, read_browser, test_model

TITLE = "CLIProxyAPI 配置助手"
# One quiet light palette for the main window and every dialog.
BG, CARD, LINE, FIELD_LINE, TEXT, MUTED, FAINT = (
    "#f3f5f8", "#ffffff", "#e3e8ee", "#d3dae3", "#1f2937", "#6b7280", "#a3abb7")
ACCENT, ACCENT_HOVER, ACCENT_PRESSED, ACCENT_DISABLED = "#2563eb", "#1d4ed8", "#1e40af", "#a9c0f3"
ACCENT_SOFT, SELECTED, STRIPE, HOVER = "#eef4ff", "#dbe6fd", "#f8fafc", "#f2f5f9"
SUCCESS, DANGER = "#15803d", "#b91c1c"
EMPTY_HINT = "尚未获取模型\n点击「获取全部模型」，或把模型节点文件拖入窗口"


class App(TkinterDnD.Tk):
    def __init__(self, management_address="", preferences_path=None):
        super().__init__()
        self.title(f"{TITLE} · {VERSION}")
        self.geometry("1040x800")
        self.minsize(960, 720)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.busy = False
        self.widgets = []
        self.gateway = None
        self.imported_payload = None
        self.selected = set()
        self.key_index = -1
        self.result_window = None
        self.last_import_plan = None
        self.last_import_outcome = None
        self.last_codex_recovery = False
        self.preferences_path = preferences_path or Path.home() / ".config/cliproxyapi-config/preferences.json"
        try:
            preferences = json.loads(read_bytes(self.preferences_path, 65536))
            if not isinstance(preferences, dict):
                preferences = {}
        except (ConfigError, OSError, ValueError):
            preferences = {}
        self.preferences = preferences
        # Older versions remembered Brave/Chrome before a permission-free entry existed.
        browser = preferences.get("browser", BROWSERS[0]) if preferences.get("browser_access_version") == 1 else BROWSERS[0]
        browser = browser if browser in BROWSERS else BROWSERS[0]
        self.url = tk.StringVar(value=management_address or str(preferences.get("url", "")))
        self.browser = tk.StringVar(value=browser)
        self.backup_enabled = tk.BooleanVar(value=preferences.get("backup_enabled", True) is not False)
        self.backup_encrypted = tk.BooleanVar(value=preferences.get("backup_encrypted", True) is not False)
        self.export_encrypted = tk.BooleanVar(value=preferences.get("export_encrypted", True) is not False)
        self.key_choice = tk.StringVar()
        self.search = tk.StringVar()
        self.model = tk.StringVar()
        self.agent = tk.StringVar(value=AGENTS["codex"].label)
        self.directory = tk.StringVar()
        self.password = tk.StringVar()
        self.confirm = tk.StringVar()
        self.count = tk.StringVar(value="尚未获取模型")
        self.status = tk.StringVar(value="从网页登录获取模型，或导入已有节点文件。")
        self._layout()
        try:
            self.directory.set(str(default_directory("codex")))
        except ConfigError as error:
            # An invalid CODEX_HOME must not prevent the window from opening.
            self.status.set(str(error) + "请手动选择配置目录。")
        self.status.trace_add("write", lambda *_: self.status_label.configure(foreground=TEXT))
        self.url.trace_add("write", self._source_changed)
        self.browser.trace_add("write", self._browser_changed)
        self.search.trace_add("write", lambda *_: self._render_models())
        self.model.trace_add("write", lambda *_: self._clear_preview())
        self.directory.trace_add("write", lambda *_: self._clear_preview())

    def _button(self, parent, text, command, primary=False):
        widget = ttk.Button(parent, text=text, command=command, style="Primary.TButton" if primary else "TButton")
        self.widgets.append(widget)
        return widget

    def _entry(self, parent, variable, **options):
        widget = ttk.Entry(parent, textvariable=variable, **options)
        self.widgets.append(widget)
        return widget

    def _combo(self, parent, variable, values=(), **options):
        widget = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", **options)
        self.widgets.append(widget)
        return widget

    def _check(self, parent, text, variable):
        widget = ttk.Checkbutton(parent, text=text, variable=variable)
        self.widgets.append(widget)
        return widget

    def _style(self):
        mac = sys.platform == "darwin"
        family, size = ("PingFang SC", 13) if mac else ("Microsoft YaHei UI", 10)
        self.fonts = {"body": (family, size), "small": (family, size - 1), "bold": (family, size, "bold"),
                      "title": (family, size + 6, "bold"), "mono": ("Menlo" if mac else "Consolas", size - 1)}
        self.option_add("*Font", self.fonts["body"])
        for option, value in (("background", CARD), ("foreground", TEXT),
                              ("selectBackground", SELECTED), ("selectForeground", TEXT)):
            self.option_add("*TCombobox*Listbox." + option, value)
        self.configure(background=BG)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=self.fonts["body"], bordercolor=LINE,
                        lightcolor=BG, darkcolor=BG, troughcolor=BG, focuscolor=ACCENT, insertcolor=TEXT,
                        selectbackground=SELECTED, selectforeground=TEXT)
        style.configure("Card.TFrame", background=CARD)
        for name, options in (("Card.TLabel", {"background": CARD}),
                              ("Muted.TLabel", {"foreground": MUTED, "font": self.fonts["small"]}),
                              ("CardMuted.TLabel", {"background": CARD, "foreground": MUTED, "font": self.fonts["small"]}),
                              ("Section.TLabel", {"background": CARD, "font": self.fonts["bold"]}),
                              ("Heading.TLabel", {"font": self.fonts["bold"]}),
                              ("Title.TLabel", {"font": self.fonts["title"]}),
                              ("Badge.TLabel", {"background": ACCENT_SOFT, "foreground": ACCENT,
                                                "font": self.fonts["small"], "padding": (8, 2)}),
                              ("Empty.TLabel", {"background": CARD, "foreground": FAINT})):
            style.configure(name, **options)

        def flat(name, base, hover, pressed, disabled, **options):
            # clam bevels with light/dark colors; matching them to the face keeps buttons flat.
            style.configure(name, background=base, lightcolor=base, darkcolor=base, **options)
            states = [("disabled", disabled), ("pressed", pressed), ("active", hover)]
            style.map(name, background=states, lightcolor=states, darkcolor=states)
        flat("TButton", CARD, HOVER, "#e6ebf1", BG, foreground=TEXT, bordercolor=FIELD_LINE, padding=(10, 4), width=0)
        style.map("TButton", foreground=[("disabled", FAINT)])
        flat("Primary.TButton", ACCENT, ACCENT_HOVER, ACCENT_PRESSED, ACCENT_DISABLED,
             foreground="white", bordercolor=ACCENT, font=self.fonts["bold"])
        style.map("Primary.TButton", foreground=[("disabled", "#eef3fd")], bordercolor=[
            ("disabled", ACCENT_DISABLED), ("pressed", ACCENT_PRESSED), ("active", ACCENT_HOVER)])
        focus = [("focus", ACCENT)]
        field = {"fieldbackground": CARD, "bordercolor": FIELD_LINE, "lightcolor": CARD, "darkcolor": CARD,
                 "padding": (8, 4)}
        style.configure("TEntry", **field)
        style.map("TEntry", bordercolor=focus, lightcolor=focus, fieldbackground=[("disabled", BG)],
                  foreground=[("disabled", FAINT)])
        style.configure("TCombobox", **field, background=CARD, arrowcolor=MUTED)
        style.map("TCombobox", bordercolor=focus, lightcolor=focus,
                  fieldbackground=[("disabled", BG), ("readonly", CARD)],
                  background=[("disabled", BG), ("active", HOVER), ("readonly", CARD)],
                  selectbackground=[("readonly", CARD)], selectforeground=[("readonly", TEXT)],
                  foreground=[("disabled", FAINT)], arrowcolor=[("disabled", FAINT)])
        checked = [("disabled", "selected", ACCENT_DISABLED), ("selected", ACCENT)]
        style.configure("TCheckbutton", background=CARD, indicatorbackground=CARD, indicatorforeground="white",
                        upperbordercolor=FIELD_LINE, lowerbordercolor=FIELD_LINE)
        style.map("TCheckbutton", background=[("active", CARD)], foreground=[("disabled", FAINT)],
                  indicatorbackground=checked + [("disabled", BG), ("pressed", HOVER)],
                  upperbordercolor=checked, lowerbordercolor=checked)
        rowheight = tkfont.Font(self, font=self.fonts["body"]).metrics("linespace") + 12
        # Drop the treeview's own field border; each list sits inside a bordered card frame.
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT, rowheight=rowheight)
        style.map("Treeview", background=[("selected", SELECTED)], foreground=[("selected", TEXT)])
        style.configure("Treeview.Heading", background=STRIPE, foreground=MUTED, font=self.fonts["small"],
                        bordercolor=LINE, lightcolor=STRIPE, darkcolor=STRIPE, relief="flat", padding=(8, 5))
        style.map("Treeview.Heading", background=[("active", HOVER)], lightcolor=[("active", HOVER)],
                  darkcolor=[("active", HOVER)])
        for orient in ("Vertical", "Horizontal"):
            thumb = [("active", "#c5cdd8")]
            style.configure(orient + ".TScrollbar", background="#d9dfe7", troughcolor=CARD, bordercolor=CARD,
                            lightcolor="#d9dfe7", darkcolor="#d9dfe7", arrowcolor=MUTED, gripcount=0)
            style.map(orient + ".TScrollbar", background=thumb, lightcolor=thumb, darkcolor=thumb)
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor="#e5eaf1", bordercolor=CARD,
                        lightcolor=ACCENT, darkcolor=ACCENT, thickness=4)

    def _bordered(self, parent):
        return tk.Frame(parent, background=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)

    def _card(self, parent, title, note="", expand=False, **pack):
        border = self._bordered(parent)
        border.pack(fill="both" if expand else "x", expand=expand, **pack)
        body = ttk.Frame(border, style="Card.TFrame", padding=(14, 8, 14, 10))
        body.pack(fill="both", expand=True)
        head = ttk.Frame(body, style="Card.TFrame")
        head.pack(fill="x", pady=(0, 8))
        tk.Frame(head, background=ACCENT, width=3, height=15).pack(side="left", padx=(0, 8))
        ttk.Label(head, text=title, style="Section.TLabel").pack(side="left")
        if note:
            ttk.Label(head, text=note, style="CardMuted.TLabel").pack(side="left", padx=(12, 0))
        return body, head

    def _text(self, parent, **options):
        border = self._bordered(parent)
        text = tk.Text(border, relief="flat", borderwidth=0, highlightthickness=0, background=CARD, foreground=TEXT,
                       insertbackground=TEXT, selectbackground=SELECTED, selectforeground=TEXT,
                       font=self.fonts["mono"], padx=12, pady=10, **options)
        return border, text

    def _layout(self):
        self._style()
        # Pack the status bar and the bottom card first so a short window squeezes only the model list.
        bar = tk.Frame(self, background=CARD)
        bar.pack(side="bottom", fill="x")
        tk.Frame(bar, background=LINE, height=1).pack(fill="x")
        status = ttk.Frame(bar, style="Card.TFrame", padding=(20, 8))
        status.pack(fill="x")
        self.status_label = ttk.Label(status, textvariable=self.status, style="Card.TLabel", wraplength=880,
                                      justify="left")
        self.status_label.pack(side="left", fill="x", expand=True)
        status.bind("<Configure>", lambda event: self.status_label.configure(wraplength=max(event.width - 220, 320)))
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=120)
        shell = ttk.Frame(self, padding=(16, 14, 16, 12))
        shell.pack(fill="both", expand=True)
        header = ttk.Frame(shell)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text=VERSION, style="Badge.TLabel").pack(side="right", anchor="n")
        ttk.Label(header, text=TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="获取或导入模型 → 勾选 → 一键配置到常用软件", style="Muted.TLabel").pack(anchor="w")

        body, _ = self._card(shell, "模型来源", "内置窗口登录后自动读取，无需浏览器授权或 OpenCLI；也可导入、粘贴或拖入模型节点。",
                             pady=(0, 10))
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="后台地址", style="Card.TLabel").pack(side="left", padx=(0, 10))
        self._entry(row, self.url).pack(side="left", fill="x", expand=True)
        self._combo(row, self.browser, BROWSERS, width=12).pack(side="left", padx=(8, 0))
        self._button(row, "获取全部模型", self._fetch, True).pack(side="left", padx=(8, 0))
        self._button(row, "导入模型", self._import).pack(side="left", padx=(8, 0))

        body, _ = self._card(shell, "配置到软件", "导入前完全退出目标软件，完成后重开并新建会话。", side="bottom", pady=(10, 0))
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="目标软件", style="Card.TLabel").pack(side="left", padx=(0, 10))
        self.agent_combo = self._combo(row, self.agent, tuple(a.label for a in AGENTS.values()), width=24)
        self.agent_combo.pack(side="left")
        self.agent_combo.bind("<<ComboboxSelected>>", self._agent_changed)
        ttk.Label(row, text="默认模型", style="Card.TLabel").pack(side="left", padx=(20, 10))
        self.defaults = self._combo(row, self.model)
        self.defaults.pack(side="left", fill="x", expand=True)
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="配置目录", style="Card.TLabel").pack(side="left", padx=(0, 10))
        self._entry(row, self.directory).pack(side="left", fill="x", expand=True)
        self._button(row, "更改", self._choose_directory).pack(side="left", padx=(8, 0))
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x", pady=(10, 0))
        self._check(row, "备份旧配置", self.backup_enabled).pack(side="left")
        self._check(row, "加密备份", self.backup_encrypted).pack(side="left", padx=(12, 0))
        self._button(row, "恢复备份", self._restore).pack(side="left", padx=(12, 0))
        self._button(row, "一键导入", self._apply, True).pack(side="right")
        for text, command in (("复制接口信息", self._copy_access), ("实测默认模型", self._test), ("预览配置", self._preview)):
            self._button(row, text, command).pack(side="right", padx=(0, 6))

        body, head = self._card(shell, "选择模型", expand=True)
        self._entry(head, self.search, width=22).pack(side="right")
        ttk.Label(head, text="搜索", style="Card.TLabel").pack(side="right", padx=(16, 8))
        self.keys = self._combo(head, self.key_choice, width=22)
        self.keys.pack(side="right")
        self.keys.bind("<<ComboboxSelected>>", self._key_changed)
        ttk.Label(head, text="客户端密钥", style="Card.TLabel").pack(side="right", padx=(0, 8))
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(side="bottom", fill="x", pady=(10, 0))
        self._button(row, "全选", lambda: self._select_all(True)).pack(side="left")
        self._button(row, "取消勾选", lambda: self._select_all(False)).pack(side="left", padx=(8, 0))
        ttk.Label(row, textvariable=self.count, style="CardMuted.TLabel").pack(side="left", padx=(12, 0))
        self._button(row, "导出模型", self._export).pack(side="right")
        self._check(row, "加密导出", self.export_encrypted).pack(side="right", padx=(0, 12))
        frame = self._bordered(body)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("selected", "model", "owner"), show="headings", selectmode="browse", height=3)
        for column, text, width in (("selected", "勾选", 60), ("model", "模型名称", 610), ("owner", "来源", 150)):
            self.tree.heading(column, text=text, anchor="center" if column == "selected" else "w")
            self.tree.column(column, width=width, minwidth=50, stretch=column == "model",
                             anchor="center" if column == "selected" else "w")
        self.tree.tag_configure("odd", background=STRIPE)
        self.tree.tag_configure("checked", background=ACCENT_SOFT)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.empty = ttk.Label(frame, text=EMPTY_HINT, style="Empty.TLabel", justify="center")
        self.empty.place(relx=.5, rely=.55, anchor="center")
        self.tree.bind("<Button-1>", self._toggle_click)
        self.tree.bind("<space>", self._toggle_keyboard)
        for widget in (self, self.tree):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._drop_files)

        self.preview_window = tk.Toplevel(self, background=BG)
        self.preview_window.title("连接配置预览 · 密钥已隐藏")
        self.preview_window.geometry("800x560")
        self.preview_window.withdraw()
        self.preview_window.protocol("WM_DELETE_WINDOW", self.preview_window.withdraw)
        border, self.preview = self._text(self.preview_window, wrap="none", state="disabled")
        border.pack(fill="both", expand=True, padx=14, pady=14)
        preview_scroll = ttk.Scrollbar(border, orient="vertical", command=self.preview.yview)
        preview_horizontal = ttk.Scrollbar(border, orient="horizontal", command=self.preview.xview)
        self.preview.configure(yscrollcommand=preview_scroll.set, xscrollcommand=preview_horizontal.set)
        preview_scroll.pack(side="right", fill="y")
        preview_horizontal.pack(side="bottom", fill="x")
        self.preview.pack(fill="both", expand=True)

    def _agent_id(self):
        return next(key for key, value in AGENTS.items() if value.label == self.agent.get())

    def _clear_preview(self):
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.configure(state="disabled")

    def _source_changed(self, *_):
        self.gateway = None
        self.imported_payload = None
        self.selected.clear()
        self.key_index = -1
        self.key_choice.set("")
        self.keys.configure(values=())
        self.model.set("")
        self.defaults.configure(values=())
        self._render_models()
        self._clear_preview()

    def _browser_changed(self, *_):
        self._source_changed()

    def _render_models(self):
        self.tree.delete(*self.tree.get_children())
        rows = self.gateway.models if self.gateway else ()
        query = self.search.get().strip().lower()
        shown = 0
        for index, row in enumerate(rows):
            if query and query not in (row["id"] + " " + row["owned_by"]).lower():
                continue
            checked = row["id"] in self.selected
            self.tree.insert("", "end", iid=str(index), values=("☑" if checked else "☐", row["id"], row["owned_by"]),
                             tags=("checked" if checked else "odd" if shown % 2 else "even",))
            shown += 1
        self.count.set(f"共 {len(rows)} 个 · 已勾选 {len(self.selected)} 个" if rows else "尚未获取模型")
        if shown:
            self.empty.place_forget()
        else:
            self.empty.configure(text="没有匹配的模型" if rows else EMPTY_HINT)
            self.empty.place(relx=.5, rely=.55, anchor="center")

    def _selection_changed(self):
        position = self.tree.yview()[0]
        choices = [row["id"] for row in self.gateway.models if row["id"] in self.selected] if self.gateway else []
        self.defaults.configure(values=choices)
        if self.model.get() not in choices:
            self.model.set(choices[0] if choices else "")
        self._render_models()
        self.tree.yview_moveto(position)
        self._clear_preview()

    def _toggle(self, item):
        if self.busy or not item or not self.gateway:
            return
        name = self.gateway.models[int(item)]["id"]
        if name in self.selected:
            self.selected.remove(name)
        else:
            self.selected.add(name)
        self._selection_changed()
        self.tree.focus_set()
        if self.tree.exists(item):
            self.tree.focus(item)
            self.tree.selection_set(item)

    def _toggle_click(self, event):
        if self.tree.identify_region(event.x, event.y) == "cell":
            self._toggle(self.tree.identify_row(event.y))
            return "break"

    def _toggle_keyboard(self, _):
        self._toggle(self.tree.focus())
        return "break"

    def _select_all(self, enabled):
        if self.gateway:
            self.selected = {row["id"] for row in self.gateway.models} if enabled else set()
            self._selection_changed()

    def _save_preferences(self):
        url = self.url.get()
        try:
            management_url(url)
        except ConfigError:
            url = ""
        self.preferences.update(url=url, browser=self.browser.get(), browser_access_version=1,
            backup_enabled=self.backup_enabled.get(), backup_encrypted=self.backup_encrypted.get(),
            export_encrypted=self.export_encrypted.get())
        self.preferences.pop("profile", None)
        try:
            atomic_write(self.preferences_path, json.dumps(self.preferences, ensure_ascii=False).encode("utf-8"))
            return True
        except (ConfigError, OSError):
            return False

    def _initial_directory(self, kind):
        value = self.preferences.get(kind + "_directory")
        if isinstance(value, str) and Path(value).expanduser().is_dir():
            return str(Path(value).expanduser())
        return str(Path.home())

    def _remember_path(self, kind, path):
        self.preferences[kind + "_directory"] = str(path.parent)
        self.preferences[kind + "_filename"] = path.name
        saved = self._save_preferences()
        if not saved:
            self.status.set("所选路径已在本次会话记住；偏好文件无法写入，下次打开可能无法保留。")
        return saved

    def _fetch(self):
        if self.busy:
            return
        url, browser = self.url.get(), self.browser.get()
        self._source_changed()
        def work():
            try:
                return read_browser(url, browser), browser
            except BrowserUnavailable:
                if browser == BROWSERS[0]:
                    raise
                return read_browser(url, BROWSERS[0]), BROWSERS[0]
        def done(result):
            gateway, used_browser = result
            if used_browser != browser:
                self.browser.set(used_browser)
            self.gateway = gateway
            self.keys.configure(values=[f"密钥 {i + 1} · 尾号 {key[-4:]}" for i, key in enumerate(gateway.keys)])
            self.keys.current(0)
            self.key_index = 0
            self._render_models()
            saved = self._save_preferences()
            self.status.set(("外部浏览器读取受限，已切换到内置窗口。" if used_browser != browser else "") +
                            f"已读取 {len(gateway.models)} 个模型，请勾选需要的模型。" +
                            ("" if saved else "偏好无法保存，请检查偏好文件权限。"))
        self._run(work, done, "请在登录窗口完成后台登录；外部浏览器读取受限时会自动打开内置窗口…")

    def _key_changed(self, _=None):
        if self.busy or not self.gateway:
            return
        gateway, index = self.gateway, self.keys.current()
        if not 0 <= index < len(gateway.keys):
            return
        # Re-picking the active key must not refetch or drop the selection and imported options.
        if index == self.key_index and gateway.models:
            return
        self.key_index = index
        self.imported_payload = None
        self.selected.clear()
        self.model.set("")
        self.defaults.configure(values=())
        self.gateway = Gateway(gateway.base_url, gateway.keys)
        self._render_models()
        self._clear_preview()
        def done(rows):
            self.gateway = Gateway(gateway.base_url, gateway.keys, rows)
            self._render_models()
            self.status.set("已按所选密钥重新获取模型，请重新勾选。")
        self._run(lambda: fetch_models(gateway.base_url, gateway.keys[index]), done, "正在验证所选密钥并获取模型…")

    def _agent_changed(self, _=None):
        try:
            self.directory.set(str(default_directory(self._agent_id())))
        except ConfigError as error:
            self.directory.set("")
            self._error(str(error))
        self._clear_preview()

    def _choose_directory(self):
        current = Path(self.directory.get().strip()).expanduser()
        value = filedialog.askdirectory(parent=self, initialdir=str(current if current.is_dir() else Path.home()))
        if value:
            self.directory.set(value)

    def _target(self):
        path = Path(self.directory.get()).expanduser()
        if not self.directory.get().strip() or not path.is_absolute():
            raise ConfigError("请选择有效的绝对配置目录。")
        return path.absolute()

    def _payload(self):
        if not self.gateway or not self.selected or self.keys.current() < 0:
            raise ConfigError("请先获取或导入模型，再勾选要使用的模型。")
        rows = [row for row in self.gateway.models if row["id"] in self.selected]
        payload = make_connection(self.gateway.base_url, self.gateway.keys[self.keys.current()], rows, self.model.get(),
                                  require_capabilities=False)
        if self.imported_payload:
            payload["provider_settings"].update(self.imported_payload["provider_settings"])
            if payload["model"] == self.imported_payload["model"]:
                payload["model_settings"].update(self.imported_payload["model_settings"])
            payload["model_settings"] = effective_model_settings(payload)
        return validate_connection(payload)

    def _password(self, title="设置加密密码"):
        if not self.password.get() and not self.confirm.get():
            dialog = PasswordDialog(self, title)
            if dialog.result is None:
                return None
            return dialog.result
        password = self.password.get()
        if len(password) < 8 or len(password.encode("utf-8")) > 1024 or password != self.confirm.get():
            raise ConfigError("请输入至少 8 个字符的密码，且两次输入必须相同，不能超过 1024 字节。")
        return password

    def _preview(self):
        try:
            text = preview_agent(self._agent_id(), self._target(), self._payload())
        except ConfigError as error:
            self._error(str(error))
            return
        self._clear_preview()
        self.preview.configure(state="normal")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")
        self.preview_window.deiconify()
        self.preview_window.lift()
        self.status.set("预览包含可导入模型和跳过原因，密钥已隐藏。")

    def _test(self):
        try:
            agent = self._agent_id()
            plan = prepare_import(agent, self._payload())
            if plan.payload is None:
                self._show_import_result(agent, plan)
                return
            if plan.default_changed:
                reason = next(row.reason for row in plan.skipped if row.name == plan.requested_default)
                raise ConfigError(f"默认模型 {plan.requested_default} 无法用于当前软件：{reason}\n"
                                  "请先选择可导入的默认模型，或完成一键导入后再实测。")
        except ConfigError as error:
            self._error(str(error))
            return
        protocol = AGENTS[agent].protocol
        self._run(lambda: test_model(plan.payload, protocol), self.status.set,
                  f"正在实测 {plan.default_model}，可能消耗少量模型额度…")

    def _show_import_result(self, agent, plan, backup=None, error=None, recovered=False):
        self.last_import_plan = plan
        self.last_import_outcome = "failed" if error else "success" if plan.accepted else "empty"
        if error:
            summary = f"配置导入失败，未标记任何模型为成功；跳过 {len(plan.skipped)} 个。\n{error}"
        elif plan.accepted:
            summary = f"配置已导入 {len(plan.accepted)} 个，跳过 {len(plan.skipped)} 个。请重开目标软件并新建会话。"
            if plan.default_changed:
                summary += f"\n原默认模型 {plan.requested_default} 不兼容，已改用 {plan.default_model}。"
            if agent == "codex" and recovered:
                summary += "\n检测到本地 Codex 配置异常，已自动修复后导入。"
            summary += "\n备份：" + str(backup) if backup else "\n本次未备份旧配置。"
            summary += "\n已写入配置，未进行联网调用测试；实际可用性取决于上游接口、授权与额度。"
            if agent == "pi":
                summary += (f"\npi：保留其他来源的账号与默认选择，以及思考偏好。输入 /model 选择 {PI_PROVIDER} 下的模型；"
                            "Ctrl+P 保留原切换范围并加入导入模型，原来未限制时仍可切换全部可用模型。")
            if agent == "siyuan":
                summary += "\n思源笔记：已同步自定义 API 和全部已选模型；重启思源后可在 AI 设置中切换模型。"
        else:
            summary = f"没有可导入的模型，已跳过 {len(plan.skipped)} 个，未修改任何配置。"
        self.status.set(summary.splitlines()[0] + ("详见导入结果。" if plan.accepted or plan.skipped else ""))
        if self.result_window is None or not self.result_window.winfo_exists():
            self.result_window = tk.Toplevel(self, background=BG)
            self.result_window.geometry("960x540")
            self.result_window.minsize(760, 400)
            self.result_window.protocol("WM_DELETE_WINDOW", self.result_window.withdraw)
            shell, _ = self._card(self.result_window, "导入结果", expand=True, padx=14, pady=14)
            self.result_summary = tk.StringVar()
            summary_label = ttk.Label(shell, textvariable=self.result_summary, style="Card.TLabel", wraplength=880)
            summary_label.pack(anchor="w", pady=(0, 12))
            footer = ttk.Frame(shell, style="Card.TFrame")
            footer.pack(side="bottom", fill="x", pady=(10, 0))
            frame = self._bordered(shell)
            frame.pack(fill="both", expand=True)
            self.result_tree = ttk.Treeview(frame, columns=("model", "result", "reason"), show="headings", selectmode="browse")
            self.result_tree.tag_configure("success", foreground=SUCCESS)
            self.result_tree.tag_configure("skipped", foreground=MUTED)
            self.result_tree.tag_configure("failed", foreground=DANGER)
            for column, title, width in (("model", "模型", 260), ("result", "结果", 80), ("reason", "说明", 680)):
                self.result_tree.heading(column, text=title)
                self.result_tree.column(column, width=width, minwidth=70, stretch=column == "reason")
            vertical = ttk.Scrollbar(frame, orient="vertical", command=self.result_tree.yview)
            horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.result_tree.xview)
            self.result_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            self.result_tree.grid(row=0, column=0, sticky="nsew")
            vertical.grid(row=0, column=1, sticky="ns")
            horizontal.grid(row=1, column=0, sticky="ew")
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            self.result_detail = tk.StringVar(value="选择模型可查看完整说明。")
            detail_label = ttk.Label(footer, textvariable=self.result_detail, style="CardMuted.TLabel", wraplength=880)
            detail_label.pack(anchor="w")
            def wrap(event):
                summary_label.configure(wraplength=max(event.width - 28, 300))
                detail_label.configure(wraplength=max(event.width - 28, 300))
            shell.bind("<Configure>", wrap)
            def detail(_):
                selected = self.result_tree.selection()
                if selected:
                    values = self.result_tree.item(selected[0], "values")
                    self.result_detail.set(f"{values[0]}：{values[2]}")
            self.result_tree.bind("<<TreeviewSelect>>", detail)
            self.result_close = ttk.Button(footer, text="关闭", command=self.result_window.withdraw)
            self.result_close.pack(anchor="e", pady=(8, 0))
        self.result_window.title("导入结果 · " + AGENTS[agent].label)
        self.result_summary.set(summary)
        self.result_detail.set("选择模型可查看完整说明。")
        self.result_tree.delete(*self.result_tree.get_children())
        for name in plan.accepted:
            self.result_tree.insert("", "end", values=(name, "未导入" if error else "已导入",
                error or "已写入配置，未进行联网调用测试。"), tags=("failed" if error else "success",))
        for row in plan.skipped:
            self.result_tree.insert("", "end", values=(row.name, "已跳过", row.reason), tags=("skipped",))
        self.result_window.deiconify()
        self.result_window.lift()

    def _backup_location(self, agent):
        name = f"{agent}-{dt.datetime.now():%Y%m%d-%H%M%S}.cproxybackup"
        selected = filedialog.asksaveasfilename(parent=self, title="保存原配置备份",
            initialdir=self._initial_directory("backup"), initialfile=name,
            defaultextension=".cproxybackup", filetypes=[("配置备份", "*.cproxybackup")])
        if not selected:
            return None
        path = Path(selected).absolute()
        self._remember_path("backup", path)
        return path

    def _apply(self):
        if self.busy:
            return
        self.last_codex_recovery = False
        if self.result_window is not None:
            self.result_window.withdraw()
        try:
            agent = self._agent_id()
            plan = prepare_import(agent, self._payload())
            if plan.payload is None:
                self._show_import_result(agent, plan)
                return
            target, payload = self._target(), plan.payload
            if agent == "codex":
                config_path = target / FILES[0]
                existing = read_bytes(config_path) if config_path.exists() else None
                self.last_codex_recovery = needs_codex_recovery(existing)
            # Validate and render before asking for a password or backup destination.
            preview_agent(agent, target, payload)
            backup = self.backup_enabled.get()
            password = self._password("设置备份密码") if backup and self.backup_encrypted.get() else None
            if backup and self.backup_encrypted.get() and password is None:
                return
            location = self._backup_location(agent) if backup else None
            if backup and location is None:
                return
        except ConfigError as error:
            self._error(str(error))
            return
        def done(path):
            saved = self._save_preferences()
            if plan.default_changed:
                self.model.set(plan.default_model)
            self._show_import_result(agent, plan, backup=path, recovered=self.last_codex_recovery)
            if not saved:
                self.status.set(self.status.get() + "\n保存路径偏好失败，下次可能无法记住。")
        self._run(lambda: apply_agent(agent, target, payload, password, backup=backup, backup_path=location),
                  done, f"正在导入 {len(plan.accepted)} 个兼容模型，跳过 {len(plan.skipped)} 个…",
                  failed=lambda error: self._show_import_result(agent, plan, error=error,
                                                                recovered=self.last_codex_recovery))

    def _restore(self):
        if self.busy:
            return
        try:
            agent, target = self._agent_id(), self._target()
            selected = filedialog.askopenfilename(parent=self, title="选择配置备份",
                initialdir=self._initial_directory("backup"), filetypes=[("配置备份", "*.cproxybackup *.json"), ("所有文件", "*")])
            if not selected:
                return
            content = read_bytes(Path(selected).absolute(), MAX_FILE_BYTES)
            encrypted = is_encrypted(content)
            password = simpledialog.askstring(TITLE, "输入备份的解密密码", show="•", parent=self) if encrypted else None
            if encrypted and password is None:
                return
            # Verify ownership and password before offering a destination for the undo snapshot.
            document = decode_document(content, password)
            expected_agent = document.get("agent", "codex")
            if (agent == "claude-code" and expected_agent == "claude" and isinstance(document.get("files"), dict)
                    and set(document["files"]) == {"settings.json"}):
                expected_agent = "claude-code"
            if document.get("kind") != "backup" or expected_agent != agent or document.get("directory") != str(target):
                raise ConfigError("该备份不属于当前软件或配置目录。")
            location = self._backup_location(agent)
            if location is None:
                return
            if location.resolve() == Path(selected).resolve():
                raise ConfigError("恢复前的备份不能覆盖正在恢复的备份文件，请选择其他文件名。")
        except (ConfigError, OSError) as error:
            self._error(str(error) if isinstance(error, ConfigError) else "无法读取备份文件。")
            return
        self._run(lambda: restore_agent(agent, target, content, password, backup_path=location),
                  lambda path: self.status.set("恢复完成。恢复前的状态已保存：" + str(path)), "正在恢复备份…")

    def _export(self):
        if self.busy:
            return
        try:
            payload = self._payload()
            encrypted = self.export_encrypted.get()
            previous = self.preferences.get("export_filename", "模型节点")
            if not isinstance(previous, str) or not previous:
                previous = "模型节点"
            initialfile = Path(previous).stem + (".cproxy" if encrypted else ".json")
            selected = filedialog.asksaveasfilename(parent=self, title="导出所选模型",
                initialdir=self._initial_directory("export"),
                initialfile=initialfile,
                defaultextension=".cproxy" if encrypted else ".json",
                filetypes=[("加密模型节点", "*.cproxy")] if encrypted else [("明文模型节点（包含 API 密钥）", "*.json")])
            if not selected:
                return
            target = Path(selected).absolute()
            protected = {self._target() / name for name in (*AGENTS[self._agent_id()].files, "auth.json")}
            if target.resolve() in {p.resolve() for p in protected}:
                raise ConfigError("不能用导出文件覆盖客户端配置。")
            password = self._password("设置导出密码") if encrypted else None
            if encrypted and password is None:
                return
        except ConfigError as error:
            self._error(str(error))
            return
        def done(_):
            saved = self._remember_path("export", target)
            self.status.set(("已加密导出：" if encrypted else "已明文导出（包含 API 密钥，请妥善保存）：") + str(target) +
                            ("" if saved else "保存路径偏好失败，下次可能无法记住。"))
        self._run(lambda: atomic_write(target, encode_document(payload, password).encode("utf-8")),
                  done, "正在导出所选模型…")

    def _import(self):
        if self.busy:
            return
        if getattr(self, "import_window", None) and self.import_window.winfo_exists():
            self.import_window.lift()
            return
        window = self.import_window = tk.Toplevel(self, background=BG)
        window.title("导入模型")
        window.geometry("720x460")
        window.minsize(640, 400)
        window.transient(self)
        frame, _ = self._card(window, "导入模型", expand=True, padx=14, pady=14)
        ttk.Label(frame, text="从网页获取，或导入程序导出的加密 / 明文节点。", style="CardMuted.TLabel").pack(anchor="w")
        row = ttk.Frame(frame, style="Card.TFrame")
        row.pack(fill="x", pady=12)
        self._button(row, "网页获取", lambda: (window.destroy(), self._fetch())).pack(side="left")
        self._button(row, "选择文件…", self._import_file).pack(side="left", padx=8)
        self._button(row, "粘贴剪贴板", self._paste_import).pack(side="left")
        ttk.Label(frame, text="将节点信息粘贴到下方，也可直接拖入一个文件：", style="Card.TLabel").pack(anchor="w")
        border, self.import_text = self._text(frame, height=8, width=1, wrap="word")
        border.pack(fill="both", expand=True, pady=8)
        scroll = ttk.Scrollbar(border, orient="vertical", command=self.import_text.yview)
        self.import_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.import_text.pack(fill="both", expand=True)
        self.import_text.drop_target_register(DND_FILES, DND_TEXT)
        self.import_text.dnd_bind("<<Drop:DND_Files>>", self._drop_files)
        self.import_text.dnd_bind("<<Drop:DND_Text>>", self._drop_text)
        row = ttk.Frame(frame, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="加密文件自动询问密码，密码不会保存。", style="CardMuted.TLabel").pack(side="left")
        self._button(row, "导入模型", lambda: self._import_content(self.import_text.get("1.0", "end")), True).pack(side="right")

    def _paste_import(self):
        if self.busy:
            return
        try:
            text = self.clipboard_get()
            if len(text.encode("utf-8")) > MAX_FILE_BYTES:
                raise ConfigError("剪贴板内容过大。")
            self.import_text.delete("1.0", "end")
            self.import_text.insert("1.0", text)
        except (tk.TclError, ConfigError):
            self._error("剪贴板没有有效的节点文本，或内容过大。")

    def _import_file(self, path=None):
        if self.busy:
            return
        selected = path or filedialog.askopenfilename(parent=self, title="导入模型节点",
            filetypes=[("模型节点", "*.cproxy *.json"), ("所有文件", "*")])
        if not selected:
            return
        try:
            self._import_content(read_bytes(Path(selected).expanduser().absolute(), MAX_FILE_BYTES))
        except (ConfigError, OSError):
            self._error("无法读取所选节点文件，请检查路径、权限及文件大小。")

    def _drop_files(self, event):
        if self.busy:
            return "none"
        try:
            paths = self.tk.splitlist(event.data)
            if len(paths) != 1:
                raise ConfigError("请一次拖入一个模型节点文件。")
            self._import_file(paths[0])
        except (ConfigError, tk.TclError) as error:
            self._error(str(error))
        return "copy"

    def _drop_text(self, event):
        self._import_content(event.data)
        return "copy"

    def _import_content(self, content):
        if self.busy:
            return
        try:
            encrypted = is_encrypted(content)
            password = simpledialog.askstring(TITLE, "输入模型节点的解密密码", show="•", parent=self) if encrypted else None
            if encrypted and password is None:
                return
        except ConfigError as error:
            self._error(str(error))
            return
        def done(payload):
            rows = tuple({"id": row["slug"], "owned_by": "导入节点", "codex": deepcopy(row)}
                         for row in payload["catalog"]["models"])
            self.imported_payload = payload
            self.gateway = Gateway(payload["base_url"], (payload["api_key"],), rows)
            self.keys.configure(values=("导入节点的客户端密钥",))
            self.keys.current(0)
            self.key_index = 0
            self.selected = {row["id"] for row in rows}
            self.model.set(payload["model"])
            self._selection_changed()
            if getattr(self, "import_window", None) and self.import_window.winfo_exists():
                self.import_window.destroy()
            self.status.set("已导入模型节点，可调整勾选后配置目标软件。")
        self._run(lambda: validate_connection(decode_document(content, password)), done, "正在读取模型节点…")

    def _copy_access(self):
        try:
            payload = self._payload()
        except ConfigError as error:
            self._error(str(error))
            return
        text = json.dumps({"接口地址（OpenAI 兼容）": payload["base_url"],
            "接口地址（Anthropic 兼容）": payload["base_url"][:-3], "API 密钥": payload["api_key"],
            "默认模型": payload["model"], "已选模型": [r["slug"] for r in payload["catalog"]["models"]]}, ensure_ascii=False, indent=2)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.set("接口地址、完整密钥和所选模型已复制。请只粘贴到你信任的软件设置中。")

    def _error(self, message):
        self.status.set(message)
        self.status_label.configure(foreground=DANGER)
        messagebox.showerror(TITLE, message, parent=self)

    def _run(self, worker, done, status, failed=None):
        if self.busy:
            return
        self.busy = True
        self.widgets = [widget for widget in self.widgets if widget.winfo_exists()]
        states = [(widget, widget.cget("state")) for widget in self.widgets]
        for widget, _ in states:
            widget.configure(state="disabled")
        self.status.set(status)
        self.progress.pack(side="right", padx=(16, 0))
        self.progress.start(14)
        result = queue.Queue()
        def work():
            try:
                result.put((True, worker()))
            except ConfigError as error:
                result.put((False, str(error)))
            except OSError:
                result.put((False, "文件操作失败，请检查目录权限、磁盘空间和文件状态。"))
            except Exception:
                result.put((False, "操作失败，请检查后台或目标软件的配置格式。"))
        def poll():
            try:
                success, value = result.get_nowait()
            except queue.Empty:
                self.after(80, poll)
                return
            self.busy = False
            self.progress.stop()
            self.progress.pack_forget()
            for widget, state in states:
                if widget.winfo_exists():
                    widget.configure(state=state)
            self.password.set("")
            self.confirm.set("")
            if success:
                done(value)
            else:
                if failed:
                    failed(value)
                else:
                    self._error(value)
        threading.Thread(target=work, daemon=True).start()
        self.after(80, poll)

    def _close(self):
        if self.busy:
            self.status.set("当前操作尚未完成，请等待后再关闭。")
            return
        self._save_preferences()
        self.gateway = None
        self.imported_payload = None
        self.password.set("")
        self.confirm.set("")
        self.destroy()


class PasswordDialog(simpledialog.Dialog):
    def body(self, master):
        ttk.Label(master, text="至少 8 个字符；密码不保存，请妥善保管。").grid(row=0, columnspan=2, pady=(0, 10))
        ttk.Label(master, text="密码").grid(row=1, column=0, sticky="w")
        self.first = ttk.Entry(master, show="•", width=30)
        self.first.grid(row=1, column=1, padx=8, pady=5)
        ttk.Label(master, text="确认密码").grid(row=2, column=0, sticky="w")
        self.second = ttk.Entry(master, show="•", width=30)
        self.second.grid(row=2, column=1, padx=8, pady=5)
        return self.first

    def validate(self):
        value = self.first.get()
        if len(value) < 8 or len(value.encode("utf-8")) > 1024 or value != self.second.get():
            messagebox.showerror(TITLE, "密码至少 8 个字符、不能超过 1024 字节，且两次输入须相同。", parent=self)
            return False
        return True

    def apply(self):
        self.result = self.first.get()


def main():
    if sys.argv[1:2] == ["--browser-reader"]:
        from .browser import browser_main
        browser_main(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--management-url", default="")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--self-check-report", type=Path)
    options = parser.parse_args()
    try:
        app = App(management_address=options.management_url)
    except (tk.TclError, RuntimeError):
        raise SystemExit("桌面组件无法启动，请使用包含 Tk 的 Python 3.11 以上环境。") from None
    if options.self_check:
        from .self_check import run_self_check
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
    else:
        app.mainloop()
