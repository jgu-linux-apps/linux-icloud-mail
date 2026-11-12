"""Application object: lifecycle, tray, notifications, theme, wiring backend <-> window."""

import os
import time
import urllib.parse

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, IMAP_HOST, IMAP_PORT, SMTP_HOST, SMTP_PORT  # noqa: E402
from . import config, desktop  # noqa: E402
from .i18n import _  # noqa: E402
from .ui_common import APP_CSS, ask  # noqa: E402


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        GLib.set_application_name(APP_NAME)
        self.settings = config.Settings()
        self.win = None
        self.login = None
        self.backend = None
        self.cache = None
        self.tray = None
        self.unread = 0
        self.started = False
        self.pending_mailto = []
        self.badge = None
        self._auth_dialog = False
        self._timers = []
        self.notified = {}            # (folder, uid) -> notification id currently shown in GNOME
        self.summary_keys = set()     # messages covered by the "N new messages" notification

    # ================================================================ startup
    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_data(APP_CSS.encode(), -1) if hasattr(css, "load_from_data") else css.load_from_string(APP_CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        theme.add_search_path(desktop.ICON_DIR)
        sm = Adw.StyleManager.get_default()
        sm.set_color_scheme(Adw.ColorScheme.DEFAULT)      # follow the system light/dark setting
        sm.connect("notify::dark", lambda *a: self.apply_theme())
        self.badge = desktop.LauncherBadge()

        def add(name, fn, ptype=None):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new(ptype) if ptype else None)
            a.connect("activate", lambda act, v: fn(v) if ptype else fn())
            self.add_action(a)
        add("preferences", self.show_prefs)
        add("shortcuts", lambda: self.win and __import__(
            "linux_icloud_mail.dialogs", fromlist=["x"]).show_shortcuts(self.win))
        add("about", self.show_about)
        add("logout", self.ask_logout)
        add("quit", self.quit_app)
        add("show", self.show_window)
        add("compose", lambda: self.compose())
        add("open-message", lambda v: self.open_message(*v.unpack()), "(su)")
        for action, accels in (("app.quit", ["<Control>q"]), ("win.compose", ["<Control>n"]),
                               ("win.reply", ["<Control>r"]), ("win.reply-all", ["<Control><Shift>r"]),
                               ("win.forward", ["<Control><Shift>f"]), ("win.refresh", ["F5"]),
                               ("win.search", ["<Control>f"]), ("win.toggle-read", ["<Control><Shift>u"]),
                               ("win.toggle-flag", ["<Control><Shift>l"]), ("win.archive", ["<Control><Shift>a"]),
                               ("win.junk", ["<Control><Shift>j"]), ("win.print", ["<Control>p"]),
                               ("win.zoom-in", ["<Control>plus", "<Control>equal", "<Control>KP_Add"]),
                               ("win.zoom-out", ["<Control>minus", "<Control>KP_Subtract"]),
                               ("win.toggle-sidebar", ["F9"]), ("app.preferences", ["<Control>comma"]),
                               ("window.close", ["<Control>w"])):
            self.set_accels_for_action(action, accels)
        from .tray import Tray
        self.tray = Tray(APP_ID, APP_NAME, desktop.tray_icon_name(sm.get_dark(), False), self.toggle_window,
                         self._tray_items())
        self.hold()   # keep running in the tray when the window is closed

    def _tray_items(self):
        return [(1, _("Show Window"), self.show_window), (2, _("New Message"), lambda: self.compose()),
                (3, _("Check Mail"), lambda: self.win and self.win.refresh()), (4, None, None),
                (5, _("Quit"), self.quit_app)]

    def do_command_line(self, cmd):
        args = cmd.get_arguments()[1:]
        hidden = "--hidden" in args
        mailtos = [a for a in args if a.startswith("mailto:")]
        if not self.started:
            self.started = True
            self.pending_mailto = mailtos
            self.start(hidden and not mailtos)
        else:
            self.show_window()
            for m in mailtos:
                self.compose(mailto=m)
        return 0

    def do_activate(self):
        self.show_window()

    def server_overrides(self, imap_only=False):
        d = {"host": os.environ.get("LIM_IMAP_HOST", IMAP_HOST),
             "port": int(os.environ.get("LIM_IMAP_PORT", IMAP_PORT))}
        if not imap_only:
            d["smtp_host"] = os.environ.get("LIM_SMTP_HOST", SMTP_HOST)
            d["smtp_port"] = int(os.environ.get("LIM_SMTP_PORT", SMTP_PORT))
        return d

    def start(self, hidden=False):
        self.apply_theme()
        email = self.settings["email"]
        pw = config.lookup_password(email) if email else None
        if email and pw:
            self.start_main(email, pw, hidden)
        else:
            self.show_login()

    def show_login(self, error=None):
        from .dialogs import LoginWindow
        if self.login is not None:
            self.login.present()
            return
        self.login = LoginWindow(self, self._login_ok, error=error)
        self.login.connect("close-request", lambda w: (setattr(self, "login", None),
                                                        self.win is None and self.quit_app(), False)[2])
        self.login.present()

    def _login_ok(self, email, pw, autostart):
        if email.lower() != (self.settings["email"] or "").lower():
            self.settings.data["aliases"] = []
        self.settings["email"] = email
        config.store_password(email, pw)
        self.set_autostart(autostart)
        lw = self.login
        self.login = None
        self.start_main(email, pw, False)
        if lw is not None:
            lw.destroy()

    def start_main(self, email, pw, hidden):
        from .backend import Backend
        from .cache import Cache
        from .window import MainWindow
        self._auth_dialog = False
        self.cache = Cache(email)
        self.backend = Backend(email, pw, self.cache, self, **self.server_overrides())
        self.win = MainWindow(self)
        if not hidden:
            self.win.present()
        self.backend.start()
        self.backend.refresh_folders(done=self._after_folders)
        for t in self._timers:
            GLib.source_remove(t)
        self._timers = [GLib.timeout_add_seconds(300, self._poll), GLib.timeout_add_seconds(900, self._poll_counts)]
        for m in self.pending_mailto:
            self.compose(mailto=m)
        self.pending_mailto = []
        if self.settings["autostart"]:
            desktop.set_autostart(True)

    def _after_folders(self):
        b = self.backend
        b.sync_folder("INBOX", prio=2, done=self.notify_new)
        for f in b.folders:
            if f.name != "INBOX" and f.name != (self.win.current if self.win else None):
                b.sync_folder(f.name, prio=7)

    def _poll(self):
        if self.backend:
            self.backend.sync_folder("INBOX", prio=4, done=self.notify_new)
            if self.win and self.win.current not in ("INBOX", "__flagged__"):
                self.backend.sync_folder(self.win.current, prio=4)
        return True

    def _poll_counts(self):
        if self.backend:
            self.backend.refresh_counts()
        return True

    # ================================================================ theme
    def app_icon_name(self):
        return desktop.app_icon_name(Adw.StyleManager.get_default().get_dark())

    def apply_theme(self):
        dark = Adw.StyleManager.get_default().get_dark()
        Gtk.Window.set_default_icon_name(desktop.app_icon_name(dark))
        desktop.apply_launcher_icon(dark)
        if self.tray:
            self.tray.set_icon(desktop.tray_icon_name(dark, self.unread > 0))
        if self.login is not None:
            self.login.icon.set_from_icon_name(desktop.app_icon_name(dark))

    # ================================================================ window / tray
    def tray_available(self):
        if self.tray is None or self.tray.bus is None:
            return False
        try:
            r = self.tray.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                        "NameHasOwner", GLib.Variant("(s)", ("org.kde.StatusNotifierWatcher",)),
                                        None, Gio.DBusCallFlags.NONE, 500, None)
            return bool(r.unpack()[0])
        except GLib.Error:
            return False

    def show_window(self):
        if self.win is not None:
            self.win.set_visible(True)
            self.win.present()
        elif self.login is not None:
            self.login.present()

    def toggle_window(self):
        if self.win is not None and self.win.get_visible() and self.win.is_active():
            self.win.close()
        else:
            self.show_window()

    def tray_hint(self):
        if self.settings["tray_hint_shown"]:
            return
        self.settings["tray_hint_shown"] = True
        n = Gio.Notification.new(_("Linux iCloud Mail is still running in the tray"))
        n.set_body(_("Right-click the tray icon and choose Quit to close it."))
        n.set_icon(Gio.ThemedIcon.new(self.app_icon_name()))
        self.send_notification("tray-hint", n)

    def set_unread(self, n):
        self.unread = n
        dark = Adw.StyleManager.get_default().get_dark()
        if self.tray:
            self.tray.set_icon(desktop.tray_icon_name(dark, n > 0))
            self.tray.set_tooltip("%s – %s" % (APP_NAME, (_("%d unread messages") % n) if n
                                                else _("No unread messages")))
        if self.badge:
            self.badge.set(n)

    def quit_app(self):
        if self.backend:
            self.backend.stop()
        if self.win is not None:
            try:
                self.win._on_close_save_only()
            except Exception:
                pass
        self.quit()

    # ================================================================ notifications
    def notify_new(self, rows):
        if not rows or not self.settings["notifications"]:
            return
        now = time.time()
        rows = [r for r in rows if not r.seen and (not r.idate or r.idate > now - 3 * 86400)]
        if not rows:
            return
        if self.win is not None and self.win.is_active() and self.win.get_visible() and self.win.current == "INBOX":
            return
        rows.sort(key=lambda r: r.idate, reverse=True)
        icon = Gio.ThemedIcon.new(self.app_icon_name())
        if len(rows) > 3:
            n = Gio.Notification.new(_("%d new messages") % len(rows))
            n.set_body("\n".join("%s: %s" % (r.from_name or r.from_addr, r.subject or _("(no subject)"))
                                 for r in rows[:4]))
            n.set_icon(icon)
            n.set_default_action_and_target("app.open-message", GLib.Variant("(su)", (rows[0].folder, 0)))
            self.send_notification("new-mail", n)
            self.summary_keys |= {(r.folder, r.uid) for r in rows}
            return
        for r in rows:
            n = Gio.Notification.new(r.from_name or r.from_addr or APP_NAME)
            body = r.subject or _("(no subject)")
            if r.preview:
                body += "\n" + r.preview[:120]
            n.set_body(body)
            n.set_icon(icon)
            n.set_default_action_and_target("app.open-message", GLib.Variant("(su)", (r.folder, r.uid)))
            nid = "mail-%s-%d" % (r.folder, r.uid)
            self.send_notification(nid, n)
            self.notified[(r.folder, r.uid)] = nid

    def withdraw_notifications(self, folder, uids):
        """Remove notifications of messages that were opened, read, moved or deleted."""
        for uid in uids:
            nid = self.notified.pop((folder, uid), None)
            if nid:
                self.withdraw_notification(nid)
            if (folder, uid) in self.summary_keys:
                self.summary_keys.discard((folder, uid))
                if not self.summary_keys:
                    self.withdraw_notification("new-mail")

    def open_message(self, folder, uid):
        # clicked notification: it has done its job - remove it from GNOME
        if uid:
            self.withdraw_notifications(folder, [uid])
        elif self.summary_keys:
            self.summary_keys.clear()
            self.withdraw_notification("new-mail")
        self.show_window()
        if self.win is None:
            return
        w = self.win
        if w.current != folder:
            row = w.folder_rows.get(folder)
            if row:
                w.folder_list.select_row(row)
            else:
                w.load_view(folder)
        if w.search.get_text():
            w.search.set_text("")
        if w.mode_filter != "all":
            w.activate_action("win.filter", GLib.Variant("s", "all"))

        tries = [0]

        def select():
            it = w.items.get((folder, uid))
            if it is None:
                row = self.cache.get(folder, uid) if self.cache else None
                if row is not None:
                    w._insert_rows([row])
                    it = w.items.get((folder, uid))
            if it is not None:
                pos = w._position_of(it)
                if pos >= 0:
                    w.sel.select_item(pos, True)
                    w.listview.scroll_to(pos, Gtk.ListScrollFlags.FOCUS | Gtk.ListScrollFlags.SELECT, None)
                    # opened on purpose from the notification -> read now (updates tray / dock counter)
                    if not it.row.seen and self.settings["mark_read"] != "manual":
                        w.flag_items([it], "\\Seen", True)
                    return False
            tries[0] += 1
            return tries[0] < 10       # message may still be arriving - retry for ~3 s
        if uid:
            GLib.timeout_add(300, select)

    # ================================================================ compose
    def compose(self, mode="new", item=None, parsed=None, mailto=None):
        if self.backend is None:
            return
        from .composer import Composer
        to = subject = body = None
        if mailto:
            u = urllib.parse.urlsplit(mailto)
            to = urllib.parse.unquote(u.path)
            q = urllib.parse.parse_qs(u.query)
            subject = (q.get("subject") or [None])[0]
            body = (q.get("body") or [None])[0]
            if q.get("cc"):
                to = to  # cc handled below
        c = Composer(self, mode=mode, item=item, parsed=parsed, to=to, subject=subject, body=body)
        if mailto and urllib.parse.parse_qs(urllib.parse.urlsplit(mailto).query).get("cc"):
            c.cc_e.set_text(urllib.parse.parse_qs(urllib.parse.urlsplit(mailto).query)["cc"][0])
            c.ccbcc_btn.set_active(True)
        c.present()

    # ================================================================ prefs / account
    def show_prefs(self):
        from .dialogs import PrefsDialog
        if self.win is not None:
            PrefsDialog(self).present(self.win)

    def show_about(self):
        from .dialogs import show_about
        show_about(self, self.win)

    def set_autostart(self, on):
        self.settings["autostart"] = on
        desktop.set_autostart(on)

    def clear_cache(self):
        if self.backend is None:
            return
        email = self.settings["email"]
        pw = self.backend.password
        self._shutdown_main()
        self.cache.wipe()
        self.start_main(email, pw, False)

    def ask_logout(self):
        ask(self.win, _("Sign out of %s?") % self.settings["email"],
            _("The password and the local cache will be removed from this computer."),
            [("cancel", _("Cancel"), None), ("logout", _("Sign Out"), "destructive")],
            lambda r: r == "logout" and self.logout())

    def logout(self):
        email = self.settings["email"]
        self._shutdown_main()
        config.clear_password(email)
        if self.cache:
            self.cache.wipe()
        self.cache = None
        self.set_unread(0)
        self.show_login()

    def _shutdown_main(self):
        if self.backend:
            self.backend.stop()
        self.backend = None
        for w in list(self.get_windows()):
            if w is not self.login:
                w.destroy()
        self.win = None

    def reconnect(self):
        if self.backend:
            self.backend.refresh_folders(done=self._after_folders)

    # ================================================================ backend listener
    def on_folders(self, folders, counts):
        if self.win:
            self.win.on_folders(folders, counts)

    def on_counts(self, counts):
        if self.win:
            self.win.on_counts(counts)

    def on_added(self, folder, rows):
        if self.win:
            self.win.on_added(folder, rows)

    def on_removed(self, folder, uids):
        self.withdraw_notifications(folder, uids)
        if self.win:
            self.win.on_removed(folder, uids)

    def on_flags(self, folder, changed):
        # read elsewhere (iPhone, icloud.com) -> drop its notification
        self.withdraw_notifications(folder, [u for u, f in changed.items() if "\\Seen" in (f or "")])
        if self.win:
            self.win.on_flags(folder, changed)

    def on_folder_reset(self, folder):
        if self.win:
            self.win.on_folder_reset(folder)

    def on_synced(self, folder, total, first):
        if self.win:
            self.win.on_synced(folder, total, first)

    def on_push(self, folder):
        if self.backend:
            self.backend.sync_folder(folder, prio=2, done=self.notify_new)

    def on_online(self, online, msg):
        if self.win:
            self.win.set_offline(not online)

    def on_auth_failed(self, msg):
        if self._auth_dialog:
            return
        self._auth_dialog = True
        self._shutdown_main()
        self.show_login(error=_("Session expired or password revoked. Please sign in again.") +
                        ("\n(%s)" % msg if msg else ""))

    def on_error(self, msg):
        if self.win and msg:
            self.win.toast("%s: %s" % (_("Error"), msg[:160]))
