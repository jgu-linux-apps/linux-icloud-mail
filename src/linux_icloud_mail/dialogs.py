"""Login window, preferences, about and shortcuts dialogs."""

from gi.repository import Adw, Gtk

from . import APP_NAME, VERSION
from .i18n import _
from .ui_common import open_uri

APPLE_ACCOUNT_URL = "https://account.apple.com/account/manage"


class LoginWindow(Adw.ApplicationWindow):
    def __init__(self, app, on_success, error=None):
        super().__init__(application=app, title=APP_NAME, default_width=520, default_height=640, resizable=True)
        self.app = app
        self.on_success = on_success
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar(show_title=False))
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=440, margin_start=18, margin_end=18, margin_bottom=24)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.icon = Gtk.Image(pixel_size=96, icon_name=app.app_icon_name())
        box.append(self.icon)
        title = Gtk.Label(label=_("Sign in to iCloud Mail"))
        title.add_css_class("title-1")
        box.append(title)
        sub = Gtk.Label(label=_("Use your iCloud email address and an app-specific password."), wrap=True,
                        justify=Gtk.Justification.CENTER)
        sub.add_css_class("dim-label")
        box.append(sub)
        grp = Adw.PreferencesGroup()
        self.email = Adw.EntryRow(title=_("iCloud email address"))
        self.email.set_text(app.settings["email"] or "")
        self.pw = Adw.PasswordEntryRow(title=_("App-specific password"))
        self.pw.connect("entry-activated", lambda r: self._submit())
        self.email.connect("entry-activated", lambda r: self.pw.grab_focus())
        grp.add(self.email)
        grp.add(self.pw)
        box.append(grp)
        auto = Adw.PreferencesGroup()
        self.autostart = Adw.SwitchRow(title=_("Start automatically and stay in the tray"),
                                       active=app.settings["autostart"])
        auto.add(self.autostart)
        box.append(auto)
        exp = Adw.PreferencesGroup()
        row = Adw.ExpanderRow(title=_("How to create an app-specific password"))
        help_lbl = Gtk.Label(wrap=True, xalign=0, margin_start=12, margin_end=12, margin_top=8, margin_bottom=8,
                             label=_("Sign in at account.apple.com → Sign-In and Security → App-Specific Passwords, "
                                     "create a password named e.g. “Linux Mail” and paste it here. Your normal "
                                     "Apple ID password and two-factor codes do not work for mail apps."))
        row.add_row(help_lbl)
        link = Adw.ActionRow(title=_("Open account.apple.com"), activatable=True)
        link.add_suffix(Gtk.Image.new_from_icon_name("send-to-symbolic"))
        link.connect("activated", lambda r: open_uri(self, APPLE_ACCOUNT_URL))
        row.add_row(link)
        exp.add(row)
        box.append(exp)
        self.error = Gtk.Label(wrap=True, visible=False, justify=Gtk.Justification.CENTER)
        self.error.add_css_class("error")
        box.append(self.error)
        self.btn = Gtk.Button(label=_("Sign In"), halign=Gtk.Align.CENTER)
        self.btn.add_css_class("suggested-action")
        self.btn.add_css_class("pill")
        self.btn.connect("clicked", lambda b: self._submit())
        box.append(self.btn)
        clamp.set_child(box)
        scroll.set_child(clamp)
        tv.set_content(scroll)
        self.set_content(tv)
        if error:
            self._show_error(error)

    def _show_error(self, text):
        self.error.set_text(text)
        self.error.set_visible(True)

    def _submit(self):
        email = self.email.get_text().strip()
        pw = self.pw.get_text().strip().replace(" ", "")
        if "@" not in email or not pw:
            self._show_error(_("Check the address and the app-specific password."))
            return
        self.btn.set_sensitive(False)
        self.btn.set_label(_("Signing in…"))
        self.error.set_visible(False)
        from .backend import Backend

        def ok(_r):
            self.on_success(email, pw, self.autostart.get_active())

        def err(e):
            self.btn.set_sensitive(True)
            self.btn.set_label(_("Sign In"))
            self._show_error(_("Sign-in failed: %s") % e + "\n" + _("Check the address and the app-specific password."))
        Backend.test_login(email, pw, ok, err, **self.app.server_overrides(imap_only=True))


