"""Phase 5 pure tests: hostile GPX parsing, filename hygiene, route metrics."""

import pytest

from app.services.gpx import GpxError, export_gpx, parse_gpx, sanitize_filename
from app.services.route_metrics import (
    DIFFICULTY_BASIS,
    GeoPoint,
    compute,
    dedupe_consecutive,
)

TRACK = b"""<?xml version="1.0"?>
<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata><name>Loop &amp; Co</name></metadata>
  <trk><name>Loop</name><trkseg>
    <trkpt lat="46.2000" lon="6.1400"><ele>400</ele></trkpt>
    <trkpt lat="46.2010" lon="6.1410"><ele>420</ele><time>2026-01-01T10:00:00Z</time></trkpt>
    <trkpt lat="46.2020" lon="6.1420"><ele>410</ele></trkpt>
  </trkseg></trk>
</gpx>
"""


def test_parse_track_extracts_points_and_flags_timestamps():
    parsed = parse_gpx(TRACK)
    assert len(parsed.points) == 3
    assert parsed.raw_count == 3
    assert parsed.name == "Loop & Co"
    assert parsed.has_timestamps is True
    assert parsed.points[0].ele == 400.0


def test_parse_dedupes_consecutive_duplicates():
    gpx = b"""<?xml version="1.0"?>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
  <rte><rtept lat="46.2" lon="6.1"/><rtept lat="46.2" lon="6.1"/>
  <rtept lat="46.3" lon="6.2"/></rte>
</gpx>"""
    parsed = parse_gpx(gpx)
    assert len(parsed.points) == 2
    assert parsed.raw_count == 3


def test_doctype_and_entity_declarations_rejected():
    for payload in (
        b'<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY a "x">]><gpx/>',
        b'<?xml version="1.0"?><!ENTITY a SYSTEM "file:///etc/passwd"><gpx/>',
    ):
        with pytest.raises(GpxError) as exc:
            parse_gpx(payload)
        assert exc.value.code == "GPX_FORBIDDEN_MARKUP"
        assert exc.value.status == 400


def test_malformed_xml_rejected():
    with pytest.raises(GpxError) as exc:
        parse_gpx(b"<gpx><trk></gpx>")
    assert exc.value.code == "GPX_INVALID_XML"


def test_wrong_root_rejected():
    with pytest.raises(GpxError) as exc:
        parse_gpx(b'<?xml version="1.0"?><kml><foo/></kml>')
    assert exc.value.code == "GPX_INVALID_XML"


def test_empty_and_size_limits():
    with pytest.raises(GpxError) as exc:
        parse_gpx(b"   ")
    assert exc.value.code == "GPX_EMPTY"

    with pytest.raises(GpxError) as exc:
        parse_gpx(TRACK, max_bytes=10)
    assert exc.value.code == "GPX_TOO_LARGE"
    assert exc.value.status == 413


def test_node_and_point_limits():
    many_nodes = b'<?xml version="1.0"?><gpx><a/><a/><a/><a/></gpx>'
    with pytest.raises(GpxError) as exc:
        parse_gpx(many_nodes, max_nodes=3)
    assert exc.value.code == "GPX_TOO_MANY_NODES"

    with pytest.raises(GpxError) as exc:
        parse_gpx(TRACK, max_points=2)
    assert exc.value.code == "GPX_TOO_MANY_POINTS"


def test_invalid_coordinates_rejected():
    for lat in ("NaN", "inf", "91", "abc", None):
        body = b'<?xml version="1.0"?><gpx><rte>'
        if lat is not None:
            body += f'<rtept lat="{lat}" lon="6.1"/>'.encode()
        body += b'<rtept lat="46.2" lon="6.1"/></rte></gpx>'
        with pytest.raises(GpxError) as exc:
            parse_gpx(body)
        assert exc.value.code in ("GPX_INVALID_COORDINATES", "GPX_EMPTY")


def test_track_jump_rejected_but_route_order_trusted():
    jump = b"""<?xml version="1.0"?>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="46.0" lon="6.0"/><trkpt lat="-33.0" lon="151.0"/>
  </trkseg></trk>
</gpx>"""
    with pytest.raises(GpxError) as exc:
        parse_gpx(jump)
    assert exc.value.code == "GPX_IMPOSSIBLE_JUMP"

    rte = b"""<?xml version="1.0"?>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
  <rte><rtept lat="46.0" lon="6.0"/><rtept lat="-33.0" lon="151.0"/></rte>
</gpx>"""
    assert len(parse_gpx(rte).points) == 2  # waypoint order is intentional


