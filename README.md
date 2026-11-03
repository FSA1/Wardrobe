<p align="center">
  <img src="https://github.com/SwordPuffin/Wardrobe/blob/main/data/icons/hicolor/scalable/apps/io.github.swordpuffin.wardrobe.svg" width=128 height=128/>
</p>
<h1 align="center">Wardrobe Plus</h1>

> **AI-generated fork modifications:**
> The changes made in this fork were generated with AI. This does not mean
> that the original application's code was written by AI. FSA1 defined the
> goals and requirements and decides whether to review, use, or
> distribute these changes.
>
> **Disclaimer:** This fork is provided “as is,” without warranties of any
> kind. Use it at your own risk. The authors and contributors are not liable
> for damage or data loss resulting from its use. Review changes and keep
> backups, especially before removing installed themes or other files.

<p align="center">
  <strong>Prettify your desktop effortlessly</strong>
</p>

<p align="center">GNOME Shell · GTK3/4 · Icons · Cursors · Wallpapers</p>

> Wardrobe Plus is the [FSA1/Wardrobe](https://github.com/FSA1/Wardrobe) fork. Upstream
> Flathub and AUR releases do not contain the changes documented here.

<p align="center">
  <img src="https://github.com/SwordPuffin/Wardrobe/blob/main/data/screenshots/Screenshot1.png" width="340"/>
  &nbsp;&nbsp;
  <img src="https://github.com/SwordPuffin/Wardrobe/blob/main/data/screenshots/Screenshot2.png" width="310"/>
  &nbsp;&nbsp;
  <img src="https://github.com/SwordPuffin/Wardrobe/blob/main/data/screenshots/Screenshot3.png" width="280"/>
  &nbsp;&nbsp;
  <img src="https://github.com/SwordPuffin/Wardrobe/blob/main/data/screenshots/Screenshot4.png" width="300"/>
</p>

## Features in this fork

- Search themes across selected categories, including numeric names, with
  category filters and automatic loading of additional results.
- Browse and manage installed GNOME Shell, GTK, icon, cursor, and wallpaper
  themes. Files belonging to one downloaded item, including wallpaper previews
  and light/dark icon variants, are grouped as one installation.
- Monitor and detect themes, icons, cursors, and wallpapers installed outside
  Wardrobe in the user's local data folders (including the legacy `~/.themes`
  and `~/.icons` folders); system-wide `/usr` resources are not scanned.
- Group common theme variants, read available local `index.theme` metadata, and
  use local preview images when available. Edit installed-item metadata, search
  the Wardrobe catalog for a match, or set a name, author, description, and
  preview image manually.
- Cache OCS metadata and preview images in SQLite (up to 250 MiB on disk and
  16 MiB in memory). Entries are revalidated after 30 minutes when the server
  provides validators; recent cached data can be used if the network is down.
- Trace download, extraction, and installation timings by setting
  `WARDROBE_TRACE_INSTALL=1`.

## Credits

The fork-specific changes were defined by **FSA1** and generated with
**GitHub Copilot (AI)**. The original Wardrobe application was created by
Nathan Perlman; the application icon is by Hylke Bons.

## Installation

There is currently no published Flatpak, AUR, RPM, or DEB package for this
fork. The repository includes an RPM build recipe; until a package is built
and distributed, the supported way to try the fork is to build and install
from source into your user account. This does not require removing an existing
system package.

### Build and install locally

```bash
git clone https://github.com/FSA1/Wardrobe.git
cd Wardrobe
meson setup build --prefix="$HOME/.local"
meson compile -C build
meson test -C build --print-errorlogs
meson install -C build
```

Log out and back in if the desktop environment does not immediately refresh its
application menu. The installed launcher is `~/.local/bin/wardrobe`.

The build requires Python 3, PyGObject, GTK 4, Libadwaita, libsoup 3,
GNOME Autoar, libportal GTK 4, GLib/GObject introspection data, gettext,
AppStream CLI, Meson, and Ninja. Package names vary by distribution. On Fedora,
the relevant packages are available from the system repositories; if Meson
reports a missing dependency, install its development package and re-run
`meson setup build --prefix="$HOME/.local"`.

