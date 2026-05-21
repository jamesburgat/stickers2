from __future__ import annotations

import copy
import datetime as dt
import io
import json
import math
import os
import pathlib
import secrets
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional local convenience dependency
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

load_dotenv()

BASE_DIR = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = pathlib.Path(os.environ.get("STICKER_CONFIG_PATH") or (BASE_DIR / "presentation_config.json"))
LOG_DIR = pathlib.Path(os.environ.get("LOG_DIR") or (BASE_DIR / "logs"))
FIGMA_CACHE_DIR = pathlib.Path(os.environ.get("FIGMA_CACHE_DIR") or (BASE_DIR / "cache" / "figma"))
HISTORY_LOG = LOG_DIR / "print_history.jsonl"

LOG_DIR.mkdir(parents=True, exist_ok=True)
FIGMA_CACHE_DIR.mkdir(parents=True, exist_ok=True)

PRINTER_TRANSFER_RASTER = (os.environ.get("PRINTER_TRANSFER_RASTER") or "Zebra_Transfer_300").strip()
TRANSFER_DPI = int(os.environ.get("TRANSFER_DPI") or "300")
DEFAULT_IMAGE_THRESHOLD = int(os.environ.get("IMAGE_THRESHOLD_DEFAULT") or "160")
ADMIN_PASSWORD = (os.environ.get("ADMIN_PASSWORD") or "").strip()
ADMIN_ENABLED = bool(ADMIN_PASSWORD)
SECRET_KEY = (os.environ.get("SECRET_KEY") or "presentation-stickers-dev").strip()
PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
DEFAULT_FIGMA_TOKEN_ENV = (os.environ.get("FIGMA_TOKEN_ENV") or "FIGMA_TOKEN").strip()
DEFAULT_FONT_SIZE = int(os.environ.get("DEFAULT_FONT_SIZE") or "54")
DEFAULT_FONT_PATHS = [
    BASE_DIR / "static" / "fonts" / "PanopticaOctagonal Regular.ttf",
    pathlib.Path("/Users/jamesburgat/Documents/stickers/static/fonts/PanopticaOctagonal Regular.ttf"),
    pathlib.Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]
ASSET_VERSION = "2026-05-20-presentation"
BASE_PATH = "/" + str(os.environ.get("BASE_PATH") or "").strip().strip("/")
if BASE_PATH == "/":
    BASE_PATH = ""

DEFAULT_PROFILE_KEY = "presentation-transfer"
DEFAULT_ORANGE_PRESET_KEY = "square-2x2-orange"
DEFAULT_YELLOW_PRESET_KEY = "square-3x3-yellow"
COUNTDOWN_TEMPLATE = "{{minutes_left}} Mins left"

app = Flask(__name__, static_url_path=(f"{BASE_PATH}/static" if BASE_PATH else "/static"))
app.secret_key = SECRET_KEY

CONFIG_LOCK = threading.RLock()
CONFIG_MTIME: float | None = None
CONFIG: dict[str, Any] = {}
PRINTER_PROFILES: dict[str, dict[str, Any]] = {}
DEFAULT_PROFILE: dict[str, Any] = {}
SCHEDULER_STARTED = False
FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _public_path(path: str) -> str:
    normalized = "/" + str(path or "").lstrip("/")
    if BASE_PATH:
        return f"{BASE_PATH}{normalized}"
    return normalized


