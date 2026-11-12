# Linux iCloud Mail

**Version 0.1.11 (2026-11-12)**

A fast, native iCloud Mail client for Ubuntu, built with GTK 4 and libadwaita.
Instead of wrapping the heavy icloud.com web app, it talks to iCloud Mail
directly over IMAP/SMTP, so it starts instantly and stays responsive even with
very large mailboxes.

## Features

- Native GTK 4 / libadwaita window: mailboxes sidebar, message list, reader
- Double-click (or Enter) opens a message in its own window with all actions
- Instant start: message headers are cached locally, only changes are fetched
- Recent messages are prefetched in the background, so most open instantly
- Push for new mail (IMAP IDLE), no polling delay
- Desktop notifications for new mail; clicking one opens the message and marks it as read
- Notifications disappear automatically when the message is read, moved or deleted (also on iPhone / icloud.com)
- Tray icon; closing the window minimizes the app to the tray
- Right-click the tray icon → **Quit** to close the app
- Unread counter on the tray icon and on the Ubuntu Dock icon
- Light / dark theme that follows the system setting automatically
- App icon with a light and a dark version that switches with the system theme (dock, app grid, tray, window)
- Optional dark rendering of message content in dark mode (one click shows the original colors)
- Mailboxes: Inbox, Flagged (smart mailbox), Drafts, Sent, Junk, Trash, Archive and your own folders (including subfolders)
- Create, rename and delete folders; mark all as read; empty Trash or Junk
- Reply, Reply All, Forward (attachments are forwarded too)
- Rich-text composer: bold, italic, underline, strikethrough, lists, quote levels, links, text size
- Signature and multiple "From" addresses (iCloud aliases, custom domains)
- Replies automatically use the alias the message was sent to (like iCloud.com), also for Bcc and mailing lists
- Aliases are detected automatically from incoming mail and from your Sent folder, no manual setup needed
- Contact autocomplete based on your mail history
- Attachments: open, save, "Download All" straight to the Downloads folder; add them with a button or by dragging files onto the composer
- Drafts: save manually (Ctrl+S), autosave every minute, continue editing from the Drafts folder
- Flag / unflag, mark read / unread, move, archive, junk / not junk, delete
- Drag & drop messages onto a folder in the sidebar to move them
- Multi-selection (Ctrl / Shift + click) with bulk actions
- Right-click menu on messages with the 3 most recently used folders for "Move to…"
- Instant local filtering while typing in search; press Enter for full-text search on the server
- Filters: all, unread, flagged, with attachments
- Group by sender: one row per sender with a message counter; expand to see the rest, "Select All from Sender" for bulk actions
- Remote images blocked by default for privacy (one-click load, or allow always in Preferences)
- Safe message rendering: scripts never run, links open in your browser
- Print, save as .eml, view source, zoom
- Can be the default mail app (mailto: links open the composer)
- Keyboard shortcuts (Ctrl+N, Ctrl+R, Ctrl+Shift+R, Ctrl+Shift+F, Delete, F5, Ctrl+F, …)
- Optional autostart, hidden in the tray
- Password stored in the GNOME keyring (libsecret)
- English and Polish UI (Polish when the system language is Polish)

## Changes

- **0.1.11** – license: the app is now published under the Freeware License (No Modifications), see `LICENSE`; free to use and share unmodified, modification is not permitted. No other changes in the app.
- **0.1.10** – no changes in the app: the repository now keeps the source code in `src/`, and the `.deb` is published on the GitHub Releases page (built by GitHub Actions for every version tag); README: installation from Releases, building from source
- **0.1.9** – group messages by sender (filter menu → Group by Sender)
- **0.1.8** – credits "Created by: JGu / Supported by Claude" in About and in the sidebar
- **0.1.7** – double-click / Enter opens a message in a separate window; remote images in quoted text are blocked in the composer too
- **0.1.6** – clicked notifications are removed from GNOME and the message is marked as read (tray / dock counter updates); notifications also vanish when a message is read, moved or deleted anywhere
- **0.1.5** – clicking a GNOME notification now opens that message (also from the tray / another folder)
- **0.1.4** – "Download All" saves directly to Downloads (no folder dialog) with an "Open Folder" button; fixed national-character file names and sizes of attachments in large messages
- **0.1.3** – alias detection also from the To/Cc field and from Received headers (no setup needed)
- **0.1.2** – "From" field always visible in the composer; names with national characters or emoji shown correctly in To/Cc
- **0.1.1** – reply from the alias the message was sent to; automatic alias detection
- **0.1.0** – first release

## Requirements

- Ubuntu 24.04 or newer (tested for Ubuntu 26.10 / GNOME)
- For the tray icon: the **Ubuntu AppIndicators** extension (enabled by default on Ubuntu)

## Installation

Download the newest `linux-icloud-mail_<version>_all.deb` from the [Releases page](https://github.com/jgu-linux-apps/linux-icloud-mail/releases/latest) and install it:

```bash
sudo apt install ./linux-icloud-mail_<version>_all.deb
```

Start **Linux iCloud Mail** from the app grid, or run `linux-icloud-mail` in a
terminal (closing the terminal does not close the app).

Uninstall:

```bash
sudo apt remove linux-icloud-mail
```

## Building from source

The source code is in the `src/` folder of this repository; the `.deb` packages are not kept in git but attached to [GitHub Releases](https://github.com/jgu-linux-apps/linux-icloud-mail/releases). Inside `src/`, `bash build_deb.sh` builds `dist/linux-icloud-mail_<version>_all.deb` (the version comes from `VERSION` in `linux_icloud_mail/__init__.py`). Pushing a tag `vX.Y.Z` with the same version makes GitHub Actions (`.github/workflows/release.yml`) build the package and publish the release.

## First sign-in (app-specific password)

iCloud mail apps need an **app-specific password**, not your Apple ID password:

1. Go to <https://account.apple.com> and sign in.
2. Open **Sign-In and Security → App-Specific Passwords**.
3. Create a new password (for example "Linux Mail") and copy it.
4. In Linux iCloud Mail enter your **@icloud.com** address and paste the password.

## Settings

Main menu → **Preferences**: notifications, autostart, close to tray, when to
mark messages as read, remote content, dark message content, rich-text
composing, your name, extra "From" addresses, signature, clearing the local
cache and signing out.

## Data and privacy

- Connects only to Apple's servers: `imap.mail.me.com` (993, TLS) and `smtp.mail.me.com` (587, STARTTLS).
- Local cache: `~/.cache/linux-icloud-mail/` (headers and up to 400 MB of recently read messages).
- Settings: `~/.config/linux-icloud-mail/settings.json`.
- Signing out removes the password and the local cache.

## License

Free of charge for everyone to use. Modifying the software and distributing
modified versions is not permitted. See [LICENSE](LICENSE).

iCloud is a trademark of Apple Inc. This project is not affiliated with Apple.

Created by: JGu  
Supported by Claude
