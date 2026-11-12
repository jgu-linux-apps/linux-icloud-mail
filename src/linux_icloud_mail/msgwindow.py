"""A message opened in its own window (double-click / Enter in the message list)."""

from gi.repository import Adw, Gio, GLib, Gtk

from .i18n import _
from .reader import Reader


class MessageWindow(Adw.ApplicationWindow):
    """Hosts a Reader and provides the interface Reader expects from its window."""

    def __init__(self, main, item):
        super().__init__(application=main.app, default_width=920, default_height=780)
        self.main = main
        self.app = main.app
        self.settings = main.settings
        self.backend = main.backend
        self.item = item
        self.set_title(item.row.subject or _("(no subject)"))

        self.toasts = Adw.ToastOverlay()
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        self.reader = Reader(self)
        self.toasts.set_child(self.reader)
        tv.set_content(self.toasts)
        self.set_content(tv)
        self._actions()

        sc = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)
        for accel, act in (("Delete", "win.delete"), ("<Shift>Delete", "win.delete-forever"),
                           ("Escape", "window.close"), ("<Control>w", "window.close"),
                           ("<Control>r", "win.reply"), ("<Control><Shift>r", "win.reply-all"),
                           ("<Control><Shift>f", "win.forward"), ("<Control>p", "win.print"),
                           ("<Control>plus", "win.zoom-in"), ("<Control>equal", "win.zoom-in"),
                           ("<Control>minus", "win.zoom-out")):
            sc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(accel), Gtk.NamedAction.new(act)))
        self.add_controller(sc)

        self._hid = item.connect("changed", lambda *a: self.reader.update_flags())
        self.connect("close-request", self._closing)
        Adw.StyleManager.get_default().connect("notify::dark", lambda *a: self.reader.rerender())
        self._load()

    # ---------------------------------------------------------------- what Reader needs
    def folder_role(self, name):
        return self.main.folder_role(name)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    def build_move_popover(self):
        return self.main.build_move_popover(items_fn=lambda: [self.item], exclude=self.item.row.folder,
                                            after=self.close)

    # ---------------------------------------------------------------- content
    def _load(self):
        r = self.item.row
        self.reader.show_loading(self.item)
        def shown(p):
            self.reader.show_message(self.item, p)
            # focus the message body (keyboard scrolling) instead of auto-selecting the subject text
            GLib.idle_add(lambda: (self.reader.web.grab_focus(), self.reader.subject.select_region(0, 0), False)[2])
        self.backend.fetch_body(r.folder, r.uid, r.size, shown,
                                error=lambda e: self.reader.show_error(self.item, str(e)))

    def _actions(self):
        m = self.main
        one = lambda: [self.item]  # noqa: E731

        def add(name, fn, param=None):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
            a.connect("activate", lambda act, v: fn(v) if param else fn())
            self.add_action(a)

        def reply(mode):
            if self.reader.parsed is not None:
                self.app.compose(mode=mode, item=self.item, parsed=self.reader.parsed)

        add("reply", lambda: reply("reply"))
        add("reply-all", lambda: reply("reply-all"))
        add("forward", lambda: reply("forward"))
        add("edit-draft", lambda: (reply("draft"), self.close()))
        add("delete", lambda: m.delete_selected(items=one(), parent=self, done=self.close))
        add("delete-forever", lambda: m.delete_selected(force=True, items=one(), parent=self, done=self.close))
        add("archive", lambda: (m.archive_selected(items=one()), self.close()))
        add("junk", lambda: (m.junk_selected(items=one()), self.close()))
        add("move-to", lambda v: (m.move_items(one(), v.unpack()), self.close()), "s")
        add("toggle-flag", lambda: m.flag_items(one(), "\\Flagged", not self.item.row.flagged))
        add("toggle-read", lambda: m.flag_items(one(), "\\Seen", not self.item.row.seen))
        add("mark-read", lambda: m.flag_items(one(), "\\Seen", True))
        add("original-colors", self.reader.toggle_original_colors)
        add("zoom-in", lambda: self.reader.zoom(0.1))
        add("zoom-out", lambda: self.reader.zoom(-0.1))
        add("print", lambda: m._print(reader=self.reader, parent=self))
        add("save-eml", lambda: m._save_eml(reader=self.reader, parent=self))
        add("view-source", lambda: m._view_source(reader=self.reader, parent=self))

    def _closing(self, *a):
        if self._hid:
            self.item.disconnect(self._hid)
            self._hid = None
        return False
