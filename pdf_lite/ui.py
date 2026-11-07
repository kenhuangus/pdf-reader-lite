"""Tkinter user interface for PDF Lite."""

from __future__ import annotations

import json
import os
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .convert import convert_pdf_to_word, find_soffice
from .core import (DocumentInfo, PasswordRequired, PdfDocument, WrongPassword,
                   human_size, search_document)
from .editor import (AddImageDialog, EditTextDialog, InsertChoiceDialog,
                     ManageImagesDialog, TableEditorDialog, delete_content_rect,
                     draw_table_in_pdf, get_element_at_point, get_page_images,
                     get_page_tables, get_text_properties_at_rect,
                     insert_image_in_pdf, insert_text_in_pdf,
                     replace_image_in_pdf, replace_selected_text_in_pdf,
                     replace_text_in_pdf)
from .signature import ask_signature
from .thumbs import ThumbnailPanel
from .view import (ContinuousView, MODE_READ, MODE_SELECT, MODE_EDIT)


APP_NAME = "PDF Lite"
APP_VERSION = "1.0.0"
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "PDF Lite"
RECENT_PATH = CONFIG_DIR / "recent.json"
SETTINGS_PATH = CONFIG_DIR / "settings.json"


LIGHT = {
    "bg": "#f1f2f4",
    "toolbar": "#fafafa",
    "sidebar": "#f6f6f8",
    "workspace": "#dfe1e5",
    "status": "#ececee",
    "button": "#ffffff",
    "button_hover": "#e7e7ea",
    "entry": "#ffffff",
    "text": "#22242a",
    "muted": "#666b75",
    "accent": "#b51c1c",
    "accent_hover": "#8e1111",
    "accent_soft": "#fdecec",
    "page_border": "#b9bdc5",
    "shadow": "#c6c9cf",
    "dark": False,
}

DARK = {
    "bg": "#212226",
    "toolbar": "#282a2f",
    "sidebar": "#24262b",
    "workspace": "#17181b",
    "status": "#2b2d32",
    "button": "#33363c",
    "button_hover": "#40444b",
    "entry": "#30333a",
    "text": "#f0f1f3",
    "muted": "#a7abb4",
    "accent": "#ef5350",
    "accent_hover": "#d33b38",
    "accent_soft": "#3d2929",
    "page_border": "#555a63",
    "shadow": "#0c0d0f",
    "dark": True,
}


