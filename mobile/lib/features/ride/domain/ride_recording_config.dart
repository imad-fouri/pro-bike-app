/// Toggle for the GPS accuracy tier requested from the platform location service.
enum LocationAccuracyLevel {
  /// Medium accuracy (~10 m); cheapest battery use.
  balanced,

  /// High accuracy (~5 m); default for ride recording.
  high,

  /// Best accuracy with navigation-grade delivery (~1–3 m); most expensive.
  bestForNavigation,
}

/// Tuning knobs for a single ride recording session; all units are SI.
class RideRecordingConfig {
  const RideRecordingConfig({
    this.distanceFilterMeters = 5.0,
    this.intervalMs = 1000,
    this.accuracy = LocationAccuracyLevel.bestForNavigation,
    this.wakeLock = true,
    this.staleFixSeconds = 10,
  });

  /// Minimum displacement before a fix is appended to the raw buffer.
  final double distanceFilterMeters;

  /// Desired fix interval while recording.
  final int intervalMs;

  /// Accuracy tier requested from the platform location service.
  final LocationAccuracyLevel accuracy;

  /// Whether recording holds a partial wake lock to survive screen-off.
  final bool wakeLock;

  /// Seconds without a fix before the UI flags the trail as stale.
  final int staleFixSeconds;

  RideRecordingConfig copyWith({
    double? distanceFilterMeters,
    int? intervalMs,
    LocationAccuracyLevel? accuracy,
    bool? wakeLock,
    int? staleFixSeconds,
  }) {
    return RideRecordingConfig(
      distanceFilterMeters: distanceFilterMeters ?? this.distanceFilterMeters,
      intervalMs: intervalMs ?? this.intervalMs,
      accuracy: accuracy ?? this.accuracy,
      wakeLock: wakeLock ?? this.wakeLock,
      staleFixSeconds: staleFixSeconds ?? this.staleFixSeconds,
    );
  }
}
