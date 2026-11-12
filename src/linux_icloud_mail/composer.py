"""Message composer window (rich text via an editable WebView)."""

import base64
import datetime
import html
import json
import mimetypes
import os
import re
from email.message import EmailMessage
from email.utils import format_datetime, getaddresses, make_msgid

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .i18n import _
from .mailparse import addresses, display_addr, html_to_plain, quote_for_reply, sanitize_html
from .reader import make_webview, system_font
from .ui_common import ask, fmt_date_long, fmt_size, open_files_dialog, prompt_text

EDITOR_CSS = """
:root { color-scheme: %(scheme)s; }
html { height: 100%%; }
body { font-family: %(font)s; font-size: 14.5px; line-height: 1.45; margin: 0; padding: 14px 18px 40px 18px;
       min-height: calc(100%% - 54px); background: %(bg)s; color: %(fg)s; outline: none; word-wrap: break-word; }
blockquote[type=cite] { border-left: 3px solid %(bar)s; margin: 6px 0 6px 2px; padding-left: 12px; color: %(quote)s; }
img { max-width: 100%%; }
a { color: %(link)s; }
.lim-sig { color: %(quote)s; }
"""

OUT_HTML = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"></head>'
            '<body style="font-family: -apple-system, \'Helvetica Neue\', Helvetica, Arial, sans-serif; '
            'font-size: 14px; line-height: 1.4; word-wrap: break-word;">%s</body></html>')


class AddressEntry(Gtk.Entry):
    """Entry with contact suggestions from the local cache."""

    def __init__(self, cache, **kw):
        super().__init__(hexpand=True, **kw)
        self.add_css_class("flat")
        self.cache = cache
        self.pop = Gtk.Popover(autohide=False, has_arrow=False, position=Gtk.PositionType.BOTTOM)
        self.pop.set_parent(self)
        self.lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.lb.add_css_class("navigation-sidebar")
        self.lb.connect("row-activated", lambda lb, row: self._pick(row))
        self.pop.set_child(self.lb)
        self.connect("changed", self._changed)
        self.connect("activate", self._activate)
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._key)
        self.add_controller(key)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda c: GLib.timeout_add(150, lambda: (self.pop.popdown(), False)[1]))
        self.add_controller(focus)
        self._suppress = False

    def _token(self):
        t = self.get_text()
        k = t.rfind(",")
        return t[k + 1:].strip()

    def _changed(self, *a):
        if self._suppress:
            return
        tok = self._token()
        while (c := self.lb.get_first_child()) is not None:
            self.lb.remove(c)
        if len(tok) < 2:
            self.pop.popdown()
            return
        res = self.cache.search_contacts(tok)
        if not res:
            self.pop.popdown()
            return
        for addr, name in res:
            row = Gtk.ListBoxRow()
            row.addr = display_addr(name, addr)
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup("%s <span alpha='60%%'>%s</span>" % (GLib.markup_escape_text(name or ""),
                                                               GLib.markup_escape_text(addr)))
            row.set_child(lbl)
            self.lb.append(row)
        self.lb.select_row(self.lb.get_row_at_index(0))
        self.pop.popup()

    def _pick(self, row):
        t = self.get_text()
        k = t.rfind(",")
        head = t[:k + 1] + " " if k >= 0 else ""
        self._suppress = True
        self.set_text((head + row.addr + ", ").lstrip())
        self._suppress = False
        self.set_position(-1)
        self.pop.popdown()

    def _activate(self, *a):
        if self.pop.get_visible():
            row = self.lb.get_selected_row()
            if row:
                self._pick(row)

    def _key(self, ctl, keyval, code, state):
        if not self.pop.get_visible():
            return False
        row = self.lb.get_selected_row()
        idx = row.get_index() if row else -1
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            nxt = self.lb.get_row_at_index(idx + (1 if keyval == Gdk.KEY_Down else -1))
            if nxt:
                self.lb.select_row(nxt)
            return True
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_Return, Gdk.KEY_KP_Enter) and row:
            self._pick(row)
            return True
        if keyval == Gdk.KEY_Escape:
            self.pop.popdown()
            return True
        return False


