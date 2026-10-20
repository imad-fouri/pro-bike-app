import 'dart:math';

import 'package:latlong2/latlong.dart';

import 'ride_point.dart';

/// Aggregated, persisted-from ride totals.
class RideMetrics {
  const RideMetrics({
    required this.distanceMeters,
    required this.movingSeconds,
    required this.durationSeconds,
    required this.avgSpeedMps,
    required this.maxSpeedMps,
    required this.elevationGainMeters,
  });

  const RideMetrics.zero()
      : distanceMeters = 0,
        movingSeconds = 0,
        durationSeconds = 0,
        avgSpeedMps = 0,
        maxSpeedMps = 0,
        elevationGainMeters = 0;

  final double distanceMeters;
  final int movingSeconds;
  final int durationSeconds;
  final double avgSpeedMps;
  final double maxSpeedMps;
  final double elevationGainMeters;
}

/// Tuning knobs for [RideMetricsCalculator].
class RideMetricsConfig {
  const RideMetricsConfig({
    this.distanceFilterMeters = 5.0,
    this.accuracyCutoffMeters = 30.0,
    this.movingSpeedThreshold = 0.5,
    this.autoPauseGraceSeconds = 5,
    this.elevationHysteresisMeters = 2.0,
    this.maxPlausibleSpeedMps = 30.0,
    this.altitudeSmoothingWindow = 5,
  });

  final double distanceFilterMeters;
  final double accuracyCutoffMeters;
  final double movingSpeedThreshold;
  final int autoPauseGraceSeconds;
  final double elevationHysteresisMeters;
  final double maxPlausibleSpeedMps;
  final int altitudeSmoothingWindow;
}

/// Pure aggregation over a raw GPS point list.
///
/// Mirrors the acceptance rules used by the recording pipeline: bad-accuracy,
/// non-advancing, jump and proximity-rejected points are dropped before any
/// cumulative distance, moving-time or elevation bookkeeping.
class RideMetricsCalculator {
  RideMetricsCalculator._();

  static final Distance _distance = Distance();

  static RideMetrics compute(
    List<RidePoint> points, {
    RideMetricsConfig config = const RideMetricsConfig(),
  }) {
    if (points.length < 2) return const RideMetrics.zero();

    RidePoint? lastAccepted;
    RidePoint? firstAccepted;

    double distanceMeters = 0;
    double movingSecondsRaw = 0;
    double belowThresholdSeconds = 0;
    double maxSpeedMps = 0;
    final speedWindow = <double>[];

    double? elevationAnchor;
    double? elevationPeak;
    double elevationGainMeters = 0;
    final altitudeWindow = <double>[];

    for (final point in points) {
      final accuracy = point.accuracyMeters;
      if (accuracy != null && accuracy > config.accuracyCutoffMeters) {
        continue;
      }

      final previous = lastAccepted;
      if (previous == null) {
        lastAccepted = point;
        firstAccepted = point;
        _pushAltitude(altitudeWindow, point.altitude, config.altitudeSmoothingWindow);
        _seedElevation(point.altitude, altitudeWindow, (a, p) {
          elevationAnchor = a;
          elevationPeak = p;
        });
        continue;
      }

      final dtSeconds = point.timestamp.difference(previous.timestamp).inMicroseconds / 1e6;
      if (dtSeconds <= 0) continue;

      final segmentMeters = _distance.as(
        LengthUnit.Meter,
        LatLng(previous.lat, previous.lng),
        LatLng(point.lat, point.lng),
      );
      final segmentSpeed = segmentMeters / dtSeconds;
      if (segmentSpeed > config.maxPlausibleSpeedMps) continue;
      if (segmentMeters < config.distanceFilterMeters) continue;

      distanceMeters += segmentMeters;

      speedWindow.add(segmentSpeed);
      if (speedWindow.length > 3) speedWindow.removeAt(0);
      final smoothedSpeed = speedWindow.reduce((a, b) => a + b) / speedWindow.length;
      if (smoothedSpeed > maxSpeedMps) maxSpeedMps = smoothedSpeed;

      if (smoothedSpeed >= config.movingSpeedThreshold) {
        movingSecondsRaw += dtSeconds;
        belowThresholdSeconds = 0;
      } else {
        if (belowThresholdSeconds <= config.autoPauseGraceSeconds) {
          movingSecondsRaw += dtSeconds;
        }
        belowThresholdSeconds += dtSeconds;
      }

      lastAccepted = point;
      firstAccepted ??= point;

      final smoothedAltitude = _pushAltitude(
        altitudeWindow,
        point.altitude,
        config.altitudeSmoothingWindow,
      );
      if (smoothedAltitude != null) {
        if (elevationAnchor == null || elevationPeak == null) {
          elevationAnchor = smoothedAltitude;
          elevationPeak = smoothedAltitude;
        } else {
          if (smoothedAltitude > elevationPeak!) elevationPeak = smoothedAltitude;
          if (smoothedAltitude < elevationPeak! - config.elevationHysteresisMeters) {
            elevationGainMeters += elevationPeak! - elevationAnchor!;
            elevationAnchor = smoothedAltitude;
            elevationPeak = smoothedAltitude;
          }
        }
      }
    }

    if (lastAccepted == null || firstAccepted == null) return const RideMetrics.zero();

    if (elevationAnchor != null && elevationPeak != null) {
      elevationGainMeters += elevationPeak! - elevationAnchor!;
    }

    final durationSeconds =
        lastAccepted.timestamp.difference(firstAccepted.timestamp).inSeconds;
    final movingSeconds = min(movingSecondsRaw.round(), durationSeconds);
    final avgSpeedMps = movingSeconds > 0 ? distanceMeters / movingSeconds : 0.0;

    return RideMetrics(
      distanceMeters: distanceMeters,
      movingSeconds: movingSeconds,
      durationSeconds: durationSeconds,
      avgSpeedMps: avgSpeedMps,
      maxSpeedMps: maxSpeedMps,
      elevationGainMeters: elevationGainMeters,
    );
  }

  static double? _pushAltitude(
    List<double> window,
    double? altitude,
    int size,
  ) {
    if (altitude == null) return null;
    window.add(altitude);
    if (window.length > size) window.removeAt(0);
    return window.reduce((a, b) => a + b) / window.length;
  }

  static void _seedElevation(
    double? altitude,
    List<double> window,
    void Function(double anchor, double peak) commit,
  ) {
    final smoothed = window.isEmpty ? null : window.reduce((a, b) => a + b) / window.length;
    if (smoothed == null) return;
    commit(smoothed, smoothed);
  }
}
