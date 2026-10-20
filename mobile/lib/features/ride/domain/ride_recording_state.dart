import 'package:latlong2/latlong.dart';

import 'ride_metrics.dart';

/// Lifecycle states of the ride recording controller.
enum RideRecordingStatus {
  /// No session; start button visible.
  idle,

  /// Location permission denied or deferred.
  permissionDenied,

  /// Device location services are off.
  locationServiceDisabled,

  /// GPS stream active and metrics updating.
  recording,

  /// Stream paused by the user; metrics frozen.
  paused,

  /// Ride is being persisted after stop.
  saving,

  /// Persisted successfully; detail page ready.
  saved,

  /// Something failed; `errorMessage` explains what.
  error,
}

/// Immutable snapshot of the live recording session.
class RideRecordingState {
  const RideRecordingState({
    this.status = RideRecordingStatus.idle,
    this.metrics = const RideMetrics.zero(),
    this.pointCount = 0,
    this.lastLocation,
    this.lastAccuracyMeters,
    this.startedAt,
    this.rideId,
    this.autoPaused = false,
    this.staleFix = false,
    this.errorMessage,
  });

  final RideRecordingStatus status;
  final RideMetrics metrics;

  /// Raw buffered points; equals persisted point count after stop.
  final int pointCount;
  final LatLng? lastLocation;
  final double? lastAccuracyMeters;
  final DateTime? startedAt;
  final String? rideId;
  final bool autoPaused;
  final bool staleFix;
  final String? errorMessage;

  bool get isRecording =>
      status == RideRecordingStatus.recording ||
      status == RideRecordingStatus.paused;

  RideRecordingState copyWith({
    RideRecordingStatus? status,
    RideMetrics? metrics,
    int? pointCount,
    LatLng? lastLocation,
    double? lastAccuracyMeters,
    DateTime? startedAt,
    String? rideId,
    bool? autoPaused,
    bool? staleFix,
    String? errorMessage,
    bool clearLastLocation = false,
    bool clearRideId = false,
    bool clearErrorMessage = false,
  }) {
    return RideRecordingState(
      status: status ?? this.status,
      metrics: metrics ?? this.metrics,
      pointCount: pointCount ?? this.pointCount,
      lastLocation: clearLastLocation ? null : lastLocation ?? this.lastLocation,
      lastAccuracyMeters: lastAccuracyMeters ?? this.lastAccuracyMeters,
      startedAt: startedAt ?? this.startedAt,
      rideId: clearRideId ? null : rideId ?? this.rideId,
      autoPaused: autoPaused ?? this.autoPaused,
      staleFix: staleFix ?? this.staleFix,
      errorMessage:
          clearErrorMessage ? null : errorMessage ?? this.errorMessage,
    );
  }
}
