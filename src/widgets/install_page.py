# install_page.py
#
# Copyright 2026 Nathan Perlman
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

import gi, os, shutil, webbrowser, json, gc, configparser, logging, time
from html.parser import HTMLParser
from gi.repository import Gtk, Gdk, Adw, Gio, GLib, Soup, Xdp, XdpGtk4
from .utils import match_theme_type, soup_get, soup_get_bytes, resolve_issues, strip_html, destroy_symlinks
from .item_carousel import ItemCarousel
from datetime import datetime, timezone

data_home = os.path.join(GLib.get_home_dir(), ".local", "share")
picture_dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)

folders = {
    0: f"{os.path.join(data_home, 'themes', 'wardrobe-installs')}",
    1: f"{os.path.join(data_home, 'icons', 'wardrobe-installs')}",
    2: f"{os.path.join(data_home, 'themes', 'wardrobe-installs')}",
    3: f"{os.path.join(data_home, 'icons', 'wardrobe-installs')}",
    4: f"{picture_dir}/",
    5: f"{GLib.get_user_data_dir()}"
}

json_path = os.path.join(folders[5], "installed.json")

def get_installed_themes():
    try:
        with open(json_path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        logging.warning("Could not read installed theme list %s: %s", json_path, error)
        return {}
    if not isinstance(data, dict):
        logging.warning("Installed theme list %s does not contain a JSON object", json_path)
        return {}
    return data

def add_theme(name, delete_paths, theme_paths, display_name, item=None):
    data = get_installed_themes()
    data[name] = (list(delete_paths), list(theme_paths), display_name, item)
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)

def pop_theme(key):
    data = get_installed_themes()
    data.pop(key, None)
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)

def _normalized_theme_name(name):
    return "".join(character for character in name.casefold() if character.isalnum())

def _download_stem(name):
    stem = name.casefold()
    for extension in (".tar.gz", ".tar.xz", ".tar.bz2", ".zip", ".tgz", ".tar"):
        if stem.endswith(extension):
            return name[:-len(extension)]
    return os.path.splitext(name)[0]

def _read_index_theme_names(index_path):
    parser = configparser.RawConfigParser()
    try:
        parser.read(index_path, encoding="utf-8")
    except (OSError, UnicodeError, configparser.Error) as error:
        logging.warning("Could not read theme metadata %s: %s", index_path, error)
        return []

    names = []
    for section in ("Icon Theme", "Desktop Entry", "X-GNOME-Metatheme", "Theme"):
        if parser.has_option(section, "Name"):
            names.append(parser.get(section, "Name"))
    return names

def _read_theme_authors(directory):
    for filename in ("AUTHORS", "AUTHORS.txt", "AUTHORS.md"):
        path = os.path.join(directory, filename)
        try:
            with open(path, encoding="utf-8") as authors_file:
                return [
                    line.strip()
                    for line in authors_file
                    if line.strip() and not line.lstrip().startswith("#")
                ]
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError) as error:
            logging.warning("Could not read theme authors %s: %s", path, error)
            return []
    return []

