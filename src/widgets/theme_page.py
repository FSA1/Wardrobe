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
import copy, json, logging, os, shutil, hashlib
from gi.repository import Gtk, Adw, Pango, GLib, Gio
from .install_page import (
    backgrounds_dir,
    icon_roots,
    _scan_installed_wallpapers,
    _scan_local_theme_directories,
    _installed_item_author,
    theme_roots,
    add_theme,
    pop_theme,
    is_valid_preview_image,
    remove_external_paths,
    remove_installed_paths,
    remove_installed_variant,
)
from .utils import ThemeSearch, create_theme_cell
from .theme_cell_flowbox import ThemeCellFlowbox
from .wallpaper_providers import (
    POPULAR_WALLPAPER_TAGS,
    search_wallhaven,
)

category_ids = {
    "GNOME Shell": 134,
    "Icons": 386,
    "GTK3/4": 366,
    "Cursors": 107,
    "Wallpapers": 261,
}

INSTALLED_CACHE_VERSION = 1
_installed_scan_cache = {}
_installed_scan_cache_loaded = False
_invalidated_installed_categories = set()


def _installed_scan_cache_path():
    return os.path.join(
        GLib.get_user_data_dir(), "wardrobe", "installed-cache.json"
    )


def _load_installed_scan_cache():
    global _installed_scan_cache_loaded
    if _installed_scan_cache_loaded:
        return
    _installed_scan_cache_loaded = True
    path = _installed_scan_cache_path()
    try:
        with open(path, encoding="utf-8") as cache_file:
            data = json.load(cache_file)
    except FileNotFoundError:
        return
    except (OSError, json.JSONDecodeError) as error:
        logging.warning("Could not read installed items cache %s: %s", path, error)
        return
    if not isinstance(data, dict) or data.get("version") != INSTALLED_CACHE_VERSION:
        return
    categories = data.get("categories")
    if isinstance(categories, dict):
        _installed_scan_cache.update(
            (category, record)
            for category, record in categories.items()
            if isinstance(record, dict)
            and isinstance(record.get("fingerprint"), str)
            and isinstance(record.get("themes"), list)
            and all(isinstance(theme, dict) for theme in record["themes"])
        )


def _save_installed_scan_cache():
    path = _installed_scan_cache_path()
    temporary_path = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(temporary_path, "w", encoding="utf-8") as cache_file:
            json.dump(
                {
                    "version": INSTALLED_CACHE_VERSION,
                    "categories": _installed_scan_cache,
                },
                cache_file,
            )
        os.replace(temporary_path, path)
    except (OSError, TypeError, ValueError) as error:
        logging.warning("Could not save installed items cache %s: %s", path, error)
        try:
            os.remove(temporary_path)
        except OSError:
            pass


def _update_cached_installed_theme(category, theme):
    record = _installed_scan_cache.get(category)
    if not record:
        return
    tracking_key = theme.get("tracking_key")
    for index, cached_theme in enumerate(record.get("themes", [])):
        if (
            isinstance(cached_theme, dict)
            and cached_theme.get("tracking_key") == tracking_key
        ):
            record["themes"][index] = copy.deepcopy(theme)
            _save_installed_scan_cache()
            return


