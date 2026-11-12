import subprocess, threading
from gi.repository import GLib
def log(*a): print("DRIVER:", *a, flush=True)
def run(app):
    W = lambda: app.win
    def a():
        w = W()
        target = w.store.get_item(7).row
        app._target = target
        w.folder_list.select_row(w.folder_rows["Archive"])   # be in another folder
        w.set_visible(False)                                 # app hidden in tray
        log("target", target.folder, target.uid, target.subject, "| window visible:", w.get_visible())
        def call():
            # exactly what GNOME Shell does when a notification is clicked
            r = subprocess.run(["gdbus", "call", "--session", "-d", "io.github.jaro.LinuxIcloudMail",
                                "-o", "/io/github/jaro/LinuxIcloudMail", "-m", "org.freedesktop.Application.ActivateAction",
                                "open-message", "[<('%s', uint32 %d)>]" % (target.folder, target.uid), "{}"],
                               capture_output=True, text=True)
            log("ActivateAction ->", r.returncode, r.stdout.strip(), r.stderr.strip()[:200])
        threading.Thread(target=call).start()
        GLib.timeout_add(2500, b)
    def b():
        w = W(); sel = w.selected_items()
        log("window visible:", w.get_visible(), "| folder:", w.current, "| selected:", [i.row.subject for i in sel],
            "| reader:", w.reader.item.row.subject if w.reader.item else None)
        subprocess.run(["import", "-window", "root", "/tmp/shots/16-notif-open.png"])
        app.quit_app()
    GLib.timeout_add(5000, lambda: (a(), False)[1])
