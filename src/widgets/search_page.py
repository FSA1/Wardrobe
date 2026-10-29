# search_page.py
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
import logging
from gi.repository import Gtk, Adw, GLib
from .utils import ThemeSearch
from .theme_cell_flowbox import ThemeCellFlowbox

class SearchPage(Adw.NavigationPage):
    def __init__(self, view):
        super().__init__(title=_("Search"), tag="search_page")
        content_box = Gtk.Box(vexpand=True, hexpand=True, orientation=Gtk.Orientation.VERTICAL, spacing=18)
        search_box = Gtk.Box()
        self.search_bar = Gtk.SearchEntry(placeholder_text=_("Search Themes"), hexpand=True, margin_start=5, margin_end=5)
        self.search_bar.connect("activate", self.search)
        self.search_bar.connect("search-changed", self.on_search_changed)
        self.current_page = -1
        self.search_generation = 0
        self.search_timeout_id = 0
        self.is_loading = False
        self.has_more = False
        self.search_controller = None
        self.make_filter()

        search_box.append(self.search_bar)
        content_box.append(Adw.Clamp(maximum_size=520, child=search_box))
        content_box.append(Adw.Clamp(maximum_size=720, child=self.filter_box))
        content_box.append(Adw.Clamp(maximum_size=720, child=self.filter_actions))
        self.no_filter_label = Gtk.Label(
            label=_("Select at least one category to search"),
            visible=False,
        )
        self.no_filter_label.add_css_class("dimmed")
        content_box.append(self.no_filter_label)
        content_box.append(Gtk.Separator())

        self.search_flowbox = ThemeCellFlowbox()
        self.search_flowbox.is_search_flowbox = True
        self.search_flowbox.page = view
        self.search_icon = Gtk.Image(icon_name="search-symbolic", pixel_size=160, valign=Gtk.Align.CENTER, vexpand=True, margin_bottom=12)
        self.search_icon.add_css_class("dimmed")
        self.search_flowbox.search_icon = self.search_icon
        self.loading_spinner = Gtk.Spinner(
            halign=Gtk.Align.CENTER,
            margin_top=24,
            margin_bottom=24,
            visible=False,
        )
        self.error_label = Gtk.Label(
            label=_("Search failed"),
            visible=False,
            margin_top=12,
            margin_bottom=12,
        )
        self.error_label.add_css_class("dimmed")
        scroll_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll_box.append(self.search_flowbox)
        scroll_box.append(self.error_label)
        scroll_box.append(self.loading_spinner)
        scroll_box.append(self.search_icon)
        self.scroller = Gtk.ScrolledWindow(child=scroll_box, vexpand=True, hexpand=True)
        self.scroller.get_vadjustment().connect(
            "value-changed", self.on_scroll_changed
        )
        content_box.append(self.scroller)
        self.set_child(content_box)

    def on_search_changed(self, entry):
        self.schedule_search()
    
    def schedule_search(self, immediate=False):
        if self.search_timeout_id:
            GLib.source_remove(self.search_timeout_id)
            self.search_timeout_id = 0

        self.search_generation += 1
        self.cancel_search_controller()
        self.is_loading = False
        self.has_more = False
        self.loading_spinner.stop()
        generation = self.search_generation
        if not self.search_bar.get_text().strip():
            self.search_flowbox.remove_all()
            self.current_page = -1
            self.loading_spinner.set_visible(False)
            self.error_label.set_visible(False)
            self.no_filter_label.set_visible(False)
            self.search_icon.set_from_icon_name("search-symbolic")
            self.search_icon.set_visible(True)
            return

        self.search_flowbox.remove_all()
        self.current_page = -1
        self.loading_spinner.set_visible(False)
        self.search_icon.set_visible(False)
        if not self.active_filters:
            self.no_filter_label.set_visible(True)
            return
        self.no_filter_label.set_visible(False)
        if immediate:
            self.request_page(0, generation)
        else:
            self.search_timeout_id = GLib.timeout_add(
                350, self.start_scheduled_search, generation
            )

    def start_scheduled_search(self, generation):
        self.search_timeout_id = 0
        if generation == self.search_generation:
            self.request_page(0, generation)
        return GLib.SOURCE_REMOVE

    def search(self, entry):
        self.schedule_search(immediate=True)

    def request_page(self, page, generation):
        if self.is_loading or generation != self.search_generation:
            return

        if self.search_controller is None:
            self.search_controller = ThemeSearch(
                self.search_bar.get_text(),
                self.active_filters,
                lambda items, has_more: self.on_search_results(
                    items, has_more, generation, page
                ),
                lambda error: self.on_search_error(error, generation),
            )
        else:
            self.search_controller.on_results = lambda items, has_more: self.on_search_results(
                items, has_more, generation, page
            )
            self.search_controller.on_error = lambda error: self.on_search_error(
                error, generation
            )

        self.is_loading = True
        self.loading_spinner.set_visible(True)
        self.loading_spinner.start()
        self.search_controller.load_page(page)

    def on_search_results(self, items, has_more, generation, page):
        if generation != self.search_generation:
            return
        self.is_loading = False
        self.current_page = page
        self.search_flowbox.add_themes({"data": items})
        self.has_more = has_more
        self.error_label.set_visible(False)
        self.loading_spinner.stop()
        self.loading_spinner.set_visible(False)
        no_results = (
            not items and self.search_flowbox.get_first_child() is None
        )
        self.search_icon.set_visible(no_results)
        if no_results:
            self.search_icon.set_from_icon_name("list-remove-symbolic")
        GLib.idle_add(self.maybe_load_more)

    def on_search_error(self, error, generation):
        if generation != self.search_generation:
            return

        self.is_loading = False
        logging.error("Theme search failed: %s", error)
        self.has_more = False
        self.error_label.set_visible(True)
        self.loading_spinner.stop()
        self.loading_spinner.set_visible(False)
        if self.search_flowbox.get_first_child() is None:
            self.search_icon.set_from_icon_name("dialog-warning-symbolic")
            self.search_icon.set_visible(True)

    def cancel_search_controller(self):
        if self.search_controller:
            self.search_controller.cancel()
            self.search_controller = None

    def on_scroll_changed(self, adjustment):
        if adjustment.get_value() + adjustment.get_page_size() >= adjustment.get_upper() - 400:
            self.maybe_load_more()

    def maybe_load_more(self):
        adjustment = self.scroller.get_vadjustment()
        near_bottom = (
            adjustment.get_value() + adjustment.get_page_size()
            >= adjustment.get_upper() - 400
        )
        if (
            near_bottom
            and self.has_more
            and not self.is_loading
            and self.search_bar.get_text().strip()
        ):
            self.request_page(self.current_page + 1, self.search_generation)
        return GLib.SOURCE_REMOVE

    def make_filter(self):
        self.active_filters = {134, 386, 366, 107, 261}
        self.filter_buttons = {}
        self.updating_filters = False
        self.filter_box = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            min_children_per_line=2,
            max_children_per_line=5,
            column_spacing=6,
            row_spacing=6,
            halign=Gtk.Align.CENTER,
        )
        for category, category_id, icon_name in [
            ("GNOME Shell", 134, "shell-symbolic"),
            ("Icons", 386, "icon-symbolic"),
            ("GTK3/4", 366, "interface-symbolic"),
            ("Cursors", 107, "cursor-symbolic"),
            ("Wallpapers", 261, "wallpaper-symbolic"),
        ]:
            content = Gtk.Box(
                spacing=8,
                margin_top=8,
                margin_bottom=8,
                margin_start=10,
                margin_end=10,
                halign=Gtk.Align.CENTER,
            )
            content.append(Gtk.Image(icon_name=icon_name, pixel_size=18))
            content.append(Gtk.Label(label=_(category)))
            button = Gtk.ToggleButton(child=content, active=True)
            button.category_id = category_id
            button.category_content = content
            button.add_css_class("pill")
            button.add_css_class(icon_name)
            button.connect("toggled", self.on_filter_toggled)
            self.filter_buttons[category_id] = button
            self.filter_box.append(button)

        self.filter_actions = Gtk.Box(
            spacing=8,
            halign=Gtk.Align.CENTER,
        )
        select_all_button = Gtk.Button(label=_("Select all"))
        select_all_button.add_css_class("flat")
        select_all_button.connect(
            "clicked", self.set_all_filters, True
        )
        clear_all_button = Gtk.Button(label=_("Clear all"))
        clear_all_button.add_css_class("flat")
        clear_all_button.connect(
            "clicked", self.set_all_filters, False
        )
        self.filter_actions.append(select_all_button)
        self.filter_actions.append(clear_all_button)

    def on_filter_toggled(self, button):
        button.category_content.set_opacity(1.0 if button.get_active() else 0.45)
        if self.updating_filters:
            return
        if button.get_active():
            self.active_filters.add(button.category_id)
        else:
            self.active_filters.discard(button.category_id)
        self.schedule_search()

    def set_all_filters(self, button, active):
        self.updating_filters = True
        self.active_filters = (
            set(self.filter_buttons) if active else set()
        )
        for category_id, filter_button in self.filter_buttons.items():
            filter_button.set_active(category_id in self.active_filters)
        self.updating_filters = False
        self.schedule_search()

    def clean(self):
        if self.search_timeout_id:
            GLib.source_remove(self.search_timeout_id)
            self.search_timeout_id = 0
        self.search_generation += 1
        self.cancel_search_controller()
        self.is_loading = False
        self.has_more = False
        self.loading_spinner.stop()
        self.loading_spinner.set_visible(False)
        self.search_flowbox.remove_all()
