"""Native media blocks; never masquerade text extraction as visual inspection."""

import base64
from pathlib import Path


def media_data(asset):
    path = Path(asset["path"])
    if path.stat().st_size > 20_000_000:
        raise ValueError("Native media exceeds request limit")
    return base64.b64encode(path.read_bytes()).decode()


def gemini_blocks(media):
    return [
        {
            "type": a["kind"],
            "mime_type": "video/mov" if a["mime_type"] == "video/quicktime" else a["mime_type"],
            "data": media_data(a),
        }
        for a in media
    ]


def router_blocks(media):
    return [
        {
            "type": "image_url" if a["kind"] == "image" else "video_url",
            "image_url" if a["kind"] == "image" else "video_url": {
                "url": f"data:{a['mime_type']};base64,{media_data(a)}"
            },
        }
        for a in media
    ]


def wire_name(name, tools):
    return next((spec.name for spec in tools if spec.name.replace(".", "_") == name), name)


def data_url(asset):
    return f"data:{asset['mime_type']};base64,{media_data(asset)}"
