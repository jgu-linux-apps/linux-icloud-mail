import subprocess
from gi.repository import GLib
def run(app):
    def a():
        app.show_about(); GLib.timeout_add(1500, b)
    def b():
        subprocess.run(["import", "-window", "root", "/tmp/shots/18-about.png"]); app.quit_app()
    GLib.timeout_add(4000, lambda: (a(), False)[1])