def _scan_local_theme_directories(theme_type):
    if theme_type in (0, 2):
        roots = (os.path.join(data_home, "themes"),)
    elif theme_type in (1, 3):
        roots = (os.path.join(data_home, "icons"),)
    else:
        return []

    themes = []
    tracked_groups = {}
    tracked_themes = get_installed_themes()
    for root in roots:
        if not os.path.isdir(root):
            continue

        try:
            entries = os.scandir(root)
        except OSError as error:
            logging.warning("Could not scan local theme directory %s: %s", root, error)
            continue

        with entries:
            for entry in entries:
                if os.path.abspath(entry.path) == os.path.abspath(folders[theme_type]):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=True):
                        continue
                except OSError as error:
                    logging.warning("Could not inspect local theme %s: %s", entry.path, error)
                    continue

                theme_names = [entry.name]
                authors = _read_theme_authors(entry.path)
                has_theme_files = False
                has_index_theme = False
                has_cursors = False
                has_gtk_theme = False
                has_shell_theme = False

                def on_walk_error(error):
                    logging.warning("Could not inspect local theme files: %s", error)

                for directory, subdirectories, files in os.walk(
                    entry.path, followlinks=False, onerror=on_walk_error
                ):
                    if "index.theme" in files:
                        has_index_theme = True
                        theme_names.extend(
                            _read_index_theme_names(os.path.join(directory, "index.theme"))
                        )
                    has_cursors = has_cursors or any(
                        name in subdirectories for name in ("cursors", "cursors_scalable")
                    )
                    has_gtk_theme = has_gtk_theme or any(
                        name in subdirectories
                        for name in ("gtk-2.0", "gtk-3.0", "gtk-4.0", "cinnamon", "xfwm4")
                    )
                    has_shell_theme = has_shell_theme or "gnome-shell" in subdirectories
                    if (
                        (theme_type == 0 and has_shell_theme)
                        or (theme_type == 2 and has_gtk_theme)
                        or (theme_type == 1 and has_index_theme and not has_cursors)
                        or (theme_type == 3 and has_cursors)
                    ):
                        break

                if theme_type == 0:
                    has_theme_files = has_shell_theme
                elif theme_type == 2:
                    has_theme_files = has_gtk_theme
                elif theme_type == 1:
                    has_theme_files = has_index_theme and not has_cursors
                elif theme_type == 3:
                    has_theme_files = has_cursors

                if has_theme_files:
                    target_path = os.path.realpath(entry.path)
                    tracked_key = None
                    tracked_record = None
                    for tracked_name, tracked in tracked_themes.items():
                        if not isinstance(tracked, (list, tuple)) or len(tracked) < 2:
                            continue
                        tracked_paths = tracked[1]
                        if not isinstance(tracked_paths, (list, tuple)):
                            continue
                        if any(
                            isinstance(path, str)
                            and (path == entry.path or os.path.realpath(path) == target_path)
                            for path in tracked_paths
                        ):
                            tracked_key = tracked_name
                            tracked_record = tracked
                            break

                    if tracked_key:
                        group = tracked_groups.get(tracked_key)
                        if group is None:
                            tracked_delete_paths = tracked_record[0]
                            tracked_theme_paths = tracked_record[1]
                            group = {
                                "path": entry.path,
                                "names": [],
                                "delete_paths": [
                                    path for path in tracked_delete_paths
                                    if isinstance(path, str) and os.path.lexists(path)
                                ] if isinstance(tracked_delete_paths, (list, tuple)) else [],
                                "theme_paths": [
                                    path for path in tracked_theme_paths
                                    if isinstance(path, str) and os.path.lexists(path)
                                ] if isinstance(tracked_theme_paths, (list, tuple)) else [],
                                "tracking_key": tracked_key,
                                "authors": [],
                                "item": (
                                    tracked_record[3]
                                    if len(tracked_record) > 3
                                    and isinstance(tracked_record[3], dict)
                                    else None
                                ),
                                "display_name": (
                                    tracked_record[2]
                                    if len(tracked_record) > 2
                                    and isinstance(tracked_record[2], str)
                                    else entry.name
                                ),
                            }
                            for path in group["theme_paths"]:
                                if path not in group["delete_paths"]:
                                    group["delete_paths"].append(path)
                            if group["display_name"]:
                                group["names"].append(group["display_name"])
                            tracked_groups[tracked_key] = group
                            themes.append(group)
                        for name in theme_names:
                            if name not in group["names"]:
                                group["names"].append(name)
                        for author in authors:
                            if author not in group["authors"]:
                                group["authors"].append(author)
                        continue

                    delete_paths = [entry.path]
                    if os.path.islink(entry.path):
                        target = target_path
                        managed_root = os.path.realpath(folders[theme_type])
                        try:
                            is_managed_target = os.path.commonpath((managed_root, target)) == managed_root
                        except ValueError:
                            is_managed_target = False
                        if is_managed_target and os.path.isdir(target):
                            delete_paths.append(target)
                    themes.append({
                        "path": entry.path,
                        "names": theme_names,
                        "delete_paths": delete_paths,
                        "tracking_key": tracked_key,
                        "authors": authors,
                        "item": None,
                        "display_name": entry.name,
                    })

    return themes

