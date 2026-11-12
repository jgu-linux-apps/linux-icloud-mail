"""Desktop integration: theme-dependent launcher icon, autostart, dock badge."""

import os
import re

from gi.repository import Gio, GLib

from . import APP_ID, BIN_NAME

HERE = os.path.dirname(os.path.abspath(__file__))
_DEV_ICONS = os.path.join(os.path.dirname(HERE), "data", "icons")
ICON_DIR = "/usr/share/%s/icons" % BIN_NAME if os.path.isdir("/usr/share/%s/icons" % BIN_NAME) else _DEV_ICONS
SYSTEM_DESKTOP = "/usr/share/applications/%s.desktop" % APP_ID
USER_APPS = os.path.join(GLib.get_user_data_dir(), "applications")
USER_DESKTOP = os.path.join(USER_APPS, "%s.desktop" % APP_ID)
AUTOSTART = os.path.join(GLib.get_user_config_dir(), "autostart", "%s.desktop" % APP_ID)
MARKER = "X-LinuxIcloudMail-Managed=theme-icon"


def app_icon_name(dark):
    return APP_ID + ("-dark" if dark else "")


def tray_icon_name(dark, unread):
    return "linux-icloud-mail-tray-%s%s" % ("dark" if dark else "light", "-unread" if unread else "")


def apply_launcher_icon(dark):
    """Make the dock / app grid icon follow the system theme.

    GNOME has no per-theme app icons, so we keep a user-level copy of the
    installed .desktop file whose Icon= points to the light or dark variant.
    GNOME Shell watches this folder and swaps the icon immediately.
    """
    if not os.path.exists(SYSTEM_DESKTOP):
        return
    try:
        if os.path.exists(USER_DESKTOP):
            with open(USER_DESKTOP, encoding="utf-8") as f:
                cur = f.read()
            if MARKER not in cur:
                return   # user's own override - leave it alone
        else:
            cur = ""
        with open(SYSTEM_DESKTOP, encoding="utf-8") as f:
            src = f.read()
        icon = app_icon_name(dark)
        out = re.sub(r"(?m)^Icon=.*$", "Icon=" + icon, src)
        if "TryExec=" not in out:
            out = out.replace("[Desktop Entry]\n", "[Desktop Entry]\nTryExec=/usr/bin/%s\n" % BIN_NAME, 1)
        out = out.replace("[Desktop Entry]\n", "[Desktop Entry]\n%s\n" % MARKER, 1)
        if out != cur:
            os.makedirs(USER_APPS, exist_ok=True)
            tmp = USER_DESKTOP + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(out)
            os.replace(tmp, USER_DESKTOP)
    except OSError:
        pass


def set_autostart(enabled):
    try:
        if enabled:
            os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
            exe = "/usr/bin/%s" % BIN_NAME if os.path.exists("/usr/bin/%s" % BIN_NAME) else BIN_NAME
            with open(AUTOSTART, "w", encoding="utf-8") as f:
                f.write("[Desktop Entry]\nType=Application\nName=Linux iCloud Mail\n"
                        "Exec=%s --hidden\nIcon=%s\nX-GNOME-Autostart-enabled=true\n"
                        "X-GNOME-Autostart-Delay=3\nNoDisplay=true\n" % (exe, APP_ID))
        elif os.path.exists(AUTOSTART):
            os.remove(AUTOSTART)
    except OSError:
        pass


class LauncherBadge:
    """Unread counter on the Ubuntu Dock icon (com.canonical.Unity.LauncherEntry)."""

    def __init__(self):
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self.bus = None
        self.count = -1

    def set(self, count):
        if self.bus is None or count == self.count:
            return
        self.count = count
        try:
            self.bus.emit_signal(None, "/com/canonical/unity/launcherentry/%d" % abs(hash(APP_ID)),
                                 "com.canonical.Unity.LauncherEntry", "Update",
                                 GLib.Variant("(sa{sv})", ("application://%s.desktop" % APP_ID, {
                                     "count": GLib.Variant("x", count),
                                     "count-visible": GLib.Variant("b", count > 0)})))
        except GLib.Error:
            pass