def _installed_roots_fingerprint(roots):
    def entry_signature(entry, depth):
        try:
            entry_stat = entry.stat(follow_symlinks=False)
        except OSError as error:
            logging.debug(
                "Could not fingerprint installed resource %s: %s",
                entry.path,
                error,
            )
            raise

        children = ()
        if depth and entry.is_dir(follow_symlinks=False):
            child_depth = depth - 1
            if entry.name == "wardrobe-installs":
                child_depth = max(child_depth, 2)
            children = directory_signatures(entry.path, child_depth)
        return (
            entry.name,
            entry_stat.st_mode,
            entry_stat.st_mtime_ns,
            entry_stat.st_size,
            entry_stat.st_ino,
            children,
        )

    def directory_signatures(directory, depth):
        try:
            with os.scandir(directory) as entries:
                return tuple(
                    sorted(entry_signature(entry, depth) for entry in entries)
                )
        except OSError as error:
            logging.debug(
                "Could not inspect installed resource directory %s: %s",
                directory,
                error,
            )
            raise

    fingerprint = []
    for root in roots:
        root = os.path.abspath(root)
        try:
            root_stat = os.stat(root, follow_symlinks=False)
        except FileNotFoundError:
            fingerprint.append((root, None))
            continue
        except OSError as error:
            logging.debug("Could not fingerprint installed resource root %s: %s", root, error)
            return None

        try:
            entries = directory_signatures(root, 1) if os.path.isdir(root) else ()
        except OSError:
            return None
        fingerprint.append(
            (
                root,
                root_stat.st_mtime_ns,
                root_stat.st_size,
                root_stat.st_ino,
                entries,
            )
        )
    serialized = json.dumps(fingerprint, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def invalidate_installed_scan_cache(category):
    _installed_scan_cache.pop(category, None)
    _invalidated_installed_categories.add(category)


def _get_cached_installed_themes(category, roots, scanner):
    _load_installed_scan_cache()
    fingerprint = _installed_roots_fingerprint(roots)
    cached = _installed_scan_cache.get(category)
    if (
        category not in _invalidated_installed_categories
        and fingerprint is not None
        and cached
        and cached.get("fingerprint") == fingerprint
    ):
        return copy.deepcopy(cached["themes"])

    installed = scanner()
    if fingerprint is not None:
        _installed_scan_cache[category] = {
            "fingerprint": fingerprint,
            "themes": installed,
        }
        _save_installed_scan_cache()
    _invalidated_installed_categories.discard(category)
    return installed


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
        self.wallpaper_search_mode = False
        self.wallpaper_page = 0
        self.wallpaper_pending = 0
        self.wallpaper_has_more = {}
        self.wallpaper_failed = set()
        self.wallpaper_requested_sources = set()
        self.wallpaper_seen = set()
        self.wallpaper_result_count = 0
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
        )
        self.search_entry.connect("search-changed", self.on_search_changed)
        search_clamp = Adw.Clamp(
            child=self.search_entry,
            maximum_size=420,
            tightening_threshold=300,
            hexpand=True,
            halign=Gtk.Align.CENTER,
            margin_start=12,
            margin_end=12,
        )
        content_box.append(search_clamp)

        button_box = Adw.ToggleGroup(hexpand=True, halign=Gtk.Align.CENTER)
        self.theme_flowbox.group = button_box
        button_box.add_css_class("round")
        button_box.connect("notify::active", self.on_type_changed)
        content_box.append(button_box)
        self.order_group = button_box

        for sortmode in ["Popular", "Alphabetical", "Rating", "Latest", "Installed"]:
            button = Adw.Toggle(label=_(sortmode), name=sortmode)
            button_box.add(button)

        self.order_group.set_active_name("Popular")

        self.wallpaper_controls = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            halign=Gtk.Align.FILL,
            hexpand=True,
            visible=False,
        )
        source_box = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        source_box.append(Gtk.Label(label=_("Search in")))
        self.wallpaper_source_group = Adw.ToggleGroup(hexpand=True)
        self.wallpaper_source_group.add_css_class("round")
        self.wallpaper_source_group.connect(
            "notify::active", self.on_wallpaper_source_changed
        )
        for source, label in (
            ("pling", _("Pling / GNOME-Look")),
            ("wallhaven", _("Wallhaven")),
        ):
            self.wallpaper_source_group.add(
                Adw.Toggle(label=label, name=source)
            )
        self.wallpaper_source_group.set_active_name("pling")
        source_box.append(self.wallpaper_source_group)
        self.wallpaper_controls.append(source_box)

        resolution_box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        resolution_box.append(Gtk.Label(label=_("Minimum resolution")))
        self.wallpaper_resolution = Gtk.DropDown.new_from_strings(
            [
                _("Any size"),
                "1280 × 720",
                "1920 × 1080",
                "2560 × 1440",
                "3840 × 2160",
            ]
        )
        self.wallpaper_resolution.connect(
            "notify::selected", self.on_wallpaper_resolution_changed
        )
        self.wallpaper_resolution.set_sensitive(False)
        self.wallpaper_resolution.set_tooltip_text(
            _("The resolution filter is available for Wallhaven")
        )
        resolution_box.append(self.wallpaper_resolution)
        self.wallpaper_controls.append(resolution_box)

        self.wallpaper_tags_box = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            min_children_per_line=1,
            max_children_per_line=10,
            homogeneous=False,
            row_spacing=4,
            column_spacing=6,
            hexpand=True,
            halign=Gtk.Align.CENTER,
            margin_top=2,
            margin_bottom=2,
        )
        self.wallpaper_controls.append(self.wallpaper_tags_box)
        self.build_wallpaper_tag_chips()

        content_box.append(self.wallpaper_controls)

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

    def build_wallpaper_tag_chips(self):
        if not hasattr(self, "wallpaper_tags_box"):
            return
        while child := self.wallpaper_tags_box.get_first_child():
            self.wallpaper_tags_box.remove(child)

        current_query = (
            self.search_entry.get_text().strip().lower()
            if hasattr(self, "search_entry")
            else ""
        )

        for tag_id, tag_label in POPULAR_WALLPAPER_TAGS:
            btn = Gtk.Button(label=tag_label)
            btn.add_css_class("wallpaper-tag-button")
            btn.get_child().add_css_class("wallpaper-tag-label")
            if current_query == tag_id.lower():
                btn.add_css_class("suggested-action")
            else:
                btn.add_css_class("flat")
            btn.connect("clicked", self.on_wallpaper_tag_clicked, tag_id)
            self.wallpaper_tags_box.append(btn)

    def on_wallpaper_tag_clicked(self, button, tag_id):
        current = self.search_entry.get_text().strip().lower()
        if current == tag_id.lower():
            self.search_entry.set_text("")
        else:
            self.search_entry.set_text(tag_id)
        self.schedule_category_search()
        self.build_wallpaper_tag_chips()

    def on_search_changed(self, entry):
        if self.updating_entry:
            return
        if self.selected == "installed":
            self.filter_installed_themes(entry.get_text())
            return
        self.schedule_category_search()
        self.build_wallpaper_tag_chips()

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
        self.build_wallpaper_tag_chips()

        if not query:
            if self.category == "Wallpapers":
                active_source = (
                    self.wallpaper_source_group.get_active_name()
                    if hasattr(self, "wallpaper_source_group")
                    else "pling"
                )
                if active_source != "pling":
                    self.order_group.set_sensitive(False)
                    self.search_timeout_id = GLib.timeout_add(
                        350, self.start_category_search, generation, ""
                    )
                    return
            self.order_group.set_sensitive(True)
            self.theme_action(
                "new_page",
                categories[self.category] + self.selected + "&page=0",
            )
            return

        self.order_group.set_sensitive(self.category != "Wallpapers")
        self.search_timeout_id = GLib.timeout_add(
            350, self.start_category_search, generation, query
        )

    def start_category_search(self, generation, query):
        self.search_timeout_id = 0
        if generation != self.request_generation or self.selected == "installed":
            return GLib.SOURCE_REMOVE
        if self.category == "Wallpapers":
            self.start_wallpaper_search_page(generation, query, 0)
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

    def selected_wallpaper_resolution(self):
        return {
            0: (0, 0),
            1: (1280, 720),
            2: (1920, 1080),
            3: (2560, 1440),
            4: (3840, 2160),
        }[self.wallpaper_resolution.get_selected()]

    def on_wallpaper_source_changed(self, group, _property):
        if getattr(self, "updating_source", False):
            return
        source = group.get_active_name()
        if hasattr(self, "wallpaper_resolution"):
            self.wallpaper_resolution.set_sensitive(source != "pling")
        if hasattr(self, "order_group"):
            self.order_group.set_sensitive(not self.search_entry.get_text().strip())
        if (
            hasattr(self, "category")
            and self.category == "Wallpapers"
            and self.selected != "installed"
        ):
            self.schedule_category_search()

    def on_wallpaper_resolution_changed(self, dropdown, _property):
        if (
            hasattr(self, "category")
            and self.category == "Wallpapers"
            and self.selected != "installed"
        ):
            self.schedule_category_search()

    def start_wallpaper_search_page(self, generation, query, page):
        active_sources = [
            self.wallpaper_source_group.get_active_name()
        ]
        active_sources = [source for source in active_sources if source]
        self.wallpaper_search_mode = True
        self.wallpaper_page = page
        self.wallpaper_pending = len(active_sources)
        self.wallpaper_has_more = {}
        self.wallpaper_failed = set()
        self.wallpaper_requested_sources = set(active_sources)
        if page == 0:
            self.wallpaper_seen = set()
            self.wallpaper_result_count = 0
            self.wallpaper_cursors = {}
            self.wallpaper_empty_pages = 0
        self.current_page = page
        self.has_more = False
        self.set_loading(bool(active_sources))
        self.order_group.set_sensitive(False)

        if not active_sources:
            self.empty_label.set_label(_("Select at least one source"))
            self.empty_label.set_visible(page == 0)
            return

        minimum_width, minimum_height = self.selected_wallpaper_resolution()

        def result_callback(source):
            return lambda items, has_more, cursor=None: self.on_wallpaper_provider_results(
                source, items, has_more, generation, page, cursor=cursor
            )

        def error_callback(source):
            return lambda error: self.on_wallpaper_provider_results(
                source, [], False, generation, page, error=error
            )

        if "pling" in active_sources:
            self.search_controller = ThemeSearch(
                query,
                [category_ids["Wallpapers"]],
                lambda items, has_more: self.on_wallpaper_provider_results(
                    "pling", items, has_more, generation, page
                ),
                lambda error: self.on_wallpaper_provider_results(
                    "pling", [], False, generation, page, error=error
                ),
            )
            self.search_controller.load_page(page)
        if "wallhaven" in active_sources:
            search_wallhaven(
                query,
                page,
                minimum_width,
                minimum_height,
                result_callback("wallhaven"),
                error_callback("wallhaven"),
                cursor=getattr(self, "wallpaper_cursors", {}).get("wallhaven"),
            )

    def on_wallpaper_provider_results(
        self, source, items, has_more, generation, page, error=None, cursor=None
    ):
        if generation != self.request_generation or not self.wallpaper_search_mode:
            return
        if error is not None:
            self.wallpaper_failed.add(source)
            logging.error("%s wallpaper search failed: %s", source, error)
        else:
            self.wallpaper_has_more[source] = has_more
            if cursor is not None and hasattr(self, "wallpaper_cursors"):
                self.wallpaper_cursors[source] = cursor
            cards_added = 0
            for item in items:
                wallpaper = item.get("provider_wallpaper")
                if isinstance(wallpaper, dict):
                    result_key = wallpaper.get("download_url")
                    if not result_key or result_key in self.wallpaper_seen:
                        continue
                    self.wallpaper_seen.add(result_key)
                    minimum_width, minimum_height = (
                        self.selected_wallpaper_resolution()
                    )
                    wallpaper["minimum_width"] = minimum_width
                    wallpaper["minimum_height"] = minimum_height
                card = create_theme_cell(item, self.theme_flowbox.page)
                card.build_cell()
                self.theme_flowbox.append(Adw.Clamp(child=card, maximum_size=375))
                self.wallpaper_result_count += 1
                cards_added += 1

            if cards_added == 0:
                self.wallpaper_empty_pages = getattr(self, "wallpaper_empty_pages", 0) + 1
            else:
                self.wallpaper_empty_pages = 0

            if getattr(self, "wallpaper_empty_pages", 0) >= 3:
                self.wallpaper_has_more[source] = False

        self.wallpaper_pending -= 1
        if self.wallpaper_pending > 0:
            return

        self.has_more = any(self.wallpaper_has_more.values())
        self.current_page = page
        self.set_loading(False)
        self.order_group.set_sensitive(False)
        if page == 0 and self.wallpaper_result_count == 0:
            self.empty_label.set_label(
                _("Search failed")
                if self.wallpaper_requested_sources
                and len(self.wallpaper_failed)
                == len(self.wallpaper_requested_sources)
                else _("No wallpapers found")
            )
            self.empty_label.set_visible(True)
        else:
            self.empty_label.set_visible(False)
        GLib.idle_add(self.maybe_load_more)

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
        if self.wallpaper_search_mode:
            self.start_wallpaper_search_page(
                self.request_generation,
                self.search_entry.get_text().strip(),
                self.current_page + 1,
            )
            return
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
        self.selected = "down"
        self.wallpaper_search_mode = False
        self.wallpaper_controls.set_visible(self.category == "Wallpapers")
        if self.category == "Wallpapers" and hasattr(self, "wallpaper_source_group"):
            self.updating_source = True
            self.wallpaper_source_group.set_active_name("pling")
            self.updating_source = False
            if hasattr(self, "wallpaper_resolution"):
                self.wallpaper_resolution.set_sensitive(False)
        self.updating_entry = True
        self.search_entry.set_text("")
        self.updating_entry = False
        self.theme_flowbox.filter_by_text("")
        self.theme_action("new_page", categories[self.category] + self.selected)

    def theme_action(self, action, url):
        if(action == "new_page"):
            self.wallpaper_search_mode = False
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

        label = group.get_active_name()
        if label == "Installed":
            self.cancel_category_search()
            self.selected = "installed"
            if hasattr(self, "wallpaper_controls"):
                self.wallpaper_controls.set_visible(False)
            self.updating_entry = True
            self.search_entry.set_text("")
            self.updating_entry = False
            self.theme_flowbox.filter_by_text("")
            self.show_installed_themes()
            return

        if hasattr(self, "wallpaper_controls"):
            self.wallpaper_controls.set_visible(self.category == "Wallpapers")
        if self.category == "Wallpapers" and hasattr(self, "wallpaper_source_group"):
            active_source = self.wallpaper_source_group.get_active_name()
            if hasattr(self, "wallpaper_resolution"):
                self.wallpaper_resolution.set_sensitive(active_source != "pling")

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
            roots = (backgrounds_dir,)
            scanner = _scan_installed_wallpapers
        else:
            theme_type = {
                "GNOME Shell": 0,
                "Icons": 1,
                "GTK3/4": 2,
                "Cursors": 3,
            }[self.category]
            roots = theme_roots if theme_type in (0, 2) else icon_roots
            scanner = lambda: _scan_local_theme_directories(theme_type)
        installed = _get_cached_installed_themes(
            self.category,
            roots,
            scanner,
        )
        self.installed_themes = installed
        self.monitor_installed_directories(installed)

        generation = self.request_generation
        for theme in installed:
            card = self.make_installed_card(theme)
            self.theme_flowbox.append(card)
            if not theme.get("item") and not theme.get("metadata_lookup_complete"):
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
        invalidate_installed_scan_cache(self.category)
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
            if generation != self.request_generation or self.selected != "installed":
                return
            if index >= len(queries):
                theme["metadata_lookup_complete"] = True
                _update_cached_installed_theme(self.category, theme)
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
                theme["metadata_lookup_complete"] = True
                _update_cached_installed_theme(self.category, theme)
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
        item_for_cell.setdefault("personid", _installed_item_author(theme))
        item_for_cell.setdefault("description", metadata.get("description") or theme.get("description", ""))
        item_for_cell.setdefault("typeid", category_ids[self.category])
        cell = create_theme_cell(item_for_cell, self.theme_flowbox.page)
        is_wardrobe_download = theme.get("source") == "wardrobe" and bool(item)
        if not is_wardrobe_download:
            cell.title = title
            cell.description = metadata.get("description") or item_for_cell.get("description", "")
        cell.dev = _installed_item_author(theme) or _("Unknown")
        metadata_previews = [
            path
            for path in metadata.get("preview_paths", [])
            if isinstance(path, str) and is_valid_preview_image(path)
        ]
        fallback_previews = [
            path
            for path in theme.get("preview_paths", [])
            if isinstance(path, str) and is_valid_preview_image(path)
        ]
        if is_wardrobe_download:
            cell.local_preview_paths = [] if cell.image_urls else fallback_previews
        elif metadata_previews:
            cell.local_preview_paths = metadata_previews
        elif cell.image_urls:
            cell.local_preview_paths = []
        else:
            cell.local_preview_paths = fallback_previews
        cell.search_aliases = list(theme.get("names", [])) + list(
            theme.get("variants", [])
        )
        cell.installed_theme = theme
        cell.theme_type = category_ids.get(self.category, cell.theme_type)
        cell.edit_installed_callback = self.edit_installed_metadata
        cell.remove_installed_callback = self.confirm_remove_installed
        cell.remove_variant_callback = self.confirm_remove_variant
        cell.installed_theme_owner = self
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
        invalidate_installed_scan_cache(self.category)
        self.show_installed_themes()
        self.close_installed_details(theme["tracking_key"])

    def close_installed_details(self, tracking_key):
        view = self.theme_flowbox.page
        visible_page = view.get_visible_page()
        if (
            visible_page
            and visible_page.get_tag() == "install_page"
            and getattr(visible_page, "installed_theme", {}).get("tracking_key")
            == tracking_key
        ):
            view.pop()

    def confirm_remove_variant(self, button, theme, variant):
        dialog = Adw.MessageDialog.new(
            self.get_root(),
            _("Remove this variant?"),
            _("Only the selected installed variant will be removed: ")
            + variant["display_name"],
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self.on_remove_variant_response, theme, variant)
        dialog.present()

    def on_remove_variant_response(self, dialog, response, theme, variant):
        if response != "remove":
            return
        theme_type = theme.get("category")
        try:
            remove_installed_variant(theme_type, theme, variant)
        except (OSError, ValueError) as error:
            logging.error(
                "Could not remove variant %s from %s: %s",
                variant.get("display_name", variant.get("path")),
                theme.get("display_name"),
                error,
            )
            error_dialog = Adw.MessageDialog.new(
                self.get_root(),
                _("Could not remove variant"),
                str(error),
            )
            error_dialog.add_response("close", _("Close"))
            error_dialog.present()
            return

        invalidate_installed_scan_cache(self.category)
        self.show_installed_themes()
        updated_theme = next(
            (
                installed
                for installed in self.installed_themes
                if installed.get("tracking_key") == theme.get("tracking_key")
            ),
            None,
        )
        view = self.theme_flowbox.page
        visible_page = view.get_visible_page()
        if updated_theme is None:
            self.close_installed_details(theme["tracking_key"])
        elif (
            visible_page
            and visible_page.get_tag() == "install_page"
            and getattr(visible_page, "installed_theme", {}).get("tracking_key")
            == theme.get("tracking_key")
        ):
            visible_page.refresh_installed_details(updated_theme)

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
            text=_installed_item_author(theme),
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
            if isinstance(path, str) and is_valid_preview_image(path)
        ]
        selected_images = {
            "paths": list(dict.fromkeys(image_paths)),
            "new_paths": set(),
        }
        choose_image = Gtk.Button(label=_("Add Preview Images"))
        preview_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        preview_list.add_css_class("boxed-list")
        preview_empty_label = Gtk.Label(
            label=_("No preview images selected"),
            xalign=0,
            visible=not selected_images["paths"],
        )

        def refresh_preview_list():
            preview_list.remove_all()
            preview_empty_label.set_visible(not selected_images["paths"])
            for path in selected_images["paths"]:
                row = Adw.ActionRow(
                    title=os.path.basename(path),
                    subtitle=path,
                )
                remove_image = Gtk.Button(
                    label=_("Remove"),
                    valign=Gtk.Align.CENTER,
                )
                remove_image.add_css_class("destructive-action")
                remove_image.connect(
                    "clicked",
                    lambda button, image_path=path: remove_preview_image(image_path),
                )
                row.add_suffix(remove_image)
                preview_list.append(row)

        def remove_preview_image(path):
            selected_images["paths"].remove(path)
            selected_images["new_paths"].discard(path)
            refresh_preview_list()

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
        content.append(Gtk.Label(label=_("Preview images"), xalign=0))
        content.append(preview_empty_label)
        content.append(preview_list)
        content.append(choose_image)
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
                _("Add Preview Images"),
                editor,
                Gtk.FileChooserAction.OPEN,
                _("Open"),
                _("Cancel"),
            )
            chooser.set_select_multiple(True)
            image_filter = Gtk.FileFilter()
            image_filter.set_name(_("Image files"))
            for extension in ("*.png", "*.jpg", "*.jpeg", "*.gif"):
                image_filter.add_pattern(extension)
            chooser.add_filter(image_filter)

            def on_file_chosen(dialog, response):
                if response == Gtk.ResponseType.ACCEPT:
                    files = dialog.get_files()
                    invalid_paths = []
                    for index in range(files.get_n_items()):
                        file = files.get_item(index)
                        selected_path = file.get_path() if file else None
                        if not selected_path:
                            continue
                        if not is_valid_preview_image(selected_path):
                            invalid_paths.append(selected_path)
                            continue
                        if selected_path not in selected_images["paths"]:
                            selected_images["paths"].append(selected_path)
                            selected_images["new_paths"].add(selected_path)
                    refresh_preview_list()
                    if invalid_paths:
                        results_label.set_text(
                            _("Preview images must be at least 512 × 512 pixels.")
                        )
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
            preview_paths = []
            try:
                for selected_path in selected_images["paths"]:
                    if selected_path in selected_images["new_paths"]:
                        preview_directory = os.path.join(
                            GLib.get_user_data_dir(), "wardrobe", "previews"
                        )
                        os.makedirs(preview_directory, exist_ok=True)
                        extension = os.path.splitext(selected_path)[1].casefold()
                        source_key = (
                            theme["tracking_key"]
                            + "\0"
                            + os.path.realpath(selected_path)
                        )
                        destination = os.path.join(
                            preview_directory,
                            hashlib.sha256(source_key.encode()).hexdigest()[:20]
                            + extension,
                        )
                        if os.path.abspath(selected_path) != os.path.abspath(destination):
                            shutil.copy2(selected_path, destination)
                        preview_paths.append(destination)
                    else:
                        preview_paths.append(selected_path)
            except OSError as error:
                logging.error("Could not save local preview images: %s", error)
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
            _update_cached_installed_theme(self.category, theme)
            close_editor()
            self.show_installed_themes()
            self.close_installed_details(theme["tracking_key"])

        search_button.connect("clicked", on_search_clicked)
        catalog_search.connect("activate", on_search_clicked)
        load_more.connect("clicked", on_load_more)
        refresh_preview_list()
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
            theme["metadata_lookup_complete"] = True
            _update_cached_installed_theme(self.category, theme)
            if search_state["search"]:
                search_state["search"].cancel()
            editor.destroy()
            self.show_installed_themes()
            self.close_installed_details(theme["tracking_key"])

        dialog.connect("response", on_response)
        dialog.present()
