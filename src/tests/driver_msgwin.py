import subprocess
from gi.repository import GLib
def log(*a): print("DRIVER:", *a, flush=True)
def mw(app): return [w for w in app.get_windows() if type(w).__name__ == "MessageWindow"]
def run(app):
    W = lambda: app.win
    import linux_icloud_mail.composer as cm
    oi = cm.Composer.__init__
    def init(self, *a, **k): oi(self, *a, **k); app._last = self
    cm.Composer.__init__ = init
    def a():
        W().sel.select_item(0, True)
        W().listview.emit("activate", 0)          # = double-click
        GLib.timeout_add(2500, b)
    def b():
        w = mw(app)[0]
        log("message windows:", len(mw(app)), "| title:", w.get_title(), "| reader:", w.reader.stack.get_visible_child_name(),
            "| atts:", [x.name for x in w.reader.parsed.attachments])
        app._mw = w
        w.present()
        GLib.timeout_add(1500, lambda: (b2(w), False)[1])
    def b2(w):
        subprocess.run(["import", "-window", "root", "/tmp/shots/17-msgwin.png"])
        flagged = w.item.row.flagged
        w.activate_action("win.toggle-flag", None)
        log("flag toggled:", flagged, "->", w.item.row.flagged, "| main list item same:", W().store.get_item(0).row.flagged)
        w.activate_action("win.reply-all", None)
        GLib.timeout_add(2500, c)
    def c():
        cmp = app._last
        log("reply from window -> to:", cmp.to_e.get_text(), "| subj:", cmp.subj_e.get_text())
        cmp.closing = True; cmp.destroy()
        before = W().store.get_n_items()
        app._mw.activate_action("win.delete", None)
        GLib.timeout_add(2000, lambda: d(before))
    def d(before):
        log("after delete: message windows:", len(mw(app)), "| inbox", before, "->", W().store.get_n_items())
        # drafts double-click still opens composer
        w = W(); w.folder_list.select_row(w.folder_rows[w.backend.role("drafts")])
        app.quit_app()
    GLib.timeout_add(5000, lambda: (a(), False)[1])
