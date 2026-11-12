from gi.repository import GLib
def log(*a): print("DRIVER:", *a, flush=True)
def run(app):
    import linux_icloud_mail.composer as cm
    oi = cm.Composer.__init__
    def init(self, *a, **k): oi(self, *a, **k); app._last = self
    cm.Composer.__init__ = init
    subjects = ["Tylko To alias", "Grupowy", "Received for", "Received primary, To alias", "Glowny + inny icloud"]
    def step(i):
        if i >= len(subjects):
            app.quit_app(); return False
        w = app.win
        for k in range(w.store.get_n_items()):
            if w.store.get_item(k).row.subject == subjects[i]:
                w.sel.select_item(w._position_of(w.store.get_item(k)), True)
        def reply():
            w.activate_action("win.reply", None)
            def chk():
                c = app._last
                log("%-28s FROM=%-24s list=%s" % (subjects[i], c.ids[c.from_dd.get_selected()], c.ids))
                c.closing = True; c.destroy(); step(i + 1)
            GLib.timeout_add(1500, chk)
        GLib.timeout_add(1500, reply)
        return False
    GLib.timeout_add(4000, lambda: step(0))
