"""Fill a local test IMAP server with varied messages."""
import imaplib
import random
import sys
import time
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
import datetime

sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else ".")
from linux_icloud_mail.imapparse import utf7_encode, quote  # noqa

c = imaplib.IMAP4("127.0.0.1", 1143)
c.login("jaro@icloud.com", "secret")
for f in ["Projekty", "Projekty/Faktury", "Zażółć gęślą"]:
    c.create(quote(utf7_encode(f)))

people = [("Anna Kowalska", "anna@example.pl"), ("GitHub", "noreply@github.com"),
          ("Marek Nowak", "marek.nowak@firma.pl"), ("Apple", "no_reply@email.apple.com"),
          ("Azure DevOps", "azuredevops@microsoft.com"), ("Kasia", "kasia@icloud.com")]
subjects = ["Spotkanie w czwartek", "Faktura za październik", "[repo] PR #42: fix pipeline",
            "Twoje zdjęcia z wakacji", "Build failed: main", "Zażółć gęślą jaźń – test",
            "Re: Plan wdrożenia Terraform", "Newsletter listopad", "Przypomnienie o płatności"]
now = datetime.datetime.now(datetime.timezone.utc)
png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000010000000100806000000"
                    "1ff3ff610000001d49444154789c63fccfc0f09f8114c0c4400a60a2"
                    "a10f8c1a30604000a5c40f11e4d03b8b0000000049454e44ae426082")


def build(i):
    name, addr = random.choice(people)
    m = EmailMessage()
    m["From"] = "%s <%s>" % (name, addr)
    m["To"] = "Jaro <jaro@icloud.com>"
    if i % 7 == 0:
        m["Cc"] = "Marek Nowak <marek.nowak@firma.pl>"
    m["Subject"] = random.choice(subjects) + (" (%d)" % i)
    d = now - datetime.timedelta(hours=i * 5)
    m["Date"] = format_datetime(d)
    m["Message-ID"] = make_msgid(domain="example.pl")
    text = ("Cześć Jaro,\n\nTo jest wiadomość testowa numer %d. Link: https://example.com/doc/%d\n\n"
            "> cytat z poprzedniej wiadomości\n> druga linia\n\nPozdrawiam,\n%s" % (i, i, name))
    m.set_content(text)
    if i % 3 == 0:
        m.add_alternative(
            "<html><body style='font-family:Arial'><h2 style='color:#0a63e6'>Wiadomość %d</h2>"
            "<p>Cześć <b>Jaro</b>, to jest <i>HTML</i> z obrazkiem: <img src='cid:logo1'></p>"
            "<p><img src='https://example.com/tracker.png' width=1 height=1>"
            "<a href='https://example.com'>Otwórz stronę</a></p>"
            "<table style='background:#f3f6fb;border:1px solid #ccd'><tr><td>Kwota</td><td>123,45 zł</td></tr></table>"
            "<script>alert(1)</script><p onclick='x()'>tekst</p></body></html>" % i, subtype="html")
        m.get_payload()[1].add_related(png, "image", "png", cid="<logo1>")
    if i % 5 == 0:
        m.add_attachment(b"%PDF-1.4 test " * 200, maintype="application", subtype="pdf",
                         filename="faktura-%d.pdf" % i)
    if i == 4:
        m.add_attachment(bytes(random.getrandbits(8) for _ in range(1_700_000)), maintype="application",
                         subtype="zip", filename="duży plik.zip")
    return m, d


for i in range(320):
    m, d = build(i)
    flags = "(\\Seen)" if i > 6 else "()"
    if i % 11 == 0:
        flags = "(\\Seen \\Flagged)"
    folder = "INBOX" if i < 260 else ("Archive" if i < 290 else utf7_encode("Projekty/Faktury"))
    c.append(quote(folder), flags, imaplib.Time2Internaldate(d.timestamp()), m.as_bytes())
print("ok")
c.logout()
