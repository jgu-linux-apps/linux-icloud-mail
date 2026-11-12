import imaplib, subprocess, threading, time
from gi.repository import GLib
def log(*a): print("DRIVER:", *a, flush=True)
def push(subj):
    c = imaplib.IMAP4("127.0.0.1", 1143); c.login("jaro@icloud.com", "secret")
    m = ("From: Natalia <hola@x.pl>\r\nTo: jaro@icloud.com\r\nSubject: %s\r\nMessage-ID: <%s@x>\r\n\r\nHola\r\n" % (subj, abs(hash(subj)))).encode()
    c.append("INBOX", "()", imaplib.Time2Internaldate(time.time()), m); c.logout()
def mark_seen_elsewhere(subj):
    c = imaplib.IMAP4("127.0.0.1", 1143); c.login("jaro@icloud.com", "secret"); c.select("INBOX")
    typ, d = c.uid("SEARCH", None, "SUBJECT", '"%s"' % subj); c.uid("STORE", d[0].split()[-1], "+FLAGS", "(\\Seen)"); c.logout()
def run(app):
    W = lambda: app.win
    def a():
        W().set_visible(False)
        log("unread before:", app.unread)
        threading.Thread(target=push, args=("Nowy mail A",)).start()
        GLib.timeout_add(4000, b)
    def b():
        log("notified:", list(app.notified.values()), "| unread:", app.unread, "| tray:", app.tray.icon_name)
        (folder, uid), nid = list(app.notified.items())[-1]
        def click():
            subprocess.run(["gdbus", "call", "--session", "-d", "io.github.jaro.LinuxIcloudMail", "-o", "/io/github/jaro/LinuxIcloudMail",
                            "-m", "org.freedesktop.Application.ActivateAction", "open-message", "[<('%s', uint32 %d)>]" % (folder, uid), "{}"],
                           capture_output=True)
        threading.Thread(target=click).start()
        GLib.timeout_add(3000, c)
    def c():
        log("after click: reader:", W().reader.item.row.subject if W().reader.item else None, "| unread:", app.unread,
            "| still notified:", list(app.notified.values()))
        W().set_visible(False)
        threading.Thread(target=push, args=("Nowy mail B",)).start()
        GLib.timeout_add(4000, d)
    def d():
        log("notified B:", list(app.notified.values()), "| unread:", app.unread)
        threading.Thread(target=mark_seen_elsewhere, args=("Nowy mail B",)).start()
        GLib.timeout_add(500, lambda: (app.backend.sync_folder("INBOX", prio=1), False)[1])
        GLib.timeout_add(3500, e)
    def e():
        log("after read on iPhone: notified:", list(app.notified.values()), "| unread:", app.unread)
        app.quit_app()
    GLib.timeout_add(5000, lambda: (a(), False)[1])
