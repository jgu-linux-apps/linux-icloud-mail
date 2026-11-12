"""Message reader pane."""

import os
import tempfile

import gi
gi.require_version("WebKit", "6.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango, WebKit  # noqa: E402

from .i18n import _  # noqa: E402
from .mailparse import addresses, render  # noqa: E402
from .ui_common import esc, fmt_date_long, fmt_size, open_file, open_uri, save_file_dialog  # noqa: E402


def system_font():
    try:
        name = Gtk.Settings.get_default().get_property("gtk-font-name") or ""
        desc = Pango.FontDescription.from_string(name)
        fam = desc.get_family()
        if fam:
            return "'%s', Cantarell, 'Noto Sans', sans-serif" % fam
    except Exception:
        pass
    return "Cantarell, 'Ubuntu Sans', 'Noto Sans', sans-serif"


def make_webview(editable=False):
    wv = WebKit.WebView()
    st = wv.get_settings()
    st.set_enable_javascript(editable)          # API-injected JS only (markup JS is off below)
    try:
        st.set_enable_javascript_markup(False)
    except AttributeError:
        pass
    st.set_auto_load_images(True)
    st.set_enable_page_cache(False)
    st.set_enable_developer_extras(False)
    st.set_enable_html5_database(False)
    st.set_enable_html5_local_storage(False)
    st.set_enable_write_console_messages_to_stdout(False)
    st.set_allow_file_access_from_file_urls(False)
    try:
        st.set_enable_webgl(False)
        st.set_enable_media_stream(False)
    except AttributeError:
        pass
    st.set_hardware_acceleration_policy(WebKit.HardwareAccelerationPolicy.NEVER) \
        if os.environ.get("LIM_NO_GPU") else None
    wv.set_background_color(Gdk.RGBA(0, 0, 0, 0))
    return wv


class Reader(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.parsed = None
        self.item = None
        self.allow_remote_once = False
        self.original_colors = False
        self.stack = Gtk.Stack(vexpand=True, hexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=90)
        self.append(self.stack)

        # empty
        self.empty = Adw.StatusPage(icon_name="mail-read-symbolic", title=_("No message selected"),
                                    description=_("Select a message to read it."))
        self.empty.add_css_class("compact")
        self.stack.add_named(self.empty, "empty")

        # multi
        self.multi = Adw.StatusPage(icon_name="lim-inbox-symbolic")
        mbox = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4, min_children_per_line=1,
                           column_spacing=8, row_spacing=8, halign=Gtk.Align.CENTER, homogeneous=True)
        for icon, label, action in (("lim-archive-symbolic", _("Move to Archive"), "win.archive"),
                                    ("mail-read-symbolic", _("Mark as Read"), "win.mark-read"),
                                    ("lim-flag-outline-symbolic", _("Flag"), "win.toggle-flag"),
                                    ("user-trash-symbolic", _("Move to Trash"), "win.delete")):
            b = Gtk.Button(action_name=action, tooltip_text=label)
            b.set_child(Adw.ButtonContent(icon_name=icon, label=label))
            b.add_css_class("pill")
            mbox.append(b)
        self.multi.set_child(mbox)
        self.stack.add_named(self.multi, "multi")

        # loading
        lb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        sp = Gtk.Spinner(spinning=True, width_request=32, height_request=32)
        lb.append(sp)
        self.stack.add_named(lb, "loading")

        # error
        self.error = Adw.StatusPage(icon_name="dialog-warning-symbolic", title=_("Could not load the message"))
        self.stack.add_named(self.error, "error")

        # message
        self.stack.add_named(self._build_message(), "message")
        self.stack.set_visible_child_name("empty")

    # ------------------------------------------------------------ building
    def _btn(self, icon, tip, action):
        b = Gtk.Button(icon_name=icon, tooltip_text=tip, action_name=action)
        b.add_css_class("flat")
        return b

    def _build_message(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar = Gtk.Box(spacing=2)
        bar.add_css_class("reader-actions")
        for icon, tip, act in (("mail-reply-sender-symbolic", _("Reply") + " (Ctrl+R)", "win.reply"),
                               ("mail-reply-all-symbolic", _("Reply All") + " (Ctrl+Shift+R)", "win.reply-all"),
                               ("mail-forward-symbolic", _("Forward") + " (Ctrl+Shift+F)", "win.forward")):
            bar.append(self._btn(icon, tip, act))
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=6, margin_end=6))
        self.archive_btn = self._btn("lim-archive-symbolic", _("Move to Archive") + " (A)", "win.archive")
        self.junk_btn = self._btn("mail-mark-junk-symbolic", _("Move to Junk"), "win.junk")
        self.delete_btn = self._btn("user-trash-symbolic", _("Move to Trash") + " (Del)", "win.delete")
        for b in (self.archive_btn, self.junk_btn, self.delete_btn):
            bar.append(b)
        self.move_btn = Gtk.MenuButton(icon_name="folder-symbolic", tooltip_text=_("Move to…"))
        self.move_btn.add_css_class("flat")
        self.move_btn.set_create_popup_func(lambda mb: mb.set_popover(self.win.build_move_popover()))
        bar.append(self.move_btn)
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=6, margin_end=6))
        self.flag_btn = self._btn("lim-flag-outline-symbolic", _("Flag"), "win.toggle-flag")
        self.read_btn = self._btn("mail-unread-symbolic", _("Mark as Unread"), "win.toggle-read")
        bar.append(self.flag_btn)
        bar.append(self.read_btn)
        bar.append(Gtk.Box(hexpand=True))
        self.draft_btn = Gtk.Button(label=_("Edit Draft"), action_name="win.edit-draft", visible=False)
        self.draft_btn.add_css_class("suggested-action")
        bar.append(self.draft_btn)
        menu = Gio.Menu()
        menu.append(_("Show Original Colors"), "win.original-colors")
        menu.append(_("Zoom In"), "win.zoom-in")
        menu.append(_("Zoom Out"), "win.zoom-out")
        s2 = Gio.Menu()
        s2.append(_("Print…"), "win.print")
        s2.append(_("Save as .eml…"), "win.save-eml")
        s2.append(_("View Source"), "win.view-source")
        menu.append_section(None, s2)
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, tooltip_text=_("More"))
        more.add_css_class("flat")
        bar.append(more)
        box.append(bar)
        box.append(Gtk.Separator())

        hdr = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        hdr.add_css_class("reader-header")
        self.subject = Gtk.Label(xalign=0, wrap=True, selectable=True, wrap_mode=2)
        self.subject.add_css_class("reader-subject")
        hdr.append(self.subject)
        row = Gtk.Box(spacing=12)
        self.avatar = Adw.Avatar(size=42, show_initials=True)
        row.append(self.avatar)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        frm_box = Gtk.Box(spacing=8)
        self.from_lbl = Gtk.Label(xalign=0, selectable=True, ellipsize=3)
        self.from_lbl.add_css_class("reader-from")
        self.addr_lbl = Gtk.Label(xalign=0, selectable=True, ellipsize=3, hexpand=True)
        self.addr_lbl.add_css_class("dim-label")
        frm_box.append(self.from_lbl)
        frm_box.append(self.addr_lbl)
        self.to_lbl = Gtk.Label(xalign=0, selectable=True, wrap=True, wrap_mode=2)
        self.to_lbl.add_css_class("dim-label")
        self.to_lbl.add_css_class("caption")
        col.append(frm_box)
        col.append(self.to_lbl)
        row.append(col)
        self.date_lbl = Gtk.Label(xalign=1, valign=Gtk.Align.START)
        self.date_lbl.add_css_class("dim-label")
        self.date_lbl.add_css_class("caption")
        row.append(self.date_lbl)
        hdr.append(row)
        self.att_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6,
                                   column_spacing=6, row_spacing=6, homogeneous=False)
        self.att_revealer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        att_head = Gtk.Box(spacing=8)
        self.att_title = Gtk.Label(xalign=0, hexpand=True)
        self.att_title.add_css_class("caption-heading")
        save_all = Gtk.Button(label=_("Download All"))
        save_all.set_tooltip_text(_("Save all attachments to the Downloads folder"))
        save_all.add_css_class("flat")
        save_all.add_css_class("caption")
        save_all.connect("clicked", lambda b: self._save_all())
        self.save_all_btn = save_all
        att_head.append(self.att_title)
        att_head.append(save_all)
        self.att_revealer.append(att_head)
        self.att_revealer.append(self.att_box)
        hdr.append(self.att_revealer)
        box.append(hdr)

        self.banner = Adw.Banner(title=_("Remote content is blocked to protect your privacy."),
                                 button_label=_("Load Remote Content"))
        self.banner.connect("button-clicked", lambda b: self._load_remote())
        box.append(self.banner)

        self.web = make_webview(editable=False)
        self.web.set_vexpand(True)
        self.web.connect("decide-policy", self._on_policy)
        self.web.connect("context-menu", self._on_context_menu)
        frame = Gtk.Box(vexpand=True)
        frame.add_css_class("webview-frame")
        self.web.set_hexpand(True)
        frame.append(self.web)
        box.append(frame)
        return box

    # ------------------------------------------------------------ state
    def show_empty(self):
        self.item = None
        self.parsed = None
        self.stack.set_visible_child_name("empty")

    def show_multi(self, n):
        self.item = None
        self.multi.set_title(_("%d messages selected") % n)
        self.stack.set_visible_child_name("multi")

    def show_loading(self, item):
        self.item = item
        # don't flash the spinner for fast (cached) loads
        token = object()
        self._loading_token = token

        def later():
            if getattr(self, "_loading_token", None) is token:
                self.stack.set_visible_child_name("loading")
            return False
        GLib.timeout_add(180, later)

    def show_error(self, item, msg):
        if item is not self.item:
            return
        self._loading_token = None
        self.error.set_description(esc(msg))
        self.stack.set_visible_child_name("error")

    def show_message(self, item, parsed):
        if item is not self.item:
            return
        self._loading_token = None
        self.parsed = parsed
        self.allow_remote_once = False
        self.original_colors = False
        r = item.row
        self.subject.set_text(parsed.h("subject") or r.subject or _("(no subject)"))
        frm = addresses(parsed.h("from"))
        name, addr = frm[0] if frm else (r.from_name, r.from_addr)
        self.avatar.set_text(name or addr or "?")
        if name and addr and name != addr:
            self.from_lbl.set_text(name)
            self.addr_lbl.set_text(addr)
        else:
            self.from_lbl.set_text(addr or name or _("(no sender)"))
            self.addr_lbl.set_text("")
        lines = []
        for key, label in (("to", _("To")), ("cc", _("Cc")), ("reply-to", _("Reply-To"))):
            v = parsed.h(key)
            if v:
                lst = addresses(v)
                txt = ", ".join((n if n and n != a else a) for n, a in lst) if lst else v
                lines.append("%s: %s" % (label, txt))
        self.to_lbl.set_text("\n".join(lines))
        self.to_lbl.set_tooltip_text(", ".join(parsed.h(k) for k in ("to", "cc") if parsed.h(k)))
        self.date_lbl.set_text(fmt_date_long(r.idate))
        self._fill_attachments(parsed)
        self.update_flags()
        is_draft = self.win.folder_role(r.folder) == "drafts"
        self.draft_btn.set_visible(is_draft)
        is_junk = self.win.folder_role(r.folder) == "junk"
        self.junk_btn.set_icon_name("mail-mark-notjunk-symbolic" if is_junk else "mail-mark-junk-symbolic")
        self.junk_btn.set_tooltip_text(_("Not Junk") if is_junk else _("Move to Junk"))
        self.archive_btn.set_visible(self.win.folder_role(r.folder) != "archive" and bool(self.win.backend.role("archive")))
        self.web.set_zoom_level(self.win.settings.get("zoom", 1.0) or 1.0)
        self._render()
        self.stack.set_visible_child_name("message")

    def update_flags(self):
        if not self.item:
            return
        r = self.item.row
        self.flag_btn.set_icon_name("lim-flag-symbolic" if r.flagged else "lim-flag-outline-symbolic")
        self.flag_btn.set_tooltip_text(_("Unflag") if r.flagged else _("Flag"))
        if r.flagged:
            self.flag_btn.add_css_class("flag-icon")
        else:
            self.flag_btn.remove_css_class("flag-icon")
        self.read_btn.set_icon_name("mail-unread-symbolic" if r.seen else "mail-read-symbolic")
        self.read_btn.set_tooltip_text(_("Mark as Unread") if r.seen else _("Mark as Read"))

    def _render(self):
        st = self.win.settings
        dark = Adw.StyleManager.get_default().get_dark()
        allow = st["remote_images"] or self.allow_remote_once
        doc, blocked = render(self.parsed, dark=dark, dark_mail=st["dark_mail"] and not self.original_colors,
                              allow_remote=allow, font=system_font())
        self.banner.set_revealed(blocked)
        self.web.load_html(doc, None)

    def rerender(self):
        if self.parsed is not None and self.stack.get_visible_child_name() == "message":
            self._render()

    def _load_remote(self):
        self.allow_remote_once = True
        self._render()

    def toggle_original_colors(self):
        self.original_colors = not self.original_colors
        self._render()

    def zoom(self, delta):
        z = max(0.5, min(3.0, (self.web.get_zoom_level() or 1.0) + delta))
        self.web.set_zoom_level(z)
        self.win.settings["zoom"] = z

    # ------------------------------------------------------------ attachments
    def _fill_attachments(self, parsed):
        while (c := self.att_box.get_first_child()) is not None:
            self.att_box.remove(c)
        atts = parsed.attachments
        self.att_revealer.set_visible(bool(atts))
        self.save_all_btn.set_visible(len(atts) > 1)
        if not atts:
            return
        self.att_title.set_text("%s (%d)" % (_("Attachments"), len(atts)))
        for att in atts:
            chip = Gtk.Box(spacing=0)
            chip.add_css_class("linked")
            gicon = Gio.content_type_get_symbolic_icon(Gio.content_type_from_mime_type(att.ctype) or att.ctype)
            open_b = Gtk.Button(tooltip_text=_("Open"))
            ob = Gtk.Box(spacing=6)
            ob.append(Gtk.Image.new_from_gicon(gicon))
            nl = Gtk.Label(label=att.name, ellipsize=2, max_width_chars=28)
            ob.append(nl)
            sl = Gtk.Label(label=fmt_size(att.size))
            sl.add_css_class("dim-label")
            sl.add_css_class("caption")
            ob.append(sl)
            open_b.set_child(ob)
            open_b.connect("clicked", lambda b, a=att: self._open_att(a))
            save_b = Gtk.Button(icon_name="document-save-symbolic", tooltip_text=_("Save Attachment"))
            save_b.connect("clicked", lambda b, a=att: self._save_att(a))
            chip.append(open_b)
            chip.append(save_b)
            self.att_box.append(chip)

    def _with_data(self, att, cb):
        item = self.item
        self.win.backend.fetch_attachment(item.row.folder, item.row.uid, att, cb,
                                          error=lambda e: self.win.toast(_("Error") + ": " + str(e)))

    def _open_att(self, att):
        def go(data):
            d = tempfile.mkdtemp(prefix="lim-")
            path = os.path.join(d, os.path.basename(att.name) or "attachment")
            with open(path, "wb") as f:
                f.write(data)
            open_file(self.win, path)
        self._with_data(att, go)

    def _save_att(self, att):
        def chosen(path):
            def go(data):
                with open(path, "wb") as f:
                    f.write(data)
                self.win.toast(_("Saved to %s") % os.path.basename(path))
            self._with_data(att, go)
        save_file_dialog(self.win, os.path.basename(att.name), chosen)

    def _save_all(self):
        """Download all attachments straight to ~/Downloads (like a browser), then offer to open it."""
        if self.parsed is None or not self.parsed.attachments:
            return
        atts = list(self.parsed.attachments)
        folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.expanduser("~/Downloads")
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            self.win.toast(_("Error") + ": " + str(e))
            return
        state = {"left": len(atts), "saved": 0, "errors": []}
        self.save_all_btn.set_sensitive(False)

        def finished():
            self.save_all_btn.set_sensitive(True)
            if state["errors"]:
                self.win.toast(_("Error") + ": " + state["errors"][0])
            toast = Adw.Toast(title=_("%d attachments saved to %s") % (state["saved"], os.path.basename(folder)),
                              button_label=_("Open Folder"), timeout=6)
            toast.connect("button-clicked", lambda t: open_file(self.win, folder))
            self.win.toasts.add_toast(toast)

        def one_done():
            state["left"] -= 1
            if state["left"] == 0:
                finished()

        for att in atts:
            def go(data, a=att):
                try:
                    base = os.path.basename((a.name or "").replace("/", "_")) or "attachment"
                    path = os.path.join(folder, base)
                    k = 1
                    while os.path.exists(path):
                        stem, ext = os.path.splitext(base)
                        path = os.path.join(folder, "%s (%d)%s" % (stem, k, ext))
                        k += 1
                    with open(path, "wb") as f:
                        f.write(data or b"")
                    state["saved"] += 1
                except OSError as e:
                    state["errors"].append(str(e))
                one_done()

            def err(e, a=att):
                state["errors"].append("%s: %s" % (a.name, e))
                one_done()
            item = self.item
            self.win.backend.fetch_attachment(item.row.folder, item.row.uid, att, go, error=err)

    # ------------------------------------------------------------ web policy
    def _on_policy(self, wv, decision, dtype):
        if dtype in (WebKit.PolicyDecisionType.NAVIGATION_ACTION, WebKit.PolicyDecisionType.NEW_WINDOW_ACTION):
            nav = decision.get_navigation_action()
            uri = nav.get_request().get_uri()
            if uri.startswith("about:") or uri.startswith("data:"):
                if nav.get_navigation_type() != WebKit.NavigationType.LINK_CLICKED:
                    decision.use()
                    return True
            decision.ignore()
            if nav.get_navigation_type() == WebKit.NavigationType.LINK_CLICKED or \
                    dtype == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION:
                if uri.startswith("mailto:"):
                    self.win.app.compose(mailto=uri)
                elif uri.startswith(("http:", "https:", "ftp:", "tel:")):
                    open_uri(self.win, uri)
            return True
        return False

    def _on_context_menu(self, wv, menu, hit):
        # drop navigation items that make no sense for an email
        keep = {WebKit.ContextMenuAction.COPY, WebKit.ContextMenuAction.COPY_LINK_TO_CLIPBOARD,
                WebKit.ContextMenuAction.COPY_IMAGE_TO_CLIPBOARD, WebKit.ContextMenuAction.SELECT_ALL,
                WebKit.ContextMenuAction.COPY_IMAGE_URL_TO_CLIPBOARD}
        for it in list(menu.get_items()):
            if it.get_stock_action() not in keep:
                menu.remove(it)
        return False
