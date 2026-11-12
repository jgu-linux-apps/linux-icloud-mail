"""Login flow test: wrong password, then correct one, then close-to-tray."""
import os
import subprocess

from gi.repository import Adw, GLib

SHOTS = os.environ.get("LIM_SHOTS", "/tmp/shots")


def log(*a):
    print("DRIVER:", *a, flush=True)


def shot(n):
    subprocess.run(["import", "-window", "root", os.path.join(SHOTS, n + ".png")])


def run(app):
    seq = []

    def s(d):
        def deco(f):
            seq.append((d, f))
            return f
        return deco

    @s(2)
    def a():
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        log("login window", app.login is not None, "icon", app.login.icon.get_icon_name())
        app.login.email.set_text("jaro@icloud.com")
        app.login.pw.set_text("wrong-pass")
        app.login._submit()

    @s(2)
    def b():
        log("error shown", app.login.error.get_visible(), app.login.error.get_text()[:80])
        shot("11-login-error-dark")
        app.login.pw.set_text("secret")
        app.login._submit()

    @s(9)
    def c():
        log("main window", app.win is not None, "items", app.win.store.get_n_items() if app.win else None)
        try:
            log("autostart file", open(os.path.expanduser("~/.config/autostart/io.github.jaro.LinuxIcloudMail.desktop")).read().splitlines()[3])
        except OSError as e:
            log("autostart", e)
        log("tray available", app.tray_available())
        app.win.close()

    @s(1)
    def d():
        log("after close: visible", app.win.get_visible(), "app alive")
        app.toggle_window()

    @s(1)
    def e():
        log("after tray activate: visible", app.win.get_visible())
        app.quit_app()

    def go(i=0):
        if i < len(seq):
            d, f = seq[i]
            GLib.timeout_add(int(d * 1000), lambda: (f(), go(i + 1), False)[2])
    go()
