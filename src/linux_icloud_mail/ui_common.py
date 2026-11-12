"""Shared UI helpers: dialogs, formatting, message list item objects."""

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from .i18n import _


# ------------------------------------------------------------------ formatting
def fmt_size(n):
    n = n or 0
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.0f KB" % (n / 1024)
    return "%.1f MB" % (n / 1024 / 1024)


def fmt_date_short(ts):
    if not ts:
        return ""
    dt = GLib.DateTime.new_from_unix_local(ts)
    now = GLib.DateTime.new_now_local()
    if dt.get_year() == now.get_year() and dt.get_day_of_year() == now.get_day_of_year():
        return dt.format("%H:%M")
    if now.to_unix() - ts < 86400 * 2 and (now.get_day_of_year() - dt.get_day_of_year()) in (1, -364, -365):
        return _("Yesterday")
    if now.to_unix() - ts < 86400 * 6:
        return dt.format("%A")
    if dt.get_year() == now.get_year():
        return dt.format("%d.%m")
    return dt.format("%d.%m.%Y")


def fmt_date_long(ts):
    if not ts:
        return ""
    return GLib.DateTime.new_from_unix_local(ts).format("%A, %-d %B %Y, %H:%M")


def esc(s):
    return GLib.markup_escape_text(s or "")


# ------------------------------------------------------------------ dialogs
def ask(parent, heading, body, responses, callback, default=None):
    """responses: list of (id, label, appearance) appearance in {None,'suggested','destructive'}"""
    dlg = Adw.AlertDialog(heading=heading, body=body or "")
    for rid, label, app in responses:
        dlg.add_response(rid, label)
        if app == "suggested":
            dlg.set_response_appearance(rid, Adw.ResponseAppearance.SUGGESTED)
        elif app == "destructive":
            dlg.set_response_appearance(rid, Adw.ResponseAppearance.DESTRUCTIVE)
    dlg.set_close_response("cancel")
    if default:
        dlg.set_default_response(default)
    dlg.connect("response", lambda d, r: callback(r))
    dlg.present(parent)
    return dlg


def prompt_text(parent, heading, initial, ok_label, callback, placeholder=""):
    dlg = Adw.AlertDialog(heading=heading)
    entry = Gtk.Entry(text=initial or "", placeholder_text=placeholder, activates_default=True)
    dlg.set_extra_child(entry)
    dlg.add_response("cancel", _("Cancel"))
    dlg.add_response("ok", ok_label)
    dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("ok")
    dlg.set_close_response("cancel")

    def resp(d, r):
        if r == "ok" and entry.get_text().strip():
            callback(entry.get_text().strip())
    dlg.connect("response", resp)
    dlg.present(parent)
    GLib.idle_add(entry.grab_focus)


def open_uri(parent, uri):
    try:
        Gtk.UriLauncher.new(uri).launch(parent, None, None, None)
    except Exception:
        Gio.AppInfo.launch_default_for_uri(uri, None)


def open_file(parent, path):
    try:
        Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(parent, None, None, None)
    except Exception:
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)


def save_file_dialog(parent, name, callback):
    dlg = Gtk.FileDialog(initial_name=name)
    dl = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
    if dl:
        dlg.set_initial_folder(Gio.File.new_for_path(dl))

    def done(d, res):
        try:
            f = d.save_finish(res)
        except GLib.Error:
            return
        if f:
            callback(f.get_path())
    dlg.save(parent, None, done)


def select_folder_dialog(parent, callback):
    dlg = Gtk.FileDialog()

    def done(d, res):
        try:
            f = d.select_folder_finish(res)
        except GLib.Error:
            return
        if f:
            callback(f.get_path())
    dlg.select_folder(parent, None, done)


def open_files_dialog(parent, callback):
    dlg = Gtk.FileDialog()

    def done(d, res):
        try:
            files = d.open_multiple_finish(res)
        except GLib.Error:
            return
        callback([files.get_item(i).get_path() for i in range(files.get_n_items())])
    dlg.open_multiple(parent, None, done)


# ------------------------------------------------------------------ list items
class MsgItem(GObject.Object):
    __gtype_name__ = "LimMsgItem"
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, row):
        super().__init__()
        self.row = row

    @property
    def key(self):
        return (self.row.folder, self.row.uid)

    def set_flags(self, flags):
        if flags != self.row.flags:
            self.row.flags = flags
            self.emit("changed")

    def toggle_flag(self, flag, add):
        fl = set((self.row.flags or "").split())
        if add:
            fl.add(flag)
        else:
            fl.discard(flag)
        self.set_flags(" ".join(sorted(fl)))


