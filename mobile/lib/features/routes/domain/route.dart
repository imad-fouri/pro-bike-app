/// Route entities (planned geometry). Canonical units from the API:
/// metres/seconds; presentation converts to km/mi or ft (§43).
///
/// A Route is never a Ride (§49): rides reference (route_id, route_version).
class AppRoute {
  final String id;
  final String name;
  final String? description;
  final String activityType;
  final String privacy;
  final String status;
  final String source;
  final int currentVersion;
  final double distanceM;
  final double? elevationGainM;
  final double? elevationLossM;
  final double? highestPointM;
  final double? lowestPointM;
  final int? estimatedDurationS;
  final String? difficulty;
  final int pointCount;
  final double? startLat;
  final double? startLon;
  final double? endLat;
  final double? endLon;
  final DateTime createdAt;
  final DateTime updatedAt;

  const AppRoute({
    required this.id,
    required this.name,
    this.description,
    required this.activityType,
    required this.privacy,
    required this.status,
    required this.source,
    required this.currentVersion,
    required this.distanceM,
    this.elevationGainM,
    this.elevationLossM,
    this.highestPointM,
    this.lowestPointM,
    this.estimatedDurationS,
    this.difficulty,
    required this.pointCount,
    this.startLat,
    this.startLon,
    this.endLat,
    this.endLon,
    required this.createdAt,
    required this.updatedAt,
  });

  bool get isArchived => status == 'archived';
  String get activityKey => 'routes.activity.$activityType';
  String get privacyKey => 'routes.privacy.$privacy';
  String get difficultyKey =>
      difficulty == null ? '' : 'routes.difficulty.$difficulty';

  /// DERIVED distance, displayed with the user's unit system.
  String distanceLabel({required bool imperial}) {
    if (imperial) return '${(distanceM / 1609.344).toStringAsFixed(1)} mi';
    return '${(distanceM / 1000).toStringAsFixed(1)} km';
  }

  /// DERIVED gain; null means "unknown", never rendered as 0.
  String gainLabel({required bool imperial}) {
    if (elevationGainM == null) return '—';
    if (imperial) return '+${(elevationGainM! * 3.28084).round()} ft';
    return '+${elevationGainM!.round()} m';
  }

  /// ESTIMATED moving time (documented formula, never claimed as exact).
  String durationLabel() {
    final s = estimatedDurationS;
    if (s == null) return '—';
    final h = s ~/ 3600;
    final m = (s % 3600) ~/ 60;
    return h > 0 ? '${h}h ${m.toString().padLeft(2, '0')}m' : '${m}m';
  }

  factory AppRoute.fromJson(Map<String, dynamic> json) => AppRoute(
    id: '${json['id']}',
    name: '${json['name']}',
    description: json['description'] as String?,
    activityType: '${json['activity_type']}',
    privacy: '${json['privacy']}',
    status: '${json['status']}',
    source: '${json['source']}',
    currentVersion: (json['current_version'] as num? ?? 1).toInt(),
    distanceM: ((json['distance_m'] as num?) ?? 0).toDouble(),
    elevationGainM: (json['elevation_gain_m'] as num?)?.toDouble(),
    elevationLossM: (json['elevation_loss_m'] as num?)?.toDouble(),
    highestPointM: (json['highest_point_m'] as num?)?.toDouble(),
    lowestPointM: (json['lowest_point_m'] as num?)?.toDouble(),
    estimatedDurationS: (json['estimated_duration_s'] as num?)?.toInt(),
    difficulty: json['difficulty'] as String?,
    pointCount: (json['point_count'] as num? ?? 0).toInt(),
    startLat: (json['start_lat'] as num?)?.toDouble(),
    startLon: (json['start_lon'] as num?)?.toDouble(),
    endLat: (json['end_lat'] as num?)?.toDouble(),
    endLon: (json['end_lon'] as num?)?.toDouble(),
    createdAt: DateTime.parse('${json['created_at']}'),
    updatedAt: DateTime.parse('${json['updated_at']}'),
  );

