import json
import logging
from urllib.parse import urlencode, urlparse

from .utils import soup_get, strip_html

try:
    _
except NameError:
    def _(text):
        return text

PAGE_SIZE = 24

WALLHAVEN_EXCLUDED_TAGS = [
    "boobs",
    "big boobs",
    "breasts",
    "ass",
    "butt",
    "creepy",
    "bikini",
    "swimsuit",
    "lingerie",
    "panties",
    "underwear",
    "ecchi",
    "hentai",
    "sexy",
    "nude",
    "cleavage",
]
WALLHAVEN_NEGATED_QUERY = " ".join(
    f"-{tag.replace(' ', '_')}" for tag in WALLHAVEN_EXCLUDED_TAGS
)

POPULAR_WALLPAPER_TAGS = [
    ("photography", _("Photography")),
    ("landscape", _("Landscape")),
    ("nature", _("Nature")),
    ("space", _("Space")),
    ("minimalism", _("Minimalism")),
    ("architecture", _("Architecture")),
    ("abstract", _("Abstract")),
    ("mountains", _("Mountains")),
    ("city", _("City")),
    ("sea", _("Sea")),
]

TAG_TRANSLATIONS = {
    "photography": _("Photography"),
    "landscape": _("Landscape"),
    "nature": _("Nature"),
    "space": _("Space"),
    "minimalism": _("Minimalism"),
    "architecture": _("Architecture"),
    "abstract": _("Abstract"),
    "mountains": _("Mountains"),
    "mountain": _("Mountains"),
    "city": _("City"),
    "sea": _("Sea"),
    "ocean": _("Ocean"),
    "forest": _("Forest"),
    "sky": _("Sky"),
    "sunset": _("Sunset"),
    "night": _("Night"),
    "water": _("Water"),
    "cars": _("Cars"),
    "car": _("Cars"),
    "technology": _("Technology"),
    "animals": _("Animals"),
    "digital art": _("Digital Art"),
    "fantasy": _("Fantasy"),
    "sci-fi": _("Sci-Fi"),
    "artwork": _("Artwork"),
}


def translate_tag(tag_name):
    if not isinstance(tag_name, str):
        return ""
    clean = tag_name.lower().strip()
    return TAG_TRANSLATIONS.get(clean, tag_name)


def _decode_response(response):
    if isinstance(response, bytes):
        response = response.decode("utf-8")
    data = json.loads(response)
    if not isinstance(data, dict):
        raise ValueError("Wallpaper provider returned an invalid response")
    return data


def _is_https_url(url):
    return isinstance(url, str) and urlparse(url).scheme == "https"


def _wallhaven_result(entry):
    resolution = entry.get("resolution", "")
    try:
        width, height = (int(value) for value in resolution.split("x", 1))
    except (TypeError, ValueError):
        width = int(entry.get("dimension_x", 0) or 0)
        height = int(entry.get("dimension_y", 0) or 0)
    thumbs = entry.get("thumbs") or {}
    if not isinstance(thumbs, dict):
        thumbs = {}
    tags = entry.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    tags = [tag for tag in tags if isinstance(tag, dict)]
    tag_names = [
        tag["name"] for tag in tags if isinstance(tag.get("name"), str)
    ]
    detail_url = entry.get("short_url") or entry.get("url", "")
    preview_url = thumbs.get("large") or thumbs.get("original") or ""
    return {
        "name": entry.get("title") or entry.get("id") or _("Wallpaper"),
        "personid": "Wallhaven",
        "description": _("Tags: ") + ", ".join(tag_names) if tag_names else "",
        "detailpage": detail_url,
        "previewpic1": preview_url,
        "typeid": 261,
        "provider_wallpaper": {
            "id": entry.get("id", ""),
            "provider": "wallhaven",
            "provider_name": "Wallhaven",
            "download_url": entry.get("path", ""),
            "origin_url": detail_url,
            "width": width,
            "height": height,
            "tags": tag_names,
            "license": "",
        },
    }


def _passes_resolution(item, minimum_width, minimum_height):
    wallpaper = item["provider_wallpaper"]
    if minimum_width == 0 and minimum_height == 0:
        return True
    return (
        wallpaper["width"] >= minimum_width
        and wallpaper["height"] >= minimum_height
    )


def _request(url, parser, on_results, on_error, headers=None):
    def handle_response(response):
        try:
            data = _decode_response(response)
            parsed = parser(data)
            if len(parsed) == 3:
                items, has_more, cursor = parsed
            else:
                items, has_more = parsed
                cursor = None
        except (
            AttributeError,
            IndexError,
            UnicodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            logging.warning("Could not parse wallpaper provider response: %s", error)
            on_error(error)
            return
        try:
            on_results(items, has_more, cursor)
        except TypeError:
            on_results(items, has_more)

    def handle_error(error):
        logging.warning("Wallpaper provider request failed: %s", error)
        on_error(error)

    soup_get(url, handle_response, handle_error, headers=headers)


def get_wallhaven_wallpaper_details(wallpaper_id, callback, on_error):
    url = f"https://wallhaven.cc/api/v1/w/{wallpaper_id}"

    def handle_response(response):
        try:
            data = _decode_response(response)
            item_data = data.get("data", {})
            tags = [
                tag["name"]
                for tag in item_data.get("tags", [])
                if isinstance(tag, dict) and isinstance(tag.get("name"), str)
            ]
            callback({
                "id": item_data.get("id", wallpaper_id),
                "tags": tags,
                "category": item_data.get("category", ""),
                "views": item_data.get("views", 0),
                "favorites": item_data.get("favorites", 0),
            })
        except Exception as error:
            logging.warning("Could not parse Wallhaven wallpaper details: %s", error)
            on_error(error)

    def handle_error(error):
        logging.warning("Wallhaven wallpaper details request failed: %s", error)
        on_error(error)

    soup_get(url, handle_response, handle_error)


def search_wallhaven(
    query, page, minimum_width, minimum_height, callback, on_error, cursor=None
):
    query_str = query.strip() if query else ""
    if query_str:
        q_param = f"{query_str} {WALLHAVEN_NEGATED_QUERY}"
        sorting = "relevance"
    else:
        q_param = f"id:109"
        sorting = "random"

    params_dict = {
        "q": q_param,
        "categories": "100",  # General: 1, Anime: 0, People: 0
        "purity": "100",       # SFW only
        "ratios": "landscape", # Widescreen / Landscape only
        "sorting": sorting,
        "page": page + 1,
    }
    params = urlencode(params_dict)
    url = f"https://wallhaven.cc/api/v1/search?{params}"

    def parse(data):
        results = data.get("data", [])
        items = []
        for entry in results:
            if not isinstance(entry, dict):
                continue
            item = _wallhaven_result(entry)
            title = str(item.get("name", "")).casefold()
            tags = [t.casefold() for t in item["provider_wallpaper"].get("tags", [])]
            if any(bad in title for bad in WALLHAVEN_EXCLUDED_TAGS) or any(
                bad in tag for tag in tags for bad in WALLHAVEN_EXCLUDED_TAGS
            ):
                continue
            if (
                _is_https_url(item["provider_wallpaper"]["download_url"])
                and _is_https_url(item["detailpage"])
                and _is_https_url(item["previewpic1"])
                and _passes_resolution(item, minimum_width, minimum_height)
            ):
                items.append(item)

        meta = data.get("meta", {})
        current_page = int(meta.get("current_page", page + 1))
        last_page = int(meta.get("last_page", current_page))
        has_more = current_page < last_page and bool(results)
        return items, has_more, None

    _request(url, parse, callback, on_error)