def _scan_installed_wallpapers():
    if not picture_dir:
        return []
    picture_root = os.path.realpath(picture_dir)
    wallpapers = []
    for name, installed in get_installed_themes().items():
        if not isinstance(installed, (list, tuple)) or len(installed) < 2:
            continue
        delete_paths = installed[0]
        theme_paths = installed[1]
        if not isinstance(delete_paths, (list, tuple)) or not isinstance(theme_paths, (list, tuple)):
            continue
        image_paths = []
        for path in theme_paths:
            if not isinstance(path, str) or not os.path.isfile(path):
                continue
            try:
                if os.path.commonpath((picture_root, os.path.realpath(path))) != picture_root:
                    continue
            except ValueError:
                continue
            image_paths.append(path)

        if not image_paths:
            continue

        display_name = installed[2] if len(installed) > 2 else name
        preview_tokens = ("preview", "screenshot", "thumbnail", "thumb")
        wallpaper_path = next(
            (
                path for path in image_paths
                if not any(token in os.path.basename(path).casefold() for token in preview_tokens)
            ),
            image_paths[0],
        )
        managed_paths = [
            path for path in delete_paths
            if isinstance(path, str) and os.path.lexists(path)
        ]
        managed_paths.extend(path for path in image_paths if path not in managed_paths)
        wallpapers.append({
            "path": wallpaper_path,
            "names": [display_name],
            "delete_paths": managed_paths,
            "tracking_key": name,
            "authors": [],
            "item": installed[3] if len(installed) > 3 and isinstance(installed[3], dict) else None,
        })
    return wallpapers

def remove_installed_paths(theme_type, delete_paths, cleanup_symlinks=False):
    for item in delete_paths:
        if os.path.islink(item):
            os.unlink(item)
        elif os.path.isdir(item):
            shutil.rmtree(item)
        elif os.path.exists(item):
            os.remove(item)
    if cleanup_symlinks and theme_type in (0, 1, 2, 3):
        destroy_symlinks(folders[theme_type])

def find_installed_theme(
    download_name,
    display_name,
    tracked_themes,
    local_themes,
    package_names=(),
    item=None,
):
    def get_tracked_install(record):
        if not isinstance(record, (list, tuple)) or len(record) < 2:
            return None
        if not isinstance(record[0], (list, tuple)) or not isinstance(record[1], (list, tuple)):
            return None
        delete_paths = [
            path for path in record[0]
            if isinstance(path, str) and os.path.lexists(path)
        ]
        theme_paths = [
            path for path in record[1]
            if isinstance(path, str) and os.path.exists(path)
        ]
        if not theme_paths:
            return None
        return {
            "delete_paths": list(dict.fromkeys(delete_paths + theme_paths)),
            "theme_paths": theme_paths,
        }

    tracked = tracked_themes.get(download_name)
    tracked_install = get_tracked_install(tracked)
    if tracked_install:
        return tracked_install

    package_names = set(package_names)
    item_name = item.get("name") if isinstance(item, dict) else None
    item_type = str(item.get("typeid")) if isinstance(item, dict) else None
    for tracked_name, record in tracked_themes.items():
        if tracked_name == download_name:
            continue
        if not get_tracked_install(record):
            continue
        installed_item = record[3] if len(record) > 3 else None
        same_item = (
            isinstance(installed_item, dict)
            and item_name
            and item_type
            and installed_item.get("name") == item_name
            and str(installed_item.get("typeid")) == item_type
        )
        if same_item or tracked_name in package_names:
            return None

    requested_names = {
        _normalized_theme_name(display_name),
        _normalized_theme_name(_download_stem(download_name)),
    }
    requested_names.discard("")
    for theme in local_themes:
        if requested_names.intersection(
            _normalized_theme_name(name) for name in theme["names"]
        ):
            return {
                "delete_paths": [theme["path"]],
                "theme_paths": [theme["path"]],
            }
    return None

