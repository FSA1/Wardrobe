# utils.py
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

import gi, os, random, json, logging
from collections import OrderedDict
import sqlite3
import time
from urllib.parse import urlencode
from gi.repository import GnomeAutoar, Soup, GLib, Gtk, Gdk, Gio, Adw
from html.parser import HTMLParser
from pathlib import Path

_CACHE_TTL_SECONDS = 30 * 60
_CACHE_STALE_WINDOW_SECONDS = 7 * 24 * 60 * 60
_DISK_CACHE_LIMIT_BYTES = 250 * 1024 * 1024
_MEMORY_CACHE_LIMIT_BYTES = 16 * 1024 * 1024
_API_ENDPOINT_PREFIX = "https://api.opendesktop.org/ocs/v1/content/data/"
_HTTP_SESSION = Soup.Session()
_MEMORY_RESPONSE_CACHE = OrderedDict()
_MEMORY_RESPONSE_CACHE_BYTES = 0
_DISK_RESPONSE_CACHE = None
_DISK_RESPONSE_CACHE_DISABLED = False
_HTTP_REQUESTS = {}

def _cache_key(kind, url):
    return kind, url

def _get_disk_cache():
    global _DISK_RESPONSE_CACHE, _DISK_RESPONSE_CACHE_DISABLED
    if _DISK_RESPONSE_CACHE_DISABLED:
        return None
    if _DISK_RESPONSE_CACHE is not None:
        return _DISK_RESPONSE_CACHE
    try:
        cache_dir = os.path.join(GLib.get_user_cache_dir(), "wardrobe")
        os.makedirs(cache_dir, mode=0o700, exist_ok=True)
        database = sqlite3.connect(
            os.path.join(cache_dir, "http-cache.sqlite3"),
            timeout=5,
        )
        database.execute("PRAGMA auto_vacuum=INCREMENTAL")
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS responses (
                kind TEXT NOT NULL,
                url TEXT NOT NULL,
                payload BLOB NOT NULL,
                expires_at REAL NOT NULL,
                accessed_at REAL NOT NULL,
                etag TEXT,
                last_modified TEXT,
                PRIMARY KEY (kind, url)
            )
            """
        )
        database.commit()
        _DISK_RESPONSE_CACHE = database
    except (OSError, sqlite3.Error) as error:
        logging.warning("Could not initialize the Wardrobe disk cache: %s", error)
        _DISK_RESPONSE_CACHE_DISABLED = True
    return _DISK_RESPONSE_CACHE

def _memory_cache_get(key):
    entry = _MEMORY_RESPONSE_CACHE.get(key)
    if entry is not None:
        _MEMORY_RESPONSE_CACHE.move_to_end(key)
    return entry

def _memory_cache_store(key, entry):
    global _MEMORY_RESPONSE_CACHE_BYTES
    previous = _MEMORY_RESPONSE_CACHE.pop(key, None)
    if previous is not None:
        _MEMORY_RESPONSE_CACHE_BYTES -= len(previous["payload"])
    payload = entry["payload"]
    if len(payload) > _MEMORY_CACHE_LIMIT_BYTES:
        return
    _MEMORY_RESPONSE_CACHE[key] = entry
    _MEMORY_RESPONSE_CACHE_BYTES += len(payload)
    while _MEMORY_RESPONSE_CACHE_BYTES > _MEMORY_CACHE_LIMIT_BYTES:
        _, evicted = _MEMORY_RESPONSE_CACHE.popitem(last=False)
        _MEMORY_RESPONSE_CACHE_BYTES -= len(evicted["payload"])

def _cache_delete(kind, url):
    global _MEMORY_RESPONSE_CACHE_BYTES
    key = _cache_key(kind, url)
    entry = _MEMORY_RESPONSE_CACHE.pop(key, None)
    if entry is not None:
        _MEMORY_RESPONSE_CACHE_BYTES -= len(entry["payload"])
    database = _DISK_RESPONSE_CACHE
    if database is not None:
        try:
            database.execute(
                "DELETE FROM responses WHERE kind = ? AND url = ?",
                (kind, url),
            )
            database.commit()
        except sqlite3.Error as error:
            logging.warning("Could not remove a Wardrobe cache entry: %s", error)

def _cache_get(kind, url):
    global _MEMORY_RESPONSE_CACHE_BYTES
    key = _cache_key(kind, url)
    entry = _memory_cache_get(key)
    now = time.time()
    if (
        entry is not None
        and entry["expires_at"] < now - _CACHE_STALE_WINDOW_SECONDS
    ):
        _MEMORY_RESPONSE_CACHE.pop(key, None)
        _MEMORY_RESPONSE_CACHE_BYTES -= len(entry["payload"])
        entry = None
    if entry is None:
        database = _get_disk_cache()
        if database is None:
            return None
        try:
            row = database.execute(
                """
                SELECT payload, expires_at, etag, last_modified
                FROM responses WHERE kind = ? AND url = ?
                """,
                (kind, url),
            ).fetchone()
            if row is None:
                return None
            payload, expires_at, etag, last_modified = row
            if expires_at < now - _CACHE_STALE_WINDOW_SECONDS:
                database.execute(
                    "DELETE FROM responses WHERE kind = ? AND url = ?",
                    (kind, url),
                )
                database.commit()
                return None
            entry = {
                "payload": bytes(payload),
                "expires_at": expires_at,
                "etag": etag,
                "last_modified": last_modified,
            }
            _memory_cache_store(key, entry)
            database.execute(
                "UPDATE responses SET accessed_at = ? WHERE kind = ? AND url = ?",
                (now, kind, url),
            )
            database.commit()
        except sqlite3.Error as error:
            logging.warning("Could not read the Wardrobe disk cache: %s", error)
            return None
    else:
        database = _get_disk_cache()
        if database is not None:
            try:
                database.execute(
                    "UPDATE responses SET accessed_at = ? WHERE kind = ? AND url = ?",
                    (time.time(), kind, url),
                )
                database.commit()
            except sqlite3.Error as error:
                logging.warning("Could not update Wardrobe cache usage: %s", error)
    return entry

def _cache_store(kind, url, payload, etag=None, last_modified=None):
    now = time.time()
    entry = {
        "payload": bytes(payload),
        "expires_at": now + _CACHE_TTL_SECONDS,
        "etag": etag,
        "last_modified": last_modified,
    }
    _memory_cache_store(_cache_key(kind, url), entry)
    if len(entry["payload"]) > _DISK_CACHE_LIMIT_BYTES:
        logging.warning(
            "Skipping disk cache for %s response larger than the cache limit",
            kind,
        )
        return
    database = _get_disk_cache()
    if database is None:
        return
    try:
        database.execute(
            """
            INSERT OR REPLACE INTO responses
                (kind, url, payload, expires_at, accessed_at, etag, last_modified)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind,
                url,
                entry["payload"],
                entry["expires_at"],
                now,
                etag,
                last_modified,
            ),
        )
        database.execute(
            "DELETE FROM responses WHERE expires_at < ?",
            (now - _CACHE_STALE_WINDOW_SECONDS,),
        )
        total_bytes = database.execute(
            "SELECT COALESCE(SUM(length(payload)), 0) FROM responses"
        ).fetchone()[0]
        while total_bytes > _DISK_CACHE_LIMIT_BYTES:
            oldest = database.execute(
                """
                SELECT kind, url, length(payload)
                FROM responses ORDER BY accessed_at ASC LIMIT 1
                """
            ).fetchone()
            if oldest is None:
                break
            database.execute(
                "DELETE FROM responses WHERE kind = ? AND url = ?",
                (oldest[0], oldest[1]),
            )
            total_bytes -= oldest[2]
        database.commit()
        database.execute("PRAGMA incremental_vacuum(128)")
    except sqlite3.Error as error:
        database.rollback()
        logging.warning("Could not write the Wardrobe disk cache: %s", error)

