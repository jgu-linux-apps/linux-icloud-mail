import subprocess
from gi.repository import GLib
def log(*a): print("DRIVER:", *a, flush=True)
def shot(n): subprocess.run(["import", "-window", "root", "/tmp/shots/%s.png" % n])
def run(app):
    W = lambda: app.win
    def heads():
        w = W(); out = []
        for i in range(w.filtered.get_n_items()):
            it = w.filtered.get_item(i); g = w._ginfo.get(id(it))
            out.append((it.row.from_name, g[0] if g else None, "head" if g and g[2] else "child"))
        return out
    def a():
        log("normal rows:", W().filtered.get_n_items())
        W().activate_action("win.group", None)
        GLib.timeout_add(800, b)
    def b():
        log("grouped rows:", W().filtered.get_n_items(), heads())
        shot("19-grouped")
        w = W(); w.toggle_group(w.filtered.get_item(1))
        GLib.timeout_add(800, c)
    def c():
        w = W()
        log("after expand #2:", w.filtered.get_n_items(), "rows")
        shot("20-expanded")
        # delete the head of group 1 -> next message from that sender becomes the head
        first = w.filtered.get_item(0); name = first.row.from_name
        w.sel.select_item(0, True); w.delete_selected()
        GLib.timeout_add(1000, lambda: d(name))
    def d(name):
        w = W(); it = w.filtered.get_item(0); g = w._ginfo.get(id(it))
        log("after delete head:", "first row", it.row.from_name, "count", g[0] if g else None, "(was %s)" % name)
        w.sel.select_item(0, True); w.activate_action("win.select-group", None)
        GLib.timeout_add(800, e)
    def e():
        w = W()
        log("select all from sender ->", len(w.selected_items()), "selected, settings:", w.settings["group_by_sender"])
        W().activate_action("win.group", None)
        GLib.timeout_add(800, f)
    def f():
        log("group off -> rows:", W().filtered.get_n_items())
        W().activate_action("win.group", None)   # leave it on (persisted)
        app.quit_app()
    GLib.timeout_add(5000, lambda: (a(), False)[1])
