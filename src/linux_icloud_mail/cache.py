"""SQLite cache: message headers (for instant start) + a bounded body cache."""

import hashlib
import os
import sqlite3
import threading
import time

from .config import CACHE_DIR
from .mailparse import Row, addresses

BODY_CACHE_LIMIT = 400 * 1024 * 1024   # bytes of raw messages kept on disk
COLS = Row.__slots__


class Cache:
    def __init__(self, account):
        os.makedirs(CACHE_DIR, exist_ok=True)
        h = hashlib.sha1(account.lower().encode()).hexdigest()[:12]
        self.path = os.path.join(CACHE_DIR, "mail-%s.db" % h)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS messages(
            folder TEXT, uid INTEGER, flags TEXT, idate INTEGER, size INTEGER, from_name TEXT,
            from_addr TEXT, to_h TEXT, cc_h TEXT, subject TEXT, msgid TEXT, preview TEXT, attach INTEGER,
            PRIMARY KEY(folder, uid));
        CREATE INDEX IF NOT EXISTS msg_date ON messages(folder, idate DESC);
        CREATE TABLE IF NOT EXISTS folders(name TEXT PRIMARY KEY, uidvalidity INTEGER);
        CREATE TABLE IF NOT EXISTS bodies(folder TEXT, uid INTEGER, raw BLOB, size INTEGER, atime INTEGER,
            PRIMARY KEY(folder, uid));
        CREATE TABLE IF NOT EXISTS contacts(addr TEXT PRIMARY KEY, name TEXT, cnt INTEGER);
        """)

    # -------------------------------------------------- folders
    def uidvalidity(self, folder):
        with self.lock:
            r = self.db.execute("SELECT uidvalidity FROM folders WHERE name=?", (folder,)).fetchone()
        return r[0] if r else None

    def reset_folder(self, folder, uidvalidity):
        with self.lock:
            self.db.execute("BEGIN")
            self.db.execute("DELETE FROM messages WHERE folder=?", (folder,))
            self.db.execute("DELETE FROM bodies WHERE folder=?", (folder,))
            self.db.execute("INSERT OR REPLACE INTO folders VALUES(?,?)", (folder, uidvalidity))
            self.db.execute("COMMIT")

    def drop_folder(self, folder):
        with self.lock:
            self.db.execute("DELETE FROM messages WHERE folder=?", (folder,))
            self.db.execute("DELETE FROM bodies WHERE folder=?", (folder,))
            self.db.execute("DELETE FROM folders WHERE name=?", (folder,))

    # -------------------------------------------------- messages
    def uids(self, folder):
        with self.lock:
            return {r[0] for r in self.db.execute("SELECT uid FROM messages WHERE folder=?", (folder,))}

    def flags_map(self, folder, uids):
        if not uids:
            return {}
        out = {}
        with self.lock:
            ul = list(uids)
            for i in range(0, len(ul), 900):
                chunk = ul[i:i + 900]
                q = "SELECT uid, flags FROM messages WHERE folder=? AND uid IN (%s)" % ",".join("?" * len(chunk))
                for uid, fl in self.db.execute(q, [folder] + chunk):
                    out[uid] = fl
        return out

    def insert(self, rows):
        if not rows:
            return
        with self.lock:
            self.db.execute("BEGIN")
            self.db.executemany("INSERT OR REPLACE INTO messages VALUES(%s)" % ",".join("?" * len(COLS)),
                                [r.as_tuple() for r in rows])
            for r in rows:
                if r.from_addr:
                    self.db.execute("INSERT INTO contacts VALUES(?,?,1) ON CONFLICT(addr) DO UPDATE SET "
                                    "cnt=cnt+1, name=CASE WHEN excluded.name!='' THEN excluded.name ELSE name END",
                                    (r.from_addr.lower(), r.from_name if r.from_name != r.from_addr else ""))
            self.db.execute("COMMIT")

    def add_contacts(self, header_values):
        with self.lock:
            for v in header_values:
                for name, addr in addresses(v):
                    if addr:
                        self.db.execute("INSERT INTO contacts VALUES(?,?,3) ON CONFLICT(addr) DO UPDATE SET "
                                        "cnt=cnt+3", (addr.lower(), name or ""))

    def delete(self, folder, uids):
        if not uids:
            return
        with self.lock:
            self.db.execute("BEGIN")
            self.db.executemany("DELETE FROM messages WHERE folder=? AND uid=?", [(folder, u) for u in uids])
            self.db.executemany("DELETE FROM bodies WHERE folder=? AND uid=?", [(folder, u) for u in uids])
            self.db.execute("COMMIT")

    def set_flags(self, folder, updates):
        """updates: {uid: flags_str}"""
        if not updates:
            return
        with self.lock:
            self.db.execute("BEGIN")
            self.db.executemany("UPDATE messages SET flags=? WHERE folder=? AND uid=?",
                                [(f, folder, u) for u, f in updates.items()])
            self.db.execute("COMMIT")

    def load(self, folder, limit=None):
        q = "SELECT %s FROM messages WHERE folder=? ORDER BY idate DESC, uid DESC" % ",".join(COLS)
        if limit:
            q += " LIMIT %d" % int(limit)
        with self.lock:
            return [Row(**dict(zip(COLS, r))) for r in self.db.execute(q, (folder,))]

    def load_flagged(self, folders):
        if not folders:
            return []
        q = ("SELECT %s FROM messages WHERE flags LIKE '%%\\Flagged%%' AND folder IN (%s) "
             "ORDER BY idate DESC" % (",".join(COLS), ",".join("?" * len(folders))))
        with self.lock:
            return [Row(**dict(zip(COLS, r))) for r in self.db.execute(q, list(folders))]

    def get(self, folder, uid):
        with self.lock:
            r = self.db.execute("SELECT %s FROM messages WHERE folder=? AND uid=?" % ",".join(COLS),
                                (folder, uid)).fetchone()
        return Row(**dict(zip(COLS, r))) if r else None

    def unread_count(self, folder):
        with self.lock:
            r = self.db.execute("SELECT COUNT(*) FROM messages WHERE folder=? AND flags NOT LIKE '%\\Seen%'",
                                (folder,)).fetchone()
        return r[0] if r else 0

    def sent_from_addresses(self, sent_folder, limit=20):
        """Addresses the user has sent from (= their working aliases), most used first."""
        if not sent_folder:
            return []
        with self.lock:
            return [r[0] for r in self.db.execute(
                "SELECT from_addr, COUNT(*) c FROM messages WHERE folder=? AND from_addr LIKE '%@%' "
                "GROUP BY lower(from_addr) ORDER BY c DESC LIMIT ?", (sent_folder, limit))]

    def search_contacts(self, text, limit=8):
        t = "%" + text.lower() + "%"
        with self.lock:
            return self.db.execute("SELECT addr, name FROM contacts WHERE addr LIKE ? OR lower(name) LIKE ? "
                                   "ORDER BY cnt DESC LIMIT ?", (t, t, limit)).fetchall()

    # -------------------------------------------------- bodies
    def get_body(self, folder, uid):
        with self.lock:
            r = self.db.execute("SELECT raw FROM bodies WHERE folder=? AND uid=?", (folder, uid)).fetchone()
            if r:
                self.db.execute("UPDATE bodies SET atime=? WHERE folder=? AND uid=?", (int(time.time()), folder, uid))
        return bytes(r[0]) if r else None

    def has_body(self, folder, uid):
        with self.lock:
            return self.db.execute("SELECT 1 FROM bodies WHERE folder=? AND uid=?", (folder, uid)).fetchone() is not None

    def put_body(self, folder, uid, raw):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO bodies VALUES(?,?,?,?,?)",
                            (folder, uid, sqlite3.Binary(raw), len(raw), int(time.time())))
            total = self.db.execute("SELECT COALESCE(SUM(size),0) FROM bodies").fetchone()[0]
            if total > BODY_CACHE_LIMIT:
                self.db.execute("DELETE FROM bodies WHERE rowid IN (SELECT rowid FROM bodies ORDER BY atime "
                                "LIMIT (SELECT COUNT(*)/5 FROM bodies))")

    def wipe(self):
        with self.lock:
            self.db.close()
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(self.path + suffix)
                except OSError:
                    pass