def _cached_get(kind, url, response_func, error_func):
    request_key = _cache_key(kind, url)
    cached = _cache_get(kind, url)
    now = time.time()
    if cached is not None and cached["expires_at"] > now:
        GLib.idle_add(response_func, GLib.Bytes.new(cached["payload"]))
        return

    callbacks = _HTTP_REQUESTS.get(request_key)
    if callbacks is not None:
        callbacks.append((response_func, error_func))
        return
    _HTTP_REQUESTS[request_key] = [(response_func, error_func)]

    message = Soup.Message.new("GET", url)
    if cached and cached["etag"]:
        message.get_request_headers().append("If-None-Match", cached["etag"])
    if cached and cached["last_modified"]:
        message.get_request_headers().append(
            "If-Modified-Since", cached["last_modified"]
        )

    def finish_request(response_bytes=None, error=None):
        pending_callbacks = _HTTP_REQUESTS.pop(request_key, [])
        if response_bytes is not None:
            for callback, _ in pending_callbacks:
                callback(response_bytes)
            return
        if cached is not None:
            logging.warning(
                "Using stale %s cache after request failed for %s: %s",
                kind,
                url,
                error,
            )
            stale_bytes = GLib.Bytes.new(cached["payload"])
            for callback, _ in pending_callbacks:
                callback(stale_bytes)
            return
        for _, callback in pending_callbacks:
            if callback:
                callback(error)
            else:
                logging.warning("HTTP request failed for %s: %s", url, error)

    def on_response(session, result):
        try:
            response_bytes = session.send_and_read_finish(result)
        except GLib.Error as error:
            finish_request(error=error)
            return

        status = message.get_status()
        if status == Soup.Status.NOT_MODIFIED and cached is not None:
            response_headers = message.get_response_headers()
            cache_control = response_headers.get_one("Cache-Control") or ""
            if "no-store" in cache_control.casefold():
                _cache_delete(kind, url)
            else:
                etag = response_headers.get_one("ETag") or cached["etag"]
                last_modified = (
                    response_headers.get_one("Last-Modified")
                    or cached["last_modified"]
                )
                _cache_store(
                    kind,
                    url,
                    cached["payload"],
                    etag,
                    last_modified,
                )
            finish_request(
                response_bytes=GLib.Bytes.new(cached["payload"])
            )
            return
        if status != Soup.Status.OK:
            finish_request(
                error=RuntimeError(
                    f"HTTP request failed for {url}: "
                    f"{status} {message.get_reason_phrase()}"
                )
            )
            return

        response_headers = message.get_response_headers()
        cache_control = response_headers.get_one("Cache-Control") or ""
        if "no-store" not in cache_control.casefold():
            _cache_store(
                kind,
                url,
                response_bytes.get_data(),
                response_headers.get_one("ETag"),
                response_headers.get_one("Last-Modified"),
            )
        else:
            _cache_delete(kind, url)
        finish_request(response_bytes=response_bytes)

    _HTTP_SESSION.send_and_read_async(
        message, GLib.PRIORITY_DEFAULT, None, on_response
    )

