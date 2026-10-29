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
import logging, os
from gi.repository import Gtk, Adw, Pango, GLib
from .install_page import (
    _scan_installed_wallpapers,
    _scan_local_theme_directories,
    pop_theme,
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
            self.theme_flowbox.filter_by_text(entry.get_text())
            return
        self.schedule_category_search()

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

        query = self.search_entry.get_text().strip().casefold()
        has_matches = not query or any(
            query in name.casefold()
            for theme in installed
            for name in theme["names"]
        )
        self.theme_flowbox.filter_by_text(query)
        if not installed:
            self.empty_label.set_label(_("No installed themes found"))
        elif not has_matches:
            self.empty_label.set_label(_("No installed themes match your search"))
        self.empty_label.set_visible(not installed or not has_matches)
        self.theme_flowbox.invalidate_filter()

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
                theme["item"] = match
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
        if isinstance(theme.get("item"), dict):
            cell = create_theme_cell(theme["item"], self.theme_flowbox.page)
            cell.build_cell()
            return Adw.Clamp(child=cell, maximum_size=375)

        card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
            width_request=300,
            height_request=110,
        )
        card.add_css_class("card")
        card.title = theme["names"][1] if len(theme["names"]) > 1 else theme["names"][0]

        title = Gtk.Label(label=card.title, xalign=0, wrap=True, hexpand=True)
        title.add_css_class("heading")
        location = Gtk.Label(
            label=os.path.basename(theme["path"]),
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            selectable=True,
        )
        location.add_css_class("dimmed")
        remove_button = self.make_remove_button(theme)
        card.append(title)
        card.append(location)
        card.append(remove_button)
        return Adw.Clamp(child=card, maximum_size=375)

    def make_remove_button(self, theme):
        remove_button = Gtk.Button(halign=Gtk.Align.END)
        remove_box = Gtk.Box(spacing=6)
        remove_box.append(Gtk.Image(icon_name="user-trash-symbolic"))
        remove_box.append(Gtk.Label(label=_("Remove")))
        remove_button.set_child(remove_box)
        remove_button.add_css_class("destructive-action")
        remove_button.connect("clicked", self.confirm_remove_installed, theme)
        return remove_button

    def confirm_remove_installed(self, button, theme):
        dialog = Adw.MessageDialog.new(
            self.get_root(),
            _("Remove installed theme?"),
            _("This will remove the selected theme from your user data."),
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
            remove_installed_paths(theme_type, theme["delete_paths"])
            if theme["tracking_key"]:
                pop_theme(theme["tracking_key"])
        except OSError as error:
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