class Composer(Adw.ApplicationWindow):
    def __init__(self, app, mode="new", item=None, parsed=None, to=None, subject=None, body=None):
        super().__init__(application=app, default_width=860, default_height=700)
        self.app = app
        self.mode = mode
        self.item = item
        self.parsed = parsed
        self.backend = app.backend
        self.settings = app.settings
        self.attachments = []   # list of dict(name, ctype, data)
        self.draft_uid = item.row.uid if (mode == "draft" and item) else None
        self.sending = False
        self.initial_snapshot = None
        self.last_saved = None
        self.closing = False
        self.set_title(_("New Message"))

        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        self.send_btn = Gtk.Button()
        self.send_btn.set_child(Adw.ButtonContent(icon_name="mail-send-symbolic", label=_("Send")))
        self.send_btn.add_css_class("suggested-action")
        self.send_btn.set_tooltip_text(_("Send") + " (Ctrl+Enter)")
        self.send_btn.connect("clicked", lambda b: self.send())
        hb.pack_start(self.send_btn)
        attach = Gtk.Button(icon_name="mail-attachment-symbolic", tooltip_text=_("Attach Files…"))
        attach.connect("clicked", lambda b: open_files_dialog(self, self.add_files))
        hb.pack_start(attach)
        save = Gtk.Button(icon_name="document-save-symbolic", tooltip_text=_("Save Draft") + " (Ctrl+S)")
        save.connect("clicked", lambda b: self.save_draft())
        hb.pack_end(save)
        self.spinner = Gtk.Spinner()
        hb.pack_end(self.spinner)
        tv.add_top_bar(hb)
        self.toast = Adw.ToastOverlay()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        ids = self.settings.identities()
        known = {a.lower() for a in ids}
        try:
            for a in app.cache.sent_from_addresses(self.backend.role("sent")):
                if a.lower() not in known:
                    ids.append(a)
                    known.add(a.lower())
        except Exception:
            pass
        self.reply_alias = None
        self.guessed = set()     # unconfirmed candidates: listed, never auto-selected
        if mode in ("reply", "reply-all", "forward"):
            cands, best = self._alias_candidates(known)
            self.guessed = {a.lower() for a in cands if not best or a.lower() != best.lower()}
            for a in cands:
                if a.lower() not in known:
                    ids.append(a)
                    known.add(a.lower())
            self.reply_alias = best
        self.ids = ids
        self.from_dd = Gtk.DropDown.new_from_strings(
            [display_addr(self.settings["display_name"], a) for a in ids]
            or ["?"])
        self.from_dd.add_css_class("flat")
        self.to_e = AddressEntry(app.cache)
        self.cc_e = AddressEntry(app.cache)
        self.bcc_e = AddressEntry(app.cache)
        self.subj_e = Gtk.Entry(hexpand=True)
        self.subj_e.add_css_class("flat")
        self.subj_e.connect("changed", lambda e: self.set_title(e.get_text() or _("New Message")))
        self.ccbcc_btn = Gtk.ToggleButton(label="%s/%s" % (_("Cc"), _("Bcc")))
        self.ccbcc_btn.add_css_class("flat")
        self.ccbcc_btn.connect("toggled", lambda b: self._show_ccbcc(b.get_active()))

        def field(label, widget, extra=None):
            r = Gtk.Box(spacing=6)
            r.add_css_class("compose-field")
            lbl = Gtk.Label(label=label + ":", xalign=0)
            lbl.add_css_class("compose-label")
            r.append(lbl)
            r.append(widget)
            if extra:
                r.append(extra)
            box.append(r)
            box.append(Gtk.Separator())
            return r

        self.from_row = field(_("From"), self.from_dd)
        self.from_dd.set_hexpand(False)
        self.from_row.set_visible(True)   # always show which address the message is sent from
        self.from_dd.connect("notify::selected", lambda *a: self._from_changed())
        field(_("To"), self.to_e, self.ccbcc_btn)
        self.cc_row = field(_("Cc"), self.cc_e)
        self.bcc_row = field(_("Bcc"), self.bcc_e)
        self.cc_sep = self.cc_row.get_next_sibling()
        self.bcc_sep = self.bcc_row.get_next_sibling()
        field(_("Subject"), self.subj_e)
        self._show_ccbcc(False)

        # formatting toolbar
        self.fmt_bar = Gtk.Box(spacing=2)
        self.fmt_bar.add_css_class("toolbar")
        for icon, tip, cmd in (("format-text-bold-symbolic", _("Bold") + " (Ctrl+B)", "bold"),
                               ("format-text-italic-symbolic", _("Italic") + " (Ctrl+I)", "italic"),
                               ("format-text-underline-symbolic", _("Underline") + " (Ctrl+U)", "underline"),
                               ("format-text-strikethrough-symbolic", _("Strikethrough"), "strikeThrough"),
                               (None, None, None),
                               ("view-list-bullet-symbolic", _("Bulleted List"), "insertUnorderedList"),
                               ("view-list-ordered-symbolic", _("Numbered List"), "insertOrderedList"),
                               ("format-indent-more-symbolic", _("Increase Quote Level"), "quote+"),
                               ("format-indent-less-symbolic", _("Decrease Quote Level"), "outdent"),
                               (None, None, None),
                               ("insert-link-symbolic", _("Insert Link…"), "link"),
                               ("edit-clear-all-symbolic", _("Remove Formatting"), "removeFormat")):
            if icon is None:
                self.fmt_bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
                continue
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.add_css_class("flat")
            b.connect("clicked", lambda btn, c=cmd: self.exec_cmd(c))
            self.fmt_bar.append(b)
        size_menu = Gio.Menu()
        for label, sz in ((_("Small"), 2), (_("Normal"), 3), (_("Large"), 5), (_("Huge"), 6)):
            size_menu.append(label, "compose.size(%d)" % sz)
        sb = Gtk.MenuButton(icon_name="font-select-symbolic", tooltip_text=_("Text Size"), menu_model=size_menu)
        sb.add_css_class("flat")
        self.fmt_bar.append(sb)
        self.fmt_bar.set_visible(self.settings["compose_html"])
        box.append(self.fmt_bar)
        box.append(Gtk.Separator())

        ag = Gio.SimpleActionGroup()
        a = Gio.SimpleAction.new("size", GLib.VariantType.new("i"))
        a.connect("activate", lambda act, v: self.js("document.execCommand('fontSize', false, '%d')" % v.unpack()))
        ag.add_action(a)
        self.insert_action_group("compose", ag)

        self.web = make_webview(editable=True)
        self.web.set_editable(True)
        self.web.set_vexpand(True)
        self.web.connect("decide-policy", self._policy)
        box.append(self.web)

        self.att_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=5,
                                    column_spacing=6, row_spacing=6, margin_start=10, margin_end=10,
                                    margin_top=6, margin_bottom=6)
        self.att_flow.set_visible(False)
        box.append(self.att_flow)

        self.toast.set_child(box)
        tv.set_content(self.toast)
        self.set_content(tv)

        # drag & drop of files
        dt = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        dt.connect("drop", self._on_drop)
        self.add_controller(dt)

        # shortcuts
        sc = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)
        for accel, fn in (("<Control>Return", self.send), ("<Control>KP_Enter", self.send),
                          ("<Control>s", self.save_draft), ("Escape", self.close),
                          ("<Control>w", self.close)):
            sc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(accel),
                                             Gtk.CallbackAction.new(lambda w, a, f=fn: (f(), True)[1])))
        self.add_controller(sc)

        self.connect("close-request", self._on_close)
        Adw.StyleManager.get_default().connect("notify::dark", lambda *a: self._apply_theme())
        self._prefill(to, subject, body)
        GLib.timeout_add_seconds(60, self._autosave)

    # ------------------------------------------------------------ setup
    def _show_ccbcc(self, show):
        for w in (self.cc_row, self.bcc_row, self.cc_sep, self.bcc_sep):
            if w is not None:
                w.set_visible(show)

    def _editor_css(self):
        dark = Adw.StyleManager.get_default().get_dark()
        return EDITOR_CSS % dict(
            scheme="dark" if dark else "light", font=system_font(),
            bg="#1e1e1e" if dark else "#ffffff", fg="#e8e8e8" if dark else "#1b1b1b",
            bar="#555" if dark else "#c8c8c8", quote="#a0a0a0" if dark else "#6a6a6a",
            link="#78aeff" if dark else "#0a63e6")

    def _apply_theme(self):
        css = json.dumps(self._editor_css())
        self.js("document.getElementById('lim-style').textContent = %s" % css)

    def _from_changed(self):
        idx = self.from_dd.get_selected()
        if idx and idx < len(self.ids):
            self.from_dd.add_css_class("accent")
        else:
            self.from_dd.remove_css_class("accent")

    def _sig_html(self):
        sig = (self.settings["signature"] or "").strip()
        if not sig:
            return ""
        return '<div><br></div><div class="lim-sig">%s</div>' % html.escape(sig).replace("\n", "<br>")

    ICLOUD_DOMAINS = ("icloud.com", "me.com", "mac.com")

    def _alias_candidates(self, known):
        """Find the alias this message was sent to when it is not configured yet.

        Returns (candidates_for_the_list, address_to_select_or_None):
          1. delivery headers (Delivered-To, X-Original-To, Received ... for <x>) - reliable;
          2. otherwise To/Cc: if none of my known addresses is there, iCloud-domain
             (or my-domain) recipients are candidates; exactly one -> select it.
        """
        p = self.parsed
        if p is None:
            return [], None
        my_domains = set(self.ICLOUD_DOMAINS) | {a.split("@")[-1].lower() for a in known}
        sender = {a.lower() for n, a in addresses(p.h("from"))}

        def mine_domain(a):
            return a.split("@")[-1].lower() in my_domains

        primary = (self.settings["email"] or "").lower()
        for name, addr in addresses(p.h("delivered-to")):
            a = addr.lower()
            if a == primary:
                continue             # iCloud may report the primary address for alias mail
            if a in known:
                return [], addr
            if mine_domain(a):
                return [addr], addr
        rcpt = [a for n, a in addresses(p.h("to")) + addresses(p.h("cc"))]
        if any(a.lower() in known for a in rcpt):
            return [], None          # a known address of mine is there; _pick_identity selects it
        cands = []
        for a in rcpt:
            if mine_domain(a) and a.lower() not in sender and a.lower() not in [c.lower() for c in cands]:
                cands.append(a)
        return cands, (cands[0] if len(cands) == 1 else None)

    def _pick_identity(self):
        """Reply from the address the message was sent to (like iCloud.com does)."""
        if not self.parsed:
            return 0
        index = {a.lower(): i for i, a in enumerate(self.ids)}
        if self.reply_alias and self.reply_alias.lower() in index:
            return index[self.reply_alias.lower()]
        for key in ("to", "cc", "bcc", "delivered-to"):
            for name, addr in addresses(self.parsed.h(key)):
                i = index.get(addr.lower())
                if i is not None and addr.lower() not in self.guessed:
                    return i
        return 0

    def _prefill(self, to, subject, body):
        p = self.parsed
        mine = {a.lower() for a in self.ids}
        content = ""
        sig = self._sig_html()
        if self.mode in ("reply", "reply-all", "forward") and p is not None:
            self.from_dd.set_selected(self._pick_identity())
            subj = p.h("subject") or ""
            orig_from = p.h("from")
            date = fmt_date_long(self.item.row.idate) if self.item else p.h("date")
            if self.mode == "forward":
                if not re.match(r"(?i)^(fwd?|pd|przek)\s*:", subj):
                    subj = "Fwd: " + subj
                hdr = ("<div>---------- %s ----------</div><div><b>%s:</b> %s</div><div><b>%s:</b> %s</div>"
                       "<div><b>%s:</b> %s</div><div><b>%s:</b> %s</div><br>") % (
                    _("Forwarded message"), _("From"), html.escape(orig_from), _("Date"), html.escape(date),
                    _("Subject"), html.escape(p.h("subject")), _("To"), html.escape(p.h("to")))
                content = "<div><br></div>%s<div><br></div>%s<div>%s</div>" % (sig, hdr, quote_for_reply(p))
                for att in p.attachments:
                    self._add_forward_attachment(att)
            else:
                if not re.match(r"(?i)^(re|odp)\s*:", subj):
                    subj = "Re: " + subj
                reply_to = p.h("reply-to") or orig_from
                to_list = [(n, a) for n, a in addresses(reply_to)]
                cc_list = []
                if self.mode == "reply-all":
                    for n, a in addresses(p.h("to")) + addresses(p.h("cc")):
                        if a.lower() in mine or any(a.lower() == x[1].lower() for x in to_list + cc_list):
                            continue
                        cc_list.append((n, a))
                    if all(a.lower() in mine for n, a in to_list) and addresses(p.h("to")):
                        # replying to my own sent message: reply to original recipients
                        to_list = [x for x in addresses(p.h("to")) if x[1].lower() not in mine]
                self.to_e.set_text(", ".join(display_addr(x) for x in to_list))
                if cc_list:
                    self.cc_e.set_text(", ".join(display_addr(x) for x in cc_list))
                    self.ccbcc_btn.set_active(True)
                who = addresses(orig_from)
                who_s = (who[0][0] or who[0][1]) if who else orig_from
                content = ('<div><br></div>%s<div><br></div><div>%s</div><blockquote type="cite">%s</blockquote>'
                           % (sig, html.escape(_("On %s, %s wrote:") % (date, who_s)), quote_for_reply(p)))
            self.subj_e.set_text(subj)
        elif self.mode == "draft" and p is not None:
            self.to_e.set_text(p.h("to"))
            if p.h("cc") or p.h("bcc"):
                self.cc_e.set_text(p.h("cc"))
                self.bcc_e.set_text(p.h("bcc"))
                self.ccbcc_btn.set_active(True)
            self.subj_e.set_text(p.h("subject"))
            frm = addresses(p.h("from"))
            if frm:
                for i, a in enumerate(self.ids):
                    if a.lower() == frm[0][1].lower():
                        self.from_dd.set_selected(i)
            if p.html:
                b = sanitize_html(p.html)
                m = re.search(r"<body[^>]*>(.*)</body>", b, re.S | re.I)
                content = m.group(1) if m else b
            else:
                content = html.escape(p.text or "").replace("\n", "<br>")
            for att in p.attachments:
                self._add_forward_attachment(att)
        else:
            if to:
                self.to_e.set_text(to)
            if subject:
                self.subj_e.set_text(subject)
            content = "<div>%s</div>%s" % (html.escape(body or "").replace("\n", "<br>") or "<br>", sig)
        # remote images (tracking pixels) in quoted mail stay blocked, as in the reader
        img_src = "data: blob: *" if self.settings["remote_images"] else "data: blob:"
        doc = ('<!DOCTYPE html><html><head><meta charset="utf-8">'
               '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src %s; '
               'style-src \'unsafe-inline\'">'
               '<style id="lim-style">%s</style></head><body>%s</body></html>') % (img_src, self._editor_css(), content)
        self.web.load_html(doc, None)
        self.web.connect("load-changed", self._on_loaded)

    def _on_loaded(self, wv, ev):
        from gi.repository import WebKit
        if ev != WebKit.LoadEvent.FINISHED:
            return
        if self.mode in ("reply", "reply-all", "forward"):
            self.web.grab_focus()
            self.js("var r=document.createRange();r.setStart(document.body,0);r.collapse(true);"
                    "var s=getSelection();s.removeAllRanges();s.addRange(r);")
        elif not self.to_e.get_text():
            self.to_e.grab_focus()
        else:
            self.web.grab_focus()
        self.get_content(lambda h, t: setattr(self, "initial_snapshot", self._snapshot(h)))

    def _policy(self, wv, decision, dtype):
        from gi.repository import WebKit
        if dtype == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri.startswith(("about:", "data:")) and \
                    decision.get_navigation_action().get_navigation_type() != WebKit.NavigationType.LINK_CLICKED:
                decision.use()
            else:
                decision.ignore()
            return True
        if dtype == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION:
            decision.ignore()
            return True
        return False

    # ------------------------------------------------------------ editor helpers
    def js(self, script, cb=None):
        def done(wv, res):
            try:
                v = wv.evaluate_javascript_finish(res)
                if cb:
                    cb(v.to_string() if v is not None else "")
            except GLib.Error:
                if cb:
                    cb("")
        self.web.evaluate_javascript(script, -1, None, None, None, done)

    def exec_cmd(self, cmd):
        if cmd == "link":
            prompt_text(self, _("Insert Link…"), "https://", _("Insert"),
                        lambda url: self.js("document.execCommand('createLink', false, %s)" % json.dumps(url)),
                        placeholder=_("Link address"))
        elif cmd == "quote+":
            self.js("document.execCommand('formatBlock', false, 'blockquote');"
                    "var q=getSelection().anchorNode; while(q && q.nodeName!='BLOCKQUOTE') q=q.parentNode;"
                    "if(q) q.setAttribute('type','cite');")
        else:
            self.js("document.execCommand(%s, false, null)" % json.dumps(cmd))
        self.web.grab_focus()

    def get_content(self, cb):
        def got(s):
            try:
                d = json.loads(s)
            except ValueError:
                d = {"h": "", "t": ""}
            cb(d.get("h", ""), d.get("t", ""))
        self.js("JSON.stringify({h: document.body.innerHTML, t: document.body.innerText})", got)

    def _snapshot(self, h):
        return (self.to_e.get_text(), self.cc_e.get_text(), self.bcc_e.get_text(), self.subj_e.get_text(),
                h, len(self.attachments))

    # ------------------------------------------------------------ attachments
    def add_files(self, paths):
        for p in paths:
            try:
                with open(p, "rb") as f:
                    data = f.read()
            except OSError as e:
                self.toast.add_toast(Adw.Toast(title=str(e)))
                continue
            ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
            self._add_att({"name": os.path.basename(p), "ctype": ctype, "data": data})

    def _add_forward_attachment(self, att):
        entry = {"name": att.name, "ctype": att.ctype, "data": att.data}
        self._add_att(entry)
        if att.data is None and self.item is not None:
            src = self.item.row

            def got(data):
                entry["data"] = data
                self._refresh_atts()
            self.backend.fetch_attachment(src.folder, src.uid, att, got,
                                          error=lambda e: self.toast.add_toast(Adw.Toast(title=str(e))))

    def _add_att(self, entry):
        self.attachments.append(entry)
        self._refresh_atts()

    def _refresh_atts(self):
        while (c := self.att_flow.get_first_child()) is not None:
            self.att_flow.remove(c)
        for entry in self.attachments:
            chip = Gtk.Box(spacing=6)
            chip.add_css_class("card")
            chip.add_css_class("attachment-chip")
            gicon = Gio.content_type_get_symbolic_icon(Gio.content_type_from_mime_type(entry["ctype"]) or "")
            chip.append(Gtk.Image.new_from_gicon(gicon))
            chip.append(Gtk.Label(label=entry["name"], ellipsize=2, max_width_chars=26))
            size = Gtk.Label(label=fmt_size(len(entry["data"])) if entry["data"] is not None else "…")
            size.add_css_class("dim-label")
            size.add_css_class("caption")
            chip.append(size)
            rm = Gtk.Button(icon_name="window-close-symbolic")
            rm.add_css_class("flat")
            rm.add_css_class("circular")
            rm.connect("clicked", lambda b, e=entry: (self.attachments.remove(e), self._refresh_atts()))
            chip.append(rm)
            self.att_flow.append(chip)
        self.att_flow.set_visible(bool(self.attachments))

    def _on_drop(self, target, value, x, y):
        files = value.get_files() if hasattr(value, "get_files") else []
        paths = [f.get_path() for f in files if f.get_path()]
        if paths:
            self.add_files(paths)
            return True
        return False

    # ------------------------------------------------------------ building the message
    def _parse_recipients(self, text):
        out = []
        for name, addr in getaddresses([text or ""]):
            addr = addr.strip()
            if not addr:
                continue
            if "@" not in addr or " " in addr:
                raise ValueError(_("Invalid address: %s") % addr)
            out.append(display_addr(name, addr))
        return out

    def build_message(self, inner_html, text, draft=False):
        msg = EmailMessage()
        idx = self.from_dd.get_selected()
        addr = self.ids[idx] if self.ids and idx < len(self.ids) else self.settings["email"]
        name = self.settings["display_name"]
        msg["From"] = display_addr(name, addr)
        to = self._parse_recipients(self.to_e.get_text())
        cc = self._parse_recipients(self.cc_e.get_text())
        bcc = self._parse_recipients(self.bcc_e.get_text())
        if to:
            msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        if bcc:
            msg["Bcc"] = ", ".join(bcc)
        msg["Subject"] = self.subj_e.get_text()
        msg["Date"] = format_datetime(datetime.datetime.now().astimezone())
        msg["Message-ID"] = make_msgid(domain=addr.split("@")[-1] if "@" in addr else None)
        msg["X-Mailer"] = "Linux iCloud Mail"
        msg["MIME-Version"] = "1.0"
        if self.mode in ("reply", "reply-all") and self.parsed is not None:
            mid = self.parsed.h("message-id")
            if mid:
                msg["In-Reply-To"] = mid
                refs = (self.parsed.h("references") + " " + mid).strip()
                msg["References"] = refs
        elif self.mode == "draft" and self.parsed is not None:
            for h in ("in-reply-to", "references"):
                if self.parsed.h(h):
                    msg[h.title()] = self.parsed.h(h)
        if self.settings["compose_html"] or "<blockquote" in inner_html:
            text = html_to_plain(inner_html)
        text = (text or "").replace("\xa0", " ")
        if not text.endswith("\n"):
            text += "\n"
        msg.set_content(text)
        if self.settings["compose_html"]:
            images = []

            def rep(m):
                ctype, b64 = m.group(1), m.group(2)
                cid = make_msgid(domain="lim.local")
                try:
                    images.append((cid, ctype, base64.b64decode(b64)))
                except Exception:
                    return m.group(0)
                return 'src="cid:%s"' % cid.strip("<>")
            body = re.sub(r'src="data:([\w/+.-]+);base64,([A-Za-z0-9+/=\s]+)"', rep, inner_html)
            msg.add_alternative(OUT_HTML % body, subtype="html")
            if images:
                hp = msg.get_payload()[1]
                for cid, ctype, data in images:
                    mt, st = (ctype.split("/", 1) + ["octet-stream"])[:2]
                    hp.add_related(data, mt, st, cid=cid)
        for a in self.attachments:
            if a["data"] is None:
                continue
            mt, st = (a["ctype"].split("/", 1) + ["octet-stream"])[:2]
            if mt == "text":
                msg.add_attachment(a["data"], maintype="application", subtype="octet-stream", filename=a["name"])
            elif mt == "message":
                msg.add_attachment(a["data"], maintype="application", subtype="octet-stream", filename=a["name"])
            else:
                msg.add_attachment(a["data"], maintype=mt, subtype=st, filename=a["name"])
        return msg

    # ------------------------------------------------------------ send / draft
    def _busy(self, on):
        self.spinner.set_spinning(on)
        self.send_btn.set_sensitive(not on)

    def send(self):
        if self.sending:
            return
        try:
            rcpt = (self._parse_recipients(self.to_e.get_text()) + self._parse_recipients(self.cc_e.get_text())
                    + self._parse_recipients(self.bcc_e.get_text()))
        except ValueError as e:
            ask(self, _("Error"), str(e), [("cancel", _("Close"), None)], lambda r: None)
            return
        if not rcpt:
            ask(self, _("Please add at least one recipient."), "", [("cancel", _("Close"), None)], lambda r: None)
            return
        if any(a["data"] is None for a in self.attachments):
            self.toast.add_toast(Adw.Toast(title=_("Loading…")))
            return
        if not self.subj_e.get_text().strip():
            ask(self, _("Send without a subject?"), "",
                [("cancel", _("Cancel"), None), ("send", _("Send Anyway"), "suggested")],
                lambda r: r == "send" and self._do_send(), default="send")
            return
        self._do_send()

    def _do_send(self):
        self.sending = True
        self._busy(True)

        def got(h, t):
            try:
                msg = self.build_message(h, t)
            except ValueError as e:
                self.sending = False
                self._busy(False)
                ask(self, _("Error"), str(e), [("cancel", _("Close"), None)], lambda r: None)
                return
            self.backend.send(msg, done=self._sent, error=self._send_failed)
        self.get_content(got)

    def _sent(self, msg):
        self.app.cache.add_contacts([msg.get("To", ""), msg.get("Cc", ""), msg.get("Bcc", "")])
        used = self.ids[self.from_dd.get_selected()] if self.from_dd.get_selected() < len(self.ids) else None
        if used and used.lower() not in [a.lower() for a in self.settings.identities()]:
            self.settings["aliases"] = list(self.settings["aliases"] or []) + [used]
        if self.item is not None and self.mode in ("reply", "reply-all", "forward"):
            flag = "\\Answered" if self.mode != "forward" else "$Forwarded"
            self.app.win.flag_items([self.item], flag, True)
        if self.draft_uid:
            drafts = self.backend.role("drafts")
            if drafts:
                self.backend.delete_forever(drafts, [self.draft_uid],
                                            done=lambda r: self.app.win.after_draft_change(drafts, self.draft_uid))
        self.app.win.toast(_("Message sent"))
        self.closing = True
        self.destroy()

    def _send_failed(self, e):
        self.sending = False
        self._busy(False)
        ask(self, _("Sending failed"), str(e), [("cancel", _("Close"), None)], lambda r: None)

    def save_draft(self, then=None, quiet=False):
        drafts = self.backend.role("drafts")
        if not drafts:
            if then:
                then()
            return

        def got(h, t):
            try:
                msg = self.build_message(h, t, draft=True)
            except ValueError:
                # keep invalid addresses as plain text in a draft
                saved = (self.to_e.get_text(), self.cc_e.get_text(), self.bcc_e.get_text())
                self.to_e.set_text("")
                self.cc_e.set_text("")
                self.bcc_e.set_text("")
                msg = self.build_message(h, t, draft=True)
                self.to_e.set_text(saved[0])
                self.cc_e.set_text(saved[1])
                self.bcc_e.set_text(saved[2])
            snap = self._snapshot(h)
            old = self.draft_uid

            def appended(uid):
                self.draft_uid = uid
                self.last_saved = snap
                if old:
                    self.backend.delete_forever(drafts, [old])
                self.backend.sync_folder(drafts, prio=4)
                if not quiet:
                    self.toast.add_toast(Adw.Toast(title=_("Draft saved"), timeout=2))
                if then:
                    then()
            self.backend.append(drafts, msg.as_bytes(), "(\\Draft \\Seen)", done=appended,
                                error=lambda e: self.toast.add_toast(Adw.Toast(title=str(e))))
        self.get_content(got)

    def _autosave(self):
        if self.closing or self.sending:
            return False

        def got(h, t):
            snap = self._snapshot(h)
            if snap != self.initial_snapshot and snap != self.last_saved:
                self.save_draft(quiet=True)
        self.get_content(got)
        return True

    def _on_close(self, *a):
        if self.closing or self.sending:
            return False

        def got(h, t):
            snap = self._snapshot(h)
            if snap == self.initial_snapshot or snap == self.last_saved:
                self.closing = True
                self.destroy()
                return

            def resp(r):
                if r == "save":
                    self.save_draft(then=lambda: (setattr(self, "closing", True), self.destroy()))
                elif r == "discard":
                    if self.draft_uid and self.mode != "draft":
                        drafts = self.backend.role("drafts")
                        self.backend.delete_forever(drafts, [self.draft_uid])
                    self.closing = True
                    self.destroy()
            ask(self, _("Save this message as a draft?"), _("You can finish it later from the Drafts folder."),
                [("cancel", _("Cancel"), None), ("discard", _("Discard"), "destructive"),
                 ("save", _("Save"), "suggested")], resp, default="save")
        self.get_content(got)
        return True
