import 'dart:math' as math;

/// Client-side GPS processing engine. Mirrors backend `gps_engine.py`
/// thresholds exactly; the server revalidates on ingest (authoritative).
/// Provenance: observations raw, metrics derived — never mixed.
///
/// Metering uses a confidence anchor: distance, moving time, and elevation
/// accrue only between fixes displaced enough to be distinguishable from
/// GPS noise. Stationary jitter advances sequence/time bookkeeping but
/// never the meters, so standing still cannot inflate a ride. Slow but
/// genuine movement is preserved — it accrues in gate-sized chunks once
/// displacement exceeds the confidence radius, only later, never lost.
class GpsPolicy {
  final double accuracyRejectM;
  final double jumpSpeedRejectMS;
  final double elevationThresholdM;
  final double movingThresholdMS;

  /// Required displacement for a confident move:
  /// `max(minConfidentDisplacementM, effectiveAccuracy * confidenceFactor)`,
  /// where effectiveAccuracy is the fix's reported accuracy or
  /// [defaultAccuracyM] when the platform reports none.
  ///
  /// Rationale: fixes scatter within roughly one sigma of truth, so a move
  /// shorter than the reported accuracy is indistinguishable from noise.
  /// The floor keeps the gate sane when the platform claims implausibly
  /// good accuracy; the default covers fixes with no accuracy at all.
  final double confidenceFactor;
  final double minConfidentDisplacementM;
  final double defaultAccuracyM;

  const GpsPolicy({
    this.accuracyRejectM = 25.0,
    this.jumpSpeedRejectMS = 80.0,
    this.elevationThresholdM = 3.0,
    this.movingThresholdMS = 1.0,
    this.confidenceFactor = 1.0,
    this.minConfidentDisplacementM = 3.0,
    this.defaultAccuracyM = 8.0,
  });

  double requiredDisplacementM(double? accuracy) {
    final effective = accuracy ?? defaultAccuracyM;
    final scaled = effective * confidenceFactor;
    return scaled > minConfidentDisplacementM
        ? scaled
        : minConfidentDisplacementM;
  }
}

class GpsObservation {
  final int seq;
  final double lat;
  final double lon;
  final DateTime time;
  final double? alt;
  final double? accuracy;
  final double? speed;
  final double? heading;

  const GpsObservation({
    required this.seq,
    required this.lat,
    required this.lon,
    required this.time,
    this.alt,
    this.accuracy,
    this.speed,
    this.heading,
  });
}

class GpsVerdict {
  final bool accepted;
  final String reason;
  final double distanceM;
  final double dtS;
  final bool moving;

  const GpsVerdict(
    this.accepted,
    this.reason, {
    this.distanceM = 0,
    this.dtS = 0,
    this.moving = false,
  });
}

double haversineM(double lat1, double lon1, double lat2, double lon2) {
  const r = 6371000.0;
  final p1 = lat1 * math.pi / 180;
  final p2 = lat2 * math.pi / 180;
  final dp = (lat2 - lat1) * math.pi / 180;
  final dl = (lon2 - lon1) * math.pi / 180;
  final a =
      math.sin(dp / 2) * math.sin(dp / 2) +
      math.cos(p1) * math.cos(p2) * math.sin(dl / 2) * math.sin(dl / 2);
  return 2 * r * math.asin(math.sqrt(a));
}

/// Mutable fold state. [resetSegment] on resume kills the pause gap: the
/// anchor and per-fix continuity are cleared (metrics, sequence, and the
/// elevation baseline persist — altitude is absolute, not relative).
class GpsEngine {
  final GpsPolicy policy;

  /// Last seen fix: per-fix continuity (ordering, jump, max speed).
  double? _lat, _lon;
  DateTime? _time;

  /// Confidence anchor: last fix that moved the meters.
  double? _anchorLat, _anchorLon, _anchorAlt;
  DateTime? _anchorTime;

  DateTime? _first;
  int? _seq;
  double distanceM = 0;
  double gainM = 0;
  double lossM = 0;
  double movingS = 0;
  double maxSpeedMS = 0;
  int acceptedCount = 0;

  GpsEngine({this.policy = const GpsPolicy()});

  void resetSegment() {
    _lat = _lon = null;
    _time = null;
    _anchorLat = _anchorLon = null;
    _anchorTime = null;
  }

