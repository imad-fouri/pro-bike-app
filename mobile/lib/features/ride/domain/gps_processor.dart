import 'dart:math' as math;

/// Client-side GPS processing engine. Mirrors backend `gps_engine.py`
/// thresholds exactly; the server revalidates on ingest (authoritative).
/// Provenance: observations raw, metrics derived — never mixed.
class GpsPolicy {
  final double accuracyRejectM;
  final double jumpSpeedRejectMS;
  final double elevationThresholdM;
  final double movingThresholdMS;

  const GpsPolicy({
    this.accuracyRejectM = 25.0,
    this.jumpSpeedRejectMS = 80.0,
    this.elevationThresholdM = 3.0,
    this.movingThresholdMS = 1.0,
  });
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

/// Mutable fold state. [resetSegment] on resume kills the pause gap.
class GpsEngine {
  final GpsPolicy policy;
  double? _lat, _lon, _eleBase;
  DateTime? _time, _first;
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
      _eleBase ??= o.alt;
      acceptedCount++;
      return const GpsVerdict(true, 'ok');
    }
    final dist = haversineM(_lat!, _lon!, o.lat, o.lon);
    final dt = o.time.difference(_time!).inMilliseconds / 1000.0;
    if (dt <= 0) return const GpsVerdict(false, 'time_regression');
    if (dist < 1.0 && dt < 1.0) {
      _seq = o.seq;
      return const GpsVerdict(false, 'duplicate');
    }
    if (dist / dt > policy.jumpSpeedRejectMS) {
      return const GpsVerdict(false, 'jump');
    }
    final moving = dist / dt >= policy.movingThresholdMS;
    distanceM += dist;
    if (moving) movingS += dt;
    if (dist / dt > maxSpeedMS) maxSpeedMS = dist / dt;
    if (o.alt != null) {
      _eleBase ??= o.alt;
      final delta = o.alt! - _eleBase!;
      if (delta >= policy.elevationThresholdM) {
        gainM += delta;
        _eleBase = o.alt;
      } else if (delta <= -policy.elevationThresholdM) {
        lossM += -delta;
        _eleBase = o.alt;
      }
    }
    _lat = o.lat;
    _lon = o.lon;
    _time = o.time;
    _seq = o.seq;
    acceptedCount++;
    return GpsVerdict(true, 'ok', distanceM: dist, dtS: dt, moving: moving);
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
