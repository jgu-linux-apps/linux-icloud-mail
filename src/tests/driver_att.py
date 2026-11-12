import os, subprocess
from gi.repository import GLib
def log(*a): print("DRIVER:", *a, flush=True)
def run(app):
    W = lambda: app.win
    dl = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.expanduser("~/Downloads")
    def sel(subj):
        st = W().store
        for k in range(st.get_n_items()):
            if st.get_item(k).row.subject == subj:
                W().sel.select_item(W()._position_of(st.get_item(k)), True)
    def a():
        sel("Trzy duze zalaczniki"); GLib.timeout_add(2500, b)
    def b():
        r = W().reader
        log("big atts", [(x.name, x.size) for x in r.parsed.attachments])
        r.save_all_btn.emit("clicked"); GLib.timeout_add(3000, c)
    def c():
        subprocess.run(["import", "-window", "root", "/tmp/shots/15-downloaded.png"])
        sel("Trzy zalaczniki"); GLib.timeout_add(2000, d)
    def d():
        W().reader.save_all_btn.emit("clicked"); GLib.timeout_add(2000, e)
    def e():
        log("Downloads:", dl, sorted((f, os.path.getsize(os.path.join(dl, f))) for f in os.listdir(dl)))
        app.quit_app()
    GLib.timeout_add(4000, lambda: (a(), False)[1])
