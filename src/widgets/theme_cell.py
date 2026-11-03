# theme_cell.py
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
from gi.repository import Gtk, Adw, GLib, Gdk
from .install_page import InstallPage
from .utils import soup_get_bytes

class ThemeCell(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, width_request=350, height_request=400)
        self.add_css_class("card")
        self.set_focusable(True)
        self.set_cursor_from_name("pointer")
        click_controller = Gtk.GestureClick()
        click_controller.connect("released", self.on_card_clicked)
        self.add_controller(click_controller)
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.add_controller(key_controller)
        self._destroyed = False
        self._footer_added = False

    def open_details(self):
        install_page = InstallPage(self)
        self.page.push(install_page)

    def on_card_clicked(self, gesture, n_press, x, y):
        if getattr(self, "is_installed_card", False):
            return
        self.open_details()

    def on_key_pressed(self, controller, keyval, keycode, state):
        if getattr(self, "is_installed_card", False):
            return False
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self.open_details()
            return True
        return False

    def show_preview(self, image_bytes):
        if self._destroyed:
            return
        try:
            texture = Gdk.Texture.new_from_bytes(image_bytes)
            picture = Gtk.Picture.new_for_paintable(texture)
            picture.set_content_fit(Gtk.ContentFit.COVER)
            picture.add_css_class("rounded")
            picture.set_vexpand(True)
            self.background.stop()
            self.background.unparent()
            self.append(picture)
        except (GLib.Error, ValueError) as error:
            logging.warning("Could not decode preview for %s: %s", self.title, error)
            self.show_preview_placeholder()
        self.add_footer()

    def on_preview_error(self, error):
        if self._destroyed:
            return
        logging.warning("Could not load preview for %s: %s", self.title, error)
        self.show_preview_placeholder()

    def show_preview_placeholder(self):
        if self._destroyed:
            return
        self.background.stop()
        if self.background.get_parent():
            self.background.unparent()
        placeholder = Gtk.Image(
            icon_name="image-missing-symbolic",
            pixel_size=96,
            vexpand=True,
            valign=Gtk.Align.CENTER,
        )
        placeholder.add_css_class("dimmed")
        self.append(placeholder)
        self.add_footer()

    def show_local_preview(self, image_paths):
        if self._destroyed:
            return
        picture_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            vexpand=True,
        )
        picture = Gtk.Picture()
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_vexpand(True)
        picture.set_can_shrink(True)
        picture_box.append(picture)
        current = {"index": 0}

        def display_image(index):
            current["index"] = index % len(image_paths)
            try:
                texture = Gdk.Texture.new_from_filename(image_paths[current["index"]])
            except (GLib.Error, OSError) as error:
                logging.warning(
                    "Could not load local preview for %s: %s",
                    self.title,
                    error,
                )
                picture.set_paintable(None)
                return
            picture.set_paintable(texture)

        display_image(0)
        if len(image_paths) > 1:
            navigation = Gtk.Box(
                spacing=12,
                halign=Gtk.Align.CENTER,
                margin_bottom=4,
            )
            previous = Gtk.Button(icon_name="go-previous-symbolic")
            next_button = Gtk.Button(icon_name="go-next-symbolic")
            count = Gtk.Label(label=f"1 / {len(image_paths)}")

            def change_image(button, offset):
                display_image(current["index"] + offset)
                count.set_label(f"{current['index'] + 1} / {len(image_paths)}")

            previous.connect("clicked", change_image, -1)
            next_button.connect("clicked", change_image, 1)
            navigation.append(previous)
            navigation.append(count)
            navigation.append(next_button)
            picture_box.append(navigation)

        self.background.stop()
        self.background.unparent()
        self.append(picture_box)
        self.add_footer()

    def add_footer(self):
        if not self._footer_added:
            self.append(self.make_bottom_box())
            self._footer_added = True

    def make_bottom_box(self):
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        bottom_box = Gtk.Box(margin_start=12, margin_end=12, margin_top=8, margin_bottom=10, spacing=4)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=2)
        title_label = Gtk.Label(label=self.title, xalign=0.0, wrap=True, hexpand=True)
        title_label.add_css_class("bold")

        download_label = Gtk.Label(label=f"↓ {self.downloads}, ★ {self.rating}", xalign=0.0, valign=Gtk.Align.CENTER)
        download_label.add_css_class("dimmed")

        dev_label = Gtk.Label(label=_("By: ") + self.dev, xalign=0.0, hexpand=True, valign=Gtk.Align.CENTER)
        dev_label.add_css_class("dimmed")

        text_box.append(title_label)
        text_box.append(dev_label)
        resource_types = getattr(self, "resource_types", [])
        if resource_types:
            category_label = Gtk.Label(
                label=" · ".join(resource_types),
                xalign=0,
                wrap=True,
                hexpand=True,
            )
            category_label.add_css_class("dimmed")
            text_box.append(category_label)
        text_box.append(download_label)

        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        wrapper.append(separator)
        wrapper.append(bottom_box)
        bottom_box.append(text_box)
        if getattr(self, "installed_theme", None):
            actions = Gtk.Box(
                spacing=8,
                halign=Gtk.Align.END,
                margin_start=8,
                margin_end=8,
                margin_bottom=8,
            )
            edit_button = Gtk.Button(label=_("Edit Metadata"))
            edit_button.add_css_class("pill")
            edit_button.connect(
                "clicked",
                lambda *_: self.edit_installed_callback(self.installed_theme),
            )
            remove_button = Gtk.Button(label=_("Remove"))
            remove_button.add_css_class("destructive-action")
            remove_button.connect(
                "clicked",
                lambda *_: self.remove_installed_callback(self.installed_theme),
            )
            actions.append(edit_button)
            actions.append(remove_button)
            wrapper.append(actions)
        return wrapper

    def destroy_cell(self):
        self._destroyed = True

    def build_cell(self):
        self.background = Gtk.Spinner(
            spinning=True,
            vexpand=True,
            valign=Gtk.Align.CENTER,
            width_request=40,
            height_request=40,
        )
        self.append(self.background)
        local_preview_paths = getattr(self, "local_preview_paths", [])
        if local_preview_paths:
            self.show_local_preview(local_preview_paths)
            return
        if not self.image_urls:
            self.show_preview_placeholder()
            return
        soup_get_bytes(
            self.image_urls[0],
            self.show_preview,
            self.on_preview_error,
        )
