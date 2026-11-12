import os
from gi.repository import GLib

def log(*a): print("DRIVER:", *a, flush=True)

def run(app):
    W = lambda: app.win
    seq = []
    def s(d):
        def deco(f): seq.append((d, f)); return f
        return deco
    def find(subj):
        st = W().store
        for i in range(st.get_n_items()):
            if st.get_item(i).row.subject == subj:
                return i
    def open_and_reply(subj):
        W().sel.select_item(W()._position_of(W().store.get_item(find(subj))), True)
        GLib.timeout_add(1500, lambda: (W().activate_action("win.reply", None), False)[1])
    import linux_icloud_mail.composer as cm
    orig_init = cm.Composer.__init__
    def init(self, *a, **k):
        orig_init(self, *a, **k); app._last = self
    cm.Composer.__init__ = init
    def check(tag):
        c = app._last
        log(tag, "FROM =", c.ids[c.from_dd.get_selected()], "| visible:", c.from_row.get_visible(), "| ids:", c.ids)
        return c
    for subj, tag in (("Do aliasu w To", "A"), ("Ukryta kopia na alias", "B"), ("Na alias z Wyslanych", "C"), ("Na glowny adres", "D")):
        @s(3)
        def o(subj=subj): open_and_reply(subj)
        @s(3)
        def c(tag=tag):
            comp = check(tag)
            if tag == "A":
                app._A = comp
                comp.send()
                GLib.timeout_add(1500, lambda: (log("A sending", comp.sending, "to", comp.to_e.get_text(), "dialogs", [(d.get_heading(), d.get_body()) for d in [comp.get_visible_dialog()] if d] if hasattr(comp, "get_visible_dialog") else "?"), False)[1])
            else:
                comp.closing = True; comp.destroy()
    @s(4)
    def end():
        log("aliases saved:", app.settings["aliases"])
        f = sorted(os.listdir("/tmp/smtpbox/mbox/new"))[-1]
        log("sent From:", [l for l in open("/tmp/smtpbox/mbox/new/" + f) if l.startswith("From:")])
        app.quit_app()
    def go(i=0):
        if i < len(seq):
            d, f = seq[i]
            GLib.timeout_add(int(d * 1000), lambda: (f(), go(i + 1), False)[2])
    GLib.timeout_add(4000, lambda: (go(), False)[1])
