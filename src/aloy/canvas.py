"""Strict version-one teaching-canvas data; no generated markup or executable UI."""

import json
import math
import re

MAX_ARTIFACT_BYTES = 20_000
MAX_BLOCKS = 24
MAX_TEXT = 4_000
BLOCK_KINDS = {"heading", "text", "sentence", "choice", "table", "chart", "drawing"}
DRAWING_COLORS = {"#3A86FF", "#FF006E", "#8338EC", "#FB5607", "#06A77D", "#222222"}
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _object(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - (set(required) | set(optional)):
        raise ValueError("Invalid canvas object fields")
    if set(required) - set(value):
        raise ValueError("Missing canvas object fields")


def _text(value, name, limit, *, allow_empty=False):
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (not allow_empty and not value.strip())
        or any(ord(char) < 32 and char not in "\n\t" for char in value)
    ):
        raise ValueError(f"Invalid canvas {name}")
    return value


def _finite(value, name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Invalid canvas {name}")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f"Invalid canvas {name}")
    return float(value)


def validate_canvas_input(value):
    """Validate provider supplied `{title, blocks}` and return normalized JSON data."""
    _object(value, {"title", "blocks"})
    title = _text(value["title"], "title", 120)
    blocks = value["blocks"]
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= MAX_BLOCKS:
        raise ValueError("Canvas needs between 1 and 24 blocks")
    normalized, seen_ids, total_points = [], set(), 0
    for block in blocks:
        if not isinstance(block, dict) or block.get("kind") not in BLOCK_KINDS:
            raise ValueError("Unknown canvas block kind")
        kind = block["kind"]
        common = {"id", "kind"}
        fields = {
            "heading": ({"text"}, set()),
            "text": ({"text"}, set()),
            "sentence": ({"source", "target"}, {"explanation"}),
            "choice": ({"prompt", "options"}, {"correct_option_id", "explanation"}),
            "table": ({"columns", "rows"}, set()),
            "chart": ({"chart_kind", "title", "labels", "series"}, set()),
            "drawing": ({"strokes"}, set()),
        }
        required, optional = fields[kind]
        _object(block, common | required, optional)
        block_id = block["id"]
        if not isinstance(block_id, str) or not _ID.fullmatch(block_id) or block_id in seen_ids:
            raise ValueError("Invalid or duplicate canvas block ID")
        seen_ids.add(block_id)
        item = {"id": block_id, "kind": kind}
        if kind in {"heading", "text"}:
            item["text"] = _text(block["text"], "text", MAX_TEXT)
        elif kind == "sentence":
            item.update(
                source=_text(block["source"], "sentence source", 1_200),
                target=_text(block["target"], "sentence target", 1_200),
            )
            if "explanation" in block:
                item["explanation"] = _text(block["explanation"], "explanation", 1_000)
        elif kind == "choice":
            item["prompt"] = _text(block["prompt"], "choice prompt", 1_000)
            options = block["options"]
            if not isinstance(options, list) or not 2 <= len(options) <= 8:
                raise ValueError("Canvas choices need between 2 and 8 options")
            item_options, option_ids = [], set()
            for option in options:
                _object(option, {"id", "label"})
                option_id = option["id"]
                if (
                    not isinstance(option_id, str)
                    or not _ID.fullmatch(option_id)
                    or option_id in option_ids
                ):
                    raise ValueError("Invalid or duplicate canvas choice ID")
                option_ids.add(option_id)
                item_options.append(
                    {"id": option_id, "label": _text(option["label"], "choice label", 500)}
                )
            item["options"] = item_options
            if "correct_option_id" in block:
                if block["correct_option_id"] not in option_ids:
                    raise ValueError("Canvas answer does not match an option")
                item["correct_option_id"] = block["correct_option_id"]
            if "explanation" in block:
                item["explanation"] = _text(block["explanation"], "explanation", 1_000)
        elif kind == "table":
            columns, rows = block["columns"], block["rows"]
            if not isinstance(columns, list) or not 1 <= len(columns) <= 8:
                raise ValueError("Canvas table needs between 1 and 8 columns")
            if not isinstance(rows, list) or len(rows) > 30:
                raise ValueError("Canvas table has too many rows")
            item["columns"] = [_text(cell, "table header", 200) for cell in columns]
            if any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
                raise ValueError("Canvas table row width does not match its columns")
            item["rows"] = [
                [_text(cell, "table cell", 500, allow_empty=True) for cell in row] for row in rows
            ]
        elif kind == "chart":
            if block["chart_kind"] not in {"bar", "line"}:
                raise ValueError("Unsupported canvas chart kind")
            labels, series = block["labels"], block["series"]
            if not isinstance(labels, list) or not 1 <= len(labels) <= 24:
                raise ValueError("Canvas chart needs between 1 and 24 labels")
            if not isinstance(series, list) or not 1 <= len(series) <= 5:
                raise ValueError("Canvas chart needs between 1 and 5 series")
            item.update(
                chart_kind=block["chart_kind"],
                title=_text(block["title"], "chart title", 120),
                labels=[_text(label, "chart label", 120) for label in labels],
            )
            item_series = []
            for current in series:
                _object(current, {"name", "values"})
                values = current["values"]
                if not isinstance(values, list) or len(values) != len(labels):
                    raise ValueError("Canvas chart values must match labels")
                item_series.append(
                    {
                        "name": _text(current["name"], "series name", 120),
                        "values": [
                            _finite(number, "chart value", -1_000_000, 1_000_000)
                            for number in values
                        ],
                    }
                )
            item["series"] = item_series
        else:
            strokes = block["strokes"]
            if not isinstance(strokes, list) or len(strokes) > 12:
                raise ValueError("Canvas drawing has too many strokes")
            item_strokes = []
            for stroke in strokes:
                _object(stroke, {"color", "width", "points"})
                if stroke["color"] not in DRAWING_COLORS:
                    raise ValueError("Unsupported canvas drawing color")
                points = stroke["points"]
                if not isinstance(points, list) or not 2 <= len(points) <= 200:
                    raise ValueError("Canvas stroke needs 2 to 200 points")
                total_points += len(points)
                if total_points > 500:
                    raise ValueError("Canvas drawing has too many points")
                normalized_points = []
                for point in points:
                    _object(point, {"x", "y"})
                    normalized_points.append(
                        {
                            "x": _finite(point["x"], "x coordinate", 0, 1),
                            "y": _finite(point["y"], "y coordinate", 0, 1),
                        }
                    )
                item_strokes.append(
                    {
                        "color": stroke["color"],
                        "width": _finite(stroke["width"], "stroke width", 0.5, 12),
                        "points": normalized_points,
                    }
                )
            item["strokes"] = item_strokes
        normalized.append(item)
    if (
        len(json.dumps({"title": title, "blocks": normalized}, ensure_ascii=False).encode())
        > MAX_ARTIFACT_BYTES
    ):
        raise ValueError("Canvas artifact exceeds the size limit")
    return {"title": title, "blocks": normalized}


def validate_action_arguments(name, arguments):
    """Validate native targets and operation bounds before asking the shell."""
    if name not in {"desktop.click", "desktop.type_text", "desktop.hide_app"}:
        raise ValueError("Unsupported desktop action")
    required = {"bundle_id"}
    optional = set()
    if name != "desktop.hide_app":
        required.add("window_id")
    if name == "desktop.click":
        required |= {"x", "y"}
    elif name == "desktop.type_text":
        required.add("text")
    _object(arguments, required, optional)
    bundle = _text(arguments["bundle_id"], "bundle ID", 255)
    if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", bundle):
        raise ValueError("Invalid application bundle ID")
    result = {"bundle_id": bundle}
    if name != "desktop.hide_app":
        window_id = arguments["window_id"]
        if (
            isinstance(window_id, bool)
            or not isinstance(window_id, int)
            or not 0 < window_id <= 4_294_967_295
        ):
            raise ValueError("Invalid CGWindowID")
        result["window_id"] = window_id
    if name == "desktop.click":
        result["x"] = _finite(arguments["x"], "x coordinate", 0, 1)
        result["y"] = _finite(arguments["y"], "y coordinate", 0, 1)
    elif name == "desktop.type_text":
        text = _text(arguments["text"], "typed text", 1_000)
        result["text"] = text
    return result
