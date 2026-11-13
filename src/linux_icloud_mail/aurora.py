"""Motyw „Aurora”: pastelowe szkło (jasny) / zorza na granatowym tle (ciemny), gradient fiolet → cyjan,
poświata po najechaniu. Wariant jasny/ciemny wybiera się sam według systemu. „System” = zwykła Adwaita.

Kolory Adwaity nadpisywane są w osobnym dostawcy CSS (zmiana na żywo, bez restartu). libadwaita ≥ 1.6 czyta
zmienne CSS (--window-bg-color), starsze – @define-color, więc blok kolorów powstaje dla wersji, na której
aplikacja działa."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

THEMES = ("system", "aurora")

COLORS = {
    False: dict(window_bg="#f4f1ff", window_fg="#1f1a3a", view_bg="rgba(255,255,255,0.70)", view_fg="#1f1a3a",
                headerbar_bg="rgba(255,255,255,0.58)", headerbar_fg="#1f1a3a", sidebar_bg="rgba(255,255,255,0.30)",
                sidebar_fg="#1f1a3a", card_bg="rgba(255,255,255,0.75)", card_fg="#1f1a3a",
                popover_bg="#fbfaff", popover_fg="#1f1a3a", dialog_bg="#fbfaff", dialog_fg="#1f1a3a",
                accent_bg="#7c5cff", accent_fg="#ffffff", accent="#6d4dff"),
    True: dict(window_bg="#0c0e1a", window_fg="#ecebff", view_bg="rgba(255,255,255,0.03)", view_fg="#ecebff",
               headerbar_bg="rgba(255,255,255,0.04)", headerbar_fg="#ecebff", sidebar_bg="rgba(255,255,255,0.012)",
               sidebar_fg="#ecebff", card_bg="rgba(255,255,255,0.05)", card_fg="#ecebff",
               popover_bg="#161a2e", popover_fg="#ecebff", dialog_bg="#161a2e", dialog_fg="#ecebff",
               accent_bg="#8b5cf6", accent_fg="#ffffff", accent="#22d3ee"),
}

EXTRA = {
    False: """
window.background:not(.aurora-off) {
  background-image: linear-gradient(135deg, #f6f2ff 0%, #eef8ff 55%, #fff4f8 100%);
}
headerbar { box-shadow: inset 0 -1px alpha(#7c5cff, 0.12); }
.navigation-sidebar > row:selected, listview > row:selected, list.content > row:selected {
  background-image: linear-gradient(90deg, #7c5cff, #22b8e6); color: #ffffff;
  box-shadow: 0 6px 18px alpha(#7c5cff, 0.30);
}
.navigation-sidebar > row:hover:not(:selected), listview > row:hover:not(:selected) {
  background-color: #ffffff;
  box-shadow: 0 0 0 1.5px alpha(#7c5cff, 0.55), 0 8px 22px alpha(#7c5cff, 0.25);
}
button.suggested-action {
  background-image: linear-gradient(90deg, #7c5cff, #22b8e6); color: #ffffff;
  box-shadow: 0 6px 18px alpha(#7c5cff, 0.40);
}
""",
    True: """
window.background:not(.aurora-off) {
  background-image: radial-gradient(ellipse at 12% -5%, alpha(#22d3ee, 0.20), transparent 55%),
                    radial-gradient(ellipse at 100% 105%, alpha(#a855f7, 0.24), transparent 55%);
}
headerbar { box-shadow: inset 0 -1px alpha(#ffffff, 0.07); }
.navigation-sidebar > row:selected, listview > row:selected, list.content > row:selected {
  background-image: linear-gradient(90deg, alpha(#a855f7, 0.60), alpha(#22d3ee, 0.48)); color: #ffffff;
  box-shadow: 0 6px 18px alpha(#a855f7, 0.30);
}
.navigation-sidebar > row:hover:not(:selected), listview > row:hover:not(:selected) {
  background-color: alpha(#ffffff, 0.05);
  box-shadow: 0 0 0 1px alpha(#22d3ee, 0.55), 0 0 20px alpha(#22d3ee, 0.28);
}
button.suggested-action {
  background-image: linear-gradient(90deg, #a855f7, #22d3ee); color: #ffffff;
  box-shadow: 0 0 18px alpha(#a855f7, 0.45);
}
""",
}

COMMON = """
.navigation-sidebar > row, listview > row { border-radius: 11px; transition: box-shadow 150ms, background 150ms; }
.navigation-sidebar > row { margin: 1px 6px; }
"""


def _color_block(c):
    if (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 6):
        return ":root {\n" + "".join(f"  --{k.replace('_', '-')}-color: {v};\n" for k, v in c.items()) + "}\n"
    return "".join(f"@define-color {k}_color {v};\n" for k, v in c.items())


def css_for(dark, extra=""):
    return _color_block(COLORS[dark]) + EXTRA[dark] + COMMON + extra


class AuroraTheme:
    """apply('aurora' | 'system'); po zmianie jasny/ciemny w systemie przelicza się sam.
    extra: dodatkowe reguły konkretnej aplikacji (np. bardziej przezroczysty pasek boczny)."""

    def __init__(self, extra=None):
        self.name = "system"
        self.extra = extra or {False: "", True: ""}
        self.provider = None
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self._reload())

    def apply(self, name):
        self.name = name if name in THEMES else "system"
        self._reload()

    def _reload(self):
        display = Gdk.Display.get_default()
        if self.provider is not None:
            Gtk.StyleContext.remove_provider_for_display(display, self.provider)
            self.provider = None
        if self.name != "aurora":
            return
        dark = Adw.StyleManager.get_default().get_dark()
        css = css_for(dark, self.extra.get(dark, ""))
        self.provider = Gtk.CssProvider()
        if hasattr(self.provider, "load_from_string"):
            self.provider.load_from_string(css)
        else:
            self.provider.load_from_data(css, -1)
        Gtk.StyleContext.add_provider_for_display(display, self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
