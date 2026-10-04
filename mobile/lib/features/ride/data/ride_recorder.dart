import 'dart:async';

import 'package:drift/drift.dart';
import 'package:uuid/uuid.dart';

import '../../../core/network/api_client.dart';
import '../domain/gps_processor.dart';
import '../domain/location_source.dart';
import 'ride_database.dart';
import 'rides_api.dart';

/// Recording lifecycle. `syncing`/`syncFailed` are local-only overlay states
/// (server enum excludes them — sync protocol doc).
///
/// `finalizing` means the rider asked to finish and every remaining point is
/// being drained; `pendingSync` means the drain could not complete (offline,
/// failure, crash) and the ride must be resumed by recovery, never treated as
/// done. Both are persisted in `LocalRides.status` before any network call so
/// a kill between steps leaves a recoverable row, not a silent gap.
enum RecordingPhase {
  recording,
  paused,
  finalizing,
  pendingSync,
  completed,
  discarded,
}

/// One chunk-upload step outcome for [RideRecorder._uploadNextChunk].
enum _ChunkResult { done, uploaded, failed }

/// Live snapshot for the recording UI. Emitted at most once per second
/// (never rebuilt per GPS point — performance §41).
class LiveRide {
  final String localId;
  final String? serverId;
  final String bikeId;
  final String bikeName;
  final RecordingPhase phase;
  final RideMetrics metrics;
  final double? currentSpeedMS;
  final String gpsQuality; // good | degraded | none
  final String
  syncState; // idle | syncing | failed | offline | finalizing | pending

  const LiveRide({
    required this.localId,
    this.serverId,
    required this.bikeId,
    required this.bikeName,
    required this.phase,
    required this.metrics,
    this.currentSpeedMS,
    required this.gpsQuality,
    required this.syncState,
  });
}

/// Local-first recorder: Drift is source of truth while recording, backend
/// is synced in idempotent chunks. Network loss never stops recording.
class RideRecorder {
  final RideDatabase db;
  final RidesApi rides;
  final LocationSource location;
  final TrackingPolicy policy;

  final _updates = StreamController<LiveRide>.broadcast();
  Stream<LiveRide> get updates => _updates.stream;

  StreamSubscription<GpsObservation>? _sub;
  GpsEngine _engine = GpsEngine();
  String? _localId;
  String? _serverId;
  String _bikeId = '';
  String _bikeName = '';
  String? _routeId;
  RecordingPhase _phase = RecordingPhase.recording;
  int _seq = 0;
  int _lastEmitMs = 0;
  double? _currentSpeed;
  String _gpsQuality = 'none';
  String _syncState = 'idle';
  final List<LocalPointsCompanion> _buffer = [];
  Timer? _flushTimer;
  Timer? _syncTimer;
  bool _syncing = false;

  RideRecorder({
    required this.db,
    required this.rides,
    required this.location,
    this.policy = TrackingPolicy.precise,
  });

  int _nextSeq() => _seq++;

  Future<LocationPermissionState> start({
    required String bikeId,
    required String bikeName,
    String? resumeLocalId,
    String? routeId,
  }) async {
    final perm = await location.ensurePrecisePermission();
    if (perm != LocationPermissionState.granted) return perm;
    _bikeId = bikeId;
    _bikeName = bikeName;
    _engine = GpsEngine();
    _seq = 0;
    if (resumeLocalId != null) {
      _localId = resumeLocalId;
      await _rehydrate(resumeLocalId);
    } else {
      _localId = const Uuid().v4();
      _routeId = routeId;
      await db
          .into(db.localRides)
          .insert(
            LocalRidesCompanion.insert(
              id: _localId!,
              bikeId: bikeId,
              routeId: Value(routeId),
              startedAt: DateTime.now(),
              updatedAt: DateTime.now(),
            ),
          );
      // Idempotent server create (same client uuid on retry/resume).
      try {
        final created = await rides.createRide(
          bikeId: bikeId,
          clientRideUuid: _localId!,
          routeId: routeId,
        );
        _serverId = '${created['id']}';
        await (db.update(db.localRides)..where((r) => r.id.equals(_localId!)))
            .write(LocalRidesCompanion(serverId: Value(_serverId)));
      } on ApiException {
        _syncState = 'offline'; // created locally; synced later
      }
    }
    _phase = RecordingPhase.recording;
    _sub = location
        .positions(policy: policy, nextSeq: _nextSeq)
        .listen(_onPosition);
    _flushTimer = Timer.periodic(
      const Duration(seconds: 5),
      (_) => _flushBuffer(),
    );
    _syncTimer = Timer.periodic(const Duration(seconds: 15), (_) => syncNow());
    _emit(force: true);
    return LocationPermissionState.granted;
  }

