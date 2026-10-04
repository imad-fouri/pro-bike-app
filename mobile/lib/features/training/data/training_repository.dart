import '../../../core/network/api_client.dart';
import '../domain/training_models.dart';

/// Real FastAPI backend via the shared [ApiClient]. No mock data.
///
/// The client never sends a test FTP value: a test protocol posts only its
/// `ride_id` and the server derives the number from that ride's own samples
/// (ADR-10 §4).
class TrainingRepository {
  final ApiClient api;
  static const prefix = '/api/v1/training';
  static const workoutsPrefix = '/api/v1/workouts';

  const TrainingRepository(this.api);

  Future<TrainingProfile> profile() async {
    final body = await api.get('$prefix/profile', auth: true);
    return TrainingProfile.fromJson(body);
  }

  /// Partial update. [ftpW] writes an append-only FTP record rather than
  /// overwriting the current one; that is why it is not a plain field set.
  Future<TrainingProfile> updateProfile({
    double? ftpW,
    FtpSource? ftpSource,
    DateTime? effectiveAt,
    double? maxHrBpm,
    double? restingHrBpm,
    HrZoneModel? hrZoneModel,
    String? timezone,
  }) async {
    final body = await api.put('$prefix/profile', {
      'ftp_w': ?ftpW,
      'ftp_source': ?ftpSource?.wire,
      'effective_at': ?effectiveAt?.toIso8601String().substring(0, 10),
      'max_hr_bpm': ?maxHrBpm,
      'resting_hr_bpm': ?restingHrBpm,
      'hr_zone_model': ?hrZoneModel?.wire,
      'timezone': ?(timezone != null && timezone.isNotEmpty ? timezone : null),
    }, auth: true);
    return TrainingProfile.fromJson(body);
  }

  Future<FtpHistory> ftpRecords() async {
    final body = await api.get('$prefix/ftp-records', auth: true);
    return FtpHistory.fromJson(body);
  }

  /// A test protocol must be identified by [rideId]; [valueW] is ignored
  /// server-side for `test_20min`/`test_ramp` on purpose.
  Future<FtpRecord> addFtpRecord({
    required FtpSource source,
    double? valueW,
    DateTime? effectiveAt,
    String? rideId,
  }) async {
    // A test-derived FTP is computed server-side from the ride, so the client
    // never transmits an asserted value for those sources. Sending one would
    // let a fabricated number compete with the ride-derived result.
    final transmittedValue = source.isTestDerived ? null : valueW;
    final body = await api.post('$prefix/ftp-records', {
      'source': source.wire,
      'value_w': ?transmittedValue,
      'effective_at': ?effectiveAt?.toIso8601String().substring(0, 10),
      'ride_id': ?rideId,
    }, auth: true);
    return FtpRecord.fromJson(body);
  }

  Future<({List<TrainingActivity> items, int total})> activities({
    int page = 1,
    int pageSize = 20,
  }) async {
    final body = await api.get(
      '$prefix/activities?page=$page&page_size=$pageSize',
      auth: true,
    );
    return (
      items: [
        for (final e in (body['items'] as List? ?? const []))
          TrainingActivity.fromJson(e as Map<String, dynamic>),
      ],
      total: (body['total'] as num? ?? 0).toInt(),
    );
  }

  Future<TrainingActivity> activity(String id) async {
    final body = await api.get('$prefix/activities/$id', auth: true);
    return TrainingActivity.fromJson(body);
  }

  /// Re-derive a ride's metrics against the FTP that is current now. Stored
  /// history keeps the FTP it was computed with until this is called.
  Future<TrainingActivity> reanalyze(String rideId) async {
    final body = await api.post(
      '$prefix/activities/$rideId/reanalyze',
      {},
      auth: true,
    );
    return TrainingActivity.fromJson(body);
  }

  Future<({List<TrainingLoad> items, String version})> loads({
    DateTime? start,
    DateTime? end,
  }) async {
    final q = <String>[
      if (start != null) 'start=${_date(start)}',
      if (end != null) 'end=${_date(end)}',
    ].join('&');
    final body = await api.get(
      '$prefix/loads${q.isEmpty ? '' : '?$q'}',
      auth: true,
    );
    return (
      items: [
        for (final e in (body['items'] as List? ?? const []))
          TrainingLoad.fromJson(e as Map<String, dynamic>),
      ],
      version: '${body['version']}',
    );
  }

  Future<RecoverySignals> recovery() async {
    final body = await api.get('$prefix/recovery', auth: true);
    return RecoverySignals.fromJson(body);
  }

  /// One round trip for the whole dashboard.
  Future<TrainingSummary> summary() async {
    final body = await api.get('$prefix/summary', auth: true);
    return TrainingSummary.fromJson(body);
  }

  Future<List<CalculationVersion>> calculationVersions() async {
    final body = await api.get('$prefix/calculation-versions', auth: true);
    return [
      for (final e in (body['items'] as List? ?? const []))
        CalculationVersion.fromJson(e as Map<String, dynamic>),
    ];
  }

  Future<Workout> createWorkout(WorkoutInput input) async {
    final body = await api.post(workoutsPrefix, input.toJson(), auth: true);
    return Workout.fromJson(body);
  }

  Future<({List<Workout> items, int total})> listWorkouts({
    int page = 1,
    int pageSize = 20,
    WorkoutStatus? status,
  }) async {
    final q = <String>[
      'page=$page',
      'page_size=$pageSize',
      if (status != null) 'status=${status.wire}',
    ].join('&');
    final body = await api.get('$workoutsPrefix?$q', auth: true);
    return (
      items: [
        for (final e in (body['items'] as List? ?? const []))
          Workout.fromJson(e as Map<String, dynamic>),
      ],
      total: (body['total'] as num? ?? 0).toInt(),
    );
  }

  Future<Workout> workout(String id) async {
    final body = await api.get('$workoutsPrefix/$id', auth: true);
    return Workout.fromJson(body);
  }

  /// [expectedVersion] is mandatory: a stale write returns 409 and the caller
  /// must reload rather than overwrite.
  Future<Workout> updateWorkout(
    String id, {
    required int expectedVersion,
    WorkoutInput? input,
    WorkoutStatus? status,
  }) async {
    final body = await api.patch('$workoutsPrefix/$id', {
      'expected_version': expectedVersion,
      if (input != null) ...input.toJson(),
      if (status != null) 'status': status.wire,
    }, auth: true);
    return Workout.fromJson(body);
  }

  Future<void> deleteWorkout(String id) async {
    await api.delete('$workoutsPrefix/$id', auth: true);
  }

  static String _date(DateTime value) =>
      '${value.year.toString().padLeft(4, '0')}-'
      '${value.month.toString().padLeft(2, '0')}-'
      '${value.day.toString().padLeft(2, '0')}';
}