class InstallPage(Adw.NavigationPage):
    def __init__(self, theme_button):
        super().__init__(title=theme_button.title, vexpand=True, hexpand=True)
        self.signal_ids = []
        self.theme_display_name = theme_button.title

        links = theme_button.download_links
        names = theme_button.download_names
        images = theme_button.image_urls
        description = theme_button.description
        content_box = Gtk.Box(vexpand=True, hexpand=True, orientation=Gtk.Orientation.VERTICAL, spacing=18)
        scroll = Gtk.ScrolledWindow(child=content_box)
        self.theme_type = match_theme_type(theme_button.theme_type)
        self.supports_installation = self.theme_type in range(5)
        self.set_tag("install_page")

        top_box = Gtk.Box(hexpand=True, halign=Gtk.Align.CENTER, spacing=125)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=12, spacing=8)
        title = Gtk.Label(margin_start=24, label=theme_button.title, wrap=True, halign=Gtk.Align.START)
        title.add_css_class("title-3")
        dev_label = Gtk.Label(margin_start=24, label=_("By: ") + theme_button.dev, halign=Gtk.Align.START)
        dev_label.set_css_classes(["title-4", "dimmed"])
        web_button = Gtk.Button(icon_name="web-symbolic", valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, margin_end=24)
        web_button.set_size_request(45, 45)
        web_button.set_css_classes(["circular", "suggested-action"])
        self.connect_button(web_button, "clicked", lambda x: webbrowser.open(theme_button.home_page))

        title_box.append(title)
        title_box.append(dev_label)
        top_box.append(title_box)
        top_box.append(web_button)
        content_box.append(top_box)
        info_box = Gtk.Box(hexpand=True, margin_start=6, margin_end=6)
        info_box.set_css_classes(["card"])
        content_box.append(Adw.Clamp(child=info_box, maximum_size=750))

        download = theme_button.downloads
        score = f"{theme_button.rating}/10"
        now = datetime.now(timezone.utc)
        try:
            updated_at = datetime.fromisoformat(
                theme_button.last_update.replace("Z", "+00:00")
            )
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            changed = updated_at.strftime("%m/%d/%Y")
            days_since = (now - updated_at).days
        except (AttributeError, TypeError, ValueError):
            changed = _("Unknown")
            days_since = None

        items = [("Downloads", download, "⤓"), ("Rating", score, "★"), ("Last Updated", changed, "⟳")]

        for tag, info, icon_name in items:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)

            icon = Gtk.Label(label=icon_name, margin_top=8)
            icon.add_css_class("title-4")

            info_label = Gtk.Label(label=str(info), xalign=0.5, hexpand=True)
            info_label.add_css_class("bold")

            tag_label = Gtk.Label(label=_(tag), xalign=0.5, margin_bottom=8)
            tag_label.add_css_class("dimmed")
            
            card.append(icon)
            card.append(info_label)
            card.append(tag_label)

            if(tag == "Downloads"):
                info_label.add_css_class("success" if int(theme_button.downloads) >= 1000 else "error")
            elif(tag == "Rating"):
                if(theme_button.rating > 7.0):
                    info_label.add_css_class("success")
                elif(theme_button.rating > 5.0):
                    info_label.add_css_class("warning")
                else:
                    info_label.add_css_class("error")
            elif(tag == "Last Updated"):
                if days_since is not None:
                    info_label.add_css_class("error" if days_since >= 365 else "success")

            info_box.append(card)
            if(tag != "Last Updated"):
                info_box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_top=8, margin_bottom=8))

        
        content_box.append(Gtk.Separator())

        if(len(images) >= 3):
            version = "infinite"
        elif(len(images) == 2):
            version = "with_buttons"
        else:
            version = "no_buttons"

        self.item_carousel = ItemCarousel(self.connect_button, version)
        self.make_carousel_images(images, self.item_carousel.carousel)
        
        content_box.append(Adw.Clamp(child=self.item_carousel, maximum_size=900, height_request=350, margin_start=12, margin_end=12))
        
        content_box.append(Gtk.Separator())
        DESCRIPTION_CHAR_LIMIT = 300

        stripped = strip_html(description)
        is_long = len(stripped) > DESCRIPTION_CHAR_LIMIT

        description_label = Gtk.Label(label=stripped if not is_long else stripped[:DESCRIPTION_CHAR_LIMIT] + "…", wrap=True, selectable=True, margin_start=12, margin_end=12)
        description_label.set_css_classes(["description-label"])
        content_box.append(Adw.Clamp(child=description_label, maximum_size=900))

        if(is_long):
            read_more_button = Gtk.Button(label=_("Show More"), halign=Gtk.Align.CENTER)
            read_more_button.set_css_classes(["pill"])
            self.expanded = False

            def on_read_more_clicked(button):
                if(not self.expanded):
                    description_label.set_label(stripped)
                    button.set_label(_("Show Less"))
                    self.expanded = True
                else:
                    description_label.set_label(stripped[:DESCRIPTION_CHAR_LIMIT] + "…")
                    button.set_label(_("Read more"))
                    self.expanded = False

            self.connect_button(read_more_button, "clicked", on_read_more_clicked)
            content_box.append(read_more_button)
        content_box.append(Gtk.Separator())

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        label = Gtk.Label(label=_("Save Folder"))
        icon = Gtk.Image.new_from_icon_name("folder-open-symbolic")
        box.append(label); box.append(icon)
        open_folder_button = Gtk.Button(child=box, halign=Gtk.Align.CENTER)
        open_folder_button.set_css_classes(["suggested-action", "pill"])
        if self.supports_installation:
            folder = Gio.File.new_for_path(folders[self.theme_type])
            self.connect_button(open_folder_button, "clicked", lambda d: Gio.AppInfo.launch_default_for_uri(folder.get_uri(), None))
            content_box.append(open_folder_button)

        download_list = Gtk.ListBox(hexpand=True, margin_start=24, margin_end=24, margin_bottom=24)
        download_list.add_css_class("boxed-list")

        link_row = Gtk.ListBoxRow()
        link_row.set_child(Gtk.LinkButton(label=_("Become a supporter"), uri="https://www.pling.com/support"))
        download_list.append(link_row)

        if self.supports_installation:
            tracked_themes = get_installed_themes()
            local_themes = _scan_local_theme_directories(self.theme_type)
            for name, link in zip(names, links):
                row = Adw.ActionRow(title=name)
                suffix_box = Gtk.Box()
                installed = find_installed_theme(
                    name,
                    theme_button.title,
                    tracked_themes,
                    local_themes,
                    theme_button.download_names,
                    getattr(theme_button, "item", None),
                )
                if installed:
                    suffix_button = Gtk.Button(valign=Gtk.Align.CENTER)
                    suffix_button.add_css_class("destructive-action")
                    self.set_action_button(suffix_button, _("Remove"), "delete-symbolic")
                    suffix_button.set_tooltip_text(_("Installed; remove this theme"))
                    self.connect_button(suffix_button, "clicked", self.delete_theme, installed["delete_paths"])
                    use_button = self.set_use_menu_button(installed["theme_paths"])
                    suffix_box.append(use_button)
                else:
                    suffix_button = Gtk.Button(valign=Gtk.Align.CENTER)
                    suffix_button.add_css_class("suggested-action")
                    self.set_action_button(suffix_button, _("Install"), "download-symbolic")
                    suffix_button.set_tooltip_text(_("Install this theme"))
                    self.connect_button(suffix_button, "clicked", self.install_theme, link, name, self.theme_type)
                suffix_box.append(suffix_button)
                suffix_button.add_css_class("circular")
                suffix_button.link = link
                suffix_button.name = name
                suffix_button.item = getattr(theme_button, "item", {})
                suffix_button.installing = False
                suffix_button.install_complete = bool(installed)
                row.add_suffix(suffix_box)
                download_list.append(row)
        else:
            download_list.append(
                Adw.ActionRow(title=_("Installation is not supported for this item"))
            )
            
        content_box.append(Adw.Clamp(child=download_list, maximum_size=600))
        self.set_child(scroll)

    def connect_button(self, widget, signal, handler, *args):
        signal_id = widget.connect(signal, handler, *args)
        self.signal_ids.append((widget, signal_id, handler))
        return signal_id

    def disconnect_button_handler(self, widget, handler):
        for connection in tuple(self.signal_ids):
            connected_widget, signal_id, connected_handler = connection
            if connected_widget is widget and connected_handler == handler:
                if widget.handler_is_connected(signal_id):
                    widget.disconnect(signal_id)
                self.signal_ids.remove(connection)

    def set_action_button(self, button, label, icon_name):
        content = Gtk.Box(spacing=6)
        content.append(Gtk.Image(icon_name=icon_name))
        content.append(Gtk.Label(label=label))
        button.set_child(content)

    def clean(self):
        for widget, signal_id, _ in self.signal_ids:
            if widget.handler_is_connected(signal_id):
                widget.disconnect(signal_id)
        self.signal_ids.clear()
        self.item_carousel.clean()
        gc.collect()

    def make_carousel_images(self, images, carousel):
        if not images:
            return

        loaded_images = 0

        def scroll_to_middle():
            carousel.scroll_to(carousel.get_nth_page(len(images) // 2), True)
            
        def append_picture(image_bytes=None, error=None):
            nonlocal loaded_images
            try:
                if error:
                    raise error
                texture = Gdk.Texture.new_from_bytes(image_bytes)
                picture = Gtk.Picture.new_for_paintable(texture)
                picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            except (GLib.Error, RuntimeError, ValueError) as error:
                logging.warning("Could not load detail preview: %s", error)
                picture = Gtk.Image(
                    icon_name="image-missing-symbolic",
                    pixel_size=96,
                )
            carousel.append(picture)
            loaded_images += 1
            if loaded_images == len(images) and len(images) >= 3:
                GLib.idle_add(scroll_to_middle)

        for image in images:
            soup_get_bytes(
                image,
                lambda image_bytes: append_picture(image_bytes=image_bytes),
                lambda error: append_picture(error=error),
            )

    def change_button_to_download(self, button):
        self.disconnect_button_handler(button, self.delete_theme)
        button.remove_css_class("destructive-action")
        button.add_css_class("suggested-action")
        self.set_action_button(button, _("Install"), "download-symbolic")
        button.set_tooltip_text(_("Install this theme"))
        self.connect_button(button, "clicked", self.install_theme, button.link, button.name, self.theme_type)
        button.install_complete = False

        first_child = button.get_parent().get_first_child()
        if(first_child != button):
            button.get_parent().remove(first_child)

    def set_use_menu_button(self, themes):
        if(self.theme_type != 0):
            use_button = Gtk.MenuButton(label=(_("Use")), margin_end=8, halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
            items = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            popover = Gtk.Popover()
            popover.set_child(items)
            for item in themes:
                if(self.theme_type != 4):
                    theme = Gtk.Button(label=os.path.basename(item))
                else:
                    theme = Gtk.Button(label=item)
                theme.add_css_class("flat")
                self.connect_button(theme, "clicked", self.set_theme)
                items.append(theme)
            use_button.set_popover(popover)
            return use_button
        return Gtk.Box()

    def install_theme(self, button, url, name, typeid):
        if button.installing or button.install_complete:
            return
        button.installing = True
        install_started = time.monotonic()
        download_started = install_started
        logging.info("Install download started: name=%s url=%s", name, url)

        def on_install_error(error):
            button.installing = False
            logging.error("Could not install %s: %s", name, error)
            self.set_action_button(button, _("Install"), "download-symbolic")
            button.set_tooltip_text(str(error))

        def change_button_to_delete(delete_paths, theme_paths):
            if button.install_complete:
                return
            button.installing = False
            button.install_complete = True
            self.disconnect_button_handler(button, self.install_theme)
            button.remove_css_class("suggested-action")
            button.add_css_class("destructive-action")
            self.set_action_button(button, _("Remove"), "delete-symbolic")
            button.set_tooltip_text(_("Installed; remove this theme"))
            self.connect_button(button, "clicked", self.delete_theme, delete_paths)

            use_button = self.set_use_menu_button(theme_paths)
            button.get_parent().prepend(use_button)
            add_theme(
                button.name,
                delete_paths,
                theme_paths,
                self.theme_display_name,
                button.item,
            )
            logging.info(
                "Install completed: name=%s total_elapsed=%.2fs",
                name,
                time.monotonic() - install_started,
            )

        def on_download(response):
            downloaded_at = time.monotonic()
            logging.info(
                "Install download finished: name=%s bytes=%d elapsed=%.2fs",
                name,
                len(response),
                downloaded_at - download_started,
            )
            download_path = os.path.join(folders[self.theme_type], name)
            try:
                os.makedirs(folders[self.theme_type], exist_ok=True)
                write_started = time.monotonic()
                with open(download_path, "wb") as file:
                    file.write(response)
            except OSError as error:
                on_install_error(error)
                return
            logging.info(
                "Install archive saved: name=%s elapsed=%.2fs",
                name,
                time.monotonic() - write_started,
            )
            resolve_issues(download_path, typeid, change_button_to_delete)

        button.set_child(Gtk.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, spinning=True))
        soup_get(url, on_download, on_install_error)

    def delete_theme(self, button, delete_paths):
        remove_installed_paths(self.theme_type, delete_paths)
        pop_theme(button.name)
        self.change_button_to_download(button)
                
    def set_theme(self, button):
        interface_settings = Gio.Settings(schema_id="org.gnome.desktop.interface")
        chosen_item = button.get_label()
        match self.theme_type:
            case 0:
                print("feature currently unavailable")
            case 1:
                interface_settings.set_string("icon-theme", os.path.basename(chosen_item))
            case 2:
                interface_settings.set_string("gtk-theme", os.path.basename(chosen_item))
            case 3:
                interface_settings.set_string("cursor-theme", os.path.basename(chosen_item))
            case 4:
                portal = Xdp.Portal()
                parent = XdpGtk4.parent_new_gtk(self.get_root())
                portal.set_wallpaper(
                    parent,
                    f"file://{chosen_item}",
                    Xdp.WallpaperFlags.PREVIEW | Xdp.WallpaperFlags.BACKGROUND | Xdp.WallpaperFlags.LOCKSCREEN
                )
