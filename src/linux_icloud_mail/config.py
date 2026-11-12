"""Settings (JSON) and password storage (libsecret with a 0600 file fallback)."""

import base64
import copy
import json
import os

from . import APP_ID

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "linux-icloud-mail")
CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "linux-icloud-mail")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
SECRET_FALLBACK = os.path.join(CONFIG_DIR, ".secret")

DEFAULTS = {
    "email": "",
    "display_name": "",
    "aliases": [],            # extra From: addresses (iCloud aliases / custom domain)
    "signature": "",
    "notifications": True,
    "autostart": True,
    "close_to_tray": True,
    "remote_images": False,   # block remote content by default (privacy + speed)
    "dark_mail": True,        # darken message content in dark mode
    "mark_read": "delay",     # immediate | delay | manual
    "compose_html": True,
    "window": {"w": 1320, "h": 840, "max": False},
    "pane_sidebar": 230,
    "pane_list": 420,
    "recent_folders": [],
    "tray_hint_shown": False,
}


class Settings:
    def __init__(self):
        self.data = copy.deepcopy(DEFAULTS)
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                self.data.update(stored)
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save()

    def get(self, key, default=None):
        return self.data.get(key, default)

    def save(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, SETTINGS_FILE)

    def identities(self):
        """List of From: addresses, primary first."""
        out = []
        for a in [self["email"]] + list(self["aliases"] or []):
            a = (a or "").strip()
            if a and a.lower() not in [x.lower() for x in out]:
                out.append(a)
        return out


# ---------------------------------------------------------------- secrets
_schema = None


def _secret():
    global _schema
    try:
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret
    except (ImportError, ValueError):
        return None, None
    if _schema is None:
        _schema = Secret.Schema.new(APP_ID, Secret.SchemaFlags.NONE,
                                    {"account": Secret.SchemaAttributeType.STRING})
    return Secret, _schema


def store_password(account, password):
    Secret, schema = _secret()
    if Secret is not None:
        try:
            ok = Secret.password_store_sync(schema, {"account": account}, Secret.COLLECTION_DEFAULT,
                                            "Linux iCloud Mail (%s)" % account, password, None)
            if ok:
                _remove_fallback()
                return
        except Exception:
            pass
    os.makedirs(CONFIG_DIR, exist_ok=True)
    fd = os.open(SECRET_FALLBACK, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({account: base64.b64encode(password.encode()).decode()}, f)


def lookup_password(account):
    Secret, schema = _secret()
    if Secret is not None:
        try:
            pw = Secret.password_lookup_sync(schema, {"account": account}, None)
            if pw:
                return pw
        except Exception:
            pass
    try:
        with open(SECRET_FALLBACK) as f:
            v = json.load(f).get(account)
        return base64.b64decode(v).decode() if v else None
    except (OSError, ValueError):
        return None


def clear_password(account):
    Secret, schema = _secret()
    if Secret is not None:
        try:
            Secret.password_clear_sync(schema, {"account": account}, None)
        except Exception:
            pass
    _remove_fallback()


def _remove_fallback():
    try:
        os.remove(SECRET_FALLBACK)
    except OSError:
        pass