class MsgRowWidget(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.add_css_class("msg-row")
        self.item = None
        self._hid = None
        self.dot = Gtk.Image.new_from_icon_name("lim-dot-symbolic")
        self.dot.add_css_class("unread-dot")
        self.dot.set_valign(Gtk.Align.START)
        self.dot.set_margin_top(3)
        self.dot.set_pixel_size(12)
        self.append(self.dot)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        top = Gtk.Box(spacing=6)
        self.sender = Gtk.Label(xalign=0, hexpand=True, ellipsize=3)
        self.sender.add_css_class("msg-sender")
        self.date = Gtk.Label(xalign=1)
        self.date.add_css_class("msg-date")
        # group-by-sender: message count badge + expand/collapse arrow
        self.badge = Gtk.Label(visible=False, valign=Gtk.Align.CENTER)
        self.badge.add_css_class("group-badge")
        self.expander = Gtk.Button(icon_name="pan-end-symbolic", visible=False, valign=Gtk.Align.CENTER)
        self.expander.add_css_class("flat")
        self.expander.add_css_class("circular")
        self.expander.add_css_class("group-expander")
        self.expander.connect("clicked", lambda b: self.on_toggle and self.item and self.on_toggle(self.item))
        self.group = None        # callable(item) -> (count, unread_in_group, is_head, expanded) or None
        self.on_toggle = None
        top.append(self.sender)
        top.append(self.badge)
        top.append(self.date)
        top.append(self.expander)
        mid = Gtk.Box(spacing=4)
        self.subject = Gtk.Label(xalign=0, hexpand=True, ellipsize=3)
        self.subject.add_css_class("msg-subject")
        self.att = Gtk.Image.new_from_icon_name("mail-attachment-symbolic")
        self.att.add_css_class("dim-label")
        self.replied = Gtk.Image.new_from_icon_name("mail-replied-symbolic")
        self.replied.add_css_class("dim-label")
        self.flag = Gtk.Image.new_from_icon_name("lim-flag-symbolic")
        self.flag.add_css_class("flag-icon")
        for w in (self.subject, self.replied, self.att, self.flag):
            mid.append(w)
        self.preview = Gtk.Label(xalign=0, wrap=True, lines=2, ellipsize=3, wrap_mode=2)
        self.preview.add_css_class("msg-preview")
        self.preview.set_max_width_chars(10)
        col.append(top)
        col.append(mid)
        col.append(self.preview)
        self.append(col)

    def bind(self, item, show_folder=None):
        if self.item is not None and self._hid:
            self.item.disconnect(self._hid)
        self.item = item
        self.show_folder = show_folder
        self._hid = item.connect("changed", lambda *a: self.update())
        self.update()

    def unbind(self):
        if self.item is not None and self._hid:
            self.item.disconnect(self._hid)
        self.item = None
        self._hid = None

    def update(self):
        r = self.item.row
        sent_like = getattr(self, "show_to", False)
        if sent_like:
            from .mailparse import addresses
            a = addresses(r.to_h)
            who = ", ".join((n or ad) for n, ad in a) or _("(no sender)")
        else:
            who = r.from_name or r.from_addr or _("(no sender)")
        self.sender.set_text(who)
        self.date.set_text(fmt_date_short(r.idate))
        self.subject.set_text(r.subject or _("(no subject)"))
        pv = r.preview or ""
        if self.show_folder:
            pv = "[%s] %s" % (self.show_folder(r.folder), pv)
        self.preview.set_text(pv)
        self.preview.set_visible(bool(pv))
        unread = not r.seen
        g = self.group(self.item) if self.group else None
        if g and g[2] and g[0] > 1:          # collapsed/expanded group header
            count, g_unread, _head, expanded = g
            self.badge.set_text(str(count))
            self.badge.set_tooltip_text(_("%d messages from this sender") % count)
            self.badge.set_visible(True)
            self.expander.set_visible(True)
            self.expander.set_icon_name("pan-down-symbolic" if expanded else "pan-end-symbolic")
            self.expander.set_tooltip_text(_("Collapse") if expanded else _("Expand"))
            if not expanded:
                unread = unread or g_unread > 0   # show the dot if anything in the group is unread
        else:
            self.badge.set_visible(False)
            self.expander.set_visible(False)
        child = bool(g and not g[2])
        self.set_margin_start(26 if child else 0)
        if child:
            self.add_css_class("group-child")
            self.preview.set_visible(False)       # compact rows inside an expanded group
        else:
            self.remove_css_class("group-child")
        self.dot.set_opacity(1.0 if unread else 0.0)
        if unread:
            self.add_css_class("unread")
        else:
            self.remove_css_class("unread")
        self.att.set_visible(bool(r.attach))
        self.flag.set_visible(r.flagged)
        self.replied.set_visible(r.answered)


APP_CSS = """
.msg-row { padding: 8px 10px 8px 4px; }
.msg-row.group-child { padding-top: 5px; padding-bottom: 5px; }
.group-badge { font-size: 0.78em; font-weight: 700; padding: 0 7px; border-radius: 999px;
               background: alpha(currentColor, 0.12); min-height: 18px; }
.group-expander { min-width: 22px; min-height: 22px; padding: 0; margin: -4px -4px -4px 0; }
.msg-sender { font-weight: 500; }
.msg-row.unread .msg-sender { font-weight: 800; }
.msg-row.unread .msg-subject { font-weight: 600; }
.msg-date { font-size: 0.86em; opacity: 0.65; font-feature-settings: "tnum"; }
.msg-subject { font-size: 0.95em; }
.msg-preview { font-size: 0.88em; opacity: 0.62; }
.unread-dot { color: @accent_color; }
.flag-icon { color: #ff9500; }
.folder-count { font-size: 0.85em; opacity: 0.7; font-feature-settings: "tnum"; }
.folder-count.unread { font-weight: 700; opacity: 1; }
.sidebar-pane { background: @sidebar_bg_color; }
.list-header { padding: 10px 12px 8px 14px; }
.list-title { font-weight: 800; font-size: 1.25em; }
.reader-header { padding: 16px 22px 12px 22px; }
.reader-subject { font-weight: 800; font-size: 1.35em; }
.reader-actions { padding: 6px 10px; }
.reader-from { font-weight: 700; }
.attachment-chip { border-radius: 10px; padding: 4px 8px; }
.account-footer { padding: 8px 14px; font-size: 0.85em; opacity: 0.7; }
.compose-field { padding: 2px 10px; }
.compose-label { opacity: 0.6; min-width: 52px; }
.drop-highlight { background: alpha(@accent_bg_color, 0.18); border-radius: 8px; }
.webview-frame { background: @view_bg_color; }
"""
