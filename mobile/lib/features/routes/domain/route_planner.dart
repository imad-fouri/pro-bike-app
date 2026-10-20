/// Route-planning models: Nominatim place matches and OSRM cycling results.
///
/// Parsed from the backend proxy (`GET /api/v1/routes/geocode`,
/// `POST /api/v1/routes/plan`) — never fabricated. Elevation and surface
/// data are absent by design (no DEM source): callers render explicit
/// unavailable states instead of inventing values.
class PlaceResult {
  final String name;
  final double lat;
  final double lon;

  const PlaceResult({required this.name, required this.lat, required this.lon});

  factory PlaceResult.fromJson(Map<String, dynamic> json) {
    return PlaceResult(
      name: '${json['name']}',
      lat: _dbl(json['lat']),
      lon: _dbl(json['lon']),
    );
  }
}

/// One provider-calculated cycling route: the provider's own geometry,
/// distance, and duration estimates. Exactly one route — alternatives are
/// never invented.
class PlannedRoute {
  final List<PlannedPoint> points;
  final double distanceM;
  final double durationS;

  const PlannedRoute({
    required this.points,
    required this.distanceM,
    required this.durationS,
  });

  factory PlannedRoute.fromJson(Map<String, dynamic> json) {
    return PlannedRoute(
      points: [
        for (final e in (json['points'] as List? ?? const []))
          PlannedPoint.fromJson(e as Map<String, dynamic>),
      ],
      distanceM: _dbl(json['distance_m']),
      durationS: _dbl(json['duration_s']),
    );
  }
}

class PlannedPoint {
  final double lat;
  final double lon;

  const PlannedPoint({required this.lat, required this.lon});

  factory PlannedPoint.fromJson(Map<String, dynamic> json) {
    return PlannedPoint(lat: _dbl(json['lat']), lon: _dbl(json['lon']));
  }
}

double _dbl(Object? v) {
  if (v is num) return v.toDouble();
  return double.tryParse('$v') ?? 0.0;
}
