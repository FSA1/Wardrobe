# theme_page.py
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

import gi
import logging, os, shutil, hashlib
from gi.repository import Gtk, Adw, Pango, GLib, Gio
from .install_page import (
    backgrounds_dir,
    icon_roots,
    _scan_installed_wallpapers,
    _scan_local_theme_directories,
    theme_roots,
    add_theme,
    pop_theme,
    remove_external_paths,
    remove_installed_paths,
)
from .utils import ThemeSearch, create_theme_cell
from .theme_cell_flowbox import ThemeCellFlowbox

category_ids = {
    "GNOME Shell": 134,
    "Icons": 386,
    "GTK3/4": 366,
    "Cursors": 107,
    "Wallpapers": 261,
}

categories = {
    "GNOME Shell": "https://api.opendesktop.org/ocs/v1/content/data/?format=json&categories=134&sortmode=",
    "Icons": "https://api.opendesktop.org/ocs/v1/content/data/?format=json&categories=386&sortmode=",
    "GTK3/4": "https://api.opendesktop.org/ocs/v1/content/data/?format=json&categories=366&sortmode=",
    "Cursors": "https://api.opendesktop.org/ocs/v1/content/data/?format=json&categories=107&sortmode=",
    "Wallpapers": "https://api.opendesktop.org/ocs/v1/content/data/?format=json&categories=261&sortmode="
}

