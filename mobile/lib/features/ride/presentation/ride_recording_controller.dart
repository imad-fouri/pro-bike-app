import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:latlong2/latlong.dart';
import 'package:uuid/uuid.dart';

import '../data/ride_providers.dart';
import '../domain/gps_processor.dart' hide RideMetrics;
import '../domain/location_source.dart';
import '../domain/ride.dart';
import '../domain/ride_metrics.dart';
import '../domain/ride_point.dart';

import '../domain/ride_recording_state.dart';
import '../domain/ride_repository.dart';

final rideRecordingControllerProvider =
    NotifierProvider<RideRecordingController, RideRecordingState>(
        RideRecordingController.new);

class RideRecordingController extends Notifier<RideRecordingState> {
  StreamSubscription<GpsObservation>? _sub;
  Timer? _tick;

  RideRepository get _repo => ref.read(rideRepositoryProvider);

  @override
  RideRecordingState build() {
    ref.onDispose(() {
      _sub?.cancel();
      _tick?.cancel();
    });
    return const RideRecordingState(
      status: RideRecordingStatus.idle,
      metrics: RideMetrics.zero(),
      pointCount: 0,
      lastLocation: null,
      lastAccuracyMeters: null,
      startedAt: null,
      rideId: null,
      autoPaused: false,
      staleFix: false,
      errorMessage: null,
    );
  }



  Future<String?> start() async {
    final rideId = const Uuid().v4();
    final now = DateTime.now().toUtc();
    state = state.copyWith(
      status: RideRecordingStatus.recording,
      rideId: rideId,
      startedAt: now,
      metrics: const RideMetrics.zero(),
      pointCount: 0,
      lastLocation: null,
      lastAccuracyMeters: null,
      autoPaused: false,
      staleFix: false,
      errorMessage: null,
    );
    await _startStream();
    _tick?.cancel();
    _tick = Timer.periodic(const Duration(seconds: 1), (_) => _tickLoop());
    return rideId;
  }

  Future<void> pause() async {
    if (state.status != RideRecordingStatus.recording) return;
    _sub?.pause();
    state = state.copyWith(status: RideRecordingStatus.paused);
  }

  Future<void> resume() async {
    if (state.status != RideRecordingStatus.paused) return;
    _sub?.resume();
    state = state.copyWith(status: RideRecordingStatus.recording);
  }

  Future<String?> stop() async {
    if (state.status != RideRecordingStatus.recording &&
        state.status != RideRecordingStatus.paused) {
      return state.rideId;
    }
    await _sub?.cancel();
    _sub = null;
    _tick?.cancel();
    _tick = null;
    final endedAt = DateTime.now().toUtc();
    final rideId = state.rideId ?? const Uuid().v4();
    final points = _buffer
        .map((p) => RidePoint(
              lat: p.lat,
              lng: p.lon,
              altitude: p.alt,
              speedMps: p.speed,
              accuracyMeters: p.accuracy,
              timestamp: p.time,
            ))
        .toList();
    final metrics = RideMetricsCalculator.compute(points);
    final ride = Ride(
      id: rideId,
      bikeId: null,
      startedAt: state.startedAt ?? endedAt,
      endedAt: endedAt,
      distanceMeters: metrics.distanceMeters,
      movingSeconds: metrics.movingSeconds,
      durationSeconds: metrics.durationSeconds,
      avgSpeedMps: metrics.avgSpeedMps,
      maxSpeedMps: metrics.maxSpeedMps,
      elevationGainMeters: metrics.elevationGainMeters,
    );
    state = state.copyWith(status: RideRecordingStatus.saving);
    await _repo.saveRide(ride, points);
    state = state.copyWith(status: RideRecordingStatus.saved, rideId: rideId);
    return rideId;
  }

  Future<void> discard() async {
    await _sub?.cancel();
    _sub = null;
    _tick?.cancel();
    _tick = null;
    _buffer.clear();
    state = const RideRecordingState(
      status: RideRecordingStatus.idle,
      metrics: RideMetrics.zero(),
      pointCount: 0,
    );
  }

  void clearError() {
    state = state.copyWith(clearErrorMessage: true);
  }

  final _buffer = <GpsObservation>[];

  Future<void> _startStream() async {
    await _sub?.cancel();
    _buffer.clear();
    final location = ref.read(locationServiceProvider);
    final policy = TrackingPolicy.precise;
    _sub = location
        .positions(
          policy: policy,
          nextSeq: () => _buffer.length,
        )
        .listen((obs) {
      _buffer.add(obs);
      state = state.copyWith(
        pointCount: _buffer.length,
        lastLocation: LatLng(obs.lat, obs.lon),
        lastAccuracyMeters: obs.accuracy,
        status: state.status == RideRecordingStatus.paused
            ? RideRecordingStatus.paused
            : RideRecordingStatus.recording,
      );
    }, onError: (e) {
      state = state.copyWith(
          status: RideRecordingStatus.error, errorMessage: e.toString());
    });
  }

  void _tickLoop() {
    // Auto-pause logic: if no new points in last grace period while recording
    if (state.status != RideRecordingStatus.recording) return;
    final now = DateTime.now().toUtc();
    final lastTs = _buffer.isNotEmpty ? _buffer.last.time : state.startedAt;
    if (lastTs == null) return;
    final since = now.difference(lastTs).inSeconds;
    if (since > 5) {
      state = state.copyWith(autoPaused: true);
      _sub?.pause();
      state = state.copyWith(status: RideRecordingStatus.paused);
    }
  }
}
