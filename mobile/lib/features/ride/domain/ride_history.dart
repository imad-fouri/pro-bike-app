/// Ride history + dashboard models. Parsed from the real `/api/v1/rides`
/// history, detail, points, and overview endpoints — never fabricated.
///
/// Numbers arrive as JSON numbers or decimal strings depending on the
/// encoder, so every numeric parse tolerates both. Missing optional data
/// stays null and the UI renders an unavailable state instead of inventing
/// a value.
double _dbl(Object? v) {
  if (v is num) return v.toDouble();
  return double.tryParse('$v') ?? 0.0;
}

int _int(Object? v) {
  if (v is num) return v.toInt();
  return int.tryParse('$v') ?? 0;
}

DateTime? _date(Object? v) {
  if (v is! String || v.isEmpty) return null;
  return DateTime.tryParse(v);
}

/// One persisted ride for the history feed and detail header.
class RideHistoryItem {
  final String id;
  final String bikeId;
  final String status;
  final DateTime? startedAt;
  final DateTime? endedAt;
  final double distanceM;
  final int movingSeconds;
  final int elapsedSeconds;
  final double avgSpeedMS;
  final double maxSpeedMS;
  final double gainM;
  final double lossM;
  final int acceptedPoints;
  final double? startLat;
  final double? startLon;
  final double? endLat;
  final double? endLon;
  final String? routeId;

  const RideHistoryItem({
    required this.id,
    required this.bikeId,
    required this.status,
    required this.startedAt,
    required this.endedAt,
    required this.distanceM,
    required this.movingSeconds,
    required this.elapsedSeconds,
    required this.avgSpeedMS,
    required this.maxSpeedMS,
    required this.gainM,
    required this.lossM,
    required this.acceptedPoints,
    this.startLat,
    this.startLon,
    this.endLat,
    this.endLon,
    this.routeId,
  });

  bool get isCompleted => status == 'completed';

  factory RideHistoryItem.fromJson(Map<String, dynamic> json) {
    final summary = json['summary'] as Map<String, dynamic>? ?? const {};
    double? opt(Object? v) => v == null ? null : _dbl(v);
    return RideHistoryItem(
      id: '${json['id']}',
      bikeId: '${json['bike_id']}',
      status: '${json['status']}',
      startedAt: _date(json['started_at']),
      endedAt: _date(json['ended_at']),
      distanceM: _dbl(summary['distance_m']),
      movingSeconds: _int(summary['moving_seconds']),
      elapsedSeconds: _int(summary['elapsed_seconds']),
      avgSpeedMS: _dbl(summary['average_speed_m_s']),
      maxSpeedMS: _dbl(summary['max_speed_m_s']),
      gainM: _dbl(summary['elevation_gain_m']),
      lossM: _dbl(summary['elevation_loss_m']),
      acceptedPoints: _int(summary['accepted_points']),
      startLat: opt(json['start_lat']),
      startLon: opt(json['start_lon']),
      endLat: opt(json['end_lat']),
      endLon: opt(json['end_lon']),
      routeId: json['route_id'] == null ? null : '${json['route_id']}',
    );
  }
}

/// One accepted fix for the detail map.
class RideTrackPoint {
  final int seq;
  final double lat;
  final double lon;
  final double? alt;

  const RideTrackPoint({
    required this.seq,
    required this.lat,
    required this.lon,
    this.alt,
  });

  factory RideTrackPoint.fromJson(Map<String, dynamic> json) {
    return RideTrackPoint(
      seq: _int(json['seq']),
      lat: _dbl(json['lat']),
      lon: _dbl(json['lon']),
      alt: json['alt'] == null ? null : _dbl(json['alt']),
    );
  }
}

/// Dashboard aggregates over completed rides (UTC calendar windows).
class RidesOverview {
  final int totalRides;
  final double totalDistanceM;
  final int totalMovingSeconds;
  final double totalGainM;
  final double weekDistanceM;
  final double monthDistanceM;
  final DateTime? weekStart;
  final DateTime? monthStart;

  const RidesOverview({
    required this.totalRides,
    required this.totalDistanceM,
    required this.totalMovingSeconds,
    required this.totalGainM,
    required this.weekDistanceM,
    required this.monthDistanceM,
    required this.weekStart,
    required this.monthStart,
  });

  bool get isEmpty => totalRides == 0;

  factory RidesOverview.fromJson(Map<String, dynamic> json) {
    return RidesOverview(
      totalRides: _int(json['total_rides']),
      totalDistanceM: _dbl(json['total_distance_m']),
      totalMovingSeconds: _int(json['total_moving_seconds']),
      totalGainM: _dbl(json['total_elevation_gain_m']),
      weekDistanceM: _dbl(json['week_distance_m']),
      monthDistanceM: _dbl(json['month_distance_m']),
      weekStart: _date(json['week_start']),
      monthStart: _date(json['month_start']),
    );
  }
}