  Future<void> _rehydrate(String localId) async {
    final row = await (db.select(
      db.localRides,
    )..where((r) => r.id.equals(localId))).getSingle();
    _serverId = row.serverId;
    _bikeId = row.bikeId;
    _routeId = row.routeId;
    final pts =
        await (db.select(db.localPoints)
              ..where((p) => p.rideId.equals(localId))
              ..orderBy([(p) => OrderingTerm.asc(p.seq)]))
            .get();
    var maxSeq = -1;
    for (final p in pts) {
      if (p.seq > maxSeq) maxSeq = p.seq;
      if (p.accepted) {
        _engine.process(
          GpsObservation(
            seq: p.seq,
            lat: p.lat,
            lon: p.lon,
            time: p.recordedAt,
            alt: p.alt,
            accuracy: p.accuracy,
            speed: p.speed,
            heading: p.heading,
          ),
        );
      }
    }
    _seq = maxSeq + 1;
  }

  Future<void> _onPosition(GpsObservation o) async {
    if (_phase != RecordingPhase.recording) return;
    final verdict = _engine.process(o);
    _gpsQuality = verdict.accepted
        ? 'good'
        : (verdict.reason == 'bad_accuracy' ? 'degraded' : 'good');
    if (verdict.accepted && verdict.moving) {
      _currentSpeed = verdict.dtS > 0 ? verdict.distanceM / verdict.dtS : 0;
    }
    _buffer.add(
      LocalPointsCompanion.insert(
        rideId: _localId!,
        pointUuid: const Uuid().v4(),
        seq: o.seq,
        lat: o.lat,
        lon: o.lon,
        alt: Value(o.alt),
        accuracy: Value(o.accuracy),
        speed: Value(o.speed),
        heading: Value(o.heading),
        recordedAt: o.time,
        accepted: Value(verdict.accepted),
        reason: Value(verdict.accepted ? null : verdict.reason),
      ),
    );
    if (_buffer.length >= 20) await _flushBuffer();
    _emit();
  }

  Future<void> _flushBuffer() async {
    if (_buffer.isEmpty) return;
    // Durability first: the batch stays in memory until the write succeeds, so
    // a kill between the write and the clear loses nothing. Only the written
    // prefix is dropped, preserving points that arrived mid-flush.
    final batch = List<LocalPointsCompanion>.of(_buffer);
    await db.batch((b) {
      b.insertAll(db.localPoints, batch);
    });
    if (_buffer.length >= batch.length) {
      _buffer.removeRange(0, batch.length);
    } else {
      _buffer.clear();
    }
    await _touch();
  }

  Future<void> _touch() async {
    if (_localId == null) return;
    await (db.update(db.localRides)..where((r) => r.id.equals(_localId!)))
        .write(LocalRidesCompanion(updatedAt: Value(DateTime.now())));
  }

  void _emit({bool force = false}) {
    if (_localId == null) return;
    final now = DateTime.now().millisecondsSinceEpoch;
    if (!force && now - _lastEmitMs < 1000) return; // ≤1 Hz UI updates
    _lastEmitMs = now;
    _updates.add(
      LiveRide(
        localId: _localId!,
        serverId: _serverId,
        bikeId: _bikeId,
        bikeName: _bikeName,
        phase: _phase,
        metrics: _engine.metrics(),
        currentSpeedMS: _currentSpeed,
        gpsQuality: _gpsQuality,
        syncState: _syncState,
      ),
    );
  }

  Future<void> pause() async {
    _phase = RecordingPhase.paused;
    await _flushBuffer();
    await _setStatus('paused');
    _emit(force: true);
  }

  Future<void> resume() async {
    _phase = RecordingPhase.recording;
    _engine.resetSegment(); // pause gap never becomes distance
    await _setStatus('recording');
    _emit(force: true);
  }

  Future<Map<String, dynamic>?> finish() async {
    // Persist everything still in memory, then record the intent to finish
    // BEFORE any network call. A kill from this point on leaves a `finalizing`
    // row that recovery can drain; it never leaves a `completed` row with
    // un-uploaded points.
    await _flushBuffer();
    _phase = RecordingPhase.finalizing;
    _syncState = 'finalizing';
    await _setStatus('finalizing');
    _emit(force: true);
    await _sub?.cancel();
    await _cancelTimers();
    try {
      final summary = await drainAndFinalize();
      if (summary == null) {
        // Drain incomplete (offline or failure): stay recoverable.
        _phase = RecordingPhase.pendingSync;
        _syncState = 'pending';
        await _setStatus('pending_sync');
        _emit(force: true);
        return null;
      }
      _phase = RecordingPhase.completed;
      _syncState = 'idle';
      await _setStatus('completed');
      _emit(force: true);
      return summary;
    } on ApiException {
      _phase = RecordingPhase.pendingSync;
      _syncState = 'failed';
      await _setStatus('pending_sync');
      _emit(force: true);
      return null;
    }
  }

  Future<void> discard() async {
    await _cancelTimers();
    try {
      if (_serverId != null) {
        await rides.transition(_serverId!, 'discard');
      }
    } on ApiException {
      // Local discard still proceeds; server row stays recording until
      // next sync — documented, never blocks the user.
    }
    _phase = RecordingPhase.discarded;
    await _setStatus('discarded');
    await _sub?.cancel();
  }