class PrefsDialog(Adw.PreferencesDialog):
    def __init__(self, app):
        super().__init__(title=_("Preferences"))
        self.app = app
        st = app.settings

        page = Adw.PreferencesPage(title=_("General"), icon_name="emblem-system-symbolic")
        g = Adw.PreferencesGroup(title=_("Notifications"))
        self.notif = Adw.SwitchRow(title=_("Show a notification for new mail in the Inbox"), active=st["notifications"])
        self.notif.connect("notify::active", lambda r, p: st.__setitem__("notifications", r.get_active()))
        g.add(self.notif)
        page.add(g)
        g = Adw.PreferencesGroup()
        auto = Adw.SwitchRow(title=_("Start with the system"), subtitle=_("Starts hidden in the tray"),
                             active=st["autostart"])
        auto.connect("notify::active", lambda r, p: app.set_autostart(r.get_active()))
        g.add(auto)
        tray = Adw.SwitchRow(title=_("Closing the window keeps the app in the tray"), active=st["close_to_tray"])
        tray.connect("notify::active", lambda r, p: st.__setitem__("close_to_tray", r.get_active()))
        g.add(tray)
        page.add(g)
        self.add(page)

        page = Adw.PreferencesPage(title=_("Reading"), icon_name="mail-read-symbolic")
        g = Adw.PreferencesGroup()
        modes = ["immediate", "delay", "manual"]
        mr = Adw.ComboRow(title=_("Mark messages as read"))
        model = Gtk.StringList.new([_("Immediately"), _("After 2 seconds"), _("Manually")])
        mr.set_model(model)
        mr.set_selected(modes.index(st["mark_read"]) if st["mark_read"] in modes else 1)
        mr.connect("notify::selected", lambda r, p: st.__setitem__("mark_read", modes[r.get_selected()]))
        g.add(mr)
        rem = Adw.SwitchRow(title=_("Load remote content automatically"),
                            subtitle=_("Images from the internet can reveal that you opened a message"),
                            active=st["remote_images"])
        rem.connect("notify::active", lambda r, p: (st.__setitem__("remote_images", r.get_active()),
                                                     app.win and app.win.reader.rerender()))
        g.add(rem)
        dm = Adw.SwitchRow(title=_("Dark message content in dark mode"), active=st["dark_mail"])
        dm.connect("notify::active", lambda r, p: (st.__setitem__("dark_mail", r.get_active()),
                                                    app.win and app.win.reader.rerender()))
        g.add(dm)
        ch = Adw.SwitchRow(title=_("Compose in rich text (HTML)"), active=st["compose_html"])
        ch.connect("notify::active", lambda r, p: st.__setitem__("compose_html", r.get_active()))
        g.add(ch)
        page.add(g)
        self.add(page)

        page = Adw.PreferencesPage(title=_("Account"), icon_name="avatar-default-symbolic")
        g = Adw.PreferencesGroup(title=_("Signed in as %s") % st["email"])
        name = Adw.EntryRow(title=_("Your name"), text=st["display_name"] or "")
        name.connect("changed", lambda r: st.__setitem__("display_name", r.get_text().strip()))
        g.add(name)
        al = Adw.EntryRow(title=_("Additional “From” addresses"), text=", ".join(st["aliases"] or []))
        al.set_tooltip_text(_("iCloud aliases or custom domain addresses, separated by commas"))
        al.connect("changed", lambda r: st.__setitem__(
            "aliases", [a.strip() for a in r.get_text().split(",") if "@" in a]))
        g.add(al)
        page.add(g)
        g = Adw.PreferencesGroup(title=_("Signature"))
        frame = Gtk.Frame()
        tv = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8, left_margin=10,
                          right_margin=10, height_request=90)
        tv.get_buffer().set_text(st["signature"] or "")
        tv.get_buffer().connect("changed", lambda b: st.__setitem__(
            "signature", b.get_text(b.get_start_iter(), b.get_end_iter(), False)))
        frame.set_child(tv)
        g.add(frame)
        page.add(g)
        g = Adw.PreferencesGroup()
        clear = Adw.ButtonRow(title=_("Clear Local Cache")) if hasattr(Adw, "ButtonRow") else None
        if clear is None:
            clear = Adw.ActionRow(title=_("Clear Local Cache"), activatable=True)
            clear.connect("activated", lambda r: app.clear_cache())
        else:
            clear.connect("activated", lambda r: app.clear_cache())
        g.add(clear)
        out = Adw.ActionRow(title=_("Sign Out"), activatable=True)
        out.add_css_class("error")
        out.connect("activated", lambda r: (self.close(), app.logout()))
        g.add(out)
        page.add(g)
        self.add(page)


CREDITS = "Created by: JGu\nSupported by Claude"


def show_about(app, parent):
    about = Adw.AboutDialog(application_name=APP_NAME, application_icon=app.app_icon_name(), version=VERSION,
                            developer_name=CREDITS, developers=["JGu"], copyright="© 2026 JGu",
                            comments=_("Fast native iCloud Mail client") + "\n\n" + CREDITS,
                            license="Free of charge. Modification and redistribution of modified versions "
                                    "are not permitted. See LICENSE.",
                            website="https://github.com/jgu-linux-apps/linux-icloud-mail")
    about.present(parent)


SHORTCUTS = [
    ("Ctrl+N", "New Message"), ("Ctrl+R", "Reply"), ("Ctrl+Shift+R", "Reply All"), ("Ctrl+Shift+F", "Forward"),
    ("Delete", "Move to Trash"), ("Shift+Delete", "Delete Permanently"), ("Ctrl+Shift+A", "Move to Archive"),
    ("Ctrl+Shift+J", "Move to Junk"),
    ("Ctrl+Shift+U", "Mark as Unread"), ("Ctrl+Shift+L", "Flag"), ("Ctrl+F", "Find"), ("F5", "Check Mail"),
    ("Ctrl+P", "Print…"), ("Ctrl++ / Ctrl+−", "Zoom In"), ("Ctrl+Enter", "Send"), ("Ctrl+S", "Save Draft"),
    ("F9", "Mailboxes"), ("Ctrl+W", "Close"), ("Ctrl+Q", "Quit"),
]


def show_shortcuts(parent):
    dlg = Adw.Dialog(title=_("Keyboard Shortcuts"), content_width=420)
    tv = Adw.ToolbarView()
    tv.add_top_bar(Adw.HeaderBar())
    page = Adw.PreferencesPage()
    g = Adw.PreferencesGroup()
    for keys, label in SHORTCUTS:
        row = Adw.ActionRow(title=_(label))
        k = Gtk.Label(label=keys)
        k.add_css_class("dim-label")
        row.add_suffix(k)
        g.add(row)
    page.add(g)
    tv.set_content(page)
    dlg.set_child(tv)
    dlg.present(parent)