def _uncached_get(url, response_func, error_func, headers=None):
    message = Soup.Message.new("GET", url)
    for name, value in (headers or {}).items():
        message.get_request_headers().append(name, value)

    def on_response(session, result):
        try:
            response_bytes = session.send_and_read_finish(result)
        except GLib.Error as error:
            if error_func:
                error_func(error)
            else:
                logging.warning("HTTP request failed for %s: %s", url, error)
            return
        if message.get_status() != Soup.Status.OK:
            error = RuntimeError(
                f"HTTP request failed for {url}: "
                f"{message.get_status()} {message.get_reason_phrase()}"
            )
            if error_func:
                error_func(error)
            else:
                logging.warning("%s", error)
            return
        response_func(response_bytes)

    _HTTP_SESSION.send_and_read_async(
        message, GLib.PRIORITY_DEFAULT, None, on_response
    )

def soup_get(url, response_func, error_func=None, headers=None):
    if not url.startswith(_API_ENDPOINT_PREFIX):
        _uncached_get(
            url,
            lambda response_bytes: response_func(response_bytes.get_data()),
            error_func,
            headers,
        )
        return

    def on_metadata_bytes(response_bytes):
        try:
            response_text = response_bytes.get_data().decode()
        except UnicodeError as error:
            if error_func:
                error_func(error)
            else:
                logging.warning(
                    "Could not decode HTTP response for %s: %s",
                    url,
                    error,
                )
            return
        response_func(response_text)

    _cached_get("metadata", url, on_metadata_bytes, error_func)

def soup_get_bytes(url, response_func, error_func=None):
    _cached_get("preview", url, response_func, error_func)