  Future<void> _setStatus(String status) async {
    if (_localId == null) return;
    await (db.update(
      db.localRides,
    )..where((r) => r.id.equals(_localId!))).write(
      LocalRidesCompanion(
        status: Value(status),
        updatedAt: Value(DateTime.now()),
      ),
    );
  }

  Future<void> _cancelTimers() async {
    _flushTimer?.cancel();
    _syncTimer?.cancel();
  }

  /// Chunked idempotent upload with resume. Safe to call anytime;
  /// no-op while offline (ApiException → syncState, no data loss).
  ///
  /// Uploads at most one chunk per call: the periodic timer stays cheap while
  /// recording. [drainAndFinalize] loops this until the ride is fully synced.
  Future<void> syncNow() async {
    if (_syncing || _localId == null) return;
    _syncing = true;
    try {
      _syncState = 'syncing';
      final result = await _uploadNextChunk();
      _syncState = switch (result) {
        _ChunkResult.done || _ChunkResult.uploaded => 'idle',
        _ChunkResult.failed => _serverId == null ? 'offline' : 'failed',
      };
    } on ApiException {
      _syncState = 'failed';
    } finally {
      _syncing = false;
      _emit(force: true);
    }
  }

  /// One chunk upload step. Returns whether more work remains.
  ///
  /// Progress is the watermark `uploadedSeq`, which advances only after a
  /// successful chunk response — never ahead of acknowledged data.
  Future<_ChunkResult> _uploadNextChunk() async {
    if (_localId == null) return _ChunkResult.failed;
    _serverId ??= await _ensureServerRide();
    if (_serverId == null) {
      return _ChunkResult.failed;
    }
    final local = await (db.select(
      db.localRides,
    )..where((r) => r.id.equals(_localId!))).getSingle();
    final pending =
        await (db.select(db.localPoints)
              ..where(
                (p) =>
                    p.rideId.equals(_localId!) &
                    p.seq.isBiggerThanValue(local.uploadedSeq),
              )
              ..orderBy([(p) => OrderingTerm.asc(p.seq)])
              ..limit(RidesApi.chunkSize))
            .get();
    if (pending.isEmpty) {
      return _ChunkResult.done;
    }
    try {
      await rides.uploadChunk(
        rideId: _serverId!,
        points: [
          for (final p in pending)
            {
              'client_point_uuid': p.pointUuid,
              'seq': p.seq,
              'lat': p.lat,
              'lon': p.lon,
              'recorded_at': p.recordedAt.toUtc().toIso8601String(),
              if (p.alt != null) 'alt': p.alt,
              if (p.accuracy != null) 'accuracy': p.accuracy,
              if (p.speed != null) 'speed': p.speed,
              if (p.heading != null) 'heading': p.heading,
            },
        ],
      );
    } on ApiException {
      return _ChunkResult.failed;
    }
    await db.markUploaded(_localId!, pending.last.seq);
    return _ChunkResult.uploaded;
  }

  /// Drain every pending chunk, then finalize the server ride.
  ///
  /// Returns the server summary on success, or null when the drain could not
  /// complete (offline or failure) — in which case the local row is left in a
  /// recoverable state and nothing is marked completed. Uploads are idempotent
  /// (stable `client_point_uuid` + `seq`), so a retried drain never duplicates.
  Future<Map<String, dynamic>?> drainAndFinalize() async {
    if (_localId == null) return null;
    while (true) {
      final result = await _uploadNextChunk();
      if (result == _ChunkResult.done) break;
      if (result == _ChunkResult.failed) return null;
    }
    // Every local point is acknowledged. Finalize the server ride. If a crash
    // happened between a previous finalize and the local write, the server is
    // already completed and the transition is rejected — in that case confirm
    // via detail instead of failing.
    try {
      return await rides.transition(_serverId!, 'finish');
    } on ApiException catch (e) {
      if (e.code == 'INVALID_TRANSITION') {
        final detail = await rides.detail(_serverId!);
        if (detail['status'] == 'completed') return detail;
      }
      rethrow;
    }
  }

  /// Attach to an existing local ride for headless sync/finalize, without
  /// starting location tracking. Used by crash/restart recovery.
  Future<bool> attachForSync(String localId) async {
    final row = await (db.select(
      db.localRides,
    )..where((r) => r.id.equals(localId))).getSingleOrNull();
    if (row == null) return false;
    _localId = row.id;
    _serverId = row.serverId;
    _bikeId = row.bikeId;
    _routeId = row.routeId;
    _phase = RecordingPhase.finalizing;
    _syncState = 'syncing';
    return true;
  }

  Future<String?> _ensureServerRide() async {
    try {
      final created = await rides.createRide(
        bikeId: _bikeId,
        clientRideUuid: _localId!,
        routeId: _routeId,
      );
      final id = '${created['id']}';
      await (db.update(db.localRides)..where((r) => r.id.equals(_localId!)))
          .write(LocalRidesCompanion(serverId: Value(id)));
      return id;
    } on ApiException {
      return null;
    }
  }

  Future<void> dispose() async {
    await _flushBuffer();
    await _cancelTimers();
    await _sub?.cancel();
    await _updates.close();
  }
}
