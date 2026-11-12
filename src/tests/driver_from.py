import os, subprocess
from gi.repository import GLib
def log(*a): print("DRIVER:", *a, flush=True)
def run(app):
    import linux_icloud_mail.composer as cm
    oi = cm.Composer.__init__
    def init(self, *a, **k): oi(self, *a, **k); app._last = self
    cm.Composer.__init__ = init
    def a():
        w = app.win
        for i in range(w.store.get_n_items()):
            if "Test emoji" in (w.store.get_item(i).row.subject or ""):
                w.sel.select_item(w._position_of(w.store.get_item(i)), True)
        GLib.timeout_add(1500, lambda: (w.activate_action("win.reply", None), False)[1])
        GLib.timeout_add(4000, b)
    def b():
        c = app._last
        log("From visible", c.from_row.get_visible(), "To:", c.to_e.get_text())
        subprocess.run(["import", "-window", "root", "/tmp/shots/12-from.png"])
        c.closing = True; c.destroy(); app.quit_app()
    GLib.timeout_add(4000, lambda: (a(), False)[1])