  GpsVerdict process(GpsObservation o) {
    if (o.lat < -90 || o.lat > 90 || o.lon < -180 || o.lon > 180) {
      return const GpsVerdict(false, 'invalid_coords');
    }
    if (o.accuracy != null && o.accuracy! > policy.accuracyRejectM) {
      return const GpsVerdict(false, 'bad_accuracy');
    }
    if (_seq != null && o.seq <= _seq!) {
      return GpsVerdict(false, o.seq == _seq ? 'duplicate' : 'out_of_order');
    }
    if (_time != null && !o.time.isAfter(_time!)) {
      return const GpsVerdict(false, 'time_regression');
    }
    if (o.speed != null && (o.speed! < 0 || o.speed! > 80.0)) {
      return const GpsVerdict(false, 'bad_speed');
    }
    if (_lat == null || _time == null) {
      _lat = o.lat;
      _lon = o.lon;
      _time = o.time;
      _seq = o.seq;
      _first ??= o.time;
      _anchorLat ??= o.lat;
      _anchorLon ??= o.lon;
      _anchorTime ??= o.time;
      _anchorAlt ??= o.alt;
      acceptedCount++;
      return const GpsVerdict(true, 'ok');
    }
    final fixDist = haversineM(_lat!, _lon!, o.lat, o.lon);
    final fixDt = o.time.difference(_time!).inMilliseconds / 1000.0;
    if (fixDt <= 0) return const GpsVerdict(false, 'time_regression');
    if (fixDist < 1.0 && fixDt < 1.0) {
      _seq = o.seq;
      return const GpsVerdict(false, 'duplicate');
    }
    if (fixDist / fixDt > policy.jumpSpeedRejectMS) {
      // Impossible jump: do NOT advance anything, so one glitch cannot
      // poison the next good fix.
      return const GpsVerdict(false, 'jump');
    }
    final fixSpeed = fixDist / fixDt;
    if (fixSpeed > maxSpeedMS) maxSpeedMS = fixSpeed;

    if (_anchorLat == null || _anchorTime == null) {
      // Continuity without an anchor (defensive: construction and resume
      // always set both): adopt the last fix so metering continues.
      _anchorLat = _lat;
      _anchorLon = _lon;
      _anchorTime = _time;
    }
    final segDist = haversineM(_anchorLat!, _anchorLon!, o.lat, o.lon);
    if (segDist < policy.requiredDisplacementM(o.accuracy)) {
      // Stationary jitter (or sub-confidence shuffle): advance continuity
      // bookkeeping so later fixes judge against fresh state, but move no
      // meters and touch no elevation baseline.
      _lat = o.lat;
      _lon = o.lon;
      _time = o.time;
      _seq = o.seq;
      return const GpsVerdict(false, 'stationary');
    }

    final segDt = o.time.difference(_anchorTime!).inMilliseconds / 1000.0;
    final moving = segDist / segDt >= policy.movingThresholdMS;
    distanceM += segDist;
    if (moving) movingS += segDt;
    if (o.alt != null) {
      _anchorAlt ??= o.alt;
      final delta = o.alt! - _anchorAlt!;
      if (delta >= policy.elevationThresholdM) {
        gainM += delta;
        _anchorAlt = o.alt;
      } else if (delta <= -policy.elevationThresholdM) {
        lossM += -delta;
        _anchorAlt = o.alt;
      }
    }
    _lat = o.lat;
    _lon = o.lon;
    _time = o.time;
    _anchorLat = o.lat;
    _anchorLon = o.lon;
    _anchorTime = o.time;
    _seq = o.seq;
    acceptedCount++;
    return GpsVerdict(
      true,
      'ok',
      distanceM: segDist,
      dtS: segDt,
      moving: moving,
    );
  }

  RideMetrics metrics() {
    final elapsed = _first == null || _time == null
        ? 0
        : _time!.difference(_first!).inSeconds;
    return RideMetrics(
      distanceM: distanceM,
      gainM: gainM,
      lossM: lossM,
      movingS: movingS.round(),
      elapsedS: elapsed,
      avgSpeedMS: movingS > 0 ? distanceM / movingS : 0,
      maxSpeedMS: maxSpeedMS,
    );
  }
}

class RideMetrics {
  final double distanceM;
  final double gainM;
  final double lossM;
  final int movingS;
  final int elapsedS;
  final double avgSpeedMS;
  final double maxSpeedMS;

  const RideMetrics({
    required this.distanceM,
    required this.gainM,
    required this.lossM,
    required this.movingS,
    required this.elapsedS,
    required this.avgSpeedMS,
    required this.maxSpeedMS,
  });
}
