/// One raw recorded GPS sample; timestamps are UTC.
class RidePoint {
  final double lat;
  final double lng;
  final double? altitude;
  final double? speedMps;
  final double? accuracyMeters;
  final DateTime timestamp;

  const RidePoint({
    required this.lat,
    required this.lng,
    this.altitude,
    this.speedMps,
    this.accuracyMeters,
    required this.timestamp,
  });

  factory RidePoint.fromMe(Map<String, dynamic> json) {
    return RidePoint(
      lat: (json['lat'] as num).toDouble(),
      lng: (json['lng'] as num).toDouble(),
      altitude: json['altitude'] == null ? null : (json['altitude'] as num).toDouble(),
      speedMps: json['speedMps'] == null ? null : (json['speedMps'] as num).toDouble(),
      accuracyMeters: json['accuracyMeters'] == null ? null : (json['accuracyMeters'] as num).toDouble(),
      timestamp: DateTime.parse('${json['timestamp']}'),
    );
  }

  Map<String, dynamic> toMe() => <String, dynamic>{
    'lat': lat,
    'lng': lng,
    'altitude': altitude,
    'speedMps': speedMps,
    'accuracyMeters': accuracyMeters,
    'timestamp': timestamp.toIso8601String(),
  };
}
