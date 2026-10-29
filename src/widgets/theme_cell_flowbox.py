# theme_cell_flowbox.py
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

import gi, json
from gi.repository import Gtk, GLib, Adw
from .utils import soup_get, parse_json

class ThemeCellFlowbox(Gtk.FlowBox):
    def __init__(self):
        super().__init__(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, column_spacing=12, row_spacing=12, halign=Gtk.Align.CENTER, margin_bottom=12)
        self.search_query = ""
        self.set_filter_func(self.filter_theme, None)

    def filter_by_text(self, text):
        self.search_query = text.strip().casefold()
        self.invalidate_filter()

    def filter_theme(self, child, user_data):
        if not self.search_query:
            return True

        widget = child.get_child()
        while widget is not None and not hasattr(widget, "title"):
            widget = widget.get_first_child()

        if widget is None:
            return False

        searchable_text = f"{widget.title} {getattr(widget, 'dev', '')}".casefold()
        return self.search_query in searchable_text
        
    def add_themes(self, response):
        parse_json(response, self)

    def build_cells(self, url, response_handler=None, error_handler=None):
        soup_get(url, response_handler or self.add_themes, error_handler)
        
    def remove_all(self):
        def destroy_cells(widget):
            if hasattr(widget, "destroy_cell"):
                widget.destroy_cell()
            child = widget.get_first_child()
            while child is not None:
                next_child = child.get_next_sibling()
                destroy_cells(child)
                child = next_child

        child = self.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            destroy_cells(child)
            child = next_child
        super().remove_all()