### Build an RPM

On Fedora, install the RPM build tool and the project's build dependencies,
then run the repository helper:

```bash
sudo dnf install rpm-build appstream desktop-file-utils gettext glib2-devel \
  gnome-autoar-devel gtk4-devel libadwaita-devel libportal-gtk4-devel \
  libsoup3-devel meson ninja-build
packaging/build-rpm.sh
```

The script builds binary and source RPMs under `~/rpmbuild/`. It packages
Git-tracked project files using their current worktree contents, plus new
public files in `packaging/` and `screenshots/`. The RPM recipe is in
[`packaging/wardrobe.spec`](packaging/wardrobe.spec). The helper does not
install or remove Wardrobe; install the generated RPM separately after
reviewing it.

A DEB package or Flatpak can also be produced, but each needs distribution-
specific packaging metadata. The existing Flatpak manifest in this repository
still builds the upstream source rather than this fork.

### Publish a GitHub pre-release

The GitHub Actions workflow builds Fedora 44 binary and source RPMs, calculates
their SHA-256 checksums, and attaches them to a GitHub pre-release. To publish,
first commit and push the source and packaging changes, update the matching
Meson and RPM spec versions, then tag the normalized RPM version and release.
For example, Meson version `1.1.6~rc1` and spec release `0.1%{?dist}` use:

```bash
git tag v1.1.6-rc1-0.1
git push origin v1.1.6-rc1-0.1
```

The workflow only runs after the tag is pushed; it does not publish a release
for this checkout automatically. Its assets are manual downloads, not a DNF
repository, so GNOME Software and DNF will not discover updates automatically.
Each new RPM release needs a new version-release tag. The current local
`1.1.5-5` build would use `v1.1.5-5`.

## Replacing the upstream installation

This fork currently keeps the upstream application ID, desktop-file ID, D-Bus
name, schema ID, and icon name. That lets it use the same application identity,
but means the upstream and fork builds should not be installed side by side as
native applications. Before installing the RPM, remove an existing Flatpak
installation using the matching `--system` or `--user` option shown by
`flatpak list --app`. Do not pass `--delete-data`.

If you previously installed the fork from source with the build directory in
this checkout, remove only the files recorded in that build's Meson install
manifest. Meson does not provide a built-in uninstall command:

```bash
meson introspect build --installed | python3 -c '
import json
import pathlib
import sys

for path in json.load(sys.stdin).values():
    pathlib.Path(path).unlink(missing_ok=True)
'
if [ -d "$HOME/.local/share/icons/hicolor" ]; then
  gtk4-update-icon-cache -q -t -f "$HOME/.local/share/icons/hicolor"
fi
```

Then install the RPM that the helper created:

```bash
sudo dnf install "$HOME/rpmbuild/RPMS/x86_64/wardrobe-1.1.5-5.fc44.x86_64.rpm"
```

These steps remove the old application files, not user-installed themes in
`~/.local/share/icons` or `~/.local/share/themes`. Flatpak user data and
settings are also retained unless explicitly deleted.

Installing with `--prefix=/usr` can place files in system directories, but
running `sudo meson install` over files owned by an RPM/DEB is not a clean or
reliable replacement: the package manager can overwrite or remove those files
later. For a system-wide replacement, build a proper RPM/DEB that declares the
appropriate conflicts/replacements and installs the same application identity;
then remove the upstream package through its package manager before installing
the fork package. Keep the upstream IDs only if the intention is to replace it.
For side-by-side installation, give the fork distinct application, D-Bus,
schema, desktop, icon, and resource IDs first.

## Updating the fork

Changes can be committed and pushed to `https://github.com/FSA1/Wardrobe` from
VS Code's Source Control view, provided the GitHub account is authorized to
push to the fork. Pushing source changes does not update an already installed
copy: rebuild and run `meson install -C build` locally, or publish a new package
through whichever distribution channel is chosen. Open a pull request only
when intentionally proposing the changes to another repository.

## Contributing

Contributions to this fork are welcome. Please test changes with
`meson compile -C build` and `meson test -C build --print-errorlogs`.

---
