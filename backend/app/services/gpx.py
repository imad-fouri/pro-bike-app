"""GPX 1.1 import/export — treat every upload as hostile (docs/05, docs/08).

Guarantees:
- bounded read (caller passes <= max_bytes + 1 bytes) before any parsing
- no DTD / entity declarations accepted (XXE + billion-laughs closed off;
  Python's expat is also entity-limited, this makes the guarantee explicit)
- element count, point count, coordinate ranges, NaN/inf all validated
- consecutive duplicates dropped; impossible jumps rejected (track segments)
- filename / creator / extensions are never trusted or executed
"""

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from itertools import pairwise

from app.core.config import settings
from app.services.gps_engine import haversine_m
from app.services.route_metrics import GeoPoint, dedupe_consecutive

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_POINTS = 5000
DEFAULT_MAX_NODES = 200_000
# A track segment jumping further than this is not a real ride/track (§13).
MAX_SEGMENT_JUMP_M = 50_000.0

_FORBIDDEN_MARKUP = (b"<!doctype", b"<!entity")


class GpxError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class ParsedGpx:
    points: list[GeoPoint]  # post-dedupe
    raw_count: int  # pre-dedupe track/route points
    name: str | None  # sanitized suggestion only (metadata is untrusted)
    has_timestamps: bool


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _clean_name(raw: str | None) -> str | None:
    if not raw:
        return None
    text = re.sub(r"[\x00-\x1f\x7f]", "", raw).strip()
    return text[:120] or None


def _parse_coord(raw: str | None, kind: str) -> float:
    if raw is None:
        raise GpxError("GPX_INVALID_COORDINATES", f"Missing {kind} coordinate.", 400)
    try:
        value = float(raw)
    except ValueError as exc:
        raise GpxError("GPX_INVALID_COORDINATES", f"Non-numeric {kind} coordinate.", 400) from exc
    if not math.isfinite(value):
        raise GpxError("GPX_INVALID_COORDINATES", f"Non-finite {kind} coordinate.", 400)
    limit = 90.0 if kind == "lat" else 180.0
    if abs(value) > limit:
        raise GpxError("GPX_INVALID_COORDINATES", f"{kind} out of range.", 400)
    return value


def _parse_ele(elem: ET.Element | None) -> float | None:
    if elem is None or elem.text is None:
        return None
    try:
        value = float(elem.text.strip())
    except ValueError:
        return None  # bad elevation is dropped, not fatal
    if not math.isfinite(value) or not -500.0 <= value <= 9000.0:
        return None
    return value


def parse_gpx(
    data: bytes,
    *,
    max_bytes: int | None = None,
    max_points: int | None = None,
    max_nodes: int | None = None,
) -> ParsedGpx:
    max_bytes = max_bytes or settings.GPX_MAX_BYTES
    max_points = max_points or settings.ROUTE_MAX_POINTS
    max_nodes = max_nodes or settings.GPX_MAX_NODES

    if len(data) > max_bytes:
        raise GpxError("GPX_TOO_LARGE", "GPX file exceeds the size limit.", 413)
    if not data.strip():
        raise GpxError("GPX_EMPTY", "GPX file is empty.", 400)
    lowered = data.lower()
    for marker in _FORBIDDEN_MARKUP:
        if marker in lowered:
            raise GpxError(
                "GPX_FORBIDDEN_MARKUP",
                "DTD/entity declarations are not allowed.",
                400,
            )
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise GpxError("GPX_INVALID_XML", "File is not well-formed XML.", 400) from exc

    nodes = list(root.iter())
    if len(nodes) > max_nodes:
        raise GpxError("GPX_TOO_MANY_NODES", "GPX contains too many elements.", 400)
    if _local(root.tag) != "gpx":
        raise GpxError("GPX_INVALID_XML", "Root element must be <gpx>.", 400)

    name = None
    has_timestamps = False
    # Depth-first iteration: flush `current` when a NEW segment starts.
    segments: list[list[GeoPoint]] = []
    current: list[GeoPoint] = []
    using_tracks = False

    for elem in nodes:
        tag = _local(elem.tag)
        if tag == "metadata":
            for child in elem:
                if _local(child.tag) == "name":
                    name = _clean_name(child.text)
        elif tag in ("trk", "rte"):
            if current:  # new track/route starts a fresh segment
                segments.append(current)
                current = []
            if name is None:
                for child in elem:
                    if _local(child.tag) == "name":
                        name = _clean_name(child.text)
        elif tag == "trkseg":
            if current:
                segments.append(current)
                current = []
        elif tag in ("trkpt", "rtept"):
            lat = _parse_coord(elem.get("lat"), "lat")
            lon = _parse_coord(elem.get("lon"), "lon")
            ele = None
            time_seen = False
            for child in elem:
                child_tag = _local(child.tag)
                if child_tag == "ele":
                    ele = _parse_ele(child)
                elif child_tag == "time":
                    time_seen = True
            has_timestamps = has_timestamps or time_seen
            if tag == "trkpt":
                using_tracks = True
            current.append(GeoPoint(lat=lat, lon=lon, ele=ele))

    if current:
        segments.append(current)
    if not using_tracks:
        # Route-style GPX (rtept): waypoint order is intentional, no jump rule.
        segments = [p for p in segments if p]

    raw_count = sum(len(s) for s in segments)
    if raw_count == 0:
        raise GpxError("GPX_EMPTY", "No track or route points found.", 400)
    if raw_count > max_points:
        raise GpxError("GPX_TOO_MANY_POINTS", "GPX contains too many points.", 400)

    flat: list[GeoPoint] = []
    for segment in segments:
        if using_tracks:
            for a, b in pairwise(segment):
                if haversine_m(a.lat, a.lon, b.lat, b.lon) > MAX_SEGMENT_JUMP_M:
                    raise GpxError(
                        "GPX_IMPOSSIBLE_JUMP",
                        "Consecutive track points are impossibly far apart.",
                        400,
                    )
        flat.extend(segment)

    points = dedupe_consecutive(flat)
    if len(points) < 2:
        raise GpxError("GPX_EMPTY", "Fewer than two distinct points.", 400)
    return ParsedGpx(points=points, raw_count=raw_count, name=name, has_timestamps=has_timestamps)


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "route"
    return f"{cleaned[:60]}.gpx"


def export_gpx(name: str, points: list[tuple[float, float, float | None]]) -> str:
    """Route → GPX 1.1 <rte>. No user/session data is ever included."""
    safe_name = _clean_name(name) or "Route"
    gpx = ET.Element(
        "gpx",
        {
            "version": "1.1",
            "creator": "CycleCoach",
            "xmlns": "http://www.topografix.com/GPX/1/1",
        },
    )
    metadata = ET.SubElement(gpx, "metadata")
    ET.SubElement(metadata, "name").text = safe_name
    rte = ET.SubElement(gpx, "rte")
    ET.SubElement(rte, "name").text = safe_name
    for lat, lon, ele in points:
        pt = ET.SubElement(rte, "rtept", {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}"})
        if ele is not None:
            ET.SubElement(pt, "ele").text = f"{ele:.1f}"
    ET.indent(gpx, space="  ")
    return ET.tostring(gpx, encoding="unicode", xml_declaration=True)
