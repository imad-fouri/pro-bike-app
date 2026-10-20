import 'package:uuid/uuid.dart';

/// Canonical persisted record of one completed ride; all values are SI units.
class Ride {
  final String id;
  final String? bikeId;
  final DateTime startedAt;
  final DateTime endedAt;
  final double distanceMeters;
  final int movingSeconds;
  final int durationSeconds;
  final double avgSpeedMps;
  final double maxSpeedMps;
  final double elevationGainMeters;

  const Ride({
    required this.id,
    this.bikeId,
    required this.startedAt,
    required this.endedAt,
    required this.distanceMeters,
    required this.movingSeconds,
    required this.durationSeconds,
    required this.avgSpeedMps,
    required this.maxSpeedMps,
    required this.elevationGainMeters,
  });

  factory Ride.create({
    String? bikeId,
    required DateTime startedAt,
    required DateTime endedAt,
    required double distanceMeters,
    required int movingSeconds,
    required int durationSeconds,
    required double avgSpeedMps,
    required double maxSpeedMps,
    required double elevationGainMeters,
  }) {
    return Ride(
      id: const Uuid().v4(),
      bikeId: bikeId,
      startedAt: startedAt,
      endedAt: endedAt,
      distanceMeters: distanceMeters,
      movingSeconds: movingSeconds,
      durationSeconds: durationSeconds,
      avgSpeedMps: avgSpeedMps,
      maxSpeedMps: maxSpeedMps,
      elevationGainMeters: elevationGainMeters,
    );
  }

  factory Ride.fromMe(Map<String, dynamic> json) {
    return Ride(
      id: '${json['id']}',
      bikeId: json['bikeId'] == null ? null : '${json['bikeId']}',
      startedAt: DateTime.parse('${json['startedAt']}'),
      endedAt: DateTime.parse('${json['endedAt']}'),
      distanceMeters: (json['distanceMeters'] as num).toDouble(),
      movingSeconds: (json['movingSeconds'] as num).toInt(),
      durationSeconds: (json['durationSeconds'] as num).toInt(),
      avgSpeedMps: (json['avgSpeedMps'] as num).toDouble(),
      maxSpeedMps: (json['maxSpeedMps'] as num).toDouble(),
      elevationGainMeters: (json['elevationGainMeters'] as num).toDouble(),
    );
  }

  Map<String, dynamic> toMe() => <String, dynamic>{
    'id': id,
    'bikeId': bikeId,
    'startedAt': startedAt.toIso8601String(),
    'endedAt': endedAt.toIso8601String(),
    'distanceMeters': distanceMeters,
    'movingSeconds': movingSeconds,
    'durationSeconds': durationSeconds,
    'avgSpeedMps': avgSpeedMps,
    'maxSpeedMps': maxSpeedMps,
    'elevationGainMeters': elevationGainMeters,
  };
}