class ThemePage(Adw.NavigationPage):
    def __init__(self, view):
        super().__init__(title=_("Themes"))
        self.current_page = 0
        self.selected = "down"
        self.updating_group = False
        self.updating_controls = False
        self.updating_entry = False
        self.request_generation = 0
        self.search_timeout_id = 0
        self.search_controller = None
        self.installed_monitors = []
        self.installed_monitored_paths = set()
        self.installed_monitor_roots = ()
        self.installed_refresh_timeout_id = 0
        self.is_loading = False
        self.has_more = False
        content_box = Gtk.Box(vexpand=True, hexpand=True, orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.theme_flowbox = ThemeCellFlowbox()
        self.search_entry = Gtk.SearchEntry(
            placeholder_text=_("Search Themes"),
            hexpand=True,
            margin_start=12,
            margin_end=12,
        )
        self.search_entry.connect("search-changed", self.on_search_changed)
        content_box.append(self.search_entry)

        button_box = Adw.ToggleGroup(hexpand=True, halign=Gtk.Align.CENTER)
        self.theme_flowbox.group = button_box
        button_box.add_css_class("round")
        button_box.connect("notify::active", self.on_type_changed)
        content_box.append(button_box)
        self.order_group = button_box

        for sortmode in ["Popular", "Alphabetical", "Rating", "Latest"]:
            button = Adw.Toggle(label=_(sortmode), name=sortmode)
            button_box.add(button)

        self.order_group.set_active_name("Popular")
        installed_box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self.installed_button = Gtk.ToggleButton(hexpand=True)
        installed_content = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        installed_content.append(Gtk.Image(icon_name="folder-symbolic"))
        installed_content.append(Gtk.Label(label=_("Installed")))
        self.installed_button.set_child(installed_content)
        self.installed_button.add_css_class("pill")
        self.installed_button.connect("toggled", self.on_installed_toggled)
        installed_box.append(self.installed_button)
        content_box.append(installed_box)

        self.theme_flowbox.page = view
        self.loading_spinner = Gtk.Spinner(
            halign=Gtk.Align.CENTER,
            margin_top=24,
            margin_bottom=24,
            visible=False,
        )
        self.empty_label = Gtk.Label(
            label=_("No installed themes found"),
            valign=Gtk.Align.CENTER,
            vexpand=True,
            visible=False,
        )
        theme_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        theme_box.append(self.theme_flowbox)
        theme_box.append(self.empty_label)
        theme_box.append(self.loading_spinner)
        self.scroller = Gtk.ScrolledWindow(child=theme_box, hexpand=True, vexpand=True)
        self.scroller.get_vadjustment().connect(
            "value-changed", self.on_scroll_changed
        )
        content_box.append(self.scroller)
        self.set_child(content_box)

    def on_search_changed(self, entry):
        if self.updating_entry:
            return
        if self.selected == "installed":
            self.filter_installed_themes(entry.get_text())
            return
        self.schedule_category_search()

    def filter_installed_themes(self, query):
        query = query.strip().casefold()
        self.theme_flowbox.filter_by_text(query)
        has_matches = not query or any(
            query in " ".join(
                [
                    *theme.get("names", []),
                    *theme.get("authors", []),
                    *theme.get("variants", []),
                    str(theme.get("description", "")),
                ]
            ).casefold()
            for theme in self.installed_themes
        )
        self.empty_label.set_label(
            _("No installed themes found")
            if not self.installed_themes
            else _("No installed themes match your search")
        )
        self.empty_label.set_visible(not self.installed_themes or not has_matches)

    def cancel_category_search(self):
        if self.search_timeout_id:
            GLib.source_remove(self.search_timeout_id)
            self.search_timeout_id = 0
        if self.search_controller:
            self.search_controller.cancel()
            self.search_controller = None

    def schedule_category_search(self):
        self.cancel_category_search()
        self.request_generation += 1
        generation = self.request_generation
        self.current_page = 0
        query = self.search_entry.get_text().strip()
        self.theme_flowbox.filter_by_text("")
        self.theme_flowbox.remove_all()
        self.has_more = False
        self.set_loading(False)
        self.empty_label.set_visible(False)

        if not query:
            self.order_group.set_sensitive(False)
            self.theme_action(
                "new_page",
                categories[self.category] + self.selected + "&page=0",
            )
            return

        self.order_group.set_sensitive(True)
        self.search_timeout_id = GLib.timeout_add(
            350, self.start_category_search, generation, query
        )

    def start_category_search(self, generation, query):
        self.search_timeout_id = 0
        if generation != self.request_generation or self.selected == "installed":
            return GLib.SOURCE_REMOVE
        self.search_controller = ThemeSearch(
            query,
            [category_ids[self.category]],
            lambda items, has_more: self.on_category_results(
                items, has_more, generation
            ),
            lambda error: self.on_category_search_error(error, generation),
        )
        self.set_loading(True)
        self.search_controller.load_page(0)
        return GLib.SOURCE_REMOVE

    def on_category_results(self, items, has_more, generation):
        if generation != self.request_generation:
            return
        self.current_page = self.search_controller.result_page
        self.theme_flowbox.add_themes({"data": items})
        self.has_more = has_more
        self.set_loading(False)
        self.order_group.set_sensitive(True)
        self.empty_label.set_label(_("No themes found"))
        self.empty_label.set_visible(
            not items and self.current_page == 0
            and self.theme_flowbox.get_first_child() is None
        )
        GLib.idle_add(self.maybe_load_more)

    def on_category_search_error(self, error, generation):
        if generation != self.request_generation:
            return
        logging.error("Theme category search failed: %s", error)
        self.has_more = False
        self.set_loading(False)
        self.order_group.set_sensitive(True)
        self.empty_label.set_label(_("Search failed"))
        self.empty_label.set_visible(self.theme_flowbox.get_first_child() is None)
    
    def on_scroll_changed(self, adjustment):
        if adjustment.get_value() + adjustment.get_page_size() >= adjustment.get_upper() - 400:
            self.maybe_load_more()

    def maybe_load_more(self):
        adjustment = self.scroller.get_vadjustment()
        near_bottom = (
            adjustment.get_value() + adjustment.get_page_size()
            >= adjustment.get_upper() - 400
        )
        if near_bottom and self.has_more and not self.is_loading:
            self.load_next_page()
        return GLib.SOURCE_REMOVE

    def load_next_page(self):
        if not self.has_more or self.is_loading:
            return
        self.set_loading(True)
        if self.search_controller:
            self.search_controller.load_page(self.current_page + 1)
            return
        self.current_page += 1
        self.theme_action(
            "add_page",
            categories[self.category] + self.selected
            + f"&page={self.current_page}",
        )

    def set_loading(self, loading):
        self.is_loading = loading
        if loading:
            self.loading_spinner.set_visible(True)
            self.loading_spinner.start()
        else:
            self.loading_spinner.stop()
            self.loading_spinner.set_visible(False)

    def make_new_page(self):
        self.cancel_installed_monitors()
        self.cancel_category_search()
        self.request_generation += 1
        self.updating_group = True
        self.order_group.set_active_name("Popular")
        self.updating_group = False
        self.updating_controls = True
        self.installed_button.set_active(False)
        self.updating_controls = False
        self.selected = "down"
        self.updating_entry = True
        self.search_entry.set_text("")
        self.updating_entry = False
        self.theme_flowbox.filter_by_text("")
        self.theme_action("new_page", categories[self.category] + self.selected)

    def theme_action(self, action, url):
        if(action == "new_page"):
            self.current_page = 0
            self.has_more = False
            self.empty_label.set_visible(False)
            self.cancel_category_search()
            self.clean()
            self.request_generation += 1
        generation = self.request_generation
        self.set_loading(True)
        self.theme_flowbox.build_cells(
            url,
            lambda response: self.on_theme_response(response, generation),
            lambda error: self.on_theme_error(error, generation),
        )

    def on_theme_response(self, response, generation):
        if generation != self.request_generation or self.selected == "installed":
            return
        try:
            data = ThemeSearch._decode_response(response)
            items = data.get("data", [])
            total = int(data.get("totalitems", 0))
        except (TypeError, ValueError) as error:
            self.on_theme_error(error, generation)
            return
        self.theme_flowbox.add_themes(response)
        self.has_more = (
            (self.current_page + 1) * ThemeSearch.page_size < total
            if total
            else len(items) >= ThemeSearch.page_size
        )
        self.set_loading(False)
        self.order_group.set_sensitive(True)
        GLib.idle_add(self.maybe_load_more)

    def on_theme_error(self, error, generation):
        if generation != self.request_generation:
            return
        logging.error("Theme category load failed: %s", error)
        self.has_more = False
        self.set_loading(False)
        self.order_group.set_sensitive(True)
        self.empty_label.set_label(_("Could not load themes"))
        self.empty_label.set_visible(True)

    def clean(self):
        self.cancel_category_search()
        self.request_generation += 1
        self.theme_flowbox.remove_all()
        self.has_more = False
        self.set_loading(False)
        
    def on_type_changed(self, group, button):
        if self.updating_group or not hasattr(self, "category"):
            return
        self.cancel_installed_monitors()

        if self.installed_button.get_active():
            self.updating_controls = True
            self.installed_button.set_active(False)
            self.updating_controls = False
        label = group.get_active_name()
        self.cancel_category_search()
        self.request_generation += 1
        self.selected = {
            "Popular": "down",
            "Alphabetical": "alpha",
            "Rating": "high",
            "Latest": "new",
        }[label]
        self.current_page = 0
        self.updating_entry = True
        self.search_entry.set_text("")
        self.updating_entry = False
        self.theme_flowbox.filter_by_text("")
        group.set_sensitive(False)
        self.empty_label.set_visible(False)
        self.theme_action("new_page", categories[self.category] + self.selected + "&page=0")

    def on_installed_toggled(self, button):
        if self.updating_controls or not hasattr(self, "category"):
            return
        self.cancel_category_search()
        if button.get_active():
            self.selected = "installed"
            self.show_installed_themes()
            return
        self.cancel_installed_monitors()
        self.selected = "down"
        self.updating_group = True
        self.order_group.set_active_name("Popular")
        self.updating_group = False
        self.updating_entry = True
        self.search_entry.set_text("")
        self.updating_entry = False
        self.theme_flowbox.filter_by_text("")
        self.theme_action("new_page", categories[self.category] + self.selected + "&page=0")

    def show_installed_themes(self):
        self.cancel_installed_monitors()
        self.cancel_category_search()
        self.request_generation += 1
        self.current_page = 0
        self.has_more = False
        self.set_loading(False)
        self.order_group.set_sensitive(True)
        self.theme_flowbox.remove_all()
        if self.category == "Wallpapers":
            installed = _scan_installed_wallpapers()
        else:
            theme_type = {
                "GNOME Shell": 0,
                "Icons": 1,
                "GTK3/4": 2,
                "Cursors": 3,
            }[self.category]
            installed = _scan_local_theme_directories(theme_type)
        self.installed_themes = installed
        self.monitor_installed_directories(installed)

        generation = self.request_generation
        for theme in installed:
            card = self.make_installed_card(theme)
            self.theme_flowbox.append(card)
            if not theme.get("item"):
                self.lookup_installed_metadata(
                    theme,
                    card,
                    category_ids[self.category],
                    generation,
                )

        self.filter_installed_themes(self.search_entry.get_text())
        self.theme_flowbox.invalidate_filter()

    def cancel_installed_monitors(self):
        if self.installed_refresh_timeout_id:
            GLib.source_remove(self.installed_refresh_timeout_id)
            self.installed_refresh_timeout_id = 0
        for monitor in self.installed_monitors:
            monitor.cancel()
        self.installed_monitors.clear()
        self.installed_monitored_paths.clear()
        self.installed_monitor_roots = ()

    def monitor_installed_directories(self, installed):
        if self.category == "Wallpapers":
            roots = (backgrounds_dir,)
        elif self.category in ("GNOME Shell", "GTK3/4"):
            roots = theme_roots
        else:
            roots = icon_roots

        self.installed_monitor_roots = tuple(
            os.path.realpath(root) for root in roots if os.path.isdir(root)
        )
        directories = set(self.installed_monitor_roots)
        for theme in installed:
            path = theme.get("path")
            if isinstance(path, str):
                directory = path if os.path.isdir(path) else os.path.dirname(path)
                if os.path.isdir(directory):
                    directories.add(directory)

        for directory in directories:
            self.add_installed_directory_monitor(directory)

    def add_installed_directory_monitor(self, directory):
        absolute_path = os.path.abspath(directory)
        if absolute_path in self.installed_monitored_paths:
            return
        real_path = os.path.realpath(directory)
        try:
            if not any(
                os.path.commonpath((root, real_path)) == root
                for root in self.installed_monitor_roots
            ):
                return
        except ValueError:
            return
        if not os.path.isdir(directory):
            return
        try:
            monitor = Gio.File.new_for_path(directory).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES,
                None,
            )
        except GLib.Error as error:
            logging.warning(
                "Could not monitor installed resource directory %s: %s",
                directory,
                error,
            )
            return
        monitor.connect("changed", self.on_installed_directory_changed)
        self.installed_monitors.append(monitor)
        self.installed_monitored_paths.add(absolute_path)

    def on_installed_directory_changed(self, monitor, file, other_file, event):
        if self.selected != "installed":
            return
        changed_path = file.get_path() if file else None
        if changed_path and os.path.isdir(changed_path):
            self.add_installed_directory_monitor(changed_path)
        if self.installed_refresh_timeout_id:
            GLib.source_remove(self.installed_refresh_timeout_id)
        self.installed_refresh_timeout_id = GLib.timeout_add(
            500,
            self.refresh_installed_resources,
        )

    def refresh_installed_resources(self):
        self.installed_refresh_timeout_id = 0
        if self.selected == "installed":
            self.show_installed_themes()
        return GLib.SOURCE_REMOVE

    def lookup_installed_metadata(self, theme, card, category_id, generation):
        names = (
            list(theme["names"])
            + [os.path.basename(theme["path"])]
            + theme.get("authors", [])
        )
        queries = []
        seen = set()
        for name in names:
            query = name.strip()
            normalized = "".join(
                character for character in query.casefold() if character.isalnum()
            )
            if normalized and normalized not in seen and not query.isdecimal():
                seen.add(normalized)
                queries.append(query)
        if not queries:
            return

        def try_query(index):
            if (
                index >= len(queries)
                or generation != self.request_generation
                or self.selected != "installed"
            ):
                return

            search = ThemeSearch(
                queries[index],
                [category_id],
                lambda items, has_more: on_results(index, search, items, has_more),
                lambda error: on_error(index, error),
            )
            search.load_page(0)

        def on_error(index, error):
            logging.debug(
                "Could not look up installed theme %s on the server: %s",
                theme["path"],
                error,
            )
            try_query(index + 1)

        def on_results(index, search, items, has_more):
            aliases = {
                "".join(
                    character for character in name.casefold() if character.isalnum()
                )
                for name in theme["names"] + [os.path.basename(theme["path"])]
            }
            match = next(
                (
                    item
                    for item in items
                    if "".join(
                        character
                        for character in str(item.get("name", "")).casefold()
                        if character.isalnum()
                    )
                    in aliases
                ),
                None,
            )
            if match:
                metadata = dict(theme.get("metadata", {}))
                metadata.update({
                    "name": match.get("name", theme.get("display_name", "")),
                    "author": match.get("personid", ""),
                    "description": match.get("description", ""),
                    "category": theme.get("category"),
                    "source": theme.get("source", "external"),
                })
                try:
                    add_theme(
                        theme["tracking_key"],
                        theme["delete_paths"],
                        theme["theme_paths"],
                        metadata["name"],
                        match,
                        metadata,
                    )
                except (OSError, TypeError, ValueError) as error:
                    self.show_metadata_error(error)
                    return
                theme["item"] = match
                theme["metadata"] = metadata
                flowbox_child = card.get_parent()
                position = flowbox_child.get_index()
                self.theme_flowbox.remove(flowbox_child)
                self.theme_flowbox.insert(self.make_installed_card(theme), position)
                return
            if has_more and search.result_page < 2:
                search.load_page(search.result_page + 1)
            else:
                try_query(index + 1)

        try_query(0)

    def make_installed_card(self, theme):
        item = theme.get("item") if isinstance(theme.get("item"), dict) else {}
        metadata = theme.get("metadata", {})
        title = (
            metadata.get("name")
            or theme.get("display_name")
            or item.get("name")
            or (theme.get("names") or [os.path.basename(theme["path"])])[0]
        )
        item_for_cell = dict(item)
        item_for_cell.setdefault("name", title)
        item_for_cell.setdefault("personid", metadata.get("author") or ", ".join(theme.get("authors", [])))
        item_for_cell.setdefault("description", metadata.get("description") or theme.get("description", ""))
        item_for_cell.setdefault("typeid", category_ids[self.category])
        cell = create_theme_cell(item_for_cell, self.theme_flowbox.page)
        cell.title = title
        cell.dev = metadata.get("author") or ", ".join(theme.get("authors", [])) or item_for_cell.get("personid", _("Unknown"))
        cell.description = metadata.get("description") or item_for_cell.get("description", "")
        cell.local_preview_paths = [
            path
            for path in (metadata.get("preview_paths") or theme.get("preview_paths", []))
            if isinstance(path, str) and os.path.isfile(path)
        ]
        cell.resource_types = list(theme.get("resource_types", []))
        cell.search_aliases = list(theme.get("names", [])) + list(
            theme.get("variants", [])
        )
        cell.installed_theme = theme
        cell.is_installed_card = True
        cell.edit_installed_callback = self.edit_installed_metadata
        cell.remove_installed_callback = self.confirm_remove_installed
        cell.variant_names = list(theme.get("variants", []))
        cell.build_cell()
        return Adw.Clamp(child=cell, maximum_size=375)

    def show_metadata_error(self, error, parent=None):
        logging.error("Could not save installed theme metadata: %s", error)
        dialog = Adw.MessageDialog.new(
            parent or self.get_root(),
            _("Could not save metadata"),
            str(error),
        )
        dialog.add_response("close", _("Close"))
        dialog.present()

    def confirm_remove_installed(self, button, theme):
        dialog = Adw.MessageDialog.new(
            self.get_root(),
            _("Remove installed theme?"),
            _("This will remove the selected item from its user folder."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self.on_remove_installed_response, theme)
        dialog.present()

    def on_remove_installed_response(self, dialog, response, theme):
        if response != "remove":
            return

        theme_type = {
            "GNOME Shell": 0,
            "Icons": 1,
            "GTK3/4": 2,
            "Cursors": 3,
            "Wallpapers": 4,
        }[self.category]
        try:
            if theme.get("source") == "external":
                remove_external_paths(theme_type, theme["delete_paths"])
            else:
                remove_installed_paths(theme_type, theme["delete_paths"])
            if theme["tracking_key"]:
                pop_theme(theme["tracking_key"])
        except (OSError, ValueError) as error:
            logging.error("Could not remove installed theme %s: %s", theme["path"], error)
            error_dialog = Adw.MessageDialog.new(
                self.get_root(),
                _("Could not remove theme"),
                str(error),
            )
            error_dialog.add_response("close", _("Close"))
            error_dialog.present()
            return
        self.show_installed_themes()

    def edit_installed_metadata(self, theme):
        root = self.get_root()
        editor = Adw.Window(
            transient_for=root,
            modal=True,
            title=_("Edit Metadata"),
            default_width=620,
            default_height=760,
        )
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        toolbar = Adw.HeaderBar()
        cancel_button = Gtk.Button(label=_("Cancel"))
        save_button = Gtk.Button(label=_("Save"))
        save_button.add_css_class("suggested-action")
        toolbar.pack_start(cancel_button)
        toolbar.pack_end(save_button)
        outer.append(toolbar)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_start=18,
            margin_end=18,
            margin_top=12,
            margin_bottom=18,
        )
        scroller = Gtk.ScrolledWindow(child=content, vexpand=True)
        outer.append(scroller)
        editor.set_content(outer)

        metadata = dict(theme.get("metadata", {}))
        name_entry = Gtk.Entry(
            text=metadata.get("name")
            or theme.get("display_name")
            or (theme.get("names") or [os.path.basename(theme["path"])])[0],
            placeholder_text=_("Name"),
        )
        author_entry = Gtk.Entry(
            text=metadata.get("author") or ", ".join(theme.get("authors", [])),
            placeholder_text=_("Author"),
        )
        description_view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=8,
            bottom_margin=8,
            left_margin=8,
            right_margin=8,
        )
        description_view.get_buffer().set_text(
            metadata.get("description") or theme.get("description", "")
        )
        image_paths = [
            path
            for path in (metadata.get("preview_paths") or theme.get("preview_paths", []))
            if isinstance(path, str) and os.path.isfile(path)
        ]
        selected_image = {"path": ""}
        preview_label = Gtk.Label(
            label=os.path.basename(image_paths[0]) if image_paths else _("No preview image selected"),
            xalign=0,
            ellipsize=Pango.EllipsizeMode.MIDDLE,
            hexpand=True,
        )
        choose_image = Gtk.Button(label=_("Choose Preview Image"))
        image_row = Gtk.Box(spacing=8)
        image_row.append(preview_label)
        image_row.append(choose_image)

        content.append(Gtk.Label(label=_("Search the Wardrobe catalog"), xalign=0))
        search_row = Gtk.Box(spacing=8)
        catalog_search = Gtk.SearchEntry(
            placeholder_text=_("Search by theme name"),
            hexpand=True,
        )
        search_button = Gtk.Button(label=_("Search"))
        search_button.add_css_class("suggested-action")
        search_row.append(catalog_search)
        search_row.append(search_button)
        content.append(search_row)
        results_label = Gtk.Label(xalign=0, wrap=True)
        content.append(results_label)
        results_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        results_box.add_css_class("boxed-list")
        content.append(results_box)
        load_more = Gtk.Button(label=_("Load More Results"), halign=Gtk.Align.CENTER, visible=False)
        content.append(load_more)

        content.append(Gtk.Separator())
        content.append(Gtk.Label(label=_("Local Metadata"), xalign=0))
        content.append(Gtk.Label(label=_("Name"), xalign=0))
        content.append(name_entry)
        content.append(Gtk.Label(label=_("Author"), xalign=0))
        content.append(author_entry)
        content.append(Gtk.Label(label=_("Description"), xalign=0))
        description_frame = Gtk.Frame(child=description_view, height_request=120)
        content.append(description_frame)
        content.append(Gtk.Label(label=_("Preview image"), xalign=0))
        content.append(image_row)
        editor.set_content(outer)

        search_state = {"search": None, "page": 0, "has_more": False}

        def on_editor_close(window):
            if search_state["search"]:
                search_state["search"].cancel()
            return False

        editor.connect("close-request", on_editor_close)

        def append_candidates(items, has_more):
            for item in items:
                row = Adw.ActionRow(
                    title=str(item.get("name") or _("Unknown")),
                    subtitle=str(item.get("personid") or ""),
                )
                use_button = Gtk.Button(label=_("Use This Match"), valign=Gtk.Align.CENTER)
                use_button.connect("clicked", self.confirm_catalog_match, editor, theme, item)
                row.add_suffix(use_button)
                results_box.append(row)
            search_state["has_more"] = has_more
            load_more.set_visible(has_more)
            if not items and not has_more:
                results_label.set_text(_("No catalog matches found. You can edit the metadata below."))
            elif items:
                results_label.set_text(_("Select a matching item to review and confirm it."))

        def run_search(page):
            query = catalog_search.get_text().strip()
            if not query:
                results_label.set_text(_("Enter a name to search the catalog."))
                return
            if search_state["search"]:
                search_state["search"].cancel()
            if page == 0:
                results_box.remove_all()
            search_state["page"] = page
            search = ThemeSearch(
                query,
                [category_ids[self.category]],
                append_candidates,
                lambda error: results_label.set_text(
                    _("Catalog search failed: ") + str(error)
                ),
            )
            search_state["search"] = search
            search.load_page(page)
            results_label.set_text(_("Searching…"))

        def close_editor():
            if search_state["search"]:
                search_state["search"].cancel()
            editor.destroy()

        def on_search_clicked(button):
            run_search(0)

        def on_load_more(button):
            run_search(search_state["page"] + 1)

        def on_choose_image(button):
            chooser = Gtk.FileChooserNative.new(
                _("Choose a Preview Image"),
                editor,
                Gtk.FileChooserAction.OPEN,
                _("Open"),
                _("Cancel"),
            )
            image_filter = Gtk.FileFilter()
            image_filter.set_name(_("Image files"))
            for extension in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp", "*.gif", "*.tif", "*.tiff", "*.svg"):
                image_filter.add_pattern(extension)
            chooser.add_filter(image_filter)

            def on_file_chosen(dialog, response):
                if response == Gtk.ResponseType.ACCEPT:
                    file = dialog.get_file()
                    if file and file.get_path():
                        selected_image["path"] = file.get_path()
                        preview_label.set_text(os.path.basename(file.get_path()))
                dialog.destroy()

            chooser.connect("response", on_file_chosen)
            chooser.show()

        def on_save(button):
            display_name = name_entry.get_text().strip()
            if not display_name:
                results_label.set_text(_("Name is required."))
                return
            description_buffer = description_view.get_buffer()
            description = description_buffer.get_text(
                description_buffer.get_start_iter(),
                description_buffer.get_end_iter(),
                True,
            ).strip()
            preview_paths = list(image_paths)
            if selected_image["path"]:
                try:
                    preview_directory = os.path.join(
                        GLib.get_user_data_dir(), "wardrobe", "previews"
                    )
                    os.makedirs(preview_directory, exist_ok=True)
                    extension = os.path.splitext(selected_image["path"])[1].casefold()
                    destination = os.path.join(
                        preview_directory,
                        hashlib.sha256(theme["tracking_key"].encode()).hexdigest()[:20]
                        + extension,
                    )
                    shutil.copy2(selected_image["path"], destination)
                    preview_paths = [destination]
                except OSError as error:
                    logging.error("Could not save local preview image: %s", error)
                    results_label.set_text(_("Could not save preview image: ") + str(error))
                    return

            updated_metadata = {
                "source": theme.get("source", "external"),
                "category": theme.get("category"),
                "name": display_name,
                "author": author_entry.get_text().strip(),
                "description": description,
                "preview_paths": preview_paths,
                "variants": list(theme.get("variants", [])),
            }
            try:
                add_theme(
                    theme["tracking_key"],
                    theme["delete_paths"],
                    theme["theme_paths"],
                    display_name,
                    theme.get("item"),
                    updated_metadata,
                )
            except (OSError, TypeError, ValueError) as error:
                results_label.set_text(_("Could not save metadata: ") + str(error))
                self.show_metadata_error(error, editor)
                return
            theme["metadata"] = updated_metadata
            theme["display_name"] = display_name
            theme["names"] = [display_name] + [
                value for value in theme.get("names", []) if value != display_name
            ]
            theme["authors"] = (
                [updated_metadata["author"]] if updated_metadata["author"] else []
            )
            theme["description"] = description
            theme["preview_paths"] = preview_paths
            close_editor()
            self.show_installed_themes()

        search_button.connect("clicked", on_search_clicked)
        catalog_search.connect("activate", on_search_clicked)
        load_more.connect("clicked", on_load_more)
        choose_image.connect("clicked", on_choose_image)
        save_button.connect("clicked", on_save)
        cancel_button.connect("clicked", lambda *_: close_editor())
        editor.present()

    def confirm_catalog_match(self, button, editor, theme, item):
        dialog = Adw.MessageDialog.new(
            editor,
            _("Use this catalog match?"),
            str(item.get("name") or _("Unknown")),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("use", _("Use Match"))
        dialog.set_default_response("use")
        dialog.set_close_response("cancel")

        def on_response(message_dialog, response):
            if response != "use":
                return
            metadata = {
                "source": theme.get("source", "external"),
                "category": theme.get("category"),
                "name": str(item.get("name") or theme.get("display_name", "")),
                "author": str(item.get("personid") or ""),
                "description": str(item.get("description") or ""),
                "preview_paths": list(theme.get("metadata", {}).get("preview_paths") or []),
                "variants": list(theme.get("variants", [])),
            }
            try:
                add_theme(
                    theme["tracking_key"],
                    theme["delete_paths"],
                    theme["theme_paths"],
                    metadata["name"],
                    item,
                    metadata,
                )
            except (OSError, TypeError, ValueError) as error:
                self.show_metadata_error(error, editor)
                return
            theme["metadata"] = metadata
            theme["item"] = item
            theme["display_name"] = metadata["name"]
            theme["names"] = [metadata["name"]]
            theme["authors"] = [metadata["author"]] if metadata["author"] else []
            theme["description"] = metadata["description"]
            if search_state["search"]:
                search_state["search"].cancel()
            editor.destroy()
            self.show_installed_themes()

        dialog.connect("response", on_response)
        dialog.present()