def _now_local() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _coerce_positive_int(value: Any, fallback: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return fallback
    return num if num > 0 else fallback


def _coerce_non_negative_int(value: Any, fallback: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return fallback
    return num if num >= 0 else fallback


def _coerce_positive_float(value: Any, fallback: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return fallback
    return num if num > 0 else fallback


def _coerce_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return fallback


def _deepcopy(value: Any) -> Any:
    return copy.deepcopy(value)


def _media_for_inches(width_in: float, height_in: float) -> str:
    return f"w{int(round(width_in * 72))}h{int(round(height_in * 72))}"


def _label_for_preset(preset: dict[str, Any]) -> str:
    return f'{float(preset["width_in"]):g}" x {float(preset["height_in"]):g}" {preset["name"]}'


def _default_paper_presets() -> list[dict[str, Any]]:
    return [
        {
            "key": DEFAULT_ORANGE_PRESET_KEY,
            "name": "Orange 2x2",
            "width_in": 2.0,
            "height_in": 2.0,
            "theme_color": "#f26f21",
            "media": "",
        },
        {
            "key": DEFAULT_YELLOW_PRESET_KEY,
            "name": "Yellow 3x3",
            "width_in": 3.0,
            "height_in": 3.0,
            "theme_color": "#f4d63c",
            "media": "",
        },
    ]


def _default_profile() -> dict[str, Any]:
    return {
        "key": DEFAULT_PROFILE_KEY,
        "name": "Presentation Printer",
        "cups_queue": PRINTER_TRANSFER_RASTER or "Zebra_Transfer_300",
        "dpi": TRANSFER_DPI or 300,
        "print_mode": "thermal_transfer",
        "enabled": True,
        "paper_presets": _default_paper_presets(),
        "active_paper_key": DEFAULT_ORANGE_PRESET_KEY,
    }


def _default_config() -> dict[str, Any]:
    profile = _default_profile()
    return {
        "printer_profiles": [profile],
        "active_profile_key": profile["key"],
        "figma": {
            "file_key": "",
            "token_env": DEFAULT_FIGMA_TOKEN_ENV,
            "default_scale": 2,
            "cached_frames": [],
            "last_sync_at": "",
            "last_error": "",
        },
        "plans": [],
        "active_plan_id": "",
        "countdown_timer": _default_timer_state(),
    }


def _default_timer_state() -> dict[str, Any]:
    return {
        "status": "idle",
        "start_at": "",
        "end_at": "",
        "preset_key": DEFAULT_ORANGE_PRESET_KEY,
        "text_template": COUNTDOWN_TEMPLATE,
        "selected_history_ids": [],
        "printed_tick_keys": [],
        "started_at": "",
        "completed_at": "",
        "end_printed_at": "",
    }


def _normalize_preset(raw: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    preset = _deepcopy(fallback)
    if not isinstance(raw, dict):
        return preset
    preset["key"] = str(raw.get("key") or fallback["key"]).strip() or fallback["key"]
    preset["name"] = str(raw.get("name") or fallback["name"]).strip() or fallback["name"]
    preset["width_in"] = _coerce_positive_float(raw.get("width_in"), float(fallback["width_in"]))
    preset["height_in"] = _coerce_positive_float(raw.get("height_in"), float(fallback["height_in"]))
    preset["theme_color"] = str(raw.get("theme_color") or fallback["theme_color"]).strip() or fallback["theme_color"]
    preset["media"] = str(raw.get("media") or "").strip()
    return preset


def _normalize_profile(raw: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    profile = _deepcopy(fallback)
    if not isinstance(raw, dict):
        return profile
    profile["key"] = str(raw.get("key") or fallback["key"]).strip() or fallback["key"]
    profile["name"] = str(raw.get("name") or fallback["name"]).strip() or fallback["name"]
    profile["cups_queue"] = str(raw.get("cups_queue") or fallback["cups_queue"]).strip() or fallback["cups_queue"]
    profile["dpi"] = _coerce_positive_int(raw.get("dpi"), int(fallback["dpi"]))
    profile["print_mode"] = "thermal_transfer"
    profile["enabled"] = _coerce_bool(raw.get("enabled"), True)
    raw_presets = raw.get("paper_presets")
    presets = []
    if isinstance(raw_presets, list):
        defaults = {preset["key"]: preset for preset in _default_paper_presets()}
        for item in raw_presets:
            key = str(item.get("key") or "") if isinstance(item, dict) else ""
            fallback_preset = defaults.get(key) or defaults[DEFAULT_ORANGE_PRESET_KEY]
            presets.append(_normalize_preset(item, fallback_preset))
    if not presets:
        presets = _default_paper_presets()
    profile["paper_presets"] = presets
    active_key = str(raw.get("active_paper_key") or profile.get("active_paper_key") or "").strip()
    valid_keys = {preset["key"] for preset in presets}
    profile["active_paper_key"] = active_key if active_key in valid_keys else presets[0]["key"]
    return profile


def _normalize_figma(raw: Any) -> dict[str, Any]:
    fallback = _default_config()["figma"]
    figma = _deepcopy(fallback)
    if not isinstance(raw, dict):
        return figma
    figma["file_key"] = str(raw.get("file_key") or "").strip()
    figma["token_env"] = str(raw.get("token_env") or DEFAULT_FIGMA_TOKEN_ENV).strip() or DEFAULT_FIGMA_TOKEN_ENV
    figma["default_scale"] = max(1, min(_coerce_positive_int(raw.get("default_scale"), 2), 4))
    raw_frames = raw.get("cached_frames")
    frames: list[dict[str, Any]] = []
    if isinstance(raw_frames, list):
        for frame in raw_frames:
            if not isinstance(frame, dict):
                continue
            frames.append(
                {
                    "id": str(frame.get("id") or "").strip(),
                    "name": str(frame.get("name") or "").strip(),
                    "page_name": str(frame.get("page_name") or "").strip(),
                    "path": str(frame.get("path") or "").strip(),
                }
            )
    figma["cached_frames"] = [frame for frame in frames if frame["id"] and frame["name"]]
    figma["last_sync_at"] = str(raw.get("last_sync_at") or "").strip()
    figma["last_error"] = str(raw.get("last_error") or "").strip()
    return figma


def _normalize_plan_item(raw: Any) -> dict[str, Any]:
    item = {
        "id": _new_id("item"),
        "kind": "frame",
        "name": "Sticker",
        "frame_id": "",
        "frame_name": "",
        "preset_key": DEFAULT_ORANGE_PRESET_KEY,
        "copies": 1,
        "threshold": DEFAULT_IMAGE_THRESHOLD,
        "overlay_text": "",
        "overlay_position": "bottom",
        "offset_seconds": 0,
        "run_at": "",
        "first_offset_seconds": 0,
        "first_run_at": "",
        "start_minutes": 4,
        "end_minutes": 0,
        "interval_seconds": 60,
        "text_template": COUNTDOWN_TEMPLATE,
        "printed_at": "",
        "printed_ticks": [],
    }
    if not isinstance(raw, dict):
        return item
    item["id"] = str(raw.get("id") or item["id"]).strip() or item["id"]
    kind = str(raw.get("kind") or "frame").strip().lower()
    item["kind"] = kind if kind in {"frame", "countdown"} else "frame"
    item["name"] = str(raw.get("name") or item["name"]).strip() or item["name"]
    item["frame_id"] = str(raw.get("frame_id") or "").strip()
    item["frame_name"] = str(raw.get("frame_name") or "").strip()
    item["preset_key"] = str(raw.get("preset_key") or DEFAULT_ORANGE_PRESET_KEY).strip() or DEFAULT_ORANGE_PRESET_KEY
    item["copies"] = max(1, min(_coerce_positive_int(raw.get("copies"), 1), 10))
    item["threshold"] = max(1, min(_coerce_positive_int(raw.get("threshold"), DEFAULT_IMAGE_THRESHOLD), 255))
    item["overlay_text"] = str(raw.get("overlay_text") or "").strip()
    position = str(raw.get("overlay_position") or "bottom").strip().lower()
    item["overlay_position"] = position if position in {"top", "center", "bottom"} else "bottom"
    item["offset_seconds"] = _coerce_non_negative_int(raw.get("offset_seconds"), 0)
    item["run_at"] = str(raw.get("run_at") or "").strip()
    item["first_offset_seconds"] = _coerce_non_negative_int(raw.get("first_offset_seconds"), item["offset_seconds"])
    item["first_run_at"] = str(raw.get("first_run_at") or raw.get("run_at") or "").strip()
    item["start_minutes"] = _coerce_non_negative_int(raw.get("start_minutes"), 4)
    item["end_minutes"] = _coerce_non_negative_int(raw.get("end_minutes"), 0)
    item["interval_seconds"] = max(1, _coerce_positive_int(raw.get("interval_seconds"), 60))
    item["text_template"] = str(raw.get("text_template") or COUNTDOWN_TEMPLATE).strip() or COUNTDOWN_TEMPLATE
    item["printed_at"] = str(raw.get("printed_at") or "").strip()
    ticks = raw.get("printed_ticks")
    item["printed_ticks"] = [str(tick).strip() for tick in ticks] if isinstance(ticks, list) else []
    return item


def _normalize_plan(raw: Any) -> dict[str, Any]:
    plan = {
        "id": _new_id("plan"),
        "name": "New Presentation Plan",
        "notes": "",
        "mode": "relative",
        "start_at": "",
        "started_at": "",
        "stopped_at": "",
        "completed_at": "",
        "status": "draft",
        "items": [],
    }
    if not isinstance(raw, dict):
        return plan
    plan["id"] = str(raw.get("id") or plan["id"]).strip() or plan["id"]
    plan["name"] = str(raw.get("name") or plan["name"]).strip() or plan["name"]
    plan["notes"] = str(raw.get("notes") or "").strip()
    mode = str(raw.get("mode") or "relative").strip().lower()
    plan["mode"] = mode if mode in {"relative", "absolute"} else "relative"
    plan["start_at"] = str(raw.get("start_at") or "").strip()
    plan["started_at"] = str(raw.get("started_at") or "").strip()
    plan["stopped_at"] = str(raw.get("stopped_at") or "").strip()
    plan["completed_at"] = str(raw.get("completed_at") or "").strip()
    status = str(raw.get("status") or "draft").strip().lower()
    plan["status"] = status if status in {"draft", "armed", "running", "paused", "done"} else "draft"
    items = raw.get("items")
    if isinstance(items, list):
        plan["items"] = [_normalize_plan_item(item) for item in items]
    return plan


def _normalize_timer_state(raw: Any) -> dict[str, Any]:
    timer = _deepcopy(_default_timer_state())
    if not isinstance(raw, dict):
        return timer
    status = str(raw.get("status") or "idle").strip().lower()
    timer["status"] = status if status in {"idle", "armed", "running", "completed"} else "idle"
    timer["start_at"] = str(raw.get("start_at") or "").strip()
    timer["end_at"] = str(raw.get("end_at") or "").strip()
    timer["preset_key"] = str(raw.get("preset_key") or DEFAULT_ORANGE_PRESET_KEY).strip() or DEFAULT_ORANGE_PRESET_KEY
    timer["text_template"] = str(raw.get("text_template") or COUNTDOWN_TEMPLATE).strip() or COUNTDOWN_TEMPLATE
    selected = raw.get("selected_history_ids")
    timer["selected_history_ids"] = [str(item).strip() for item in selected] if isinstance(selected, list) else []
    printed = raw.get("printed_tick_keys")
    timer["printed_tick_keys"] = [str(item).strip() for item in printed] if isinstance(printed, list) else []
    timer["started_at"] = str(raw.get("started_at") or "").strip()
    timer["completed_at"] = str(raw.get("completed_at") or "").strip()
    timer["end_printed_at"] = str(raw.get("end_printed_at") or "").strip()
    return timer


def _normalize_config(raw: Any) -> dict[str, Any]:
    fallback = _default_config()
    if not isinstance(raw, dict):
        raw = {}
    profiles = raw.get("printer_profiles")
    normalized_profiles = []
    if isinstance(profiles, list):
        for profile in profiles:
            normalized_profiles.append(_normalize_profile(profile, _default_profile()))
    if not normalized_profiles:
        normalized_profiles = [_default_profile()]
    active_profile_key = str(raw.get("active_profile_key") or normalized_profiles[0]["key"]).strip()
    valid_profile_keys = {profile["key"] for profile in normalized_profiles}
    if active_profile_key not in valid_profile_keys:
        active_profile_key = normalized_profiles[0]["key"]
    plans = raw.get("plans")
    normalized_plans = [_normalize_plan(plan) for plan in plans] if isinstance(plans, list) else []
    active_plan_id = str(raw.get("active_plan_id") or "").strip()
    if active_plan_id and active_plan_id not in {plan["id"] for plan in normalized_plans}:
        active_plan_id = ""
    return {
        "printer_profiles": normalized_profiles,
        "active_profile_key": active_profile_key,
        "figma": _normalize_figma(raw.get("figma")),
        "plans": normalized_plans,
        "active_plan_id": active_plan_id,
        "countdown_timer": _normalize_timer_state(raw.get("countdown_timer")),
    }


def _load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        config = _default_config()
        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return config
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = _default_config()
    return _normalize_config(data)


def _build_preset_runtime(preset: dict[str, Any], dpi: int) -> dict[str, Any]:
    width_in = float(preset["width_in"])
    height_in = float(preset["height_in"])
    explicit_media = str(preset.get("media") or "").strip()
    return {
        **preset,
        "label": _label_for_preset(preset),
        "px_w": int(round(width_in * dpi)),
        "px_h": int(round(height_in * dpi)),
        # Leave media blank unless the preset explicitly sets one so CUPS can
        # use the computed PageSize option for square labels.
        "media": explicit_media,
        "computed_media": _media_for_inches(width_in, height_in),
    }


def _apply_config(config: dict[str, Any]) -> None:
    global CONFIG, PRINTER_PROFILES, DEFAULT_PROFILE
    runtime_profiles: dict[str, dict[str, Any]] = {}
    for profile in config["printer_profiles"]:
        dpi = _coerce_positive_int(profile.get("dpi"), TRANSFER_DPI)
        preset_map: dict[str, dict[str, Any]] = {}
        for preset in profile.get("paper_presets", []):
            runtime = _build_preset_runtime(preset, dpi)
            preset_map[runtime["key"]] = runtime
        active_key = str(profile.get("active_paper_key") or "").strip()
        if active_key not in preset_map:
            active_key = next(iter(preset_map))
        runtime_profile = {
            **profile,
            "dpi": dpi,
            "preset_map": preset_map,
            "active_paper_key": active_key,
            "active_preset": preset_map[active_key],
        }
        runtime_profiles[runtime_profile["key"]] = runtime_profile
    active_profile = runtime_profiles.get(config.get("active_profile_key")) or next(iter(runtime_profiles.values()))
    PRINTER_PROFILES = runtime_profiles
    DEFAULT_PROFILE = active_profile
    CONFIG = _deepcopy(config)


def _save_config(config: dict[str, Any]) -> None:
    global CONFIG_MTIME
    normalized = _normalize_config(config)
    CONFIG_PATH.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    _apply_config(normalized)
    try:
        CONFIG_MTIME = CONFIG_PATH.stat().st_mtime
    except OSError:
        CONFIG_MTIME = None


def _refresh_config_from_disk() -> None:
    global CONFIG_MTIME
    try:
        mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else None
    except OSError:
        mtime = None
    if mtime == CONFIG_MTIME and CONFIG:
        return
    with CONFIG_LOCK:
        config = _load_config()
        _apply_config(config)
        CONFIG_MTIME = mtime


def _get_profile(profile_key: str | None = None) -> dict[str, Any]:
    if profile_key and profile_key in PRINTER_PROFILES:
        return PRINTER_PROFILES[profile_key]
    return DEFAULT_PROFILE


def _get_preset(profile: dict[str, Any], preset_key: str | None = None) -> dict[str, Any]:
    if preset_key and preset_key in profile["preset_map"]:
        return profile["preset_map"][preset_key]
    return profile["active_preset"]


def _page_size_for_pixels(px_w: int, px_h: int, dpi: int, fallback: str) -> str:
    if dpi <= 0:
        return fallback
    width_pts = int(round(px_w / dpi * 72))
    height_pts = int(round(px_h / dpi * 72))
    if width_pts <= 0 or height_pts <= 0:
        return fallback
    return f"w{width_pts}h{height_pts}"


def _normalize_print_bitmap(pil_img: Image.Image) -> Image.Image:
    if pil_img.mode in {"RGBA", "LA"} or (pil_img.mode == "P" and "transparency" in pil_img.info):
        bg = Image.new("RGBA", pil_img.size, (255, 255, 255, 255))
        bg.paste(pil_img, (0, 0), pil_img.convert("RGBA"))
        pil_img = bg
    return pil_img.convert("L")


def _print_via_raster(pil_img: Image.Image, profile: dict[str, Any], media: str) -> None:
    pil_img = _normalize_print_bitmap(pil_img)
    dpi = _coerce_positive_int(profile.get("dpi"), TRANSFER_DPI)
    queue = str(profile.get("cups_queue") or "").strip()
    if not queue:
        raise RuntimeError("Printer queue is not configured.")
    px_w, px_h = pil_img.size
    fallback_media = _media_for_inches(px_w / dpi, px_h / dpi)
    explicit_media = str(media or "").strip()
    size_opt_value = explicit_media or _page_size_for_pixels(px_w, px_h, dpi, fallback_media)
    size_opt_name = "media" if explicit_media else "PageSize"
    tmp = LOG_DIR / f"_print_{dt.datetime.now().strftime('%Y%m%d%H%M%S%f')}.png"
    try:
        pil_img.save(tmp, format="PNG", dpi=(dpi, dpi))
        cmd = [
            "lp",
            "-d",
            queue,
            "-o",
            f"{size_opt_name}={size_opt_value}",
            "-o",
            "orientation-requested=3",
            "-o",
            f"Resolution={dpi}dpi",
            "-o",
            "print-scaling=none",
            "-o",
            "position=TopLeft",
            "-o",
            "page-left=0",
            "-o",
            "page-right=0",
            "-o",
            "page-top=0",
            "-o",
            "page-bottom=0",
            str(tmp),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _load_font(size: int) -> ImageFont.ImageFont:
    size = max(12, size)
    for path in DEFAULT_FONT_PATHS:
        cache_key = (str(path), size)
        if cache_key in FONT_CACHE:
            return FONT_CACHE[cache_key]
        if path.exists():
            font = ImageFont.truetype(str(path), size=size)
            FONT_CACHE[cache_key] = font
            return font
    return ImageFont.load_default()


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, max_height: int, base_size: int) -> ImageFont.ImageFont:
    size = base_size
    while size >= 16:
        font = _load_font(size)
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=max(4, size // 10), align="center")
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width <= max_width and height <= max_height:
            return font
        size -= 2
    return _load_font(16)


def _draw_outlined_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, position: str) -> None:
    if not text:
        return
    left, top, right, bottom = box
    width = max(10, right - left)
    height = max(10, bottom - top)
    font = _fit_font(draw, text, int(width * 0.9), int(height * 0.8), DEFAULT_FONT_SIZE)
    spacing = max(4, getattr(font, "size", DEFAULT_FONT_SIZE) // 10)
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align="center")
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = left + (width - text_w) / 2 - bbox[0]
    if position == "top":
        y = top + height * 0.08 - bbox[1]
    elif position == "center":
        y = top + (height - text_h) / 2 - bbox[1]
    else:
        y = bottom - text_h - height * 0.08 - bbox[1]
    stroke = max(2, int(getattr(font, "size", DEFAULT_FONT_SIZE) * 0.08))
    draw.multiline_text(
        (x, y),
        text,
        fill=0,
        font=font,
        align="center",
        spacing=spacing,
        stroke_width=stroke,
        stroke_fill=255,
    )


def _flatten_to_white(image: Image.Image) -> Image.Image:
    if image.mode == "RGBA":
        bg = Image.new("RGBA", image.size, (255, 255, 255, 255))
        bg.paste(image, (0, 0), image)
        image = bg
    elif image.mode != "RGB":
        image = image.convert("RGB")
    return image.convert("RGB")


def _build_diamond_layout(src: Image.Image, px_w: int, px_h: int, overlay_text: str = "", overlay_position: str = "bottom") -> Image.Image:
    src = _flatten_to_white(src)
    canvas = Image.new("RGBA", (px_w, px_h), (255, 255, 255, 255))
    inner_side = max(1, int(round(min(px_w, px_h) / math.sqrt(2))))
    fitted = ImageOps.contain(src.convert("RGBA"), (inner_side, inner_side), method=Image.LANCZOS)
    tile = Image.new("RGBA", (inner_side, inner_side), (255, 255, 255, 0))
    offset = ((inner_side - fitted.width) // 2, (inner_side - fitted.height) // 2)
    tile.paste(fitted, offset, fitted)
    rotated = tile.rotate(45, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(255, 255, 255, 0))
    paste_x = (px_w - rotated.width) // 2
    paste_y = (px_h - rotated.height) // 2
    canvas.paste(rotated, (paste_x, paste_y), rotated)
    if overlay_text:
        draw = ImageDraw.Draw(canvas)
        pad = max(12, int(px_w * 0.06))
        _draw_outlined_text(draw, (pad, pad, px_w - pad, px_h - pad), overlay_text, overlay_position)
    return canvas


def _to_print_bitmap(image: Image.Image, threshold: int) -> Image.Image:
    gray = ImageOps.autocontrast(_flatten_to_white(image).convert("L"))
    bw = gray.point(lambda p: 255 if p > threshold else 0, mode="1")
    return bw.rotate(180, expand=False)


def _fit_image_to_label(src: Image.Image, px_w: int, px_h: int) -> Image.Image:
    return ImageOps.fit(src, (px_w, px_h), method=Image.LANCZOS, centering=(0.5, 0.5))


def _build_text_sticker(text: str, px_w: int, px_h: int) -> Image.Image:
    canvas = Image.new("RGBA", (px_w, px_h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    pad = max(16, int(min(px_w, px_h) * 0.08))
    _draw_outlined_text(draw, (pad, pad, px_w - pad, px_h - pad), text, "center")
    return _to_print_bitmap(canvas, 200)


def _append_history(entry: dict[str, Any]) -> None:
    with HISTORY_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=True) + "\n")


def _normalize_history_entry(entry: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(entry)
    image_path = str(normalized.get("image_path") or "").strip()
    history_id = str(normalized.get("history_id") or "").strip()
    if not history_id:
        history_id = pathlib.Path(image_path).name if image_path else str(normalized.get("timestamp") or _new_id("history"))
    normalized["history_id"] = history_id
    if not normalized.get("label"):
        normalized["label"] = str(normalized.get("overlay_text") or normalized.get("item_name") or normalized.get("plan_name") or normalized.get("kind") or "Sticker")
    return normalized


def _history_entries(limit: int | None = None) -> list[dict[str, Any]]:
    if not HISTORY_LOG.exists():
        return []
    try:
        lines = HISTORY_LOG.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    source_lines = lines[-limit:] if limit is not None else lines
    for line in source_lines:
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if isinstance(parsed, dict):
            rows.append(_normalize_history_entry(parsed))
    return rows


def _recent_history(limit: int = 12) -> list[dict[str, Any]]:
    rows = _history_entries(limit)
    rows.reverse()
    return rows


def _history_map() -> dict[str, dict[str, Any]]:
    return {entry["history_id"]: entry for entry in _history_entries(None)}


def _parse_dt(value: str) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def _iso(value: dt.datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else ""


def _human_schedule(value: str) -> str:
    parsed = _parse_dt(value)
    if not parsed:
        return ""
    return parsed.strftime("%b %d %I:%M:%S %p")


def _format_offset(seconds: int) -> str:
    seconds = max(0, int(seconds))
    mins, sec = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}:{mins:02d}:{sec:02d}"
    return f"{mins}:{sec:02d}"


def _active_plan(config: dict[str, Any]) -> dict[str, Any] | None:
    active_id = str(config.get("active_plan_id") or "").strip()
    for plan in config.get("plans", []):
        if plan.get("id") == active_id:
            return plan
    return None


def _resolve_relative_anchor(plan: dict[str, Any]) -> dt.datetime | None:
    started = _parse_dt(str(plan.get("started_at") or ""))
    if started:
        return started
    if str(plan.get("status") or "") == "armed":
        return _parse_dt(str(plan.get("start_at") or ""))
    return None


def _frame_due_at(plan: dict[str, Any], item: dict[str, Any]) -> dt.datetime | None:
    if plan["mode"] == "absolute":
        return _parse_dt(item.get("run_at") or "")
    anchor = _resolve_relative_anchor(plan)
    if not anchor:
        return None
    return anchor + dt.timedelta(seconds=int(item.get("offset_seconds") or 0))


def _countdown_due_jobs(plan: dict[str, Any], item: dict[str, Any], now: dt.datetime) -> list[dict[str, Any]]:
    if plan["mode"] == "absolute":
        first_due = _parse_dt(item.get("first_run_at") or item.get("run_at") or "")
    else:
        anchor = _resolve_relative_anchor(plan)
        first_due = anchor + dt.timedelta(seconds=int(item.get("first_offset_seconds") or 0)) if anchor else None
    if not first_due:
        return []
    start_minutes = int(item.get("start_minutes") or 0)
    end_minutes = int(item.get("end_minutes") or 0)
    step = 1 if end_minutes >= start_minutes else -1
    interval = max(1, int(item.get("interval_seconds") or 60))
    printed = set(item.get("printed_ticks") or [])
    due_jobs = []
    index = 0
    for minutes_left in range(start_minutes, end_minutes + step, step):
        due_at = first_due + dt.timedelta(seconds=index * interval)
        tick_key = f"{item['id']}:{minutes_left}"
        if tick_key not in printed and due_at <= now:
            due_jobs.append(
                {
                    "job_key": tick_key,
                    "due_at": due_at,
                    "minutes_left": minutes_left,
                    "overlay_text": str(item.get("text_template") or COUNTDOWN_TEMPLATE).replace("{{minutes_left}}", str(minutes_left)),
                }
            )
        index += 1
    return due_jobs


def _pending_jobs_for_plan(plan: dict[str, Any], now: dt.datetime) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for index, item in enumerate(plan.get("items", [])):
        if item.get("kind") == "frame":
            due_at = _frame_due_at(plan, item)
            if due_at and due_at <= now and not item.get("printed_at"):
                jobs.append(
                    {
                        "job_key": item["id"],
                        "item_id": item["id"],
                        "item_index": index,
                        "due_at": due_at,
                        "overlay_text": item.get("overlay_text") or "",
                    }
                )
        else:
            for job in _countdown_due_jobs(plan, item, now):
                jobs.append(
                    {
                        "item_id": item["id"],
                        "item_index": index,
                        **job,
                    }
                )
    jobs.sort(key=lambda job: (job["due_at"], job["item_index"], job["job_key"]))
    return jobs


def _remaining_jobs_for_plan(plan: dict[str, Any], now: dt.datetime | None = None) -> list[dict[str, Any]]:
    now = now or _now_local()
    jobs: list[dict[str, Any]] = []
    for index, item in enumerate(plan.get("items", [])):
        if item.get("kind") == "frame":
            due_at = _frame_due_at(plan, item)
            if due_at and not item.get("printed_at"):
                jobs.append(
                    {
                        "job_key": item["id"],
                        "item_index": index,
                        "name": item.get("name") or item.get("frame_name") or "Sticker",
                        "due_at": due_at,
                        "past_due": due_at <= now,
                    }
                )
        else:
            if plan["mode"] == "absolute":
                first_due = _parse_dt(item.get("first_run_at") or item.get("run_at") or "")
            else:
                anchor = _resolve_relative_anchor(plan)
                first_due = anchor + dt.timedelta(seconds=int(item.get("first_offset_seconds") or 0)) if anchor else None
            if not first_due:
                continue
            printed = set(item.get("printed_ticks") or [])
            start_minutes = int(item.get("start_minutes") or 0)
            end_minutes = int(item.get("end_minutes") or 0)
            step = 1 if end_minutes >= start_minutes else -1
            interval = max(1, int(item.get("interval_seconds") or 60))
            idx = 0
            for minutes_left in range(start_minutes, end_minutes + step, step):
                tick_key = f"{item['id']}:{minutes_left}"
                if tick_key in printed:
                    idx += 1
                    continue
                due_at = first_due + dt.timedelta(seconds=idx * interval)
                jobs.append(
                    {
                        "job_key": tick_key,
                        "item_index": index,
                        "name": str(item.get("text_template") or COUNTDOWN_TEMPLATE).replace("{{minutes_left}}", str(minutes_left)),
                        "due_at": due_at,
                        "past_due": due_at <= now,
                    }
                )
                idx += 1
    jobs.sort(key=lambda job: (job["due_at"], job["item_index"], job["job_key"]))
    return jobs


def _plan_is_complete(plan: dict[str, Any]) -> bool:
    for item in plan.get("items", []):
        if item.get("kind") == "frame":
            if not item.get("printed_at"):
                return False
            continue
        start_minutes = int(item.get("start_minutes") or 0)
        end_minutes = int(item.get("end_minutes") or 0)
        expected = abs(end_minutes - start_minutes) + 1
        if len(item.get("printed_ticks") or []) < expected:
            return False
    return True


def _timer_summary(timer: dict[str, Any]) -> dict[str, Any]:
    summary = _deepcopy(timer)
    summary["start_at_label"] = _human_schedule(timer.get("start_at") or "")
    summary["end_at_label"] = _human_schedule(timer.get("end_at") or "")
    summary["history_count"] = len(timer.get("selected_history_ids") or [])
    start_at = _parse_dt(timer.get("start_at") or "")
    end_at = _parse_dt(timer.get("end_at") or "")
    if start_at and end_at and end_at > start_at:
        summary["minute_count"] = max(1, math.ceil((end_at - start_at).total_seconds() / 60))
    else:
        summary["minute_count"] = 0
    return summary


def _timer_tick_jobs(timer: dict[str, Any], now: dt.datetime) -> list[dict[str, Any]]:
    start_at = _parse_dt(timer.get("start_at") or "")
    end_at = _parse_dt(timer.get("end_at") or "")
    if not start_at or not end_at or end_at <= start_at or now < start_at:
        return []
    printed = set(timer.get("printed_tick_keys") or [])
    jobs: list[dict[str, Any]] = []
    tick_time = start_at
    while tick_time < end_at and tick_time <= now:
        job_key = tick_time.isoformat(timespec="seconds")
        if job_key not in printed:
            minutes_left = max(1, math.ceil((end_at - tick_time).total_seconds() / 60))
            jobs.append({"job_key": job_key, "tick_time": tick_time, "minutes_left": minutes_left})
        tick_time += dt.timedelta(minutes=1)
    return jobs


def _print_timer_tick(timer: dict[str, Any], job: dict[str, Any], config: dict[str, Any]) -> None:
    profile = _get_profile(config.get("active_profile_key"))
    preset = _get_preset(profile, timer.get("preset_key"))
    label = str(timer.get("text_template") or COUNTDOWN_TEMPLATE).replace("{{minutes_left}}", str(job["minutes_left"]))
    bitmap = _build_text_sticker(label, int(preset["px_w"]), int(preset["px_h"]))
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    image_path = LOG_DIR / f"countdown_{timestamp}_{preset['key']}.png"
    bitmap.save(image_path, dpi=(profile["dpi"], profile["dpi"]))
    _print_via_raster(bitmap, profile, str(preset["media"]))
    _record_print_history("countdown", label, preset, image_path, {"minutes_left": job["minutes_left"]})


def _print_timer_end_history(timer: dict[str, Any], config: dict[str, Any]) -> None:
    selected = timer.get("selected_history_ids") or []
    if not selected:
        return
    history_map = _history_map()
    profile = _get_profile(config.get("active_profile_key"))
    for history_id in selected:
        entry = history_map.get(str(history_id))
        if not entry:
            continue
        _print_history_item(entry, profile)


def _run_countdown_timer_tick(config: dict[str, Any], now: dt.datetime) -> bool:
    timer = config.get("countdown_timer")
    if not isinstance(timer, dict):
        return False
    if timer.get("status") not in {"armed", "running"}:
        return False
    start_at = _parse_dt(timer.get("start_at") or "")
    end_at = _parse_dt(timer.get("end_at") or "")
    if not start_at or not end_at or end_at <= start_at:
        timer["status"] = "completed"
        timer["completed_at"] = _iso(now)
        return True
    changed = False
    if now >= start_at and timer.get("status") == "armed":
        timer["status"] = "running"
        timer["started_at"] = _iso(now)
        changed = True
    for job in _timer_tick_jobs(timer, now):
        _print_timer_tick(timer, job, config)
        printed = set(timer.get("printed_tick_keys") or [])
        printed.add(job["job_key"])
        timer["printed_tick_keys"] = sorted(printed)
        changed = True
    if now >= end_at and not timer.get("end_printed_at"):
        _print_timer_end_history(timer, config)
        timer["end_printed_at"] = _iso(now)
        timer["completed_at"] = _iso(now)
        timer["status"] = "completed"
        changed = True
    return changed


def _figma_token(config: dict[str, Any]) -> str:
    token_env = str(config.get("figma", {}).get("token_env") or DEFAULT_FIGMA_TOKEN_ENV).strip() or DEFAULT_FIGMA_TOKEN_ENV
    return (os.environ.get(token_env) or "").strip()


def _figma_api_json(file_key: str, path: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
    with CONFIG_LOCK:
        token = _figma_token(CONFIG)
    if not token:
        raise RuntimeError("Figma token is missing. Set the configured token env var.")
    url = f"https://api.figma.com{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
    req = urllib.request.Request(url, headers={"X-Figma-Token": token})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _walk_figma_frames(node: dict[str, Any], parents: list[str], frames: list[dict[str, Any]]) -> None:
    node_type = str(node.get("type") or "")
    name = str(node.get("name") or "").strip()
    next_parents = parents + ([name] if name else [])
    if node_type == "FRAME":
        page_name = parents[0] if parents else ""
        frames.append(
            {
                "id": str(node.get("id") or "").strip(),
                "name": name or "Untitled Frame",
                "page_name": page_name,
                "path": " / ".join(next_parents),
            }
        )
    for child in node.get("children", []) or []:
        if isinstance(child, dict):
            _walk_figma_frames(child, next_parents, frames)


def _refresh_figma_frames(config: dict[str, Any]) -> list[dict[str, Any]]:
    file_key = str(config.get("figma", {}).get("file_key") or "").strip()
    if not file_key:
        raise RuntimeError("Add a Figma file key first.")
    payload = _figma_api_json(file_key, f"/v1/files/{file_key}")
    frames: list[dict[str, Any]] = []
    document = payload.get("document") or {}
    if isinstance(document, dict):
        _walk_figma_frames(document, [], frames)
    deduped = []
    seen = set()
    for frame in frames:
        key = frame["id"]
        if key and key not in seen:
            seen.add(key)
            deduped.append(frame)
    config["figma"]["cached_frames"] = deduped
    config["figma"]["last_sync_at"] = _iso(_now_local())
    config["figma"]["last_error"] = ""
    return deduped


def _fetch_figma_frame_image(file_key: str, frame_id: str, scale: int) -> Image.Image:
    scale = max(1, min(scale, 4))
    meta = _figma_api_json(
        file_key,
        f"/v1/images/{file_key}",
        {"ids": frame_id, "format": "png", "scale": scale},
    )
    image_url = str((meta.get("images") or {}).get(frame_id) or "").strip()
    if not image_url:
        raise RuntimeError(f"Figma export failed for frame {frame_id}.")
    req = urllib.request.Request(image_url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    cache_path = FIGMA_CACHE_DIR / f"{frame_id}.png"
    cache_path.write_bytes(data)
    return Image.open(io.BytesIO(data)).convert("RGBA")


def _load_frame_source(item: dict[str, Any], config: dict[str, Any]) -> Image.Image:
    frame_id = str(item.get("frame_id") or "").strip()
    if not frame_id:
        return Image.new("RGBA", (1200, 1200), (255, 255, 255, 255))
    file_key = str(config.get("figma", {}).get("file_key") or "").strip()
    scale = int(config.get("figma", {}).get("default_scale") or 2)
    try:
        return _fetch_figma_frame_image(file_key, frame_id, scale)
    except Exception:
        cache_path = FIGMA_CACHE_DIR / f"{frame_id}.png"
        if cache_path.exists():
            return Image.open(cache_path).convert("RGBA")
        raise


def _build_job_image(plan: dict[str, Any], item: dict[str, Any], overlay_text: str, config: dict[str, Any]) -> tuple[Image.Image, dict[str, Any]]:
    profile = _get_profile(config.get("active_profile_key"))
    preset = _get_preset(profile, item.get("preset_key"))
    src = _load_frame_source(item, config)
    composed = _build_diamond_layout(
        src,
        int(preset["px_w"]),
        int(preset["px_h"]),
        overlay_text=overlay_text,
        overlay_position=str(item.get("overlay_position") or "bottom"),
    )
    bitmap = _to_print_bitmap(composed, int(item.get("threshold") or DEFAULT_IMAGE_THRESHOLD))
    return bitmap, preset


def _print_scheduled_job(plan: dict[str, Any], item: dict[str, Any], job: dict[str, Any], config: dict[str, Any]) -> None:
    profile = _get_profile(config.get("active_profile_key"))
    bitmap, preset = _build_job_image(plan, item, str(job.get("overlay_text") or ""), config)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    kind = str(item.get("kind") or "frame")
    filename = f"{kind}_{plan['id']}_{item['id']}_{timestamp}.png"
    img_path = LOG_DIR / filename
    bitmap.save(img_path, dpi=(profile["dpi"], profile["dpi"]))
    for _ in range(int(item.get("copies") or 1)):
        _print_via_raster(bitmap, profile, str(preset["media"]))
    history = {
        "timestamp": _iso(_now_local()),
        "plan_id": plan["id"],
        "plan_name": plan["name"],
        "item_id": item["id"],
        "item_name": item["name"],
        "kind": kind,
        "frame_name": item.get("frame_name") or "",
        "overlay_text": str(job.get("overlay_text") or ""),
        "preset_key": preset["key"],
        "preset_label": preset["label"],
        "image_url": f"{PUBLIC_BASE_URL}{_public_path(f'/logs/{filename}')}" if PUBLIC_BASE_URL else _public_path(f"/logs/{filename}"),
        "image_path": str(img_path),
    }
    _append_history(history)


def _record_print_history(kind: str, label: str, preset: dict[str, Any], image_path: pathlib.Path, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    history = {
        "history_id": image_path.name,
        "timestamp": _iso(_now_local()),
        "kind": kind,
        "label": label,
        "preset_key": preset["key"],
        "preset_label": preset["label"],
        "image_url": f"{PUBLIC_BASE_URL}{_public_path(f'/logs/{image_path.name}')}" if PUBLIC_BASE_URL else _public_path(f"/logs/{image_path.name}"),
        "image_path": str(image_path),
    }
    if extra:
        history.update(extra)
    _append_history(history)
    return history


def _print_history_item(history_entry: dict[str, Any], profile: dict[str, Any]) -> None:
    image_path = pathlib.Path(str(history_entry.get("image_path") or ""))
    if not image_path.exists():
        raise RuntimeError(f"History image is missing: {image_path.name}")
    with Image.open(image_path) as opened:
        img = opened.copy()
    _print_via_raster(img, profile, str(_get_preset(profile, history_entry.get("preset_key")).get("media") or ""))


def _mark_job_complete(plan: dict[str, Any], item: dict[str, Any], job: dict[str, Any]) -> None:
    stamp = _iso(_now_local())
    if item.get("kind") == "frame":
        item["printed_at"] = stamp
    else:
        ticks = set(item.get("printed_ticks") or [])
        ticks.add(str(job["job_key"]))
        item["printed_ticks"] = sorted(ticks)
    if plan.get("status") == "armed":
        plan["status"] = "running"
    if _plan_is_complete(plan):
        plan["status"] = "done"
        plan["completed_at"] = stamp


def _run_scheduler_tick() -> None:
    with CONFIG_LOCK:
        config = _deepcopy(CONFIG)
    now = _now_local()
    if _run_countdown_timer_tick(config, now):
        with CONFIG_LOCK:
            _save_config(config)
    active_plan_id = str(config.get("active_plan_id") or "").strip()
    if not active_plan_id:
        return
    plan = next((entry for entry in config.get("plans", []) if entry.get("id") == active_plan_id), None)
    if not plan:
        return
    changed = False
    if plan["mode"] == "relative" and plan.get("status") == "armed" and not plan.get("started_at"):
        scheduled_start = _parse_dt(plan.get("start_at") or "")
        if scheduled_start and now >= scheduled_start:
            plan["started_at"] = _iso(scheduled_start)
            plan["status"] = "running"
            changed = True
    if plan["mode"] == "absolute" and plan.get("status") not in {"armed", "running"}:
        return
    if plan["mode"] == "relative" and plan.get("status") not in {"running", "armed"}:
        return
    jobs = _pending_jobs_for_plan(plan, now)
    if not jobs:
        if _plan_is_complete(plan) and plan.get("status") != "done":
            plan["status"] = "done"
            plan["completed_at"] = _iso(now)
            changed = True
        if changed:
            with CONFIG_LOCK:
                _save_config(config)
        return
    for job in jobs:
        item = next((entry for entry in plan.get("items", []) if entry.get("id") == job.get("item_id")), None)
        if not item:
            continue
        _print_scheduled_job(plan, item, job, config)
        _mark_job_complete(plan, item, job)
        with CONFIG_LOCK:
            _save_config(config)
        changed = True
    if changed:
        return


def _scheduler_loop() -> None:
    while True:
        try:
            _refresh_config_from_disk()
            _run_scheduler_tick()
        except Exception as exc:
            print(f"[scheduler] {exc}")
        time.sleep(1)


def _start_scheduler_once() -> None:
    global SCHEDULER_STARTED
    if SCHEDULER_STARTED:
        return
    thread = threading.Thread(target=_scheduler_loop, name="presentation-scheduler", daemon=True)
    thread.start()
    SCHEDULER_STARTED = True


def _plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    remaining = _remaining_jobs_for_plan(plan)
    next_jobs = [
        {
            "name": job["name"],
            "due_at": _iso(job["due_at"]),
            "due_label": _human_schedule(_iso(job["due_at"])),
            "past_due": job["past_due"],
        }
        for job in remaining[:6]
    ]
    return {
        "id": plan["id"],
        "name": plan["name"],
        "notes": plan.get("notes") or "",
        "mode": plan["mode"],
        "start_at": plan.get("start_at") or "",
        "start_at_label": _human_schedule(plan.get("start_at") or ""),
        "started_at": plan.get("started_at") or "",
        "started_at_label": _human_schedule(plan.get("started_at") or ""),
        "status": plan["status"],
        "item_count": len(plan.get("items") or []),
        "next_jobs": next_jobs,
    }


def _admin_state() -> dict[str, Any]:
    with CONFIG_LOCK:
        config = _deepcopy(CONFIG)
    profile = _get_profile(config.get("active_profile_key"))
    plans = [_plan_summary(plan) for plan in config.get("plans", [])]
    detailed_plans = _deepcopy(config.get("plans", []))
    active_plan = _active_plan(config)
    return {
        "printer": {
            "queue": profile.get("cups_queue", ""),
            "dpi": profile.get("dpi", TRANSFER_DPI),
            "paper_presets": list(profile.get("preset_map", {}).values()),
            "active_profile_key": profile.get("key", DEFAULT_PROFILE_KEY),
            "active_paper_key": profile.get("active_paper_key", ""),
        },
        "figma": config.get("figma", {}),
        "plans": detailed_plans,
        "plan_summaries": plans,
        "active_plan_id": config.get("active_plan_id", ""),
        "active_plan": _plan_summary(active_plan) if active_plan else None,
        "recent_jobs": _recent_history(12),
        "admin_enabled": ADMIN_ENABLED,
    }


def _dashboard_state() -> dict[str, Any]:
    with CONFIG_LOCK:
        config = _deepcopy(CONFIG)
    active_plan = _active_plan(config)
    profile = _get_profile(config.get("active_profile_key"))
    return {
        "active_plan": _plan_summary(active_plan) if active_plan else None,
        "paper_presets": list(profile.get("preset_map", {}).values()),
        "recent_jobs": _recent_history(16),
        "countdown_timer": _timer_summary(config.get("countdown_timer") or _default_timer_state()),
        "figma": {
            "file_key": config.get("figma", {}).get("file_key", ""),
            "last_sync_at": config.get("figma", {}).get("last_sync_at", ""),
            "frame_count": len(config.get("figma", {}).get("cached_frames", [])),
            "last_error": config.get("figma", {}).get("last_error", ""),
        },
    }


def _is_admin_authed() -> bool:
    return bool(session.get("admin_authed"))


def _require_admin_json():
    if not ADMIN_ENABLED:
        return jsonify(ok=False, msg="Admin is disabled. Set ADMIN_PASSWORD."), 403
    if not _is_admin_authed():
        return jsonify(ok=False, msg="Login required."), 403
    return None


def route_with_base(rule: str, **options: Any):
    def decorator(func):
        target_rule = rule
        if BASE_PATH:
            if rule == "/":
                target_rule = BASE_PATH
            else:
                target_rule = f"{BASE_PATH}{rule}"
        app.route(target_rule, **options)(func)
        if BASE_PATH and rule == "/":
            app.route(f"{BASE_PATH}/", **options)(func)
        return func

    return decorator


@app.before_request
def _boot() -> None:
    _refresh_config_from_disk()
    _start_scheduler_once()


@route_with_base("/", methods=["GET"])
def index():
    return render_template(
        "index.html",
        state=_dashboard_state(),
        asset_ver=ASSET_VERSION,
        base_path=BASE_PATH,
        default_threshold=DEFAULT_IMAGE_THRESHOLD,
    )


@route_with_base("/health", methods=["GET"])
def health():
    return jsonify(ok=True, status="healthy", active_plan=_dashboard_state().get("active_plan"))


@route_with_base("/api/state", methods=["GET"])
def api_state():
    return jsonify(ok=True, state=_dashboard_state())


@route_with_base("/api/history", methods=["GET"])
def api_history():
    return jsonify(ok=True, history=_recent_history(24))


@route_with_base("/print-text", methods=["POST"])
def print_text():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()
    if not text:
        return jsonify(ok=False, msg="Enter text to print."), 400
    profile = _get_profile(data.get("profile_key"))
    preset = _get_preset(profile, data.get("preset_key"))
    bitmap = _build_text_sticker(text, int(preset["px_w"]), int(preset["px_h"]))
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    image_path = LOG_DIR / f"text_{timestamp}_{preset['key']}.png"
    bitmap.save(image_path, dpi=(profile["dpi"], profile["dpi"]))
    try:
        _print_via_raster(bitmap, profile, str(preset["media"]))
    except Exception as exc:
        return jsonify(ok=False, msg=str(exc)), 500
    history = _record_print_history("text", text, preset, image_path)
    return jsonify(ok=True, msg=f"Printed {preset['label']}.", history=history)


@route_with_base("/print-image", methods=["POST"])
def print_image():
    uploaded = request.files.get("image")
    if not uploaded:
        return jsonify(ok=False, msg="Choose an image first."), 400
    profile = _get_profile(request.form.get("profile_key"))
    preset = _get_preset(profile, request.form.get("preset_key"))
    try:
        threshold = max(1, min(255, int(request.form.get("threshold") or DEFAULT_IMAGE_THRESHOLD)))
    except Exception:
        threshold = DEFAULT_IMAGE_THRESHOLD
    try:
        src = Image.open(uploaded.stream)
        fitted = _fit_image_to_label(_flatten_to_white(src), int(preset["px_w"]), int(preset["px_h"]))
        bitmap = _to_print_bitmap(fitted, threshold)
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        image_path = LOG_DIR / f"image_{timestamp}_{preset['key']}.png"
        bitmap.save(image_path, dpi=(profile["dpi"], profile["dpi"]))
        _print_via_raster(bitmap, profile, str(preset["media"]))
    except Exception as exc:
        return jsonify(ok=False, msg=str(exc)), 500
    history = _record_print_history("image", "Image sticker", preset, image_path, {"threshold": threshold})
    return jsonify(ok=True, msg=f"Printed {preset['label']}.", history=history)


@route_with_base("/api/timer/start", methods=["POST"])
def api_timer_start():
    data = request.get_json(silent=True) or {}
    start_at = _parse_dt(str(data.get("start_at") or ""))
    end_at = _parse_dt(str(data.get("end_at") or ""))
    if not start_at or not end_at:
        return jsonify(ok=False, msg="Choose both a start and end time."), 400
    if end_at <= start_at:
        return jsonify(ok=False, msg="End time must be after start time."), 400
    with CONFIG_LOCK:
        config = _deepcopy(CONFIG)
        timer = _default_timer_state()
        timer["status"] = "armed"
        timer["start_at"] = _iso(start_at)
        timer["end_at"] = _iso(end_at)
        timer["preset_key"] = str(data.get("preset_key") or DEFAULT_ORANGE_PRESET_KEY).strip() or DEFAULT_ORANGE_PRESET_KEY
        timer["text_template"] = str(data.get("text_template") or COUNTDOWN_TEMPLATE).strip() or COUNTDOWN_TEMPLATE
        selected = data.get("selected_history_ids")
        timer["selected_history_ids"] = [str(item).strip() for item in selected] if isinstance(selected, list) else []
        config["countdown_timer"] = timer
        _save_config(config)
    return jsonify(ok=True, state=_dashboard_state())


@route_with_base("/api/timer/cancel", methods=["POST"])
def api_timer_cancel():
    with CONFIG_LOCK:
        config = _deepcopy(CONFIG)
        config["countdown_timer"] = _default_timer_state()
        _save_config(config)
    return jsonify(ok=True, state=_dashboard_state())


@route_with_base("/logs/<path:fname>", methods=["GET"])
def serve_log_file(fname: str):
    return send_from_directory(LOG_DIR, fname, mimetype="image/png", max_age=3600)


@route_with_base("/admin", methods=["GET"])
def admin_page():
    if not ADMIN_ENABLED or not _is_admin_authed():
        return render_template("admin.html", authed=False, admin_enabled=ADMIN_ENABLED, state=_admin_state(), asset_ver=ASSET_VERSION, base_path=BASE_PATH)
    return render_template("admin.html", authed=True, admin_enabled=ADMIN_ENABLED, state=_admin_state(), asset_ver=ASSET_VERSION, base_path=BASE_PATH)


@route_with_base("/admin/login", methods=["POST"])
def admin_login():
    if not ADMIN_ENABLED:
        return redirect(url_for("admin_page"))
    password = (request.form.get("password") or "").strip()
    if password and secrets.compare_digest(password, ADMIN_PASSWORD):
        session["admin_authed"] = True
    return redirect(url_for("admin_page"))


@route_with_base("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin_authed", None)
    return redirect(url_for("admin_page"))


@route_with_base("/api/admin/settings", methods=["POST"])
def api_admin_settings():
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    data = request.get_json(silent=True) or {}
    with CONFIG_LOCK:
        config = _deepcopy(CONFIG)
        profile = config["printer_profiles"][0]
        profile["cups_queue"] = str(data.get("queue") or profile.get("cups_queue") or "").strip()
        profile["dpi"] = _coerce_positive_int(data.get("dpi"), int(profile.get("dpi") or TRANSFER_DPI))
        active_paper_key = str(data.get("active_paper_key") or profile.get("active_paper_key") or "").strip()
        preset_keys = {preset["key"] for preset in profile.get("paper_presets", [])}
        if active_paper_key in preset_keys:
            profile["active_paper_key"] = active_paper_key
        config["figma"]["file_key"] = str(data.get("file_key") or config["figma"].get("file_key") or "").strip()
        config["figma"]["token_env"] = str(data.get("token_env") or config["figma"].get("token_env") or DEFAULT_FIGMA_TOKEN_ENV).strip() or DEFAULT_FIGMA_TOKEN_ENV
        config["figma"]["default_scale"] = max(1, min(_coerce_positive_int(data.get("default_scale"), int(config["figma"].get("default_scale") or 2)), 4))
        _save_config(config)
    return jsonify(ok=True, state=_admin_state())


@route_with_base("/api/admin/figma/refresh", methods=["POST"])
def api_admin_figma_refresh():
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    with CONFIG_LOCK:
        config = _deepcopy(CONFIG)
    try:
        frames = _refresh_figma_frames(config)
    except Exception as exc:
        with CONFIG_LOCK:
            latest = _deepcopy(CONFIG)
            latest["figma"]["last_error"] = str(exc)
            _save_config(latest)
        return jsonify(ok=False, msg=str(exc), state=_admin_state()), 400
    with CONFIG_LOCK:
        _save_config(config)
    return jsonify(ok=True, frames=frames, state=_admin_state())


@route_with_base("/api/admin/plans/save", methods=["POST"])
def api_admin_plan_save():
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    payload = request.get_json(silent=True) or {}
    plan = _normalize_plan(payload.get("plan"))
    with CONFIG_LOCK:
        config = _deepcopy(CONFIG)
        plans = config.get("plans", [])
        for index, existing in enumerate(plans):
            if existing.get("id") == plan["id"]:
                plans[index] = plan
                break
        else:
            plans.append(plan)
        config["plans"] = plans
        if not config.get("active_plan_id"):
            config["active_plan_id"] = plan["id"]
        _save_config(config)
    return jsonify(ok=True, state=_admin_state(), plan=plan)


@route_with_base("/api/admin/plans/<plan_id>/delete", methods=["POST"])
def api_admin_plan_delete(plan_id: str):
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    with CONFIG_LOCK:
        config = _deepcopy(CONFIG)
        config["plans"] = [plan for plan in config.get("plans", []) if plan.get("id") != plan_id]
        if config.get("active_plan_id") == plan_id:
            config["active_plan_id"] = config["plans"][0]["id"] if config["plans"] else ""
        _save_config(config)
    return jsonify(ok=True, state=_admin_state())


def _plan_action(config: dict[str, Any], plan_id: str, action: str) -> dict[str, Any]:
    plan = next((entry for entry in config.get("plans", []) if entry.get("id") == plan_id), None)
    if not plan:
        raise RuntimeError("Plan not found.")
    now = _iso(_now_local())
    if action == "activate":
        config["active_plan_id"] = plan_id
    elif action == "arm":
        plan["status"] = "armed"
        plan["completed_at"] = ""
        plan["stopped_at"] = ""
        if plan["mode"] == "absolute":
            plan["started_at"] = ""
        elif plan.get("start_at"):
            plan["started_at"] = ""
        else:
            plan["started_at"] = now
            plan["status"] = "running"
        for item in plan.get("items", []):
            item["printed_at"] = ""
            item["printed_ticks"] = []
    elif action == "start":
        plan["status"] = "running"
        plan["started_at"] = now
        plan["completed_at"] = ""
        plan["stopped_at"] = ""
        for item in plan.get("items", []):
            item["printed_at"] = ""
            item["printed_ticks"] = []
    elif action == "pause":
        plan["status"] = "paused"
        plan["stopped_at"] = now
    elif action == "reset":
        plan["status"] = "draft"
        plan["started_at"] = ""
        plan["stopped_at"] = ""
        plan["completed_at"] = ""
        for item in plan.get("items", []):
            item["printed_at"] = ""
            item["printed_ticks"] = []
    else:
        raise RuntimeError("Unknown plan action.")
    return plan


@route_with_base("/api/admin/plans/<plan_id>/<action>", methods=["POST"])
def api_admin_plan_action(plan_id: str, action: str):
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    with CONFIG_LOCK:
        config = _deepcopy(CONFIG)
        try:
            plan = _plan_action(config, plan_id, action)
        except Exception as exc:
            return jsonify(ok=False, msg=str(exc)), 400
        _save_config(config)
    return jsonify(ok=True, state=_admin_state(), plan=plan)


@route_with_base("/api/admin/plans/<plan_id>/print-now/<item_id>", methods=["POST"])
def api_admin_plan_print_now(plan_id: str, item_id: str):
    auth_error = _require_admin_json()
    if auth_error:
        return auth_error
    with CONFIG_LOCK:
        config = _deepcopy(CONFIG)
    plan = next((entry for entry in config.get("plans", []) if entry.get("id") == plan_id), None)
    if not plan:
        return jsonify(ok=False, msg="Plan not found."), 404
    item = next((entry for entry in plan.get("items", []) if entry.get("id") == item_id), None)
    if not item:
        return jsonify(ok=False, msg="Item not found."), 404
    overlay_text = item.get("overlay_text") or ""
    if item.get("kind") == "countdown":
        overlay_text = str(item.get("text_template") or COUNTDOWN_TEMPLATE).replace("{{minutes_left}}", str(item.get("start_minutes") or 0))
    job = {"job_key": f"manual:{item_id}", "overlay_text": overlay_text}
    try:
        _print_scheduled_job(plan, item, job, config)
    except Exception as exc:
        return jsonify(ok=False, msg=str(exc)), 500
    return jsonify(ok=True, state=_admin_state())


_refresh_config_from_disk()

if __name__ == "__main__":
    app.run("127.0.0.1", 8000, debug=True)