class ThemeSearch:
    page_size = 10
    numeric_page_size = 100

    def __init__(self, query, categories, on_results, on_error):
        self.query = query.strip()
        self.categories = tuple(sorted(set(categories)))
        self.on_results = on_results
        self.on_error = on_error
        self.cancelled = False
        self.numeric_query = self.query.isdecimal()
        self.results = []
        self.seen_items = set()
        self.category_pages = {
            category: {"page": 0, "exhausted": False}
            for category in self.categories
        }
        self.result_page = 0
        self.pending_requests = 0
        self.numeric_delivered = 0

    def cancel(self):
        self.cancelled = True

    def load_page(self, page):
        if self.cancelled:
            return
        self.result_page = page
        if self.numeric_query:
            self._scan_numeric_pages()
            return

        params = {
            "format": "json",
            "search": self.query,
            "page": page,
            "pagesize": self.page_size,
            "categories": "x".join(str(category) for category in self.categories),
        }
        url = "https://api.opendesktop.org/ocs/v1/content/data/?" + urlencode(params)
        soup_get(
            url,
            lambda response: self._on_search_response(response, page),
            self._on_error,
        )

    def _on_search_response(self, response, page):
        if self.cancelled:
            return
        try:
            data = self._decode_response(response)
            items = data.get("data", [])
            total = int(data.get("totalitems", 0))
            has_more = (page + 1) * self.page_size < total if total else len(items) == self.page_size
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as error:
            self._on_error(error)
            return
        self.on_results(items, has_more)

    def _scan_numeric_pages(self):
        end_index = min(
            self.numeric_delivered + self.page_size,
            len(self.results),
        )
        if self.numeric_delivered < len(self.results):
            items = self.results[self.numeric_delivered:end_index]
            self.numeric_delivered = end_index
            has_more = self.numeric_delivered < len(self.results) or not all(
                state["exhausted"] for state in self.category_pages.values()
            )
            self.on_results(
                items,
                has_more,
            )
            return

        if all(state["exhausted"] for state in self.category_pages.values()):
            self.on_results([], False)
            return

        active_categories = [
            category for category, state in self.category_pages.items()
            if not state["exhausted"]
        ]
        if not active_categories:
            self.on_results(
                self.results[self.result_page * self.page_size:end_index],
                False,
            )
            return

        self.pending_requests = len(active_categories)
        batch_items = []
        batch_error = []
        for category in active_categories:
            state = self.category_pages[category]
            params = {
                "format": "json",
                "page": state["page"],
                "pagesize": self.numeric_page_size,
                "categories": category,
            }
            url = "https://api.opendesktop.org/ocs/v1/content/data/?" + urlencode(params)
            soup_get(
                url,
                lambda response, category=category: self._on_numeric_response(
                    response, category, batch_items, batch_error
                ),
                lambda error: self._on_numeric_error(error, batch_error),
            )

    def _on_numeric_response(self, response, category, batch_items, batch_error):
        if self.cancelled:
            return
        try:
            data = self._decode_response(response)
            items = data.get("data", [])
            if not isinstance(items, list):
                raise ValueError("The search response does not contain a result list")
            state = self.category_pages[category]
            if not items:
                state["exhausted"] = True
            else:
                state["page"] += 1
                state["exhausted"] = state["page"] * self.numeric_page_size >= int(
                    data.get("totalitems", 0)
                )
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "")
                    if self.query.casefold() not in name.casefold():
                        continue
                    identifier = (
                        item.get("id")
                        or item.get("detailpage")
                        or (name.casefold(), item.get("typeid"))
                    )
                    if identifier not in self.seen_items:
                        self.seen_items.add(identifier)
                        batch_items.append(item)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as error:
            batch_error.append(error)

        self._finish_numeric_request(batch_items, batch_error)

    def _on_numeric_error(self, error, batch_error):
        if self.cancelled:
            return
        batch_error.append(error)
        self._finish_numeric_request([], batch_error)

    def _finish_numeric_request(self, batch_items, batch_error):
        self.pending_requests -= 1
        if batch_error:
            self.cancelled = True
            self.on_error(batch_error[0])
            return
        if self.pending_requests == 0:
            self.results.extend(batch_items)
            self._scan_numeric_pages()

    def _on_error(self, error):
        if not self.cancelled:
            self.cancelled = True
            self.on_error(error)

    @staticmethod
    def _decode_response(response):
        data = json.loads(response) if isinstance(response, (str, bytes)) else response
        if not isinstance(data, dict) or not isinstance(data.get("data", []), list):
            raise ValueError("The search response has an unexpected format")
        return data

