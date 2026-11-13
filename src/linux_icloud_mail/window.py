"""Main window: folders sidebar, message list and reader."""


from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from . import APP_NAME, BUILD_DATE, VERSION
from .i18n import _
from .reader import Reader
from .ui_common import MsgItem, MsgRowWidget, ask, prompt_text, save_file_dialog

FLAGGED = "__flagged__"
ROLE_ORDER = ["inbox", "flagged", "drafts", "sent", "junk", "trash", "archive"]
ROLE_ICON = {"inbox": "lim-inbox-symbolic", "flagged": "lim-flag-symbolic", "drafts": "document-edit-symbolic",
             "sent": "mail-send-symbolic", "junk": "mail-mark-junk-symbolic", "trash": "user-trash-symbolic",
             "archive": "lim-archive-symbolic"}
ROLE_LABEL = {"inbox": "Inbox", "flagged": "Flagged", "drafts": "Drafts", "sent": "Sent", "junk": "Junk",
              "trash": "Trash", "archive": "Archive"}
DND_PREFIX = "lim-msgs:"


class FolderRow(Gtk.ListBoxRow):
    def __init__(self, win, name, role, label, depth=0):
        super().__init__()
        self.win = win
        self.name = name
        self.role = role
        self.section = "mailboxes" if role else "folders"
        box = Gtk.Box(spacing=10, margin_start=depth * 16)
        self.icon = Gtk.Image.new_from_icon_name(ROLE_ICON.get(role, "folder-symbolic"))
        if role == "flagged":
            self.icon.add_css_class("flag-icon")
        box.append(self.icon)
        self.label = Gtk.Label(label=label, xalign=0, hexpand=True, ellipsize=3)
        box.append(self.label)
        self.count = Gtk.Label()
        self.count.add_css_class("folder-count")
        box.append(self.count)
        self.set_child(box)
        self.set_tooltip_text(label)
        if name != FLAGGED:
            dt = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
            dt.connect("drop", self._drop)
            dt.connect("enter", lambda *a: (self.add_css_class("drop-highlight"), Gdk.DragAction.MOVE)[1])
            dt.connect("leave", lambda *a: self.remove_css_class("drop-highlight"))
            self.add_controller(dt)
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", lambda g, n, x, y: self.win.folder_menu(self, x, y))
        self.add_controller(click)

    def set_count(self, unseen, total=None):
        if self.role in ("sent", "trash", "archive", "drafts") and self.role != "drafts":
            unseen = 0
        if self.role == "drafts":
            self.count.set_text(str(total) if total else "")
            self.count.remove_css_class("unread")
            return
        self.count.set_text(str(unseen) if unseen else "")
        if unseen:
            self.count.add_css_class("unread")
        else:
            self.count.remove_css_class("unread")

    def _drop(self, target, value, x, y):
        self.remove_css_class("drop-highlight")
        if isinstance(value, str) and value.startswith(DND_PREFIX):
            self.win.move_dragged(self.name)
            return True
        return False


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.app = app
        self.settings = app.settings
        self.cache = app.cache
        self.backend = app.backend
        self.current = "INBOX"
        self.folder_rows = {}
        self.counts = {}
        self.items = {}
        self.text_filter = ""
        self.server_hits = None
        self.mode_filter = "all"
        self.sticky = set()
        self.open_token = None
        self.mark_timer = None
        self.dragged = []
        self.syncing = 0
        w = self.settings["window"]
        self.set_default_size(w.get("w", 1320), w.get("h", 840))
        if w.get("max"):
            self.maximize()
        self._build()
        self._actions()
        self.connect("close-request", self._on_close)
        Adw.StyleManager.get_default().connect("notify::dark", lambda *a: self.reader.rerender())
        self.load_view("INBOX")

    # ================================================================ UI
    def _build(self):
        self.toasts = Adw.ToastOverlay()
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        self.sidebar_btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", active=True,
                                            tooltip_text=_("Mailboxes") + " (F9)")
        self.sidebar_btn.connect("toggled", lambda b: self.sidebar.set_visible(b.get_active()))
        hb.pack_start(self.sidebar_btn)
        compose = Gtk.Button(icon_name="mail-message-new-symbolic", tooltip_text=_("New Message") + " (Ctrl+N)",
                             action_name="win.compose")
        hb.pack_start(compose)
        self.search = Gtk.SearchEntry(placeholder_text=_("Search mail"), width_chars=34)
        self.search.set_tooltip_text(_("Press Enter to search the whole mailbox on the server"))
        self.search.connect("search-changed", self._search_changed)
        self.search.connect("activate", self._search_server)
        self.search.connect("stop-search", lambda e: e.set_text(""))
        hb.set_title_widget(self.search)
        menu = Gio.Menu()
        look = Gio.Menu()
        look.append(_("System"), "app.theme::system")
        look.append(_("Aurora"), "app.theme::aurora")
        menu.append_submenu(_("Theme"), look)
        menu.append(_("Preferences"), "app.preferences")
        menu.append(_("Keyboard Shortcuts"), "app.shortcuts")
        menu.append(_("About Linux iCloud Mail"), "app.about")
        sec = Gio.Menu()
        sec.append(_("Sign Out"), "app.logout")
        sec.append(_("Quit"), "app.quit")
        menu.append_section(None, sec)
        mb = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True)
        hb.pack_end(mb)
        self.refresh_stack = Gtk.Stack()
        rb = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Check Mail") + " (F5)",
                        action_name="win.refresh")
        self.refresh_stack.add_named(rb, "btn")
        self.refresh_stack.add_named(Gtk.Spinner(spinning=True, margin_start=8, margin_end=8), "spin")
        hb.pack_end(self.refresh_stack)
        tv.add_top_bar(hb)

        self.offline = Adw.Banner(title=_("Offline – trying to reconnect…"), button_label=_("Retry"))
        self.offline.connect("button-clicked", lambda b: self.app.reconnect())
        tv.add_top_bar(self.offline)

        # sidebar ---------------------------------------------------------
        self.sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sidebar.add_css_class("sidebar-pane")
        sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.folder_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.folder_list.add_css_class("navigation-sidebar")
        self.folder_list.set_header_func(self._folder_header)
        self.folder_list.connect("row-selected", self._folder_selected)
        sw.set_child(self.folder_list)
        self.sidebar.append(sw)
        foot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        foot.add_css_class("account-footer")
        acc = Gtk.Label(label=self.settings["email"], xalign=0, ellipsize=3)
        ver = Gtk.Label(label="v%s · %s" % (VERSION, BUILD_DATE), xalign=0)
        ver.add_css_class("caption")
        cred = Gtk.Label(label="Created by: JGu\nSupported by Claude", xalign=0)
        cred.add_css_class("caption")
        cred.add_css_class("dim-label")
        foot.append(acc)
        foot.append(ver)
        foot.append(cred)
        self.sidebar.append(foot)
        self._add_folder_row(FolderRow(self, "INBOX", "inbox", _("Inbox")))

        # message list -----------------------------------------------------
        listpane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        lh = Gtk.Box(spacing=6)
        lh.add_css_class("list-header")
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.list_title = Gtk.Label(xalign=0, ellipsize=3)
        self.list_title.add_css_class("list-title")
        self.list_sub = Gtk.Label(xalign=0, ellipsize=3)
        self.list_sub.add_css_class("dim-label")
        self.list_sub.add_css_class("caption")
        titles.append(self.list_title)
        titles.append(self.list_sub)
        lh.append(titles)
        fmenu = Gio.Menu()
        gsec = Gio.Menu()
        gsec.append(_("Group by Sender"), "win.group")
        fmenu.append_section(None, gsec)
        for label, val in ((_("All Messages"), "all"), (_("Unread"), "unread"), (_("Flagged"), "flagged"),
                           (_("With Attachments"), "attach")):
            fmenu.append(label, "win.filter::" + val)
        self.filter_btn = Gtk.MenuButton(icon_name="view-list-symbolic", menu_model=fmenu, tooltip_text=_("Filter"),
                                         valign=Gtk.Align.CENTER)
        self.filter_btn.add_css_class("flat")
        lh.append(self.filter_btn)
        listpane.append(lh)
        listpane.append(Gtk.Separator())

        self.store = Gio.ListStore(item_type=MsgItem)
        self.group_mode = bool(self.settings.get("group_by_sender", False))
        self.expanded = set()      # sender keys whose groups are open
        self.gstore = Gio.ListStore(item_type=MsgItem)   # grouped order: head, then its messages
        self._ginfo = {}           # id(item) -> (count, unread, is_head, key)
        self._regroup_pending = False
        self.filter = Gtk.CustomFilter.new(self._filter_func)
        self.filtered = Gtk.FilterListModel(model=self.gstore if self.group_mode else self.store,
                                            filter=self.filter)
        self.sel = Gtk.MultiSelection(model=self.filtered)
        self.sel.connect("selection-changed", self._selection_changed)
        fac = Gtk.SignalListItemFactory()
        fac.connect("setup", self._row_setup)
        fac.connect("bind", self._row_bind)
        fac.connect("unbind", lambda f, li: li.get_child().unbind())
        self.listview = Gtk.ListView(model=self.sel, factory=fac, show_separators=True, vexpand=True)
        self.listview.connect("activate", self._row_activated)
        self.list_stack = Gtk.Stack()
        lsw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        lsw.set_child(self.listview)
        self.list_scroller = lsw
        self.list_stack.add_named(lsw, "list")
        self.list_empty = Adw.StatusPage(icon_name="lim-inbox-symbolic", title=_("No messages"),
                                         description=_("This mailbox is empty."))
        self.list_empty.add_css_class("compact")
        self.list_stack.add_named(self.list_empty, "empty")
        sp = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        sp.append(Gtk.Spinner(spinning=True, width_request=28, height_request=28))
        self.list_stack.add_named(sp, "loading")
        listpane.append(self.list_stack)

        # keys that must only act when the list/reader has focus
        sc = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)
        for accel, act in (("Delete", "win.delete"), ("<Shift>Delete", "win.delete-forever"),
                           ("KP_Delete", "win.delete")):
            sc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(accel),
                                             Gtk.NamedAction.new(act)))
        self.listview.add_controller(sc)

        self.reader = Reader(self)
        sc2 = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)
        sc2.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Delete"), Gtk.NamedAction.new("win.delete")))
        self.reader.web.add_controller(sc2)

        self.inner = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=listpane, end_child=self.reader,
                               shrink_start_child=False, shrink_end_child=False, resize_start_child=False)
        self.inner.set_position(self.settings["pane_list"])
        listpane.set_size_request(300, -1)
        self.reader.set_size_request(360, -1)
        self.outer = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=self.sidebar,
                               end_child=self.inner, shrink_start_child=False, resize_start_child=False)
        self.outer.set_position(self.settings["pane_sidebar"])
        self.sidebar.set_size_request(170, -1)
        self.toasts.set_child(self.outer)
        tv.set_content(self.toasts)
        self.set_content(tv)

    # ---------------------------------------------------------------- actions
    def _actions(self):
        def add(name, fn, param=None):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
            a.connect("activate", lambda act, v: fn(v) if param else fn())
            self.add_action(a)
            return a

        add("compose", lambda: self.app.compose())
        add("reply", lambda: self._reply("reply"))
        add("reply-all", lambda: self._reply("reply-all"))
        add("forward", lambda: self._reply("forward"))
        add("edit-draft", self._edit_draft)
        add("delete", self.delete_selected)
        add("delete-forever", lambda: self.delete_selected(force=True))
        add("archive", self.archive_selected)
        add("junk", self.junk_selected)
        add("toggle-flag", self.toggle_flag_selected)
        add("toggle-read", self.toggle_read_selected)
        add("mark-read", lambda: self.flag_items(self.selected_items(), "\\Seen", True))
        add("mark-unread", lambda: self.flag_items(self.selected_items(), "\\Seen", False))
        add("move-to", lambda v: self.move_items(self.selected_items(), v.unpack()), "s")
        add("refresh", self.refresh)
        add("search", lambda: self.search.grab_focus())
        add("print", self._print)
        add("save-eml", self._save_eml)
        add("view-source", self._view_source)
        add("original-colors", self.reader.toggle_original_colors)
        add("zoom-in", lambda: self.reader.zoom(0.1))
        add("zoom-out", lambda: self.reader.zoom(-0.1))
        add("toggle-sidebar", lambda: self.sidebar_btn.set_active(not self.sidebar_btn.get_active()))
        ga = Gio.SimpleAction.new_stateful("group", None, GLib.Variant("b", self.group_mode))
        ga.connect("activate", lambda act, v: (act.set_state(GLib.Variant("b", not act.get_state().unpack())),
                                               self.set_group_mode(act.get_state().unpack())))
        self.add_action(ga)
        add("select-group", self._select_group_of_selection)
        fa = Gio.SimpleAction.new_stateful("filter", GLib.VariantType.new("s"), GLib.Variant("s", "all"))
        fa.connect("activate", lambda act, v: (act.set_state(v), self._set_mode(v.unpack())))
        self.add_action(fa)
        add("next", lambda: self._step(1))
        add("prev", lambda: self._step(-1))

    # ================================================================ folders
    def folder_role(self, name):
        if name == FLAGGED:
            return "flagged"
        if name.upper() == "INBOX":
            return "inbox"
        for f in self.backend.folders:
            if f.name == name:
                return f.role
        return None

    def folder_display(self, name):
        if name == FLAGGED:
            return _("Flagged")
        role = self.folder_role(name)
        if role in ROLE_LABEL:
            return _(ROLE_LABEL[role])
        for f in self.backend.folders:
            if f.name == name:
                return f.display
        return name

    def _add_folder_row(self, row):
        self.folder_rows[row.name] = row
        self.folder_list.append(row)

    def _folder_header(self, row, before):
        if before is None or before.section != row.section:
            if row.section == "folders":
                lbl = Gtk.Label(label=_("Folders"), xalign=0, margin_start=10, margin_top=14, margin_bottom=4)
            else:
                lbl = Gtk.Label(label=_("Mailboxes"), xalign=0, margin_start=10, margin_top=6, margin_bottom=4)
            lbl.add_css_class("heading")
            lbl.add_css_class("dim-label")
            row.set_header(lbl)
        else:
            row.set_header(None)

    def populate_folders(self, folders):
        self._populating = True
        cur = self.current
        while (r := self.folder_list.get_first_child()) is not None:
            self.folder_list.remove(r)
        self.folder_rows = {}
        by_role = {}
        others = []
        for f in folders:
            if f.role and f.role not in by_role and f.depth == 0:
                by_role[f.role] = f
            else:
                others.append(f)
        for role in ROLE_ORDER:
            if role == "flagged":
                self._add_folder_row(FolderRow(self, FLAGGED, "flagged", _("Flagged")))
            elif role in by_role:
                f = by_role[role]
                self._add_folder_row(FolderRow(self, f.name, role, _(ROLE_LABEL[role])))
        for f in sorted(others, key=lambda x: x.name.lower()):
            self._add_folder_row(FolderRow(self, f.name, None, f.display, f.depth))
        self._populating = False
        self.update_counts()
        row = self.folder_rows.get(cur) or self.folder_rows.get("INBOX")
        if row:
            self.folder_list.select_row(row)

    def update_counts(self):
        inbox_unread = 0
        for name, row in self.folder_rows.items():
            if name == FLAGGED:
                continue
            total, unseen = self.counts.get(name, (0, 0))
            row.set_count(unseen, total)
            if name.upper() == "INBOX":
                inbox_unread = unseen
        self.app.set_unread(inbox_unread)
        self._update_subtitle()

    def set_count_from_cache(self, folder):
        if folder in self.backend.initial_synced:
            unseen = self.cache.unread_count(folder)
            total = len(self.cache.uids(folder))
            self.counts[folder] = (total, unseen)
            self.update_counts()

    def _folder_selected(self, lb, row):
        if row is None or getattr(self, "_populating", False):
            return
        if row.name != self.current:
            self.load_view(row.name)

    def folder_menu(self, row, x, y):
        menu = Gio.Menu()
        ag = Gio.SimpleActionGroup()

        def act(name, fn):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *a: fn())
            ag.add_action(a)

        if row.name != FLAGGED:
            menu.append(_("Mark All as Read"), "fm.readall")
            act("readall", lambda: self._mark_all_read(row.name))
        if row.role in ("trash", "junk"):
            menu.append(_("Empty Trash…") if row.role == "trash" else _("Empty Junk…"), "fm.empty")
            act("empty", lambda: self._empty(row.name))
        sec = Gio.Menu()
        sec.append(_("New Folder…"), "fm.new")
        act("new", self._new_folder)
        if row.role is None:
            sec.append(_("Rename Folder…"), "fm.rename")
            sec.append(_("Delete Folder…"), "fm.delete")
            act("rename", lambda: self._rename_folder(row))
            act("delete", lambda: self._delete_folder(row))
        menu.append_section(None, sec)
        row.insert_action_group("fm", ag)
        pop = Gtk.PopoverMenu.new_from_model(menu)
        pop.set_parent(row)
        pop.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    def _new_folder(self):
        prompt_text(self, _("New Folder…"), "", _("Create"), lambda n: self.backend.create_folder(n),
                    placeholder=_("Folder name"))

    def _rename_folder(self, row):
        prompt_text(self, _("Rename Folder…"), row.label.get_text(), _("Rename"),
                    lambda n: self.backend.rename_folder(row.name, n))

    def _delete_folder(self, row):
        ask(self, _("Delete folder “%s”?") % row.label.get_text(), _("All messages in this folder will be deleted."),
            [("cancel", _("Cancel"), None), ("delete", _("Delete"), "destructive")],
            lambda r: r == "delete" and self._do_delete_folder(row.name))

    def _do_delete_folder(self, name):
        if self.current == name:
            self.load_view("INBOX")
        self.backend.delete_folder(name)

    def _mark_all_read(self, folder):
        for it in list(self.items.values()):
            if it.row.folder == folder:
                it.toggle_flag("\\Seen", True)
        self.cache.set_flags(folder, {u: (f + " \\Seen").strip() for u, f in
                                      self.cache.flags_map(folder, self.cache.uids(folder)).items()
                                      if "\\Seen" not in f})
        self.counts[folder] = (self.counts.get(folder, (0, 0))[0], 0)
        self.update_counts()
        self.backend.mark_all_read(folder)

    def _empty(self, folder):
        ask(self, _("Empty “%s”?") % self.folder_display(folder), _("Messages will be deleted permanently."),
            [("cancel", _("Cancel"), None), ("delete", _("Delete"), "destructive")],
            lambda r: r == "delete" and self._do_empty(folder))

    def _do_empty(self, folder):
        if self.current == folder:
            self.store.remove_all()
            self.items.clear()
            self._update_empty()
        self.backend.empty_folder(folder, done=lambda r: (self.counts.__setitem__(folder, (0, 0)),
                                                          self.update_counts()))

    def build_move_popover(self, items_fn=None, exclude=None, after=None):
        items_fn = items_fn or self.selected_items
        exclude = exclude or self.current
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        entry = Gtk.SearchEntry(placeholder_text=_("Folder name"))
        box.append(entry)
        sw = Gtk.ScrolledWindow(max_content_height=380, propagate_natural_height=True, min_content_width=240,
                                hscrollbar_policy=Gtk.PolicyType.NEVER)
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        lb.add_css_class("navigation-sidebar")
        for name, row in self.folder_rows.items():
            if name in (FLAGGED, exclude):
                continue
            r = Gtk.ListBoxRow()
            r.name = name
            b = Gtk.Box(spacing=8)
            b.append(Gtk.Image.new_from_icon_name(ROLE_ICON.get(row.role, "folder-symbolic")))
            b.append(Gtk.Label(label=row.label.get_text(), xalign=0))
            r.label = row.label.get_text().lower()
            r.set_child(b)
            lb.append(r)
        lb.set_filter_func(lambda r: entry.get_text().lower() in r.label)
        entry.connect("search-changed", lambda e: lb.invalidate_filter())

        def chosen(lb_, r):
            pop.popdown()
            self.move_items(items_fn(), r.name)
            if after:
                after()
        lb.connect("row-activated", chosen)

        def first(e):
            for r in [lb.get_row_at_index(i) for i in range(200)]:
                if r is None:
                    break
                if r.get_child_visible() and entry.get_text().lower() in r.label:
                    chosen(lb, r)
                    return
        entry.connect("activate", first)
        sw.set_child(lb)
        box.append(sw)
        pop.set_child(box)
        pop.connect("show", lambda p: entry.grab_focus())
        return pop

    # ================================================================ list
    def _row_setup(self, fac, li):
        w = MsgRowWidget()
        li.set_child(w)
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", lambda g, n, x, y: self._row_menu(w, x, y))
        w.add_controller(click)
        ds = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        ds.connect("prepare", lambda src, x, y: self._drag_prepare(w))
        ds.connect("drag-begin", lambda src, drag: src.set_icon(
            Gtk.IconTheme.get_for_display(self.get_display()).lookup_icon(
                "mail-unread-symbolic", None, 32, 1, Gtk.TextDirection.NONE, 0), 0, 0))
        w.add_controller(ds)

    def _row_bind(self, fac, li):
        w = li.get_child()
        w.show_to = self.folder_role(self.current) in ("sent", "drafts")
        w.group = self._group_info if self.group_mode else None
        w.on_toggle = self.toggle_group
        w.bind(li.get_item(), show_folder=self.folder_display if self.current == FLAGGED else None)

    def _position_of(self, item):
        n = self.filtered.get_n_items()
        for i in range(n):
            if self.filtered.get_item(i) is item:
                return i
        return -1

    def _ensure_selected(self, item):
        if item not in self.selected_items():
            pos = self._position_of(item)
            if pos >= 0:
                self.sel.select_item(pos, True)

    def _drag_prepare(self, w):
        if w.item is None:
            return None
        self._ensure_selected(w.item)
        self.dragged = self.selected_items()
        return Gdk.ContentProvider.new_for_value(DND_PREFIX + str(len(self.dragged)))

    def move_dragged(self, dest):
        items = [i for i in self.dragged if i.row.folder != dest]
        self.dragged = []
        if items:
            self.move_items(items, dest)

    def _row_menu(self, w, x, y):
        if w.item is None:
            return
        self._ensure_selected(w.item)
        items = self.selected_items()
        role = self.folder_role(w.item.row.folder)
        menu = Gio.Menu()
        s1 = Gio.Menu()
        s1.append(_("Reply"), "win.reply")
        s1.append(_("Reply All"), "win.reply-all")
        s1.append(_("Forward"), "win.forward")
        menu.append_section(None, s1)
        s2 = Gio.Menu()
        any_unread = any(not i.row.seen for i in items)
        s2.append(_("Mark as Read") if any_unread else _("Mark as Unread"),
                  "win.mark-read" if any_unread else "win.mark-unread")
        any_unflag = any(not i.row.flagged for i in items)
        s2.append(_("Flag") if any_unflag else _("Unflag"), "win.toggle-flag")
        s2.append(_("Select All from Sender"), "win.select-group")
        menu.append_section(None, s2)
        s3 = Gio.Menu()
        mv = Gio.Menu()
        recent = [f for f in self.settings["recent_folders"] if f in self.folder_rows and f != self.current][:3]
        for name in recent:
            mv.append(self.folder_display(name), "win.move-to(%s)" % GLib.Variant("s", name).print_(False))
        allm = Gio.Menu()
        for name in self.folder_rows:
            if name not in (FLAGGED, self.current) and name not in recent:
                allm.append(self.folder_display(name), "win.move-to(%s)" % GLib.Variant("s", name).print_(False))
        mv.append_section(None, allm)
        s3.append_submenu(_("Move to…"), mv)
        if role != "archive" and self.backend.role("archive"):
            s3.append(_("Move to Archive"), "win.archive")
        s3.append(_("Not Junk") if role == "junk" else _("Move to Junk"), "win.junk")
        s3.append(_("Delete Permanently") if role == "trash" else _("Move to Trash"), "win.delete")
        menu.append_section(None, s3)
        pop = Gtk.PopoverMenu.new_from_model(menu)
        pop.set_parent(w)
        pop.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    def _filter_func(self, item):
        if self.group_mode:
            return True              # gstore already contains exactly the visible rows
        return self._base_filter(item)

    # ---------------------------------------------------------------- group by sender
    def _group_key(self, r):
        if self.folder_role(self.current) in ("sent", "drafts"):
            from .mailparse import addresses
            a = addresses(r.to_h)
            return (a[0][1] or a[0][0]).lower() if a else ""
        return (r.from_addr or r.from_name or "").lower()

    def _group_info(self, item):
        g = self._ginfo.get(id(item))
        if g is None:
            return None
        count, unread, head, key = g
        return (count, unread, head, key in self.expanded)

    def _regroup(self):
        """Recompute groups over the messages that pass the normal filters (newest first)."""
        groups = {}
        order = []
        for i in range(self.store.get_n_items()):
            it = self.store.get_item(i)
            if not (it.key in self.sticky or self._base_filter(it)):
                continue
            k = self._group_key(it.row)
            g = groups.get(k)
            if g is None:
                groups[k] = g = []
                order.append(k)
            g.append(it)
        rows = []
        info = {}
        for k in order:
            g = groups[k]
            head = g[0]
            info[id(head)] = (len(g), sum(1 for x in g if not x.row.seen), True, k)
            rows.append(head)
            if k in self.expanded:
                for x in g[1:]:
                    rows.append(x)
                    info[id(x)] = (0, 0, False, k)
        old = self._ginfo
        self._ginfo = info
        self._groups = groups
        self._apply_order(rows)
        # refresh rows whose badge / indentation changed
        changed = [i for i in set(old) | set(info) if old.get(i) != info.get(i)]
        if changed:
            by_id = {id(x): x for g in groups.values() for x in g}
            for i in changed:
                it = by_id.get(i)
                if it is not None:
                    it.emit("changed")

    def _apply_order(self, new):
        """Update gstore with a minimal splice so selection and scroll position survive."""
        st = self.gstore
        n = st.get_n_items()
        old = [st.get_item(i) for i in range(n)]
        p = 0
        while p < n and p < len(new) and old[p] is new[p]:
            p += 1
        s = 0
        while s < n - p and s < len(new) - p and old[n - 1 - s] is new[len(new) - 1 - s]:
            s += 1
        if p == n == len(new):
            return
        st.splice(p, n - p - s, new[p:len(new) - s])

    def _regroup_soon(self):
        if not self.group_mode or self._regroup_pending:
            return
        self._regroup_pending = True

        def go():
            self._regroup_pending = False
            self._regroup()
            self.filter.changed(Gtk.FilterChange.DIFFERENT)
            self._update_empty()
            return False
        GLib.idle_add(go)

    def set_group_mode(self, on):
        sel = self.selected_items()
        self.group_mode = on
        self.settings["group_by_sender"] = on
        self.expanded.clear()
        self._ginfo = {}
        if on:
            self._regroup()
            self.filtered.set_model(self.gstore)
        else:
            self.filtered.set_model(self.store)
            self.gstore.remove_all()
        for it in sel[:1]:
            pos = self._position_of(it)
            if pos >= 0:
                self.sel.select_item(pos, True)
        # rebind rows so they pick up / drop the group decorations
        fac = self.listview.get_factory()
        self.listview.set_factory(None)
        self.listview.set_factory(fac)
        self._refilter()

    def toggle_group(self, item):
        g = self._ginfo.get(id(item))
        if not g:
            return
        key = g[3]
        if key in self.expanded:
            self.expanded.discard(key)
        else:
            self.expanded.add(key)
        self._regroup()
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        item.emit("changed")

    def _select_group_of_selection(self):
        items = self.selected_items()
        if not items:
            return
        key = self._group_key(items[0].row)
        self.expanded.add(key)
        if self.group_mode:
            self._regroup()
            self.filter.changed(Gtk.FilterChange.DIFFERENT)
        members = [it for it in (getattr(self, "_groups", {}).get(key) or [])] if self.group_mode else \
            [self.filtered.get_item(i) for i in range(self.filtered.get_n_items())
             if self._group_key(self.filtered.get_item(i).row) == key]
        first = True
        for it in members:
            pos = self._position_of(it)
            if pos >= 0:
                self.sel.select_item(pos, first)
                first = False

    def _base_filter(self, item):
        r = item.row
        if item.key in self.sticky:
            return True
        if self.server_hits is not None and r.uid not in self.server_hits:
            return False
        m = self.mode_filter
        if m == "unread" and r.seen:
            return False
        if m == "flagged" and not r.flagged:
            return False
        if m == "attach" and not r.attach:
            return False
        t = self.text_filter
        if t and self.server_hits is None:
            hay = ("%s %s %s %s %s" % (r.subject, r.from_name, r.from_addr, r.to_h, r.preview)).lower()
            return all(w in hay for w in t.split())
        return True

    def _refilter(self):
        if self.group_mode:
            self._regroup()
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        sel = self.selected_items()
        pos = self._position_of(sel[0]) if sel else -1
        if pos >= 0:
            GLib.idle_add(lambda: (self.listview.scroll_to(pos, Gtk.ListScrollFlags.NONE, None), False)[1])
        else:
            self.list_scroller.get_vadjustment().set_value(0)
        self._update_empty()
        self._update_subtitle()

    def _set_mode(self, mode):
        self.mode_filter = mode
        self.sticky.clear()
        if mode != "all":
            self.filter_btn.add_css_class("accent")
        else:
            self.filter_btn.remove_css_class("accent")
        self._refilter()

    def _search_changed(self, e):
        self.text_filter = e.get_text().strip().lower()
        self.server_hits = None
        self.sticky.clear()
        self._refilter()

    def _search_server(self, e):
        text = e.get_text().strip()
        if not text or self.current == FLAGGED:
            return
        folder = self.current
        self.list_sub.set_text(_("Searching…"))

        def done(uids):
            if self.current != folder or self.search.get_text().strip() != text:
                return
            self.server_hits = uids
            missing = uids - set(u for (f, u) in self.items)
            self._refilter()
            self.list_sub.set_text(_("Search results on server: %d") % len(uids))
            if missing:
                self.backend.sync_folder(folder, prio=1)
        self.backend.search(folder, text, done, error=lambda ex: self.toast(str(ex)))

    def _update_empty(self):
        if self.filtered.get_n_items() == 0:
            self.list_stack.set_visible_child_name("loading" if self._view_loading else "empty")
        else:
            self.list_stack.set_visible_child_name("list")

    def _update_subtitle(self):
        if self.server_hits is not None:
            return
        if self.current == FLAGGED:
            self.list_sub.set_text(_("%d messages") % self.store.get_n_items())
            return
        total, unseen = self.counts.get(self.current, (self.store.get_n_items(), 0))
        total = max(total, self.store.get_n_items())
        s = _("%d messages") % total
        if unseen and self.folder_role(self.current) not in ("sent", "drafts", "trash"):
            s += ", " + _("%d unread") % unseen
        self.list_sub.set_text(s)

    # ---------------------------------------------------------------- view loading
    def load_view(self, folder):
        self.current = folder
        self.sticky.clear()
        self.server_hits = None
        if self.search.get_text():
            self.search.set_text("")
        self.list_title.set_text(self.folder_display(folder))
        self.set_title("%s – %s" % (self.folder_display(folder), APP_NAME))
        self.store.remove_all()
        self.items = {}
        self.reader.show_empty()
        if folder == FLAGGED:
            names = [f.name for f in self.backend.folders if f.role not in ("trash", "junk")] or ["INBOX"]
            rows = self.cache.load_flagged(names)
        else:
            rows = self.cache.load(folder)
        self._view_loading = not rows
        items = [MsgItem(r) for r in rows]
        for it in items:
            self.items[it.key] = it
        self.store.splice(0, 0, items)
        self.expanded.clear()
        self._regroup_now()
        self._update_empty()
        self._update_subtitle()
        self.list_scroller.get_vadjustment().set_value(0)
        if folder != FLAGGED:
            self.backend.sync_folder(folder, prio=2)
            if rows:
                self.backend.prefetch(folder, rows[:30])
        else:
            self._view_loading = False
            self._update_empty()

    def _insert_rows(self, rows):
        rows = [r for r in rows if (r.folder, r.uid) not in self.items]
        if not rows:
            return
        rows.sort(key=lambda r: (r.idate, r.uid), reverse=True)
        adj = self.list_scroller.get_vadjustment()
        at_top = adj.get_value() < 4
        if at_top:
            GLib.idle_add(lambda: (adj.set_value(0), False)[1])
        items = [MsgItem(r) for r in rows]
        for it in items:
            self.items[it.key] = it
        n = self.store.get_n_items()

        def k(it):
            return (it.row.idate, it.row.uid)
        if n == 0:
            self.store.splice(0, 0, items)
        elif k(items[-1]) >= k(self.store.get_item(0)):
            self.store.splice(0, 0, items)
        elif k(items[0]) <= k(self.store.get_item(n - 1)):
            self.store.splice(n, 0, items)
        else:
            for it in items:
                lo, hi = 0, self.store.get_n_items()
                key = k(it)
                while lo < hi:
                    mid = (lo + hi) // 2
                    if k(self.store.get_item(mid)) > key:
                        lo = mid + 1
                    else:
                        hi = mid
                self.store.insert(lo, it)
        self._regroup_now()

    def _remove_items(self, items):
        for it in items:
            self.items.pop(it.key, None)
            ok, pos = self.store.find(it)
            if ok:
                self.store.remove(pos)
        self._regroup_now()

    def _regroup_now(self):
        if self.group_mode:
            self._regroup()
            self.filter.changed(Gtk.FilterChange.DIFFERENT)

    # ================================================================ selection / reading
    def selected_items(self):
        bs = self.sel.get_selection()
        out = []
        for i in range(bs.get_size()):
            it = self.filtered.get_item(bs.get_nth(i))
            if it is not None:
                out.append(it)
        return out

    def _selection_changed(self, sel, pos, n):
        if getattr(self, "_sel_pending", False):
            return
        self._sel_pending = True
        GLib.idle_add(self._apply_selection)

    def _apply_selection(self):
        self._sel_pending = False
        items = self.selected_items()
        if len(items) == 1:
            if self.reader.item is not items[0]:
                self.open_item(items[0])
        elif len(items) > 1:
            self._cancel_mark()
            self.reader.show_multi(len(items))
        else:
            self._cancel_mark()
            self.reader.show_empty()
        return False

    def _cancel_mark(self):
        if self.mark_timer:
            GLib.source_remove(self.mark_timer)
            self.mark_timer = None

    def open_item(self, item):
        self._cancel_mark()
        self.reader.show_loading(item)
        r = item.row
        self.backend.fetch_body(r.folder, r.uid, r.size, lambda p: self._opened(item, p),
                                error=lambda e: self.reader.show_error(item, str(e)))

    def _opened(self, item, parsed):
        self.reader.show_message(item, parsed)
        if item.row.seen:
            return
        mode = self.settings["mark_read"]
        if mode == "immediate":
            self._mark_read_now(item)
        elif mode == "delay":
            self.mark_timer = GLib.timeout_add(1800, lambda: (self._mark_read_now(item), False)[1])

    def _mark_read_now(self, item):
        self.mark_timer = None
        if self.reader.item is item and not item.row.seen:
            if self.mode_filter == "unread":
                self.sticky.add(item.key)
            self.flag_items([item], "\\Seen", True)

    def _row_activated(self, lv, pos):
        """Double-click / Enter: drafts open in the composer, other messages in their own window."""
        item = self.filtered.get_item(pos)
        if item is None:
            return
        if self.folder_role(item.row.folder) == "drafts":
            self._edit_draft()
        else:
            from .msgwindow import MessageWindow
            MessageWindow(self, item).present()

    def _step(self, d):
        items = self.selected_items()
        n = self.filtered.get_n_items()
        if not n:
            return
        pos = self._position_of(items[0]) if items else -1
        pos = max(0, min(n - 1, pos + d))
        self.sel.select_item(pos, True)
        self.listview.scroll_to(pos, Gtk.ListScrollFlags.FOCUS, None)

    # ================================================================ operations
    def flag_items(self, items, flag, add):
        by_folder = {}
        for it in items:
            has = flag in (it.row.flags or "").split()
            if has == add:
                continue
            it.toggle_flag(flag, add)
            by_folder.setdefault(it.row.folder, []).append(it)
        for folder, its in by_folder.items():
            self.cache.set_flags(folder, {i.row.uid: i.row.flags for i in its})
            self.backend.set_flag(folder, [i.row.uid for i in its], flag, add)
            if flag == "\\Seen" and add:
                self.app.withdraw_notifications(folder, [i.row.uid for i in its])
            if flag == "\\Seen" and folder in self.counts:
                total, unseen = self.counts[folder]
                unseen = max(0, unseen + (-len(its) if add else len(its)))
                self.counts[folder] = (total, unseen)
        if by_folder:
            self.update_counts()
            self.reader.update_flags()
            self._regroup_soon()

    def toggle_flag_selected(self):
        items = self.selected_items()
        if items:
            self.flag_items(items, "\\Flagged", any(not i.row.flagged for i in items))

    def toggle_read_selected(self):
        items = self.selected_items()
        if items:
            self._cancel_mark()
            self.flag_items(items, "\\Seen", any(not i.row.seen for i in items))

    def _select_after_removal(self, items):
        positions = [self._position_of(i) for i in items]
        positions = [p for p in positions if p >= 0]
        return min(positions) if positions else -1

    def _finish_removal(self, items, pos):
        keys = {i.key for i in items}
        reading = self.reader.item is not None and self.reader.item.key in keys
        if reading:
            self._cancel_mark()
        self._remove_items(items)
        n = self.filtered.get_n_items()
        self._update_empty()
        self._update_subtitle()
        if n and pos >= 0:
            pos = min(pos, n - 1)
            self.sel.select_item(pos, True)
        elif reading or not n:
            # (messages removed from outside the list, e.g. from a notification, keep the reader as is)
            self.reader.show_empty()

    def move_items(self, items, dest):
        items = [i for i in items if i.row.folder != dest]
        if not items:
            return
        pos = self._select_after_removal(items)
        by_folder = {}
        for it in items:
            by_folder.setdefault(it.row.folder, []).append(it)
        tokens, restore = [], []
        for folder, its in by_folder.items():
            uids = [i.row.uid for i in its]
            self.app.withdraw_notifications(folder, uids)
            unseen = sum(1 for i in its if not i.row.seen)
            self.cache.delete(folder, uids)
            tokens.append(self.backend.move_delayed(folder, uids, dest))
            restore.append((folder, [i.row for i in its], unseen))
            self._count_move(folder, dest, len(its), unseen)
        self._finish_removal(items, pos)
        self.update_counts()
        rec = [dest] + [f for f in self.settings["recent_folders"] if f != dest]
        self.settings["recent_folders"] = rec[:6]
        self._undo_toast(_("Moved to %s") % self.folder_display(dest), tokens, restore, dest)

    def _count_move(self, src, dest, n, unseen, sign=1):
        t, u = self.counts.get(src, (0, 0))
        self.counts[src] = (max(0, t - sign * n), max(0, u - sign * unseen))
        t, u = self.counts.get(dest, (0, 0))
        self.counts[dest] = (max(0, t + sign * n), max(0, u + sign * unseen))

    UNDO_SECONDS = 5

    def _undo_toast(self, text, tokens, restore, dest):
        """Toast with Undo. The move reaches the server only when the toast goes away (or after a
        safety timeout); Undo cancels it before the server ever sees it."""
        state = {"done": False}

        def commit(*_a):
            if not state["done"]:
                state["done"] = True
                for t in tokens:
                    self.backend.commit(t)
            return False

        def undo(*_a):
            if state["done"]:
                return
            state["done"] = True
            for t in tokens:
                self.backend.cancel(t)
            first = None
            for folder, rows, unseen in restore:
                self.cache.insert(rows)
                self._count_move(folder, dest, len(rows), unseen, sign=-1)
                if folder == self.current:
                    self._insert_rows(rows)
                    first = first or self.items.get((rows[0].folder, rows[0].uid))
            self.update_counts()
            self._update_empty()
            self._update_subtitle()
            if first is not None:
                pos = self._position_of(first)
                if pos >= 0:
                    self.sel.select_item(pos, True)
            self.toast(_("Undone"))

        t = Adw.Toast(title=text, button_label=_("Undo"), timeout=self.UNDO_SECONDS)
        t.set_priority(Adw.ToastPriority.HIGH)      # a newer action replaces (and commits) the older one
        t.connect("button-clicked", undo)
        t.connect("dismissed", commit)
        GLib.timeout_add_seconds(self.UNDO_SECONDS + 3, commit)
        self.toasts.add_toast(t)

    def delete_selected(self, force=False, items=None, parent=None, done=None, _retried=False):
        items = items if items is not None else self.selected_items()
        if not items:
            return
        trash = self.backend.role("trash")
        if not trash and not force and not _retried:
            # folder list not loaded yet (just started, offline): never fall back to a permanent
            # delete silently - load the folders first, then move to Trash
            self.toast(_("Loading folders…"))
            self.backend.refresh_folders(done=lambda: self.delete_selected(
                items=items, parent=parent, done=done, _retried=True))
            return
        in_trash = [i for i in items if i.row.folder == trash or not trash]
        others = [i for i in items if i not in in_trash]
        if force:
            in_trash, others = items, []
        if others:
            self.move_items(others, trash)
            if done and not in_trash:
                done()
        if in_trash:
            title = _("Delete message permanently?") if len(in_trash) == 1 else \
                _("Delete %d messages permanently?") % len(in_trash)

            def resp(r):
                if r == "delete":
                    self._delete_forever(in_trash)
                    if done:
                        done()
            ask(parent or self, title, _("This cannot be undone."),
                [("cancel", _("Cancel"), None), ("delete", _("Delete"), "destructive")], resp, default="delete")

    def _delete_forever(self, items):
        pos = self._select_after_removal(items)
        by_folder = {}
        for it in items:
            by_folder.setdefault(it.row.folder, []).append(it.row.uid)
        for folder, uids in by_folder.items():
            self.app.withdraw_notifications(folder, uids)
            self.cache.delete(folder, uids)
            self.backend.delete_forever(folder, uids)
        self._finish_removal(items, pos)
        self.toast(_("Deleted"))

    def archive_selected(self, items=None):
        arch = self.backend.role("archive")
        if arch:
            self.move_items(items if items is not None else self.selected_items(), arch)

    def junk_selected(self, items=None):
        items = items if items is not None else self.selected_items()
        if not items:
            return
        junk = self.backend.role("junk")
        if self.folder_role(items[0].row.folder) == "junk":
            self.move_items(items, "INBOX")
        elif junk:
            self.move_items(items, junk)

    def after_draft_change(self, folder, uid):
        it = self.items.get((folder, uid))
        if it:
            self._remove_items([it])
            if self.reader.item is it:
                self.reader.show_empty()
            self._update_empty()

    def _reply(self, mode):
        item = self.reader.item
        if item is None or self.reader.parsed is None:
            return
        self.app.compose(mode=mode, item=item, parsed=self.reader.parsed)

    def _edit_draft(self):
        item = self.reader.item
        if item is not None and self.reader.parsed is not None:
            self.app.compose(mode="draft", item=item, parsed=self.reader.parsed)

    def refresh(self):
        self.set_syncing(True)
        GLib.timeout_add_seconds(20, lambda: (self.set_syncing(False), False)[1])
        self.backend.refresh_folders()
        if self.current != FLAGGED:
            self.backend.sync_folder(self.current, prio=1)
        if self.current != "INBOX":
            self.backend.sync_folder("INBOX", prio=2, done=self.app.notify_new)

    def _print(self, reader=None, parent=None):
        reader = reader or self.reader
        parent = parent or self
        if reader.parsed is not None:
            from gi.repository import WebKit
            op = WebKit.PrintOperation.new(reader.web)
            op.run_dialog(parent)

    def _save_eml(self, reader=None, parent=None):
        reader = reader or self.reader
        parent = parent or self
        item = reader.item
        if item is None:
            return
        name = "".join(c for c in (item.row.subject or "message") if c not in '/\\:*?"<>|')[:80] + ".eml"

        def chosen(path):
            def got(raw):
                with open(path, "wb") as f:
                    f.write(raw or b"")
                self.toast(_("Saved to %s") % path)
            self.backend.fetch_raw(item.row.folder, item.row.uid, got, error=lambda e: self.toast(str(e)))
        save_file_dialog(parent, name, chosen)

    def _view_source(self, reader=None, parent=None):
        reader = reader or self.reader
        parent = parent or self
        item = reader.item
        if item is None:
            return

        def got(raw):
            win = Adw.Window(transient_for=parent, default_width=820, default_height=640,
                             title=_("View Source"))
            tv = Adw.ToolbarView()
            tv.add_top_bar(Adw.HeaderBar())
            sw = Gtk.ScrolledWindow()
            view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.CHAR,
                                left_margin=12, right_margin=12, top_margin=12, bottom_margin=12)
            txt = (raw or b"").decode("utf-8", "replace")
            view.get_buffer().set_text(txt[:2_000_000])
            sw.set_child(view)
            tv.set_content(sw)
            win.set_content(tv)
            win.present()
        self.backend.fetch_raw(item.row.folder, item.row.uid, got, error=lambda e: self.toast(str(e)))

    # ================================================================ backend events
    def on_folders(self, folders, counts):
        for name, (total, unseen) in counts.items():
            self.counts[name] = (total, unseen)
        self.populate_folders(folders)
        self.list_title.set_text(self.folder_display(self.current))

    def on_counts(self, counts):
        for name, v in counts.items():
            self.counts[name] = v
        self.update_counts()

    def on_added(self, folder, rows):
        if folder == self.current:
            self._view_loading = False
            self._insert_rows(rows)
            self._update_empty()
        elif self.current == FLAGGED:
            self._insert_rows([r for r in rows if r.flagged])
        self.set_count_from_cache(folder)

    def on_removed(self, folder, uids):
        if folder == self.current or self.current == FLAGGED:
            items = [self.items[(folder, u)] for u in uids if (folder, u) in self.items]
            if items:
                was_open = self.reader.item in items
                self._remove_items(items)
                if was_open:
                    self.reader.show_empty()
                self._update_empty()
        self.set_count_from_cache(folder)

    def on_flags(self, folder, changed):
        if folder == self.current or self.current == FLAGGED:
            for uid, fl in changed.items():
                it = self.items.get((folder, uid))
                if it is not None:
                    it.set_flags(fl)
                elif self.current == FLAGGED and "\\Flagged" in fl:
                    r = self.cache.get(folder, uid)
                    if r:
                        self._insert_rows([r])
            self.reader.update_flags()
            self._regroup_soon()
        self.set_count_from_cache(folder)

    def on_folder_reset(self, folder):
        if folder == self.current:
            self.store.remove_all()
            self.items = {}
            self.reader.show_empty()

    def on_synced(self, folder, total, first):
        t, u = self.counts.get(folder, (0, 0))
        self.counts[folder] = (total, u)
        self.set_count_from_cache(folder)
        if folder == self.current:
            self.set_syncing(False)
            self._view_loading = False
            self._update_empty()
            self._update_subtitle()
            if first:
                rows = [self.store.get_item(i).row for i in range(min(30, self.store.get_n_items()))]
                self.backend.prefetch(folder, rows)

    def set_syncing(self, busy):
        self.refresh_stack.set_visible_child_name("spin" if busy else "btn")

    def set_offline(self, offline):
        self.offline.set_revealed(offline)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    # ================================================================ window
    def _on_close_save_only(self):
        w = {"w": self.get_width(), "h": self.get_height(), "max": self.is_maximized()}
        if not w["max"]:
            self.settings.data["window"] = w
        else:
            self.settings.data["window"]["max"] = True
        self.settings.data["pane_sidebar"] = self.outer.get_position()
        self.settings.data["pane_list"] = self.inner.get_position()
        self.settings.save()

    def _on_close(self, *a):
        self._on_close_save_only()
        if self.settings["close_to_tray"] and self.app.tray_available():
            self.set_visible(False)
            self.app.tray_hint()
            return True
        self.app.quit_app()
        return True