  Map<String, dynamic> toCreateJson({required List<RoutePointInput> points}) =>
      {
        'name': name,
        if (description != null && description!.isNotEmpty)
          'description': description,
        'activity_type': activityType,
        'privacy': privacy,
        'points': [for (final p in points) p.toJson()],
      };

  /// Server-shaped JSON (offline cache round-trips through [fromJson]).
  Map<String, dynamic> toJson() => {
    'id': id,
    'name': name,
    'description': description,
    'activity_type': activityType,
    'privacy': privacy,
    'status': status,
    'source': source,
    'current_version': currentVersion,
    'distance_m': distanceM,
    'elevation_gain_m': elevationGainM,
    'elevation_loss_m': elevationLossM,
    'highest_point_m': highestPointM,
    'lowest_point_m': lowestPointM,
    'estimated_duration_s': estimatedDurationS,
    'difficulty': difficulty,
    'point_count': pointCount,
    'start_lat': startLat,
    'start_lon': startLon,
    'end_lat': endLat,
    'end_lon': endLon,
    'created_at': createdAt.toIso8601String(),
    'updated_at': updatedAt.toIso8601String(),
  };
}

/// One vertex of a planned route. ele == null means "no elevation data".
class RoutePointInput {
  final double lat;
  final double lon;
  final double? ele;

  const RoutePointInput({required this.lat, required this.lon, this.ele});

  Map<String, dynamic> toJson() => {
    'lat': lat,
    'lon': lon,
    if (ele != null) 'ele': ele,
  };
}

/// Ordered geometry of one immutable route version.
class RouteGeometry {
  final String routeId;
  final int versionNo;
  final int pointCount;
  final List<RoutePointInput> points;
  final List<List<double>> elevationProfile;

  const RouteGeometry({
    required this.routeId,
    required this.versionNo,
    required this.pointCount,
    required this.points,
    this.elevationProfile = const [],
  });

  factory RouteGeometry.fromJson(Map<String, dynamic> json) => RouteGeometry(
    routeId: '${json['route_id']}',
    versionNo: (json['version_no'] as num? ?? 1).toInt(),
    pointCount: (json['point_count'] as num? ?? 0).toInt(),
    points: [
      for (final p in (json['points'] as List? ?? const []))
        RoutePointInput(
          lat: ((p as Map)['lat'] as num).toDouble(),
          lon: (p['lon'] as num).toDouble(),
          ele: (p['ele'] as num?)?.toDouble(),
        ),
    ],
    elevationProfile: [
      for (final row in (json['elevation_profile'] as List? ?? const []))
        [for (final v in row as List) (v as num).toDouble()],
    ],
  );

  Map<String, dynamic> toJson() => {
    'route_id': routeId,
    'version_no': versionNo,
    'point_count': pointCount,
    'points': [
      for (final p in points)
        {'lat': p.lat, 'lon': p.lon, if (p.ele != null) 'ele': p.ele},
    ],
    if (elevationProfile.isNotEmpty) 'elevation_profile': elevationProfile,
  };
}

/// Version history row — proof that edits never rewrite history.
class RouteVersionSummary {
  final String id;
  final int versionNo;
  final int pointCount;
  final double distanceM;
  final double? elevationGainM;
  final int? estimatedDurationS;
  final String? changelog;
  final DateTime createdAt;

  const RouteVersionSummary({
    required this.id,
    required this.versionNo,
    required this.pointCount,
    required this.distanceM,
    this.elevationGainM,
    this.estimatedDurationS,
    this.changelog,
    required this.createdAt,
  });

  factory RouteVersionSummary.fromJson(Map<String, dynamic> json) =>
      RouteVersionSummary(
        id: '${json['id']}',
        versionNo: (json['version_no'] as num? ?? 1).toInt(),
        pointCount: (json['point_count'] as num? ?? 0).toInt(),
        distanceM: ((json['distance_m'] as num?) ?? 0).toDouble(),
        elevationGainM: (json['elevation_gain_m'] as num?)?.toDouble(),
        estimatedDurationS: (json['estimated_duration_s'] as num?)?.toInt(),
        changelog: json['changelog'] as String?,
        createdAt: DateTime.parse('${json['created_at']}'),
      );
}