def match_theme_type(typeid):
    category_map = {
        134: 0, 386: 1, 199: 1, 132: 1, 197: 1,
        366: 2, 135: 2, 136: 2,
        107: 3, 300: 4, 286: 4, 312: 4, 261: 4, 299: 4, 283: 4, 360: 4,
        287: 4, 302: 4, 290: 4, 311: 4, 359: 4,
    }
    return category_map.get(int(typeid), int(typeid))

def is_picture(file):
    if(any(ext in file for ext in [".png", ".jpg", ".svg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".jxl"])):
        return True
    else:
        return False

def destroy_symlinks(folder_path):
    parent = Path(folder_path).parent
    for path in parent.iterdir():
        if(path.is_symlink() and not path.exists()):
            path.unlink()

def search_for_images(folders):
    return_val = []
    for folder in folders:
        if(os.path.isdir(folder)):
            for root, dirs, files in os.walk(folder):
                for file in files:
                    if(is_picture(file)):
                        return_val.append(os.path.join(root, file))
        elif is_picture(folder):
            return_val.append(folder)
    return return_val

class HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []

    def handle_data(self, data):
        self.result.append(data)

    def handle_starttag(self, tag, attrs):
        if(tag in ("p", "div", "br", "li")):
            self.result.append("\n")

    def handle_endtag(self, tag):
        if(tag in ("p", "div", "li")):
            self.result.append("\n")

    def get_data(self):
        return ''.join(self.result)

def strip_html(source):
    parser = HTMLStripper()
    parser.feed(source)
    return parser.get_data()

def resolve_issues(archive_path, typeid, change_func):
    install_started = time.monotonic()
    archive_file = Gio.File.new_for_path(archive_path)
    output = os.path.dirname(archive_path)
    from .install_page import folders as theme_dirs
    theme_dir = os.path.dirname(theme_dirs[typeid])
    gio_output = Gio.File.new_for_path(output)
    before = set(os.listdir(output))
    completed = False
    try:
        archive_size = os.path.getsize(archive_path)
    except OSError:
        archive_size = 0
    logging.info(
        "Install extraction started: archive=%s size=%d bytes type=%s",
        archive_path,
        archive_size,
        typeid,
    )

    def arrange_folders(extractor):
        nonlocal completed
        if completed:
            return
        completed = True
        extracted_at = time.monotonic()
        logging.info(
            "Install extraction finished: archive=%s elapsed=%.2fs files=%s bytes=%s",
            archive_path,
            extracted_at - install_started,
            extractor.get_property("completed-files"),
            extractor.get_property("completed-size"),
        )
        folders = {
            0: ["gnome-shell"],
            1: ["index.theme"],
            2: ["gtk-2.0", "gnome-shell", "gtk-3.0", "gtk-4.0", "cinnamon", "xfwm4"],
            3: ["cursors", "cursors_scalable", "index.theme"]
        }

        organization_started = time.monotonic()
        after = set(os.listdir(output))
        added = set()
        for item in after - before:
            item_path = os.path.join(output, item)
            if(os.path.isdir(item_path)):
                added.add(item_path)

        if(typeid == 4):
            for item in after - before:
                item_path = os.path.join(output, item)
                if os.path.isdir(item_path) or os.path.isfile(item_path):
                    added.add(item_path)
            change_func(added, search_for_images(added))
            logging.info(
                "Install organization finished: archive=%s elapsed=%.2fs roots=%d",
                archive_path,
                time.monotonic() - organization_started,
                len(added),
            )
            return
        else:
            important_items = folders.get(typeid)

        # I can't tell if this code is very good or very bad
        scan_started = time.monotonic()
        important_paths = set()
        linked_paths = set()
        visited_directories = 0
        for folder in added:
            if typeid == 1:
                for root, dirs, files in os.walk(folder):
                    visited_directories += 1
                    if "index.theme" in files:
                        important_paths.add(root)
                        dirs.clear()
            else:
                for root, dirs, files in os.walk(folder, topdown=False):
                    visited_directories += 1
                    for folder_name in important_items:
                        if folder_name == "index.theme":
                            search = files
                        else:
                            search = dirs
                        for item in search:
                            if item == folder_name:
                                important_paths.add(os.path.dirname(os.path.join(root, item)))
        logging.info(
            "Install theme discovery finished: archive=%s elapsed=%.2fs directories=%d themes=%d",
            archive_path,
            time.monotonic() - scan_started,
            visited_directories,
            len(important_paths),
        )
        if(typeid != 1):
            for path in important_paths:
                try:
                    os.symlink(path, os.path.join(theme_dir, os.path.basename(path)))
                except:
                    continue
            linked_paths = important_paths
        else:
            for path in important_paths:
                extension = ""
                folder = os.path.basename(path)
                if('dark' in folder.lower()):
                    extension = '-dark'
                elif('light' in folder.lower()):
                    extension = '-light'

                parent_name = os.path.basename(os.path.dirname(path)).lower()
                correct_folder_name = folder.lower().replace(extension, '')

                if(parent_name in correct_folder_name):
                    new_folder_name = correct_folder_name + extension
                elif(correct_folder_name in parent_name):
                    new_folder_name = parent_name + extension
                else:
                    new_folder_name = folder.lower() + '-' + str(random.randint(1, 1000))

                new_path = os.path.join(theme_dir, os.path.basename(new_folder_name))
                os.symlink(path, new_path)
                
                linked_paths.add(new_path)
        change_func(added, linked_paths)
        logging.info(
            "Install organization finished: archive=%s elapsed=%.2fs roots=%d links=%d",
            archive_path,
            time.monotonic() - organization_started,
            len(added),
            len(linked_paths),
        )

    def log_extraction_progress(extractor, property_spec):
        now = time.monotonic()
        if now - progress_state["last_logged"] < 2:
            return
        progress_state["last_logged"] = now
        logging.info(
            "Install extraction progress: archive=%s files=%s/%s bytes=%s/%s elapsed=%.2fs",
            archive_path,
            extractor.get_property("completed-files"),
            extractor.get_property("total-files"),
            extractor.get_property("completed-size"),
            extractor.get_property("total-size"),
            now - install_started,
        )

    if(is_picture(archive_path)):
        change_func([archive_path], [archive_path])
        logging.info(
            "Install completed: archive=%s elapsed=%.2fs",
            archive_path,
            time.monotonic() - install_started,
        )
        return
    extractor = GnomeAutoar.Extractor.new(archive_file, gio_output)
    extractor.set_delete_after_extraction(True)

    progress_state = {"last_logged": install_started}
    extractor.connect("completed", arrange_folders)
    extractor.connect("notify::completed-size", log_extraction_progress)
    extractor.connect("notify::completed-files", log_extraction_progress)
    extractor.start_async()

def parse_json(response, flowbox):
    if(isinstance(response, str)):
        data = json.loads(response)
    else:
        data = response
            
    content = data.get("data", [])
            
    for item in content:
        cell = create_theme_cell(item, flowbox.page)
        cell.build_cell()
        flowbox.append(Adw.Clamp(child=cell, maximum_size=375))
                
        if(hasattr(flowbox, "get_n_pages")):
            if(flowbox.get_n_pages() == 7):
                mid = flowbox.get_n_pages() // 2
                def scroll(attempts=0):
                    page = flowbox.get_nth_page(mid)
                    if(page is None or attempts > 20):
                        return False
                    flowbox.scroll_to(page, False)
                    if(round(flowbox.get_position()) != mid):
                        GLib.timeout_add(20, scroll, attempts + 1)
                    return False
                GLib.idle_add(scroll)
                break
        
    if(hasattr(flowbox, "group")):
        flowbox.group.set_sensitive(True)

def create_theme_cell(item, page):
    from .theme_cell import ThemeCell

    cell = ThemeCell()
    cell.page = page
    cell.item = dict(item)
    cell.title = item.get("name", "Unknown")
    cell.home_page = item.get("detailpage", "")
    cell.dev = item.get("personid", "Unknown")
    cell.rating = int(item.get("score", 0)) / 10 if item.get("score") else 0
    cell.last_update = item.get("changed", "")
    cell.description = item.get("description", "")
    cell.downloads = item.get("downloads", "0") if item.get("downloads") else 0
    cell.theme_type = item.get("typeid", 0)
    cell.download_links = []
    cell.download_names = []

    index = 1
    while True:
        download_link = item.get(f"downloadlink{index}")
        download_name = item.get(f"downloadname{index}")
        if download_link is None or download_name is None:
            break
        if download_link and "." in download_name:
            cell.download_links.append(download_link)
            cell.download_names.append(download_name)
        index += 1

    cell.image_urls = []
    index = 1
    while True:
        preview = item.get(f"previewpic{index}")
        if preview is None:
            break
        cell.image_urls.append(preview)
        index += 1
    return cell
    
    
