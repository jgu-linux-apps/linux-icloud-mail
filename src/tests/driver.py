"""In-process UI test driver (only loaded when LIM_TEST_DRIVER points here)."""
import imaplib
import os
import subprocess
import threading
import time
import traceback

from gi.repository import Adw, GLib

SHOTS = os.environ.get("LIM_SHOTS", "/tmp/shots")
os.makedirs(SHOTS, exist_ok=True)
LOG = []


def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print("DRIVER:", s, flush=True)


def shot(name):
    subprocess.run(["import", "-window", "root", os.path.join(SHOTS, name + ".png")])
    log("shot", name)


def run(app):
    steps = []

    def step(delay):
        def deco(fn):
            steps.append((delay, fn))
            return fn
        return deco

    W = lambda: app.win  # noqa: E731

    @step(5)
    def s1():
        log("window visible", W().get_visible(), "items", W().store.get_n_items(), "folders", list(W().folder_rows))
        shot("01-main-light")

    @step(1)
    def s2():
        W().sel.select_item(0, True)

    @step(2)
    def s3():
        r = W().reader
        log("reader item", r.item.row.subject if r.item else None, "banner", r.banner.get_revealed(),
            "atts", [a.name for a in r.parsed.attachments] if r.parsed else None)
        shot("02-reader-html")
        from gi.repository import Gtk
        def walk(w, d=0):
            m = w.measure(Gtk.Orientation.HORIZONTAL, -1)
            if m[0] > 300 and d < 9:
                log("  " * d, type(w).__name__, m[0], m[1], w.get_allocated_width())
                c = w.get_first_child()
                while c:
                    walk(c, d + 1)
                    c = c.get_next_sibling()
        walk(r)

    @step(1)
    def s4():
        W().sel.select_item(1, True)

    @step(2)
    def s5():
        shot("03-reader-plain")
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    @step(2)
    def s6():
        W().sel.select_item(0, True)

    @step(2)
    def s7():
        shot("04-dark-html")
        log("tray icon", app.tray.icon_name, "unread", app.unread)
        try:
            log("user desktop override", open(os.path.expanduser("~/.local/share/applications/io.github.jaro.LinuxIcloudMail.desktop")).read()[:200])
        except OSError as e:
            log("no override (expected in dev run)", e)
        W().activate_action("win.reply-all", None)

    @step(3)
    def s8():
        comp = [w for w in app.get_windows() if w.__class__.__name__ == "Composer"][0]
        log("composer to", comp.to_e.get_text(), "cc", comp.cc_e.get_text(), "subj", comp.subj_e.get_text())
        comp.js("document.body.insertAdjacentHTML('afterbegin', '<div>Dzięki! <b>Wysyłam</b> odpowiedź testową.</div>')")
        GLib.timeout_add(500, lambda: comp.get_content(lambda h, t: log("composer content", t[:80].replace("\n", " | "))))
        shot("05-composer-dark")
        app._comp = comp

    @step(1)
    def s9():
        app._comp.send()

    @step(4)
    def s10():
        log("composer windows", [w.__class__.__name__ for w in app.get_windows()])
        try:
            log("smtp mbox size", os.path.getsize("/tmp/smtpbox/mbox"))
        except OSError as e:
            log("smtp mbox", e)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        w = W()
        w.folder_list.select_row(w.folder_rows["__flagged__"])

    @step(2)
    def s11():
        log("flagged items", W().store.get_n_items())
        shot("06-flagged")
        w = W()
        w.folder_list.select_row(w.folder_rows["INBOX"])

    @step(2)
    def s12():
        def push():
            c = imaplib.IMAP4("127.0.0.1", 1143)
            c.login("jaro@icloud.com", "secret")
            msg = (b"From: Szef <szef@firma.pl>\r\nTo: jaro@icloud.com\r\nSubject: Pilne: nowa wiadomosc push\r\n"
                   b"Date: " + time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime()).encode() +
                   b"\r\nMessage-ID: <push1@firma.pl>\r\n\r\nTo jest test powiadomien push przez IDLE.\r\n")
            c.append("INBOX", "()", imaplib.Time2Internaldate(time.time()), msg)
            c.logout()
        threading.Thread(target=push).start()
        app.win.set_visible(False)  # notifications only when the window is not focused

    @step(5)
    def s13():
        w = W()
        first = w.store.get_item(0).row
        log("after push first row", first.subject, "unread", app.unread, "tray", app.tray.icon_name)
        app.show_window()

    @step(2)
    def s14():
        w = W()
        # move the 3rd message to Archive, delete the 4th
        w.sel.select_item(2, True)
        before = w.store.get_n_items()
        w.archive_selected()
        w.sel.select_item(2, True)
        w.delete_selected()
        log("after archive+delete", before, "->", w.store.get_n_items())

    @step(3)
    def s15():
        c = imaplib.IMAP4("127.0.0.1", 1143)
        c.login("jaro@icloud.com", "secret")
        for f in ("INBOX", "Archive", '"Deleted Messages"', '"Sent Messages"', "Drafts"):
            log("server", f, c.status(f, "(MESSAGES UNSEEN)")[1])
        c.logout()
        shot("07-after-ops")
        w = W()
        w.search.set_text("faktura")

    @step(2)
    def s16():
        log("local search hits", W().filtered.get_n_items())
        W().search.emit("activate")

    @step(2)
    def s17():
        log("server search", W().list_sub.get_text(), W().filtered.get_n_items())
        shot("08-search")
        W().search.set_text("")
        app.compose()

    @step(2)
    def s18():
        comp = [w for w in app.get_windows() if w.__class__.__name__ == "Composer"][-1]
        comp.to_e.set_text("an")
        comp.to_e.grab_focus()
        app._comp2 = comp

    @step(1)
    def s19():
        shot("09-composer-autocomplete")
        c = app._comp2
        c.to_e.set_text("anna@example.pl")
        c.subj_e.set_text("Szkic testowy")
        c.save_draft()

    @step(3)
    def s20():
        c = app._comp2
        log("draft uid", c.draft_uid)
        c.closing = True
        c.destroy()
        from linux_icloud_mail.dialogs import PrefsDialog
        PrefsDialog(app).present(app.win)

    @step(2)
    def s21():
        shot("10-prefs")
        log("DONE")
        with open(os.path.join(SHOTS, "log.txt"), "w") as f:
            f.write("\n".join(LOG))
        app.quit_app()

    def run_steps(i=0):
        if i >= len(steps):
            return False
        delay, fn = steps[i]

        def go():
            try:
                fn()
            except Exception:
                log("ERROR in", fn.__name__, traceback.format_exc())
            run_steps(i + 1)
            return False
        GLib.timeout_add(int(delay * 1000), go)
        return False
    run_steps()