class PDFLiteApp(tk.Tk):
    def __init__(self, initial_path: str | None = None) -> None:
        self.initial_path = initial_path
        self.doc: PdfDocument | None = None
        self.path: str | None = None
        self.current_page = 0
        self.history: list[int] = [0]
        self.history_index = 0
        self.modified = False
        self._word_dialog_open = False
        self.dark_enabled = False
        self.sidebar_visible = True
        self.recent: list[str] = []
        self._search_cancel: threading.Event | None = None
        self._search_token = 0
        self._search_queue: queue.Queue | None = None
        self._search_query = ""
        self._search_matches: list[tuple[int, tuple[float, float, float, float]]] = []
        self._search_index = -1
        self._search_running = False
        self._status_after: str | None = None
        self._fullscreen = False
        self.dark_var: tk.BooleanVar | None = None
        self.palette = dict(LIGHT)
        self._load_settings()

        super().__init__()
        self.option_add("*Font", ("Segoe UI", 9))
        self.title(f"{APP_NAME} — lightweight PDF reader")
        self.geometry("1180x820")
        self.minsize(760, 540)
        self._configure_fonts()
        self._set_window_icon()
        self._build_styles()
        self._build_menubar()
        self._build_toolbar()
        self._build_searchbar()
        self._build_body()
        self._build_statusbar()
        self._bind_keys()
        self._populate_recent_menu()
        self._apply_palette()
        self._set_doc_buttons(False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._start_initial_open)

    # ------------------------------------------------------------------
    # Appearance and settings
    # ------------------------------------------------------------------
    def _load_settings(self) -> None:
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            self.dark_enabled = bool(data.get("dark", False))
            self.sidebar_visible = bool(data.get("sidebar_visible", True))
        except Exception:
            pass
        if self.dark_enabled:
            self.palette = dict(DARK)
        try:
            data = json.loads(RECENT_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                valid: list[str] = []
                for p in data:
                    sp = str(p).strip()
                    # Block UNC network paths to avoid NTLM hash leaks during file existence checks
                    if sp.startswith(("\\\\", "//")):
                        continue
                    if os.path.isfile(sp):
                        valid.append(sp)
                self.recent = valid[:10]
        except Exception:
            self.recent = []

    def _save_settings(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_PATH.write_text(json.dumps({
                "dark": self.dark_enabled,
                "sidebar_visible": self.sidebar_visible,
            }, indent=2), encoding="utf-8")
            RECENT_PATH.write_text(json.dumps(self.recent[:10], indent=2),
                                   encoding="utf-8")
        except Exception:
            pass

    def _configure_fonts(self) -> None:
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                     "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
            try:
                tkfont.nametofont(name).configure(family="Segoe UI", size=9)
            except tk.TclError:
                pass

    def _set_window_icon(self) -> None:
        icon = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
        if icon.is_file():
            try:
                self.iconbitmap(default=str(icon))
            except tk.TclError:
                pass

    def _build_styles(self) -> None:
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self._update_styles()

    def _update_styles(self) -> None:
        p = self.palette
        self.style.configure(".", background=p["bg"], foreground=p["text"],
                             font=("Segoe UI", 9))
        self.style.configure("TFrame", background=p["bg"])
        self.style.configure("Toolbar.TFrame", background=p["toolbar"])
        self.style.configure("Sidebar.TFrame", background=p["sidebar"])
        self.style.configure("Status.TFrame", background=p["status"])
        self.style.configure("TLabel", background=p["bg"], foreground=p["text"])
        self.style.configure("Toolbar.TLabel", background=p["toolbar"], foreground=p["text"])
        self.style.configure("Status.TLabel", background=p["status"], foreground=p["text"])
        self.style.configure("Brand.TLabel", background=p["toolbar"], foreground=p["accent"],
                             font=("Segoe UI", 11, "bold"))
        self.style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
        self.style.configure("TButton", background=p["button"], foreground=p["text"],
                             padding=(8, 3), borderwidth=0, relief="flat")
        self.style.map("TButton",
                       background=[("active", p["button_hover"]), ("pressed", p["button_hover"])],
                       foreground=[("disabled", p["muted"])])
        self.style.configure("Toolbar.TButton", background=p["button"], foreground=p["text"],
                             padding=(8, 4), borderwidth=0, relief="flat")
        self.style.map("Toolbar.TButton",
                       background=[("active", p["button_hover"]), ("pressed", p["button_hover"])],
                       foreground=[("disabled", p["muted"])])
        self.style.configure("Accent.TButton", background=p["accent"], foreground="#ffffff",
                             padding=(10, 4), borderwidth=0, relief="flat")
        self.style.map("Accent.TButton",
                       background=[("active", p["accent_hover"])])
        self.style.configure("TEntry", fieldbackground=p["entry"], foreground=p["text"],
                             insertcolor=p["text"], padding=(5, 3), borderwidth=0)
        self.style.configure("Toolbar.TEntry", fieldbackground=p["entry"], foreground=p["text"],
                             insertcolor=p["text"], padding=(5, 3), borderwidth=0)
        self.style.configure("TNotebook", background=p["sidebar"], borderwidth=0)
        self.style.configure("TNotebook.Tab", background=p["sidebar"], foreground=p["muted"],
                             padding=(10, 4), borderwidth=0)
        self.style.map("TNotebook.Tab",
                       background=[("selected", p["toolbar"])],
                       foreground=[("selected", p["text"])])
        self.style.configure("Treeview", background=p["sidebar"], fieldbackground=p["sidebar"],
                             foreground=p["text"], borderwidth=0, rowheight=21)
        self.style.configure("Treeview.Heading", background=p["toolbar"],
                             foreground=p["muted"], borderwidth=0)
        self.style.map("Treeview", background=[("selected", p["accent_soft"])],
                       foreground=[("selected", p["text"])])
        self.style.configure("Vertical.TScrollbar", background=p["button"],
                             troughcolor=p["bg"], borderwidth=0, arrowsize=13)
        self.style.configure("Horizontal.TScrollbar", background=p["button"],
                             troughcolor=p["bg"], borderwidth=0, arrowsize=13)
        self.style.configure("TSeparator", background=p["page_border"])

    def _apply_palette(self) -> None:
        self.palette = dict(DARK if self.dark_enabled else LIGHT)
        self._update_styles()
        self.configure(background=self.palette["bg"])
        self.toolbar.configure(style="Toolbar.TFrame")
        self.searchbar.configure(style="Toolbar.TFrame")
        self.body.configure(style="TFrame")
        self.statusbar.configure(style="Status.TFrame")
        self.sidebar.configure(style="Sidebar.TFrame")
        self.brand.configure(style="Brand.TLabel")
        self.status_left.configure(background=self.palette["status"], foreground=self.palette["muted"])
        self.status_message.configure(background=self.palette["status"], foreground=self.palette["text"])
        self.status_right.configure(background=self.palette["status"], foreground=self.palette["muted"])
        self.find_status.configure(style="Toolbar.TLabel")
        self.view.set_palette(self.palette)
        self.thumbnails.set_palette(self.palette)
        self.info_text.configure(bg=self.palette["sidebar"], fg=self.palette["text"],
                                 insertbackground=self.palette["text"])
        self._update_info_panel()

    # ------------------------------------------------------------------
    # Menus
    # ------------------------------------------------------------------
    def _build_menubar(self) -> None:
        self.menubar = tk.Menu(self)
        file_menu = tk.Menu(self.menubar, tearoff=0)
        file_menu.add_command(label="Open…", accelerator="Ctrl+O", command=self.open_dialog)
        self.recent_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Open Recent", menu=self.recent_menu)
        file_menu.add_separator()
        file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self.save_document)
        file_menu.add_command(label="Save As…", accelerator="Ctrl+Shift+S", command=self.save_as)
        file_menu.add_command(label="Export to Word…", accelerator="Ctrl+Shift+E",
                              command=self.export_word)
        file_menu.add_separator()
        file_menu.add_command(label="Close Document", accelerator="Ctrl+W", command=self.close_document)
        file_menu.add_command(label="Properties…", accelerator="Ctrl+Shift+P", command=self.show_properties)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        self.menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(self.menubar, tearoff=0)
        edit_menu.add_command(label="Copy", accelerator="Ctrl+C", command=self.copy_selection)
        edit_menu.add_command(label="Copy Page Text", accelerator="Ctrl+Shift+C", command=self.copy_page_text)
        edit_menu.add_command(label="Copy Entire Document", command=self.copy_document_text)
        edit_menu.add_separator()
        edit_menu.add_command(label="Select All on Page", accelerator="Ctrl+A", command=self.select_all)
        edit_menu.add_command(label="Select Entire Document", accelerator="Ctrl+Shift+A", command=self.select_all_document)
        edit_menu.add_separator()
        self.edit_mode_var = tk.BooleanVar(value=False)
        edit_menu.add_checkbutton(label="Click-to-Edit Mode", variable=self.edit_mode_var,
                                  accelerator="Ctrl+E", command=self.toggle_edit_mode)
        edit_menu.add_command(label="Edit Selected Text…", command=self.action_edit_or_add_text)
        edit_menu.add_command(label="Delete (Redact) Selected Text", command=self.action_delete_selection)
        edit_menu.add_command(label="Add Text Box…", accelerator="Ctrl+T",
                              command=lambda: self.add_text_at(self.current_page, (50.0, 100.0)))
        edit_menu.add_command(label="Insert Image…", accelerator="Ctrl+I",
                              command=lambda: self.add_image_at(self.current_page))
        edit_menu.add_command(label="Manage Images on Page…",
                              command=lambda: self.manage_images(self.current_page))
        edit_menu.add_command(label="Insert Table…",
                              command=lambda: self.add_table_at(self.current_page))
        edit_menu.add_command(label="Edit Tables on Page…",
                              command=lambda: self.edit_tables_on_page(self.current_page))
        edit_menu.add_separator()
        edit_menu.add_command(label="Find…", accelerator="Ctrl+F", command=self.show_find)
        edit_menu.add_command(label="Find Next", accelerator="F3", command=lambda: self._find_next(1))
        edit_menu.add_command(label="Find Previous", accelerator="Shift+F3", command=lambda: self._find_next(-1))
        self.menubar.add_cascade(label="Edit", menu=edit_menu)

        view_menu = tk.Menu(self.menubar, tearoff=0)
        view_menu.add_command(label="Show/Hide Sidebar", accelerator="F4", command=self.toggle_sidebar)
        view_menu.add_separator()
        self.mode_var = tk.StringVar(value=MODE_READ)
        view_menu.add_radiobutton(label="Read Mode (Touch / Pan)", variable=self.mode_var,
                                  value=MODE_READ, accelerator="Ctrl+1", command=self.set_read_mode)
        view_menu.add_radiobutton(label="Select Mode (Text Selection)", variable=self.mode_var,
                                  value=MODE_SELECT, accelerator="Ctrl+2", command=self.set_select_mode)
        view_menu.add_radiobutton(label="Click-to-Edit Mode", variable=self.mode_var,
                                  value=MODE_EDIT, accelerator="Ctrl+E", command=self.toggle_edit_mode)
        view_menu.add_separator()
        view_menu.add_command(label="Fit Width", command=self.fit_width)
        view_menu.add_command(label="Fit Page", command=self.fit_page)
        view_menu.add_command(label="Actual Size", accelerator="Ctrl+0", command=self.actual_size)
        view_menu.add_command(label="Zoom In", accelerator="Ctrl++", command=self.zoom_in)
        view_menu.add_command(label="Zoom Out", accelerator="Ctrl+-", command=self.zoom_out)
        view_menu.add_separator()
        view_menu.add_command(label="Rotate Clockwise", accelerator="Ctrl+R", command=self.rotate_cw)
        view_menu.add_command(label="Rotate Counter-Clockwise", accelerator="Ctrl+Shift+R",
                              command=self.rotate_ccw)
        view_menu.add_command(label="Reset Rotation", command=self.reset_rotation)
        view_menu.add_separator()
        self.dark_var = tk.BooleanVar(value=self.dark_enabled)
        view_menu.add_checkbutton(label="Dark Mode", variable=self.dark_var,
                                  command=self.toggle_dark)
        view_menu.add_command(label="Full Screen", accelerator="F11", command=self.toggle_fullscreen)
        self.menubar.add_cascade(label="View", menu=view_menu)

        nav_menu = tk.Menu(self.menubar, tearoff=0)
        nav_menu.add_command(label="First Page", accelerator="Ctrl+Home", command=self.first_page)
        nav_menu.add_command(label="Previous Page", accelerator="Page Up", command=self.previous_page)
        nav_menu.add_command(label="Next Page", accelerator="Page Down", command=self.next_page)
        nav_menu.add_command(label="Last Page", accelerator="Ctrl+End", command=self.last_page)
        nav_menu.add_separator()
        nav_menu.add_command(label="Go to Page…", accelerator="Ctrl+G", command=self.show_go_to_page)
        self.menubar.add_cascade(label="Navigate", menu=nav_menu)

        tools_menu = tk.Menu(self.menubar, tearoff=0)
        tools_menu.add_command(label="Read Mode (Touch / Pan)", accelerator="Ctrl+1",
                               command=self.set_read_mode)
        tools_menu.add_command(label="Select Mode (Text Selection)", accelerator="Ctrl+2",
                               command=self.set_select_mode)
        tools_menu.add_separator()
        tools_menu.add_command(label="Hand-drawn Signature…", accelerator="Ctrl+Shift+S",
                               command=self.hand_signature)
        tools_menu.add_command(label="Convert PDF to Word…", accelerator="Ctrl+Shift+E",
                               command=self.export_word)
        self.menubar.add_cascade(label="Tools", menu=tools_menu)

        help_menu = tk.Menu(self.menubar, tearoff=0)
        help_menu.add_command(label="Keyboard Shortcuts", command=self.show_shortcuts)
        help_menu.add_command(label="About PDF Lite", command=self.show_about)
        self.menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=self.menubar)

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------
    def _build_toolbar(self) -> None:
        self.toolbar = ttk.Frame(self, style="Toolbar.TFrame", padding=(9, 6))
        self.toolbar.grid(row=0, column=0, sticky="ew")
        for column in range(18):
            self.toolbar.grid_columnconfigure(column, weight=0)
        self.brand = ttk.Label(self.toolbar, text="PDF LITE", style="Brand.TLabel")
        self.brand.pack(side="left", padx=(2, 12))

        self.back_btn = ttk.Button(self.toolbar, text="←", width=3, style="Toolbar.TButton",
                                   command=self.history_back)
        self.back_btn.pack(side="left", padx=(0, 2))
        self.fwd_btn = ttk.Button(self.toolbar, text="→", width=3, style="Toolbar.TButton",
                                 command=self.history_forward)
        self.fwd_btn.pack(side="left", padx=(0, 6))
        ttk.Separator(self.toolbar, orient="vertical").pack(side="left", fill="y", padx=5)
        self.prev_btn = ttk.Button(self.toolbar, text="‹", width=3, style="Toolbar.TButton",
                                   command=self.previous_page)
        self.prev_btn.pack(side="left", padx=(0, 2))
        self.page_var = tk.StringVar(value="1")
        self.page_entry = ttk.Entry(self.toolbar, width=5, justify="center",
                                    textvariable=self.page_var, style="Toolbar.TEntry")
        self.page_entry.pack(side="left", padx=(0, 1))
        self.page_entry.bind("<Return>", lambda _e: self._page_entry_go())
        self.page_entry.bind("<FocusIn>", lambda _e: self.page_entry.select_range(0, "end"))
        self.page_total = ttk.Label(self.toolbar, text="/ —", style="Toolbar.TLabel")
        self.page_total.pack(side="left", padx=(0, 2))
        self.next_btn = ttk.Button(self.toolbar, text="›", width=3, style="Toolbar.TButton",
                                   command=self.next_page)
        self.next_btn.pack(side="left", padx=(0, 4))
        ttk.Separator(self.toolbar, orient="vertical").pack(side="left", fill="y", padx=5)

        self.zoom_out_btn = ttk.Button(self.toolbar, text="−", width=3, style="Toolbar.TButton",
                                       command=self.zoom_out)
        self.zoom_out_btn.pack(side="left", padx=(0, 2))
        self.zoom_var = tk.StringVar(value="100")
        self.zoom_entry = ttk.Entry(self.toolbar, width=6, justify="center",
                                    textvariable=self.zoom_var, style="Toolbar.TEntry")
        self.zoom_entry.pack(side="left", padx=(0, 1))
        self.zoom_entry.bind("<Return>", lambda _e: self._zoom_entry_commit())
        self.zoom_entry.bind("<FocusIn>", lambda _e: self.zoom_entry.select_range(0, "end"))
        self.zoom_suffix = ttk.Label(self.toolbar, text="%", style="Toolbar.TLabel")
        self.zoom_suffix.pack(side="left", padx=(0, 2))
        self.zoom_in_btn = ttk.Button(self.toolbar, text="+", width=3, style="Toolbar.TButton",
                                      command=self.zoom_in)
        self.zoom_in_btn.pack(side="left", padx=(0, 6))
        ttk.Separator(self.toolbar, orient="vertical").pack(side="left", fill="y", padx=5)

        self.fit_width_btn = ttk.Button(self.toolbar, text="Fit W", style="Toolbar.TButton",
                                        command=self.fit_width)
        self.fit_width_btn.pack(side="left", padx=(0, 2))
        self.fit_page_btn = ttk.Button(self.toolbar, text="Fit P", style="Toolbar.TButton",
                                       command=self.fit_page)
        self.fit_page_btn.pack(side="left", padx=(0, 6))
        ttk.Separator(self.toolbar, orient="vertical").pack(side="left", fill="y", padx=5)

        self.save_btn = ttk.Button(self.toolbar, text="Save", style="Toolbar.TButton",
                                   command=self.save_document)
        self.save_btn.pack(side="left", padx=(0, 2))
        self.read_mode_btn = ttk.Button(self.toolbar, text="✋ Read Mode", style="Accent.TButton",
                                        command=self.set_read_mode)
        self.read_mode_btn.pack(side="left", padx=(0, 2))
        self.select_mode_btn = ttk.Button(self.toolbar, text="I Select Mode", style="Toolbar.TButton",
                                          command=self.set_select_mode)
        self.select_mode_btn.pack(side="left", padx=(0, 2))
        self.edit_mode_btn = ttk.Button(self.toolbar, text="✎ Edit Mode", style="Toolbar.TButton",
                                        command=self.toggle_edit_mode)
        self.edit_mode_btn.pack(side="left", padx=(0, 2))
        self.text_btn = ttk.Button(self.toolbar, text="Text", style="Toolbar.TButton",
                                   command=self.action_edit_or_add_text)
        self.text_btn.pack(side="left", padx=(0, 2))
        self.img_btn = ttk.Button(self.toolbar, text="Image", style="Toolbar.TButton",
                                   command=lambda: self.add_image_at(self.current_page))
        self.img_btn.pack(side="left", padx=(0, 2))
        self.tbl_btn = ttk.Button(self.toolbar, text="Table", style="Toolbar.TButton",
                                   command=lambda: self.add_table_at(self.current_page))
        self.tbl_btn.pack(side="left", padx=(0, 2))
        self.sign_btn = ttk.Button(self.toolbar, text="Sign", style="Toolbar.TButton",
                                   command=self.hand_signature)
        self.sign_btn.pack(side="left", padx=(0, 2))
        self.word_btn = ttk.Button(self.toolbar, text="Word", style="Toolbar.TButton",
                                   command=self.export_word)
        self.word_btn.pack(side="left", padx=(0, 6))
        ttk.Separator(self.toolbar, orient="vertical").pack(side="left", fill="y", padx=5)

        self.find_btn = ttk.Button(self.toolbar, text="Find", style="Toolbar.TButton",
                                   command=self.show_find)
        self.find_btn.pack(side="left", padx=(0, 2))
        self.sidebar_btn = ttk.Button(self.toolbar, text="Sidebar", style="Toolbar.TButton",
                                      command=self.toggle_sidebar)
        self.sidebar_btn.pack(side="left", padx=(0, 2))
        self.open_btn = ttk.Button(self.toolbar, text="Open", style="Accent.TButton",
                                   command=self.open_dialog)
        self.open_btn.pack(side="left", padx=(8, 0))

        self._doc_buttons = [
            self.back_btn, self.fwd_btn, self.prev_btn, self.next_btn, self.zoom_out_btn,
            self.zoom_in_btn, self.fit_width_btn, self.fit_page_btn, self.find_btn,
            self.save_btn, self.read_mode_btn, self.select_mode_btn, self.edit_mode_btn,
            self.text_btn, self.img_btn, self.tbl_btn, self.sign_btn, self.word_btn,
        ]

    def _page_entry_go(self) -> None:
        if self.doc is None:
            return
        text = self.page_var.get().strip()
        try:
            page = int(text)
        except ValueError:
            self.page_var.set(str(self.current_page + 1))
            return
        page = max(1, min(page, self.doc.page_count))
        self.go_to_page(page, remember=True)

    def _zoom_entry_commit(self) -> None:
        if self.doc is None:
            return
        text = self.zoom_var.get().strip().replace("%", "").strip()
        try:
            zoom = float(text)
        except ValueError:
            self.zoom_var.set(f"{self.view.zoom * 100:.0f}")
            return
        self.view.set_zoom(zoom / 100.0)

    # ------------------------------------------------------------------
    # Find bar
    # ------------------------------------------------------------------
    def _build_searchbar(self) -> None:
        self.searchbar = ttk.Frame(self, style="Toolbar.TFrame", padding=(12, 6))
        self.searchbar.grid(row=1, column=0, sticky="ew")
        self.searchbar.grid_remove()
        self.find_var = tk.StringVar()
        ttk.Label(self.searchbar, text="Find:", style="Toolbar.TLabel").pack(side="left", padx=(0, 5))
        self.find_entry = ttk.Entry(self.searchbar, width=34, textvariable=self.find_var,
                                    style="Toolbar.TEntry")
        self.find_entry.pack(side="left", padx=(0, 5))
        self.find_entry.bind("<Return>", lambda _e: self._find_from_entry())
        self.find_entry.bind("<Escape>", lambda _e: self.hide_find())
        ttk.Button(self.searchbar, text="Previous", style="Toolbar.TButton",
                   command=lambda: self._find_next(-1)).pack(side="left", padx=(0, 2))
        ttk.Button(self.searchbar, text="Next", style="Toolbar.TButton",
                   command=lambda: self._find_next(1)).pack(side="left", padx=(0, 5))
        ttk.Button(self.searchbar, text="Done", style="Toolbar.TButton",
                   command=self.hide_find).pack(side="left", padx=(0, 8))
        self.find_status = ttk.Label(self.searchbar, text="", style="Toolbar.TLabel")
        self.find_status.pack(side="left")

    def show_find(self) -> None:
        if self.doc is None:
            self.set_status("Open a PDF before searching")
            return
        if not self.searchbar.winfo_ismapped():
            self.searchbar.grid()
        selection = self.view.selection_text.strip()
        if selection and "\n" not in selection and len(selection) <= 80 and not self.find_var.get().strip():
            self.find_var.set(selection)
        self.find_entry.focus_set()
        self.find_entry.select_range(0, "end")

    def hide_find(self) -> None:
        self.searchbar.grid_remove()
        self.view.canvas.focus_set()

    def _find_from_entry(self) -> None:
        query = self.find_var.get().strip()
        if not query:
            self.find_status.configure(text="Type something to find")
            return
        if query == self._search_query and self._search_matches:
            self._find_next(1)
            return
        self._start_search(query)

    def _start_search(self, query: str) -> None:
        if self.doc is None:
            return
        if self._search_cancel is not None:
            self._search_cancel.set()
        self._search_token += 1
        token = self._search_token
        cancel = threading.Event()
        self._search_cancel = cancel
        self._search_queue = queue.Queue()
        self._search_query = query
        self._search_matches = []
        self._search_index = -1
        self._search_running = True
        self.find_status.configure(text=f"Searching for “{query}”…")
        self.set_status(f"Searching {os.path.basename(self.path or '')}…", 60_000)
        path = self.doc.path
        password = self.doc.password
        progress_q = self._search_queue

        def progress(_s, _r, hits: int) -> None:
            if progress_q is not None:
                progress_q.put(("progress", hits), block=False)

        def worker() -> None:
            try:
                results, total = search_document(path, query, password, cancel, progress)
                progress_q.put(("done", results, total), block=False)
            except Exception as exc:
                progress_q.put(("error", str(exc)), block=False)

        threading.Thread(target=worker, daemon=True, name="pdf-search").start()
        self.after(90, lambda: self._poll_search(token))

    def _poll_search(self, token: int) -> None:
        if token != self._search_token or self._search_queue is None:
            return
        items = []
        while True:
            try:
                items.append(self._search_queue.get_nowait())
            except queue.Empty:
                break
        item = items[-1] if items else None
        if item is not None:
            kind = item[0]
            if kind == "progress":
                self.find_status.configure(text=f"Searching… {item[1]} match(es) so far")
            elif kind == "error":
                self._search_running = False
                self.find_status.configure(text="Search failed")
                self.set_status(f"Search failed: {item[1]}")
                return
            elif kind == "done":
                self._search_running = False
                results, total = item[1], item[2]
                self._finish_search(results, total)
                return
        if self._search_running:
            self.after(90, lambda: self._poll_search(token))

    def _finish_search(self, results: dict[int, list[tuple]], total: int) -> None:
        self._search_matches = []
        for page in sorted(results):
            for rect in results[page]:
                self._search_matches.append((page, rect))
        self.view.set_search_hits(results)
        self.find_status.configure(
            text=f"{total} match{'es' if total != 1 else ''}" if total else "No matches")
        if self._search_matches:
            self._search_index = 0
            page, rect = self._search_matches[0]
            self.view.jump_to_hit(page, rect)
            self.thumbnails.select_page(page)
            self.set_status(f"Found {total} match{'es' if total != 1 else ''} — F3 for next", 8_000)
        else:
            self.set_status(f"No matches for “{self._search_query}”", 8_000)

    def _find_next(self, direction: int) -> None:
        if not self._search_matches:
            if self._search_query:
                self._start_search(self._search_query)
            else:
                self.set_status("No search results yet")
            return
        count = len(self._search_matches)
        wrapped = False
        new_index = self._search_index + direction
        if new_index >= count:
            new_index = 0
            wrapped = True
        elif new_index < 0:
            new_index = count - 1
            wrapped = True
        self._search_index = new_index
        page, rect = self._search_matches[new_index]
        self.view.jump_to_hit(page, rect)
        self.thumbnails.select_page(page)
        current = self.view.current_page()
        if page != current:
            self.current_page = page
        self._refresh_status()
        if wrapped:
            self.set_status(f"Search wrapped to {new_index + 1} of {count}", 4_000)
        else:
            self.set_status(f"Match {new_index + 1} of {count} on page {page + 1}", 4_000)

    # ------------------------------------------------------------------
    # Main layout
    # ------------------------------------------------------------------
    def _build_body(self) -> None:
        self.body = ttk.Frame(self)
        self.body.grid(row=2, column=0, sticky="nsew")
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_columnconfigure(1, weight=1)
        self.body.grid_columnconfigure(0, weight=0)

        self.sidebar = ttk.Frame(self.body, width=238, style="Sidebar.TFrame")
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_rowconfigure(0, weight=1)
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.notebook = ttk.Notebook(self.sidebar)
        self.notebook.grid(row=0, column=0, sticky="nsew", padx=0, pady=(2, 0))

        pages_tab = ttk.Frame(self.notebook)
        self.notebook.add(pages_tab, text="Pages")
        pages_tab.grid_rowconfigure(0, weight=1)
        pages_tab.grid_columnconfigure(0, weight=1)
        self.thumbnails = ThumbnailPanel(pages_tab, self)
        self.thumbnails.grid(row=0, column=0, sticky="nsew")

        bookmarks_tab = ttk.Frame(self.notebook)
        self.notebook.add(bookmarks_tab, text="Bookmarks")
        bookmarks_tab.grid_rowconfigure(0, weight=1)
        bookmarks_tab.grid_columnconfigure(0, weight=1)
        self.toc = ttk.Treeview(bookmarks_tab, columns=("page",), show="tree headings",
                                selectmode="browse")
        self.toc.heading("#0", text="Bookmark", anchor="w")
        self.toc.heading("page", text="Page", anchor="center")
        self.toc.column("#0", width=165, stretch=True)
        self.toc.column("page", width=54, anchor="center", stretch=False)
        self.toc.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        toc_scroll = ttk.Scrollbar(bookmarks_tab, orient="vertical", command=self.toc.yview)
        toc_scroll.grid(row=0, column=1, sticky="ns")
        self.toc.configure(yscrollcommand=toc_scroll.set)
        self.toc.bind("<<TreeviewSelect>>", self._on_toc_select)

        info_tab = ttk.Frame(self.notebook)
        self.notebook.add(info_tab, text="Document")
        info_tab.grid_rowconfigure(0, weight=1)
        info_tab.grid_columnconfigure(0, weight=1)
        self.info_text = tk.Text(info_tab, wrap="word", bd=0, padx=9, pady=9,
                                 font=("Segoe UI", 9), cursor="arrow", state="disabled")
        self.info_text.grid(row=0, column=0, sticky="nsew")
        info_scroll = ttk.Scrollbar(info_tab, orient="vertical", command=self.info_text.yview)
        info_scroll.grid(row=0, column=1, sticky="ns")
        self.info_text.configure(yscrollcommand=info_scroll.set)

        self.view = ContinuousView(self.body, self)
        self.view.grid(row=0, column=1, sticky="nsew")
        self._bookmark_pages: dict[str, int] = {}
        self._sidebar_was_visible = self.sidebar_visible
        if not self.sidebar_visible:
            self.sidebar.grid_remove()
            self.body.grid_columnconfigure(0, weight=0)
        self._update_info_panel()

    def _build_statusbar(self) -> None:
        self.statusbar = ttk.Frame(self, style="Status.TFrame", padding=(10, 5))
        self.statusbar.grid(row=3, column=0, sticky="ew")
        self.status_left = tk.Label(self.statusbar, text="No document open", anchor="w",
                                    font=("Segoe UI", 9), bd=0)
        self.status_left.pack(side="left")
        self.status_message = tk.Label(self.statusbar, text="", anchor="center",
                                       font=("Segoe UI", 9), bd=0)
        self.status_message.pack(side="left", fill="x", expand=True, padx=12)
        self.status_right = tk.Label(self.statusbar, text="", anchor="e",
                                     font=("Segoe UI", 9), bd=0)
        self.status_right.pack(side="right")

    # ------------------------------------------------------------------
    # Open / close / history
    # ------------------------------------------------------------------
    def _start_initial_open(self) -> None:
        if self.initial_path:
            self.open_path(self.initial_path)

    def open_dialog(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Open PDF",
            filetypes=[("PDF documents", "*.pdf;*.PDF"), ("All files", "*.*")],
        )
        if path:
            self.open_path(path)

    def open_path(self, path: str, password: str | None = None) -> bool:
        if not path:
            return False
        # Block remote UNC paths to protect against NTLM credential harvesting over SMB
        if str(path).startswith(("\\\\", "//")):
            messagebox.showerror(APP_NAME, "Opening remote network (UNC) paths is blocked to protect your system.", parent=self)
            return False
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            safe_display = "".join(c for c in path if c.isprintable() and c not in "\r\n\t")
            messagebox.showerror(APP_NAME, f"File not found:\n{safe_display}", parent=self)
            return False
        if self.modified and not self._confirm_save_changes():
            return False
        doc: PdfDocument | None = None
        for attempt in range(3):
            try:
                doc = PdfDocument(path, password)
                break
            except PasswordRequired:
                password = self._ask_password(path)
                if password is None:
                    return False
            except WrongPassword:
                messagebox.showerror(APP_NAME, "That password did not open the PDF.", parent=self)
                password = self._ask_password(path)
                if password is None:
                    return False
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"Could not open this PDF:\n{exc}", parent=self)
                return False
        if doc is None:
            return False

        old = self.doc
        self.doc = doc
        self.path = doc.path
        self.current_page = 0
        self.history = [0]
        self.history_index = 0
        self._search_token += 1
        if self._search_cancel is not None:
            self._search_cancel.set()
        self._search_matches = []
        self._search_query = ""
        self._search_index = -1
        self.modified = False
        self.view.cancel_signature_placement(silent=True)
        self.view.clear_document()
        self.thumbnails.set_document(doc)
        self.view.set_document(doc, initial_fit="width")
        self._build_bookmarks(doc)
        self._update_info_panel()
        self._add_recent(path)
        self._set_doc_buttons(True)
        self._update_mode_ui()
        self.page_total.configure(text=f"/ {doc.page_count}")
        self._refresh_title()
        self.set_status(f"Opened {os.path.basename(path)}", 4_000)
        self._refresh_status()
        if old is not None:
            old.close()
        return True

    def close_document(self) -> None:
        if self.doc is None:
            return
        if self.modified and not self._confirm_save_changes():
            return
        if self._search_cancel is not None:
            self._search_cancel.set()
        self.view.cancel_signature_placement(silent=True)
        self.view.clear_document()
        self.thumbnails.set_document(None)
        self._clear_bookmarks()
        self.doc.close()
        self.doc = None
        self.path = None
        self.modified = False
        self.current_page = 0
        self.history = [0]
        self.history_index = 0
        self._set_doc_buttons(False)
        self.page_var.set("")
        self.page_total.configure(text="/ —")
        self.title(f"{APP_NAME} — lightweight PDF reader")
        self.status_left.configure(text="No document open")
        self.status_right.configure(text="")
        self._update_info_panel()
        self.view._draw_empty_state()

    def _add_recent(self, path: str) -> None:
        if path in self.recent:
            self.recent.remove(path)
        self.recent.insert(0, path)
        self.recent = self.recent[:10]
        self._save_settings()
        self._populate_recent_menu()

    def _populate_recent_menu(self) -> None:
        self.recent_menu.delete(0, "end")
        if not self.recent:
            self.recent_menu.add_command(label="No recent documents", state="disabled")
            return
        for path in self.recent:
            label = os.path.basename(path)
            parent = os.path.dirname(path)
            if len(parent) > 55:
                parent = "…" + parent[-52:]
            self.recent_menu.add_command(
                label=f"{label}  —  {parent}",
                command=lambda p=path: self.open_path(p))
        self.recent_menu.add_separator()
        self.recent_menu.add_command(label="Clear Recent Documents", command=self._clear_recent)

    def _clear_recent(self) -> None:
        self.recent = []
        self._save_settings()
        self._populate_recent_menu()

    def _refresh_title(self) -> None:
        if self.doc is None or not self.path:
            self.title(f"{APP_NAME} — lightweight PDF reader")
            return
        suffix = " *" if self.modified else ""
        self.title(f"{os.path.basename(self.path)}{suffix} — {APP_NAME}")

    def mark_modified(self) -> None:
        if not self.modified:
            self.modified = True
            self._refresh_title()

    def _confirm_save_changes(self) -> bool:
        if not self.modified:
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved changes",
            "This PDF has unsaved changes.\n\n"
            "Save changes before closing or opening another PDF?",
            parent=self)
        if answer is None:
            return False
        if answer:
            return self.save_document()
        return True

    def save_document(self) -> bool:
        if self.doc is None:
            self.set_status("Open a PDF first")
            return False
        if not self.doc.path or self.doc.path.startswith("<"):
            return self.save_as()
        try:
            saved_path = self.doc.save()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not save the PDF:\n{exc}", parent=self)
            return False
        self.doc.path = saved_path
        self.path = saved_path
        self.modified = False
        self._refresh_title()
        self._add_recent(saved_path)
        self._refresh_status()
        self.set_status(f"Saved: {os.path.basename(saved_path)}", 4_000)
        return True

    def save_as(self, quiet: bool = False) -> bool:
        if self.doc is None:
            self.set_status("Open a PDF first")
            return False
        source = os.path.abspath(self.doc.path)
        base = os.path.splitext(os.path.basename(source))[0]
        target = filedialog.asksaveasfilename(
            parent=self,
            title="Save PDF As",
            initialdir=os.path.dirname(source),
            initialfile=f"{base}-edited.pdf",
            defaultextension=".pdf",
            filetypes=[("PDF documents", "*.pdf")],
        )
        if not target:
            return False
        if str(target).startswith(("\\\\", "//")):
            messagebox.showwarning("Security Warning", "Saving to remote network (UNC) paths is blocked for security.", parent=self)
            return False
        target = os.path.abspath(target)
        if os.path.normcase(target) == os.path.normcase(source):
            return self.save_document()
        try:
            self.doc.save_copy(target)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not save the PDF:\n{exc}", parent=self)
            return False
        self.doc.path = target
        self.path = target
        self.modified = False
        self._refresh_title()
        self._add_recent(target)
        self._refresh_status()
        self.set_status(f"Saved a copy: {os.path.basename(target)}", 6_000)
        if not quiet:
            messagebox.showinfo(
                APP_NAME,
                f"Saved PDF copy to:\n\n{target}",
                parent=self)
        return True

    # ------------------------------------------------------------------
    # Content Editing: Text, Images, Tables
    # ------------------------------------------------------------------
    def set_read_mode(self) -> None:
        self.set_mode(MODE_READ)

    def set_select_mode(self) -> None:
        self.set_mode(MODE_SELECT)

    def set_mode(self, mode: str) -> None:
        if self.doc is None:
            self.set_status("Open a PDF first")
            return
        self.view.set_mode(mode)
        if hasattr(self, "mode_var") and self.mode_var is not None:
            self.mode_var.set(mode)
        if hasattr(self, "edit_mode_var") and self.edit_mode_var is not None:
            self.edit_mode_var.set(mode == MODE_EDIT)
        self._update_mode_ui()

    def _update_mode_ui(self) -> None:
        mode = self.view.mode
        if mode == MODE_READ:
            self.read_mode_btn.configure(style="Accent.TButton", text="✋ Read Mode")
            self.select_mode_btn.configure(style="Toolbar.TButton", text="I Select Mode")
            self.edit_mode_btn.configure(style="Toolbar.TButton", text="✎ Edit Mode")
            self.set_status("Read Mode active — Drag to pan with touch or mouse, click links", 5_000)
        elif mode == MODE_SELECT:
            self.read_mode_btn.configure(style="Toolbar.TButton", text="✋ Read Mode")
            self.select_mode_btn.configure(style="Accent.TButton", text="I Select Mode")
            self.edit_mode_btn.configure(style="Toolbar.TButton", text="✎ Edit Mode")
            self.set_status("Select Mode active — Drag to select text and display action menu", 5_000)
        elif mode == MODE_EDIT:
            self.read_mode_btn.configure(style="Toolbar.TButton", text="✋ Read Mode")
            self.select_mode_btn.configure(style="Toolbar.TButton", text="I Select Mode")
            self.edit_mode_btn.configure(style="Accent.TButton", text="✎ Editing ON")
            self.set_status("Click-to-Edit ON — Click anywhere in the document to edit or insert content", 8_000)

    def toggle_edit_mode(self) -> None:
        if self.doc is None:
            self.set_status("Open a PDF first")
            return
        if self.view.mode == MODE_EDIT:
            self.set_mode(MODE_READ)
        else:
            self.set_mode(MODE_EDIT)

    def handle_click_to_edit(self, page_num: int, point: tuple[float, float]) -> None:
        if self.doc is None:
            return
        elem = get_element_at_point(self.doc, page_num, point)
        kind = elem.get("type")

        if kind == "table":
            table = elem["data"]
            dialog = TableEditorDialog(
                self,
                initial_data=table["data"],
                title=f"Edit Table on Page {page_num + 1}",
                initial_rect=table["bbox"],
            )
            if dialog.result:
                try:
                    delete_content_rect(self.doc, page_num, table["bbox"])
                    draw_table_in_pdf(
                        self.doc,
                        page_num,
                        dialog.result["rect"],
                        dialog.result["data"],
                        has_header=dialog.result["has_header"],
                    )
                except Exception as exc:
                    messagebox.showerror(APP_NAME, f"Could not update table: {exc}", parent=self)
                    return
                self.mark_modified()
                self.view._invalidate_page(page_num)
                self.thumbnails.invalidate_page(page_num)
                self.set_status(f"Updated table on page {page_num + 1}", 5_000)

        elif kind == "image":
            img = elem["data"]
            dialog = ManageImagesDialog(self, [img], page_num)
            if dialog.result:
                action = dialog.result.get("action")
                try:
                    if action == "delete":
                        delete_content_rect(self.doc, page_num, dialog.result["rect"])
                        self.set_status(f"Deleted image on page {page_num + 1}", 5_000)
                    elif action == "replace":
                        replace_image_in_pdf(self.doc, page_num, dialog.result["rect"], dialog.result["new_path"])
                        self.set_status(f"Replaced image on page {page_num + 1}", 5_000)
                except Exception as exc:
                    messagebox.showerror(APP_NAME, f"Could not update image: {exc}", parent=self)
                    return
                self.mark_modified()
                self.view._invalidate_page(page_num)
                self.thumbnails.invalidate_page(page_num)

        elif kind == "text_line":
            dialog = EditTextDialog(
                self,
                initial_text=elem["text"],
                initial_size=elem["fontsize"],
                title=f"Edit Text (Page {page_num + 1})",
            )
            if dialog.result:
                try:
                    replace_text_in_pdf(
                        self.doc,
                        page_num,
                        elem["bbox"],
                        dialog.result["text"],
                        fontname=dialog.result["fontname"],
                        fontsize=dialog.result["fontsize"],
                        text_color=dialog.result["color"],
                    )
                except Exception as exc:
                    messagebox.showerror(APP_NAME, f"Could not edit text: {exc}", parent=self)
                    return
                self.mark_modified()
                self.view.clear_selection()
                self.view._invalidate_page(page_num)
                self.thumbnails.invalidate_page(page_num)
                self.set_status(f"Updated text on page {page_num + 1}", 5_000)

        elif kind == "empty":
            choice_dialog = InsertChoiceDialog(self, point)
            choice = choice_dialog.result
            if choice == "text":
                self.add_text_at(page_num, point)
            elif choice == "image":
                self.add_image_at(page_num, point)
            elif choice == "table":
                self.add_table_at(page_num, point)
    def action_edit_or_add_text(self) -> None:
        if self.doc is None:
            self.set_status("Open a PDF first")
            return
        if self.view.selection_text and self.view.selection_page_rects:
            page = self.view.selection_page if self.view.selection_page is not None else self.current_page
            self.edit_selection(page)
        else:
            self.add_text_at(self.current_page, (50.0, 100.0))

    def edit_selection(self, page_num: int) -> None:
        if self.doc is None:
            return
        text = self.view.selection_text
        rects = self.view.selection_page_rects
        if not text or not rects:
            self.set_status("Select text to edit first")
            return
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[2] for r in rects)
        y1 = max(r[3] for r in rects)
        bbox = (x0, y0, x1, y1)

        props = get_text_properties_at_rect(self.doc, page_num, bbox)
        dialog = EditTextDialog(
            self,
            initial_text=text,
            initial_size=props["fontsize"],
            initial_font=props["fontname"],
            initial_color=props["color"],
            title="Edit Selected Text",
        )
        if dialog.result:
            try:
                replace_selected_text_in_pdf(
                    self.doc,
                    page_num,
                    bbox,
                    dialog.result["text"],
                    fontname=dialog.result["fontname"],
                    fontsize=dialog.result["fontsize"],
                    text_color=dialog.result["color"],
                )
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"Could not edit text: {exc}", parent=self)
                return
            self.mark_modified()
            self.view.clear_selection()
            self.view._invalidate_page(page_num)
            self.thumbnails.invalidate_page(page_num)
            self.set_status(f"Updated text on page {page_num + 1}", 5_000)

    def action_delete_selection(self) -> None:
        if self.doc is None:
            self.set_status("Open a PDF first")
            return
        page = self.view.selection_page if self.view.selection_page is not None else self.current_page
        if not self.view.selection_page_rects:
            self.set_status("No text selected to delete")
            return
        self.delete_selection(page)

    def delete_selection(self, page_num: int) -> None:
        if self.doc is None:
            return
        rects = self.view.selection_page_rects
        if not rects:
            self.set_status("No text selected to delete")
            return
        if not messagebox.askyesno(APP_NAME, "Delete / redact selected text on this page?", parent=self):
            return
        try:
            for r in rects:
                delete_content_rect(self.doc, page_num, r)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not delete text: {exc}", parent=self)
            return
        self.mark_modified()
        self.view.clear_selection()
        self.view._invalidate_page(page_num)
        self.thumbnails.invalidate_page(page_num)
        self.set_status(f"Deleted selected text on page {page_num + 1}", 5_000)

    def add_text_at(self, page_num: int, point: tuple[float, float]) -> None:
        if self.doc is None:
            self.set_status("Open a PDF first")
            return
        dialog = EditTextDialog(self, initial_text="", title="Insert New Text Box")
        if dialog.result:
            try:
                insert_text_in_pdf(
                    self.doc,
                    page_num,
                    point,
                    dialog.result["text"],
                    fontname=dialog.result["fontname"],
                    fontsize=dialog.result["fontsize"],
                    text_color=dialog.result["color"],
                )
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"Could not insert text: {exc}", parent=self)
                return
            self.mark_modified()
            self.view._invalidate_page(page_num)
            self.thumbnails.invalidate_page(page_num)
            self.set_status(f"Inserted text on page {page_num + 1}", 5_000)

    def add_image_at(self, page_num: int, point: tuple[float, float] | None = None) -> None:
        if self.doc is None:
            self.set_status("Open a PDF first")
            return
        page_rect = self.doc.page(page_num).rect
        dialog = AddImageDialog(self, page_rect.width, page_rect.height)
        if dialog.result:
            rect = dialog.result["rect"]
            if point is not None:
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                rect = (point[0], point[1], point[0] + w, point[1] + h)
            try:
                insert_image_in_pdf(self.doc, page_num, dialog.result["image_path"], rect)
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"Could not insert image: {exc}", parent=self)
                return
            self.mark_modified()
            self.view._invalidate_page(page_num)
            self.thumbnails.invalidate_page(page_num)
            self.set_status(f"Inserted image on page {page_num + 1}", 5_000)

    def manage_images(self, page_num: int) -> None:
        if self.doc is None:
            self.set_status("Open a PDF first")
            return
        images = get_page_images(self.doc, page_num)
        if not images:
            if messagebox.askyesno(APP_NAME, f"No embedded images found on page {page_num + 1}.\n\nWould you like to insert an image?", parent=self):
                self.add_image_at(page_num)
            return
        dialog = ManageImagesDialog(self, images, page_num)
        if dialog.result:
            action = dialog.result.get("action")
            try:
                if action == "delete":
                    delete_content_rect(self.doc, page_num, dialog.result["rect"])
                    self.set_status(f"Deleted image on page {page_num + 1}", 5_000)
                elif action == "replace":
                    replace_image_in_pdf(self.doc, page_num, dialog.result["rect"], dialog.result["new_path"])
                    self.set_status(f"Replaced image on page {page_num + 1}", 5_000)
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"Could not update image: {exc}", parent=self)
                return
            self.mark_modified()
            self.view._invalidate_page(page_num)
            self.thumbnails.invalidate_page(page_num)

    def add_table_at(self, page_num: int, point: tuple[float, float] | None = None) -> None:
        if self.doc is None:
            self.set_status("Open a PDF first")
            return
        initial_rect = (point[0], point[1], point[0] + 480.0, point[1] + 160.0) if point else (48.0, 100.0, 564.0, 260.0)
        dialog = TableEditorDialog(self, title="Insert New Table", initial_rect=initial_rect)
        if dialog.result:
            try:
                draw_table_in_pdf(
                    self.doc,
                    page_num,
                    dialog.result["rect"],
                    dialog.result["data"],
                    has_header=dialog.result["has_header"],
                )
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"Could not insert table: {exc}", parent=self)
                return
            self.mark_modified()
            self.view._invalidate_page(page_num)
            self.thumbnails.invalidate_page(page_num)
            self.set_status(f"Inserted table on page {page_num + 1}", 5_000)

    def edit_tables_on_page(self, page_num: int) -> None:
        if self.doc is None:
            self.set_status("Open a PDF first")
            return
        tables = get_page_tables(self.doc, page_num)
        if not tables:
            if messagebox.askyesno(APP_NAME, f"No structured tables were automatically detected on page {page_num + 1}.\n\nWould you like to insert a new table?", parent=self):
                self.add_table_at(page_num)
            return
        table = tables[0]
        dialog = TableEditorDialog(self, initial_data=table["data"], title=f"Edit Table on Page {page_num + 1}", initial_rect=table["bbox"])
        if dialog.result:
            try:
                delete_content_rect(self.doc, page_num, table["bbox"])
                draw_table_in_pdf(
                    self.doc,
                    page_num,
                    dialog.result["rect"],
                    dialog.result["data"],
                    has_header=dialog.result["has_header"],
                )
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"Could not update table: {exc}", parent=self)
                return
            self.mark_modified()
            self.view._invalidate_page(page_num)
            self.thumbnails.invalidate_page(page_num)
            self.set_status(f"Updated table on page {page_num + 1}", 5_000)

    def _remember_page(self, page: int) -> None:
        if self.history_index < len(self.history) - 1:
            self.history = self.history[:self.history_index + 1]
        if not self.history or self.history[-1] != page:
            self.history.append(page)
            self.history_index = len(self.history) - 1
        self._update_history_buttons()

    def go_to_page(self, page_number: int, remember: bool = False) -> None:
        if self.doc is None:
            return
        page_number = max(1, min(int(page_number), self.doc.page_count))
        old_page = self.current_page
        if remember and page_number - 1 != old_page:
            self._remember_page(old_page)
        self.current_page = page_number - 1
        self.view.scroll_to_page(self.current_page)
        self.thumbnails.select_page(self.current_page)
        self.page_var.set(str(page_number))
        self._refresh_status()
        self._update_history_buttons()

    def history_back(self) -> None:
        if self.history_index <= 0:
            return
        self.history_index -= 1
        page = self.history[self.history_index]
        self.current_page = page
        self.view.scroll_to_page(page)
        self.thumbnails.select_page(page)
        self.page_var.set(str(page + 1))
        self._refresh_status()
        self._update_history_buttons()

    def history_forward(self) -> None:
        if self.history_index >= len(self.history) - 1:
            return
        self.history_index += 1
        page = self.history[self.history_index]
        self.current_page = page
        self.view.scroll_to_page(page)
        self.thumbnails.select_page(page)
        self.page_var.set(str(page + 1))
        self._refresh_status()
        self._update_history_buttons()

    def _update_history_buttons(self) -> None:
        if self.doc is None:
            for btn in (self.back_btn, self.fwd_btn):
                btn.state(["disabled"])
            return
        self.back_btn.state(["!disabled"] if self.history_index > 0 else ["disabled"])
        self.fwd_btn.state(["!disabled"] if self.history_index < len(self.history) - 1
                           else ["disabled"])

    def _set_doc_buttons(self, enabled: bool) -> None:
        state = ["!disabled"] if enabled else ["disabled"]
        for button in self._doc_buttons:
            if button is self.back_btn or button is self.fwd_btn:
                continue
            button.state(state)
        if enabled:
            self._update_history_buttons()
        else:
            self.back_btn.state(["disabled"])
            self.fwd_btn.state(["disabled"])

    # ------------------------------------------------------------------
    # Page callbacks and status
    # ------------------------------------------------------------------
    def on_page_changed(self, page: int, zoom_pct: float) -> None:
        self.current_page = page
        self.page_var.set(str(page + 1))
        zoom_text = f"{zoom_pct:.0f}" if abs(zoom_pct - round(zoom_pct)) < 0.05 else f"{zoom_pct:.1f}"
        self.zoom_var.set(zoom_text)
        self.thumbnails.select_page(page)
        self._refresh_status()

    def _refresh_status(self) -> None:
        if self.doc is None:
            return
        zoom_text = f"{self.view.zoom * 100:.0f}%"
        self.status_left.configure(
            text=f"Page {self.current_page + 1} / {self.doc.page_count}   {zoom_text}")
        self.status_right.configure(
            text=f"{os.path.basename(self.path or '')}   {human_size(self._file_size())}")
        if self._search_matches:
            self.find_status.configure(
                text=f"Match {self._search_index + 1} of {len(self._search_matches)}"
                if self._search_index >= 0 else
                f"{len(self._search_matches)} matches")
        self._update_history_buttons()

    def _file_size(self) -> int:
        try:
            return os.path.getsize(self.path) if self.path else 0
        except OSError:
            return 0

    def set_status(self, message: str, timeout_ms: int = 4_000) -> None:
        self.status_message.configure(text=message)
        if self._status_after is not None:
            self.after_cancel(self._status_after)
        self._status_after = self.after(timeout_ms, self._clear_status_message)

    def _clear_status_message(self) -> None:
        self._status_after = None
        self.status_message.configure(text="")

    # ------------------------------------------------------------------
    # View actions
    # ------------------------------------------------------------------
    def fit_width(self) -> None:
        if self.doc:
            self.view.fit_width()

    def fit_page(self) -> None:
        if self.doc:
            self.view.fit_page()

    def actual_size(self) -> None:
        if self.doc:
            self.view.actual_size()

    def zoom_in(self) -> None:
        if self.doc:
            self.view.zoom_in()

    def zoom_out(self) -> None:
        if self.doc:
            self.view.zoom_out()

    def rotate_cw(self) -> None:
        if self.doc:
            self.view.rotate_cw()

    def rotate_ccw(self) -> None:
        if self.doc:
            self.view.rotate_ccw()

    def reset_rotation(self) -> None:
        if self.doc:
            self.view.reset_rotation()

    def previous_page(self) -> None:
        if self.doc is None:
            return
        new = max(0, self.current_page - 1)
        self.go_to_page(new + 1, remember=new != self.current_page)

    def next_page(self) -> None:
        if self.doc is None:
            return
        new = min(self.doc.page_count - 1, self.current_page + 1)
        self.go_to_page(new + 1, remember=new != self.current_page)

    def first_page(self) -> None:
        self.go_to_page(1, remember=True)

    def last_page(self) -> None:
        if self.doc:
            self.go_to_page(self.doc.page_count, remember=True)

    def copy_selection(self) -> None:
        self.view.copy_selection()

    def copy_page_text(self) -> None:
        self.view.copy_page_text()

    def copy_document_text(self) -> None:
        self.view.copy_document_text()

    def select_all(self) -> None:
        if self.doc:
            self.view.select_all_page()

    def select_all_document(self) -> None:
        if self.doc:
            self.view.select_all_document()

    def show_go_to_page(self) -> None:
        if self.doc is None:
            self.set_status("Open a PDF first")
            return
        self.page_entry.focus_set()
        self.page_entry.select_range(0, "end")

    # ------------------------------------------------------------------
    # Signatures and Word export
    # ------------------------------------------------------------------
    def hand_signature(self) -> None:
        if self.doc is None:
            self.set_status("Open a PDF first")
            return
        self.view.cancel_signature_placement(silent=True)
        result = ask_signature(self, self.palette)
        if result is None:
            return
        self.view.begin_signature_placement(result.strokes, result.ratio,
                                            result.width_pt)

    def _choose_word_mode(self, soffice_available: bool) -> str | None:
        dialog = tk.Toplevel(self)
        dialog.title("Convert PDF to Word")
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.configure(background=self.palette["bg"])
        frame = ttk.Frame(dialog, padding=(20, 16))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Choose a Word output", style="Brand.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(frame, text="The PDF is not modified. A new .docx file is created.",
                  foreground=self.palette["muted"]).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))
        mode = tk.StringVar(value="reflow")
        choice = {"value": None}
        ttk.Radiobutton(frame, text="Editable reflow — compact paragraphs and headings",
                        variable=mode, value="reflow").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(0, 3))
        ttk.Label(frame, text="Best for editing. Original page positions and images are not retained.",
                  foreground=self.palette["muted"], wraplength=560).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=(24, 0), pady=(0, 12))
        layout_state = "normal" if soffice_available else "disabled"
        ttk.Radiobutton(frame, text="Layout-faithful — LibreOffice Writer PDF import",
                        variable=mode, value="layout", state=layout_state).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(0, 3))
        detail = ("Keep the visual page layout; editable text lives in text frames."
                  if soffice_available else
                  "LibreOffice was not found. Editable reflow is available.")
        ttk.Label(frame, text=detail, foreground=self.palette["muted"],
                  wraplength=560).grid(row=5, column=0, columnspan=2,
                                       sticky="w", padx=(24, 0), pady=(0, 16))
        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e")
        def accept() -> None:
            choice["value"] = mode.get()
            dialog.destroy()

        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Continue", style="Accent.TButton",
                   command=accept).pack(side="left")
        dialog.grab_set()
        self.wait_window(dialog)
        selected = choice["value"]
        if selected == "layout" and not soffice_available:
            return "reflow"
        return selected

    def export_word(self) -> None:
        if self.doc is None:
            self.set_status("Open a PDF first")
            return
        if self._word_dialog_open:
            self.set_status("A Word conversion is already running")
            return
        soffice = find_soffice()
        mode = self._choose_word_mode(bool(soffice))
        if mode is None:
            return
        source = os.path.abspath(self.doc.path)
        target = filedialog.asksaveasfilename(
            parent=self,
            title="Save as Word document",
            initialdir=os.path.dirname(source),
            initialfile=f"{os.path.splitext(os.path.basename(source))[0]}.docx",
            defaultextension=".docx",
            filetypes=[("Word document", "*.docx")],
        )
        if not target:
            return
        if str(target).startswith(("\\\\", "//")):
            messagebox.showwarning("Security Warning", "Saving to remote network (UNC) paths is blocked for security.", parent=self)
            return
        target = os.path.abspath(target)
        self._start_word_conversion(target, mode)

    def _start_word_conversion(self, target: str, mode: str) -> None:
        self._word_dialog_open = True
        dialog = tk.Toplevel(self)
        dialog.title("Converting to Word")
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.configure(background=self.palette["bg"])
        frame = ttk.Frame(dialog, padding=(24, 18))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Converting PDF to Word…", font=("Segoe UI", 12, "bold")).pack(
            anchor="w", pady=(0, 6))

        total_pages = self.doc.page_count if self.doc else 1
        page_status_lbl = ttk.Label(
            frame,
            text=f"Preparing conversion for {total_pages} page(s)…",
            font=("Segoe UI", 10, "bold"),
            foreground=self.palette["accent"],
        )
        page_status_lbl.pack(anchor="w", pady=(0, 6))

        ttk.Label(frame, text=(f"Target: {target}\n"
                               f"Mode: {'LibreOffice Layout-Faithful' if mode == 'layout' else 'Editable Reflow'}"),
                  foreground=self.palette["muted"], justify="left", wraplength=500).pack(
            anchor="w", pady=(0, 10))

        progress = ttk.Progressbar(frame, mode="determinate", length=460, maximum=total_pages)
        progress.pack(fill="x", pady=(0, 6))
        progress["value"] = 0

        percent_lbl = ttk.Label(frame, text="0%", font=("Segoe UI", 9), foreground=self.palette["muted"])
        percent_lbl.pack(anchor="e", pady=(0, 8))

        dialog.protocol("WM_DELETE_WINDOW", lambda: None)
        dialog.grab_set()
        message_queue: queue.Queue = queue.Queue()

        def on_progress(current: int, total: int, detail: str = "") -> None:
            message_queue.put(("progress", current, total, detail))

        def worker() -> None:
            try:
                method = convert_pdf_to_word(
                    self.doc.path, target,
                    self.doc.password, mode,
                    progress_callback=on_progress,
                ) if self.doc else None
            except Exception as exc:
                message_queue.put(("error", str(exc)))
            else:
                message_queue.put(("done", method))

        threading.Thread(target=worker, daemon=True, name="pdf-word-export").start()
        self.after(50, lambda: self._poll_word(message_queue, dialog, target, mode, progress, page_status_lbl, percent_lbl))

    def _poll_word(self, message_queue: queue.Queue, dialog: tk.Toplevel,
                   target: str, mode: str, progress: ttk.Progressbar,
                   page_status_lbl: ttk.Label, percent_lbl: ttk.Label) -> None:
        messages = []
        while True:
            try:
                messages.append(message_queue.get_nowait())
            except queue.Empty:
                break

        for message in messages:
            if message[0] == "progress":
                curr, total, detail = message[1], message[2], message[3]
                progress.configure(maximum=total, value=curr)
                pct = int(round((curr / max(1, total)) * 100))
                percent_lbl.configure(text=f"{pct}%")
                if mode == "layout":
                    page_status_lbl.configure(text=detail or f"Step {curr} of {total}…")
                    self.set_status(f"Word export: {detail or f'Step {curr} of {total}'}", 10_000)
                else:
                    page_status_lbl.configure(text=f"Converting page {curr} of {total}…")
                    self.set_status(f"Converting page {curr} of {total} to Word…", 10_000)

            elif message[0] == "error":
                self._word_dialog_open = False
                try:
                    dialog.grab_release()
                    dialog.destroy()
                except tk.TclError:
                    pass
                self.set_status("Word conversion failed")
                messagebox.showerror(APP_NAME, f"Could not create the Word document:\n\n{message[1]}",
                                     parent=self)
                return

            elif message[0] == "done":
                self._word_dialog_open = False
                try:
                    dialog.grab_release()
                    dialog.destroy()
                except tk.TclError:
                    pass
                method = message[1]
                self.set_status(f"Word document created: {os.path.basename(target)}", 10_000)
                messagebox.showinfo(
                    APP_NAME,
                    f"Word document created successfully!\n\n"
                    f"{target}\n\n"
                    f"Method: {method}",
                    parent=self)
                return

        if self._word_dialog_open:
            self.after(50, lambda: self._poll_word(message_queue, dialog, target, mode, progress, page_status_lbl, percent_lbl))

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------
    def toggle_sidebar(self) -> None:
        self.sidebar_visible = not bool(self.sidebar.grid_info())
        if self.sidebar_visible:
            self.sidebar.grid()
        else:
            self.sidebar.grid_remove()
        self._save_settings()

    def _build_bookmarks(self, doc: PdfDocument) -> None:
        self.toc.delete(*self.toc.get_children())
        self._bookmark_pages = {}
        outline = doc.outline()
        if not outline:
            self.toc.insert("", "end", text="This PDF has no embedded bookmarks",
                            values=("",))
            return
        parents: dict[int, str] = {0: ""}
        for level, title, page in outline:
            level = max(1, min(level, 8))
            parent = parents.get(level - 1, "")
            if parent and not self.toc.exists(parent):
                parent = ""
            item = self.toc.insert(parent, "end", text=title, values=(page,), open=True)
            self._bookmark_pages[item] = page
            parents[level] = item
            for deeper in list(parents):
                if deeper > level:
                    parents.pop(deeper, None)
        self.notebook.tab(1, text=f"Bookmarks ({len(outline)})")

    def _clear_bookmarks(self) -> None:
        self.toc.delete(*self.toc.get_children())
        self._bookmark_pages = {}
        self.toc.insert("", "end", text="Open a PDF to see its bookmarks", values=("",))
        self.notebook.tab(1, text="Bookmarks")

    def _on_toc_select(self, _event=None) -> None:
        selection = self.toc.selection()
        if not selection:
            return
        page = self._bookmark_pages.get(selection[0])
        if page:
            self.go_to_page(page, remember=True)

    def _update_info_panel(self) -> None:
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        if self.doc is None:
            self.info_text.insert("end", "No document open.\n\nOpen a PDF with Ctrl+O.")
        else:
            info = self.doc.info()
            rows = [
                ("File", os.path.basename(info.path)),
                ("Pages", str(info.page_count)),
                ("Size", human_size(info.file_size)),
                ("Title", info.title or "—"),
                ("Author", info.author or "—"),
                ("Subject", info.subject or "—"),
                ("Keywords", info.keywords or "—"),
                ("Creator", info.creator or "—"),
                ("Producer", info.producer or "—"),
                ("Created", info.created or "—"),
                ("Modified", info.modified or "—"),
                ("PDF format", info.format or "—"),
                ("Encryption", info.encryption or "None"),
            ]
            for label, value in rows:
                self.info_text.insert("end", f"{label}\n", ("label",))
                self.info_text.insert("end", f"{value}\n\n")
            self.info_text.tag_configure("label", foreground=self.palette["muted"],
                                         font=("Segoe UI", 8, "bold"))
        self.info_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------
    def _ask_password(self, path: str) -> str | None:
        dialog = tk.Toplevel(self)
        dialog.title("Password required")
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.configure(background=self.palette["bg"])
        clean_name = "".join(c for c in os.path.basename(path) if c.isprintable() and c not in "\r\n\t")
        ttk.Label(dialog, text=f"Enter the password for\n{clean_name}",
                  style="TLabel", justify="left").grid(row=0, column=0, columnspan=2,
                                                      sticky="w", padx=18, pady=(18, 8))
        entry = ttk.Entry(dialog, width=34, show="*")
        entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 14))
        result: list[str | None] = [None]

        def ok() -> None:
            result[0] = entry.get()
            dialog.destroy()

        def cancel() -> None:
            result[0] = None
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.grid(row=2, column=0, columnspan=2, sticky="e", padx=18, pady=(0, 18))
        ttk.Button(buttons, text="Cancel", command=cancel).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Open", style="Accent.TButton", command=ok).pack(side="left")
        entry.bind("<Return>", lambda _e: ok())
        entry.bind("<Escape>", lambda _e: cancel())
        dialog.grab_set()
        entry.focus_set()
        self.wait_window(dialog)
        return result[0]

    def show_properties(self) -> None:
        if self.doc is None:
            self.set_status("Open a PDF first")
            return
        info: DocumentInfo = self.doc.info()
        dialog = tk.Toplevel(self)
        dialog.title(f"Properties — {os.path.basename(info.path)}")
        dialog.transient(self)
        dialog.geometry("620x470")
        dialog.configure(background=self.palette["bg"])
        frame = ttk.Frame(dialog, padding=(18, 14))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Document properties", style="Brand.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        rows = [
            ("File", info.path),
            ("Pages", str(info.page_count)),
            ("File size", human_size(info.file_size)),
            ("Title", info.title or "—"),
            ("Author", info.author or "—"),
            ("Subject", info.subject or "—"),
            ("Keywords", info.keywords or "—"),
            ("Creator", info.creator or "—"),
            ("Producer", info.producer or "—"),
            ("Created", info.created or "—"),
            ("Modified", info.modified or "—"),
            ("PDF format", info.format or "—"),
            ("Encryption", info.encryption or "None"),
        ]
        for row, (label, value) in enumerate(rows, start=1):
            ttk.Label(frame, text=label, foreground=self.palette["muted"],
                      width=12).grid(row=row, column=0, sticky="nw", pady=2)
            ttk.Label(frame, text=value, wraplength=430, justify="left").grid(
                row=row, column=1, sticky="nw", pady=2)
        ttk.Button(frame, text="Close", command=dialog.destroy).grid(
            row=len(rows) + 2, column=1, sticky="e", pady=(16, 0))
        dialog.grab_set()
        self.wait_window(dialog)

    def show_shortcuts(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Keyboard Shortcuts")
        dialog.transient(self)
        dialog.configure(background=self.palette["bg"])
        frame = ttk.Frame(dialog, padding=(20, 16))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="PDF Lite keyboard shortcuts", style="Brand.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        shortcuts = [
            ("Ctrl+O", "Open PDF"),
            ("Ctrl+S", "Save changes to PDF"),
            ("Ctrl+Shift+S", "Save As (new copy)"),
            ("Ctrl+1", "Read Mode (touch screen / pan browsing)"),
            ("Ctrl+2", "Select Mode (text selection & popup menu)"),
            ("Ctrl+E", "Toggle Click-to-Edit mode"),
            ("Alt+Click", "Click anywhere to edit element"),
            ("Ctrl+T", "Add text box"),
            ("Ctrl+I", "Insert image"),
            ("Ctrl+Shift+E", "Convert PDF to Word"),
            ("Ctrl+W", "Close document"),
            ("Ctrl+F", "Find text"),
            ("F3 / Shift+F3", "Find next / previous"),
            ("Ctrl+G", "Go to page"),
            ("Ctrl+C", "Copy selection (or current page text)"),
            ("Ctrl+Shift+C", "Copy current page text"),
            ("Ctrl+A", "Select all on page"),
            ("Ctrl+Shift+A", "Select entire document"),
            ("Ctrl++ / Ctrl+-", "Zoom in / out"),
            ("Ctrl+0", "Actual size (100%)"),
            ("Ctrl+R", "Rotate clockwise"),
            ("Ctrl+Shift+R", "Rotate counter-clockwise"),
            ("Page Up / Page Down", "Previous / next page"),
            ("Left / Right Arrow", "Previous / next page (slides)"),
            ("Ctrl+Home / Ctrl+End", "First / last page"),
            ("F4", "Show or hide sidebar"),
            ("F11 / Esc", "Enter / leave full screen"),
        ]
        for row, (key, action) in enumerate(shortcuts, start=1):
            ttk.Label(frame, text=key, foreground=self.palette["accent"],
                      font=("Consolas", 9)).grid(row=row, column=0, sticky="nw", pady=2)
            ttk.Label(frame, text=action).grid(row=row, column=1, sticky="nw", pady=2)
        ttk.Button(frame, text="Close", command=dialog.destroy).grid(
            row=len(shortcuts) + 2, column=1, sticky="e", pady=(18, 0))
        dialog.grab_set()
        self.wait_window(dialog)

    def show_about(self) -> None:
        messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_NAME} {APP_VERSION}\n\n"
            "A lightweight, Acrobat-style PDF reader and editor for Windows.\n"
            "Python + Tkinter + PyMuPDF.\n\n"
            "Reading features: continuous scroll, zoom, fit modes, text selection, "
            "clipboard copy, full-document search, page thumbnails, bookmarks, "
            "rotation, dark chrome, full screen and encrypted PDFs.\n\n"
            "Editing features: full editing of text (replace, insert text box, redact), "
            "images (insert, replace, remove), and tables (extract, edit cell grid, insert), "
            "plus atomic in-place saving and Save As.\n\n"
            "Tools: hand-drawn ink signatures and PDF-to-Word conversion (editable reflow "
            "or layout-faithful through LibreOffice).",
            parent=self)

    # ------------------------------------------------------------------
    # Full screen and theme
    # ------------------------------------------------------------------
    def toggle_fullscreen(self) -> None:
        self._toggle_fullscreen_to(not self._fullscreen)

    def _toggle_fullscreen_to(self, value: bool) -> None:
        self._fullscreen = value
        if value:
            self._fullscreen_sidebar = bool(self.sidebar.grid_info())
            self._fullscreen_search = bool(self.searchbar.grid_info())
            self.attributes("-fullscreen", True)
            if self._fullscreen_sidebar:
                self.sidebar.grid_remove()
            self.toolbar.grid_remove()
            self.searchbar.grid_remove()
            self.statusbar.grid_remove()
            self.config(menu="")
            self.body.grid_remove()
            self.body.grid(row=0, column=0, sticky="nsew")
            self.grid_rowconfigure(2, weight=0)
            self.grid_rowconfigure(0, weight=1)
        else:
            self.attributes("-fullscreen", False)
            self.body.grid_remove()
            self.body.grid(row=2, column=0, sticky="nsew")
            self.grid_rowconfigure(0, weight=0)
            self.grid_rowconfigure(2, weight=1)
            self.toolbar.grid(row=0, column=0, sticky="ew")
            self.statusbar.grid(row=3, column=0, sticky="ew")
            if self._fullscreen_sidebar:
                self.sidebar.grid()
            if self._fullscreen_search:
                self.searchbar.grid()
            self.config(menu=self.menubar)
        self.view.canvas.focus_set()

    def toggle_dark(self) -> None:
        self.dark_enabled = not self.dark_enabled
        if self.dark_var is not None:
            self.dark_var.set(self.dark_enabled)
        self._apply_palette()
        self._save_settings()

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------
    def _bind_keys(self) -> None:
        self.bind_all("<Control-o>", lambda _e: self.open_dialog())
        self.bind_all("<Control-f>", self._key_find)
        self.bind_all("<Control-g>", self._key_goto)
        self.bind_all("<Control-w>", lambda _e: self.close_document())
        self.bind_all("<Control-c>", self._key_copy)
        self.bind_all("<Control-Shift-C>", lambda _e: self.copy_page_text())
        self.bind_all("<Control-Shift-c>", lambda _e: self.copy_page_text())
        self.bind_all("<Control-a>", self._key_select_all)
        self.bind_all("<Control-Shift-A>", lambda _e: self.select_all_document())
        self.bind_all("<Control-Shift-a>", lambda _e: self.select_all_document())
        self.bind_all("<Control-plus>", lambda _e: self.zoom_in())
        self.bind_all("<Control-equal>", lambda _e: self.zoom_in())
        self.bind_all("<Control-minus>", lambda _e: self.zoom_out())
        self.bind_all("<Control-1>", lambda _e: self.set_read_mode())
        self.bind_all("<Control-2>", lambda _e: self.set_select_mode())
        self.bind_all("<Control-0>", lambda _e: self.actual_size())
        self.bind_all("<Control-r>", lambda _e: self.rotate_cw())
        self.bind_all("<Control-Shift-R>", lambda _e: self.rotate_ccw())
        self.bind_all("<Control-Shift-P>", lambda _e: self.show_properties())
        self.bind_all("<Control-s>", lambda _e: self.save_document())
        self.bind_all("<Control-Shift-S>", lambda _e: self.save_as())
        self.bind_all("<Control-e>", lambda _e: self.toggle_edit_mode())
        self.bind_all("<Control-t>", lambda _e: self.add_text_at(self.current_page, (50.0, 100.0)))
        self.bind_all("<Control-i>", lambda _e: self.add_image_at(self.current_page))
        self.bind_all("<Control-Shift-E>", lambda _e: self.export_word())
        self.bind_all("<F3>", lambda _e: self._find_next(1))
        self.bind_all("<Shift-F3>", lambda _e: self._find_next(-1))
        self.bind_all("<F4>", lambda _e: self.toggle_sidebar())
        self.bind_all("<F11>", lambda _e: self.toggle_fullscreen())
        self.bind_all("<Escape>", self._on_escape)
        self.bind_all("<Prior>", self._key_page_up)
        self.bind_all("<Next>", self._key_page_down)
        self.bind_all("<Control-Home>", lambda _e: self.first_page())
        self.bind_all("<Control-End>", lambda _e: self.last_page())
        self.bind_all("<space>", self._key_space)
        self.bind_all("<Right>", self._key_right)
        self.bind_all("<Left>", self._key_left)
        self.bind_all("<Down>", self._key_down)
        self.bind_all("<Up>", self._key_up)

    def is_slides(self) -> bool:
        return self.doc is not None and self.doc.is_slides()

    def _in_text_widget(self) -> bool:
        try:
            widget = self.focus_get()
            return isinstance(widget, (tk.Entry, ttk.Entry, tk.Text, tk.Spinbox))
        except Exception:
            return False

    def _key_find(self, event) -> None:
        if isinstance(event.widget, (tk.Entry, ttk.Entry)):
            return
        self.show_find()
        return "break"

    def _key_goto(self, event) -> None:
        if isinstance(event.widget, (tk.Entry, ttk.Entry)):
            return
        self.show_go_to_page()
        return "break"

    def _key_copy(self, event) -> None:
        if isinstance(event.widget, (tk.Entry, ttk.Entry, tk.Text)):
            return
        self.copy_selection()
        return "break"

    def _key_select_all(self, event) -> None:
        if isinstance(event.widget, (tk.Entry, ttk.Entry, tk.Text)):
            return
        if getattr(event, "state", 0) & 0x0001:  # Shift held
            self.select_all_document()
        else:
            self.select_all()
        return "break"

    def _key_page_up(self, event) -> None:
        if self._in_text_widget():
            return
        self.previous_page()
        return "break"

    def _key_page_down(self, event) -> None:
        if self._in_text_widget():
            return
        self.next_page()
        return "break"

    def _key_space(self, event) -> None:
        if self._in_text_widget():
            return
        if self.doc is None:
            return
        self.view.canvas.yview_scroll(max(20, int(self.view.canvas.winfo_height() * 0.85)), "units")
        self.view._after_scroll()
        return "break"

    def _key_right(self, event) -> None:
        if self._in_text_widget():
            return
        if self.doc is None:
            return
        self.next_page()
        return "break"

    def _key_left(self, event) -> None:
        if self._in_text_widget():
            return
        if self.doc is None:
            return
        self.previous_page()
        return "break"

    def _key_down(self, event) -> None:
        if self._in_text_widget():
            return
        if self.doc is None:
            return
        if self.is_slides() or self._fullscreen:
            self.next_page()
            return "break"
        self.view.canvas.yview_scroll(3, "units")
        self.view._after_scroll()
        return "break"

    def _key_up(self, event) -> None:
        if self._in_text_widget():
            return
        if self.doc is None:
            return
        if self.is_slides() or self._fullscreen:
            self.previous_page()
            return "break"
        self.view.canvas.yview_scroll(-3, "units")
        self.view._after_scroll()
        return "break"

    def _on_escape(self, _event=None) -> None:
        if self.view._placing_signature:
            self.view.cancel_signature_placement()
        elif self._fullscreen:
            self.toggle_fullscreen()
        elif self.searchbar.winfo_ismapped():
            self.hide_find()

    # ------------------------------------------------------------------
    # Closing and errors
    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        if self._word_dialog_open:
            self.set_status("Word conversion is still running; wait for it to finish")
            return
        if self.doc is not None and self.modified and not self._confirm_save_changes():
            return
        self._save_settings()
        if self._search_cancel is not None:
            self._search_cancel.set()
        if self.doc is not None:
            self.doc.close()
        self.destroy()

    def report_callback_exception(self, exc, val, tb) -> None:
        traceback.print_exception(exc, val, tb)
        try:
            messagebox.showerror(APP_NAME, f"Unexpected error:\n{val}", parent=self)
        except Exception:
            pass