def test_name_control_chars_stripped():
    # DEL (#x7F) is legal XML but never legal in a display/filename.
    gpx = b"""<?xml version="1.0"?>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata><name>Bad&#x7F;Name</name></metadata>
  <rte><rtept lat="46.0" lon="6.0"/><rtept lat="46.1" lon="6.1"/></rte>
</gpx>"""
    parsed = parse_gpx(gpx)
    assert parsed.name == "BadName"


def test_sanitize_filename_blocks_path_traversal():
    assert "/" not in sanitize_filename("../../etc/passwd")
    assert "\\" not in sanitize_filename("..\\..\\windows\\system32")
    assert sanitize_filename("Route One").endswith(".gpx")
    assert len(sanitize_filename("x" * 300)) <= 64  # 60 + ".gpx"
    assert sanitize_filename("///") == "route.gpx"


def test_export_gpx_shape_and_no_user_data():
    content = export_gpx("My Route", [(46.2, 6.14, 400.0), (46.3, 6.15, None)])
    assert content.startswith("<?xml")
    assert 'version="1.1"' in content
    assert content.count("<rtept") == 2
    assert "<ele>400.0</ele>" in content
    assert "46.300000" in content
    # Export is geometry + name only: no ids, no users, no timestamps.
    for leak in ("uuid", "user", "email", "owner"):
        assert leak not in content.lower()


# --- metrics (pure, deterministic) -----------------------------------------
def test_distance_and_gain_derived_from_points():
    pts = [
        GeoPoint(46.2000, 6.1400, 400.0),
        GeoPoint(46.2010, 6.1410, 420.0),
        GeoPoint(46.2020, 6.1420, 410.0),
    ]
    m = compute(pts, "road")
    assert 150.0 < m.distance_m < 350.0
    assert m.elevation_gain_m == 20.0
    assert m.elevation_loss_m == 10.0
    assert m.highest_point_m == 420.0
    assert m.lowest_point_m == 400.0
    assert m.estimated_duration_s and m.estimated_duration_s > 0
    assert m.difficulty in ("easy", "moderate", "hard", "extreme")
    assert m.profile and m.profile[0][0] == 0.0


def test_metrics_without_elevation_are_unknown_not_zero():
    pts = [GeoPoint(46.2, 6.14), GeoPoint(46.3, 6.15)]
    m = compute(pts, "road")
    assert m.distance_m > 0
    assert m.elevation_gain_m is None
    assert m.elevation_loss_m is None
    assert m.highest_point_m is None
    assert m.profile == []


def test_metrics_deterministic():
    pts = [GeoPoint(46.2, 6.14, 100.0), GeoPoint(46.3, 6.15, 300.0)]
    assert compute(pts, "road") == compute(pts, "road")


def test_elevation_hysteresis_matches_ride_engine():
    # ±1 m noise below the 3 m threshold must not create fake gain.
    noisy = [
        GeoPoint(46.20, 6.14, 100.0),
        GeoPoint(46.21, 6.15, 101.0),
        GeoPoint(46.22, 6.16, 99.5),
        GeoPoint(46.23, 6.17, 100.5),
    ]
    m = compute(noisy, "road")
    assert m.elevation_gain_m == 0.0
    assert m.elevation_loss_m == 0.0


def test_difficulty_rules_are_labelled():
    assert DIFFICULTY_BASIS == "rule_based_v1"
    flat_short = compute([GeoPoint(46.2, 6.14), GeoPoint(46.21, 6.15)], "road")
    assert flat_short.difficulty == "easy"
    climb = compute([GeoPoint(46.2, 6.14, 0.0), GeoPoint(46.2, 6.15, 1300.0)], "road")
    assert climb.difficulty in ("hard", "extreme")


def test_dedupe_consecutive_exact_match_only():
    pts = [
        GeoPoint(46.2, 6.14, 100.0),
        GeoPoint(46.2, 6.14, 100.0),
        GeoPoint(46.2, 6.14, 101.0),
    ]
    out = dedupe_consecutive(pts)
    assert len(out) == 2  # elevation change keeps the point
