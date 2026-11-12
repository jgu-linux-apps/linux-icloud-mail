#!/bin/bash
# Builds dist/linux-icloud-mail_<version>_all.deb
set -euo pipefail
cd "$(dirname "$0")"
VER=$(python3 -c "import re;print(re.search(r'VERSION = \"(.+?)\"', open('linux_icloud_mail/__init__.py').read()).group(1))")
ID=io.github.jaro.LinuxIcloudMail
B=build/deb
rm -rf build && mkdir -p $B/DEBIAN dist
install -Dm755 packaging/linux-icloud-mail $B/usr/bin/linux-icloud-mail
mkdir -p $B/usr/lib/linux-icloud-mail/linux_icloud_mail
for f in linux_icloud_mail/*.py; do install -m644 "$f" $B/usr/lib/linux-icloud-mail/linux_icloud_mail/; done
# release entry point without the developer test hook
cat > $B/usr/lib/linux-icloud-mail/linux_icloud_mail/__main__.py <<'PY'
import sys


def main():
    from .app import App
    return App().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
PY
install -Dm644 packaging/$ID.desktop $B/usr/share/applications/$ID.desktop
install -Dm644 packaging/$ID.metainfo.xml $B/usr/share/metainfo/$ID.metainfo.xml
install -Dm644 data/icons/$ID.svg $B/usr/share/icons/hicolor/scalable/apps/$ID.svg
install -Dm644 data/icons/$ID-dark.svg $B/usr/share/icons/hicolor/scalable/apps/$ID-dark.svg
for s in 16 24 32 48 64 128 256 512; do
  mkdir -p $B/usr/share/icons/hicolor/${s}x${s}/apps
  rsvg-convert -w $s -h $s data/icons/$ID.svg -o $B/usr/share/icons/hicolor/${s}x${s}/apps/$ID.png
  rsvg-convert -w $s -h $s data/icons/$ID-dark.svg -o $B/usr/share/icons/hicolor/${s}x${s}/apps/$ID-dark.png
done
mkdir -p $B/usr/share/linux-icloud-mail/icons/hicolor/scalable/actions
install -m644 data/icons/linux-icloud-mail-tray-*.svg data/icons/$ID.svg data/icons/$ID-dark.svg $B/usr/share/linux-icloud-mail/icons/
install -m644 data/icons/hicolor/scalable/actions/*.svg $B/usr/share/linux-icloud-mail/icons/hicolor/scalable/actions/
install -Dm644 LICENSE $B/usr/share/doc/linux-icloud-mail/copyright
install -Dm644 README.md $B/usr/share/doc/linux-icloud-mail/README.md
SIZE=$(du -sk $B/usr | cut -f1)
cat > $B/DEBIAN/control <<CTL
Package: linux-icloud-mail
Version: $VER
Section: mail
Priority: optional
Architecture: all
Installed-Size: $SIZE
Depends: python3 (>= 3.10), python3-gi (>= 3.42), gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.5), gir1.2-webkit-6.0, gir1.2-secret-1, librsvg2-common
Recommends: gnome-shell-extension-appindicator, gnome-keyring
Maintainer: JGu <noreply@localhost>
Homepage: https://www.icloud.com/mail
Description: Fast native iCloud Mail client for Ubuntu
 Linux iCloud Mail is a lightweight GTK 4 / libadwaita mail client for
 iCloud Mail. It talks to iCloud directly over IMAP and SMTP, so it starts
 instantly and stays fast even with large mailboxes.
 .
 Features: tray icon with close-to-tray, push notifications for new mail
 (IMAP IDLE), rich-text composer, attachments, server-side search,
 folders, drag and drop, and automatic light/dark theme that also switches
 the application icon.
CTL
cat > $B/DEBIAN/postinst <<'SH'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -qtf /usr/share/icons/hicolor || true
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
fi
exit 0
SH
cat > $B/DEBIAN/postrm <<'SH'
#!/bin/sh
set -e
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -qtf /usr/share/icons/hicolor || true
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
exit 0
SH
chmod 755 $B/DEBIAN/postinst $B/DEBIAN/postrm
find $B -type d -exec chmod 755 {} +
dpkg-deb --root-owner-group -Zxz --build $B dist/linux-icloud-mail_${VER}_all.deb
echo "built dist/linux-icloud-mail_${VER}_all.deb"
