/// Training domain entities. Canonical units from the API: watts, bpm,
/// seconds, metres. Presentation converts via [formatPower] etc. (§43).
///
/// Every nullable number stays null here. A null is "we do not know" and must
/// be shown as unavailable, never as 0 — a rider with no power meter has no
/// intensity factor, not a zero one (§32, ADR-10 §2).
library;

import '../../../core/units/api_number.dart';

/// Provenance of an FTP value. Never presented without its basis.
enum FtpBasis {
  measured,
  estimated,
  unavailable;

  static FtpBasis parse(String? raw) => switch (raw) {
    'measured' => FtpBasis.measured,
    'estimated' => FtpBasis.estimated,
    _ => FtpBasis.unavailable,
  };

  bool get hasValue => this != FtpBasis.unavailable;
}

enum FtpSource {
  manual,
  test20min,
  testRamp,
  imported,
  estimated;

  static FtpSource parse(String? raw) => switch (raw) {
    'manual' => FtpSource.manual,
    'test_20min' => FtpSource.test20min,
    'test_ramp' => FtpSource.testRamp,
    'imported' => FtpSource.imported,
    _ => FtpSource.estimated,
  };

  String get wire => switch (this) {
    FtpSource.manual => 'manual',
    FtpSource.test20min => 'test_20min',
    FtpSource.testRamp => 'test_ramp',
    FtpSource.imported => 'imported',
    FtpSource.estimated => 'estimated',
  };

  String get l10nKey => 'training.ftpSource.$wire';

  /// Test sources are measured from a ride by the server, so the client has no
  /// value to send for them.
  bool get isTestDerived =>
      this == FtpSource.test20min || this == FtpSource.testRamp;
}

enum HrZoneModel {
  auto,
  hrMax,
  hrr;

  static HrZoneModel parse(String? raw) => switch (raw) {
    'hr_max' => HrZoneModel.hrMax,
    'hrr' => HrZoneModel.hrr,
    _ => HrZoneModel.auto,
  };

  String? get wire => switch (this) {
    HrZoneModel.auto => null,
    HrZoneModel.hrMax => 'hr_max',
    HrZoneModel.hrr => 'hrr',
  };
}

/// The rider's training inputs. [ftpW] is a cache of the resolved record; the
/// append-only history lives in [FtpRecord].
class TrainingProfile {
  final String userId;
  final double? ftpW;
  final FtpSource? ftpSource;
  final FtpBasis ftpBasis;
  final double? maxHrBpm;
  final double? restingHrBpm;
  final HrZoneModel hrZoneModel;
  final String? timezone;
  final String effectiveTimezone;

  const TrainingProfile({
    required this.userId,
    this.ftpW,
    this.ftpSource,
    this.ftpBasis = FtpBasis.unavailable,
    this.maxHrBpm,
    this.restingHrBpm,
    this.hrZoneModel = HrZoneModel.auto,
    this.timezone,
    required this.effectiveTimezone,
  });

  bool get hasFtp => ftpW != null && ftpBasis.hasValue;
  bool get hasHrThresholds => maxHrBpm != null;
  bool get canZoneByHrr => maxHrBpm != null && restingHrBpm != null;

  factory TrainingProfile.fromJson(Map<String, dynamic> json) =>
      TrainingProfile(
        userId: '${json['user_id']}',
        ftpW: apiDouble(json['ftp_w']),
        ftpSource: json['ftp_source'] == null
            ? null
            : FtpSource.parse('${json['ftp_source']}'),
        ftpBasis: FtpBasis.parse(json['ftp_basis'] as String?),
        maxHrBpm: apiDouble(json['max_hr_bpm']),
        restingHrBpm: apiDouble(json['resting_hr_bpm']),
        hrZoneModel: HrZoneModel.parse(json['hr_zone_model'] as String?),
        timezone: json['timezone'] as String?,
        effectiveTimezone: '${json['effective_timezone'] ?? 'UTC'}',
      );

  Map<String, dynamic> toJson() => {
    'user_id': userId,
    'ftp_w': ftpW,
    'ftp_source': ftpSource?.wire,
    'ftp_basis': ftpBasis.name,
    'max_hr_bpm': maxHrBpm,
    'resting_hr_bpm': restingHrBpm,
    'hr_zone_model': hrZoneModel.wire,
    'timezone': timezone,
    'effective_timezone': effectiveTimezone,
  };
}

/// One immutable FTP measurement. Append-only: nothing is ever edited.
class FtpRecord {
  final String id;
  final FtpSource source;
  final double valueW;
  final DateTime effectiveAt;
  final bool confirmed;
  final bool approximation;
  final Map<String, dynamic>? evidence;
  final DateTime createdAt;

  const FtpRecord({
    required this.id,
    required this.source,
    required this.valueW,
    required this.effectiveAt,
    required this.confirmed,
    this.approximation = false,
    this.evidence,
    required this.createdAt,
  });

  String get l10nKey => 'training.ftpSource.${source.wire}';

  factory FtpRecord.fromJson(Map<String, dynamic> json) => FtpRecord(
    id: '${json['id']}',
    source: FtpSource.parse(json['source'] as String?),
    valueW: apiDoubleOr(json['value_w'], 0),
    effectiveAt: DateTime.parse('${json['effective_at']}'),
    confirmed: json['confirmed'] as bool? ?? false,
    approximation: json['approximation'] as bool? ?? false,
    evidence: json['evidence'] as Map<String, dynamic>?,
    createdAt: DateTime.parse('${json['created_at']}'),
  );

  Map<String, dynamic> toJson() => {
    'id': id,
    'source': source.wire,
    'value_w': valueW,
    'effective_at': effectiveAt.toIso8601String(),
    'confirmed': confirmed,
    'approximation': approximation,
    'evidence': evidence,
    'created_at': createdAt.toIso8601String(),
  };
}

/// FTP history plus the resolved value the API considers effective.
class FtpHistory {
  final List<FtpRecord> items;
  final double? effectiveFtpW;
  final FtpSource? effectiveSource;
  final FtpBasis effectiveBasis;

  const FtpHistory({
    required this.items,
    this.effectiveFtpW,
    this.effectiveSource,
    this.effectiveBasis = FtpBasis.unavailable,
  });

  factory FtpHistory.fromJson(Map<String, dynamic> json) => FtpHistory(
    items: [
      for (final e in (json['items'] as List? ?? const []))
        FtpRecord.fromJson(e as Map<String, dynamic>),
    ],
    effectiveFtpW: apiDouble(json['effective_ftp_w']),
    effectiveSource: json['effective_source'] == null
        ? null
        : FtpSource.parse('${json['effective_source']}'),
    effectiveBasis: FtpBasis.parse(json['effective_basis'] as String?),
  );
}

/// Seconds spent in one zone for one metric.
class ZoneSeconds {
  final String kind; // 'power' | 'hr'
  final int zone;
  final double seconds;

  const ZoneSeconds({
    required this.kind,
    required this.zone,
    required this.seconds,
  });

  factory ZoneSeconds.fromJson(Map<String, dynamic> json) => ZoneSeconds(
    kind: '${json['kind']}',
    zone: apiIntOr(json['zone'], 0),
    seconds: apiDoubleOr(json['seconds'], 0),
  );
}

/// A derived ride analysis. Metrics are DERIVED; the FTP they were computed
/// with is stored so history is never silently restated (§32).
class TrainingActivity {
  final String id;
  final String? rideId;
  final DateTime localDate;
  final DateTime startedAt;
  final DateTime? endedAt;
  final int elapsedSeconds;
  final int movingSeconds;
  final double? distanceM;
  final double? elevationGainM;
  final String analysisVersion;
  final bool insufficientData;
  final bool hasPower;
  final bool hasHeartRate;
  final bool hasCadence;
  final double analyzedSeconds;
  final double powerSeconds;
  final double hrSeconds;
  final double npSeconds;
  final double? averagePowerW;
  final double? maxPowerW;
  final double? normalizedPowerW;
  final double? intensityFactor;
  final double? powerLoad;
  final double? averageHrBpm;
  final double? maxHrBpm;
  final double? averageCadenceRpm;
  final double? hrLoad;
  final String? hrZoneModel;
  final double? effectiveFtpW;
  final FtpSource? ftpSource;
  final FtpBasis ftpBasis;
  final List<ZoneSeconds> zones;

  const TrainingActivity({
    required this.id,
    this.rideId,
    required this.localDate,
    required this.startedAt,
    this.endedAt,
    required this.elapsedSeconds,
    required this.movingSeconds,
    this.distanceM,
    this.elevationGainM,
    required this.analysisVersion,
    required this.insufficientData,
    required this.hasPower,
    required this.hasHeartRate,
    required this.hasCadence,
    required this.analyzedSeconds,
    required this.powerSeconds,
    required this.hrSeconds,
    required this.npSeconds,
    this.averagePowerW,
    this.maxPowerW,
    this.normalizedPowerW,
    this.intensityFactor,
    this.powerLoad,
    this.averageHrBpm,
    this.maxHrBpm,
    this.averageCadenceRpm,
    this.hrLoad,
    this.hrZoneModel,
    this.effectiveFtpW,
    this.ftpSource,
    this.ftpBasis = FtpBasis.unavailable,
    this.zones = const [],
  });

  List<ZoneSeconds> get powerZones =>
      zones.where((z) => z.kind == 'power').toList();
  List<ZoneSeconds> get hrZones => zones.where((z) => z.kind == 'hr').toList();

  /// True when the ride carried no sensor channels at all. The UI must say so
  /// rather than rendering a table of zeros.
  bool get hasNoSensors => !hasPower && !hasHeartRate;

  factory TrainingActivity.fromJson(Map<String, dynamic> json) =>
      TrainingActivity(
        id: '${json['id']}',
        rideId: json['ride_id'] as String?,
        localDate: DateTime.parse('${json['local_date']}'),
        startedAt: DateTime.parse('${json['started_at']}'),
        endedAt: json['ended_at'] == null
            ? null
            : DateTime.parse('${json['ended_at']}'),
        elapsedSeconds: apiIntOr(json['elapsed_seconds'], 0),
        movingSeconds: apiIntOr(json['moving_seconds'], 0),
        distanceM: apiDouble(json['distance_m']),
        elevationGainM: apiDouble(json['elevation_gain_m']),
        analysisVersion: '${json['analysis_version']}',
        insufficientData: json['insufficient_data'] as bool? ?? false,
        hasPower: json['has_power'] as bool? ?? false,
        hasHeartRate: json['has_heart_rate'] as bool? ?? false,
        hasCadence: json['has_cadence'] as bool? ?? false,
        analyzedSeconds: apiDoubleOr(json['analyzed_seconds'], 0),
        powerSeconds: apiDoubleOr(json['power_seconds'], 0),
        hrSeconds: apiDoubleOr(json['hr_seconds'], 0),
        npSeconds: apiDoubleOr(json['np_seconds'], 0),
        averagePowerW: apiDouble(json['average_power_w']),
        maxPowerW: apiDouble(json['max_power_w']),
        normalizedPowerW: apiDouble(json['normalized_power_w']),
        intensityFactor: apiDouble(json['intensity_factor']),
        powerLoad: apiDouble(json['power_load']),
        averageHrBpm: apiDouble(json['average_hr_bpm']),
        maxHrBpm: apiDouble(json['max_hr_bpm']),
        averageCadenceRpm: apiDouble(json['average_cadence_rpm']),
        hrLoad: apiDouble(json['hr_load']),
        hrZoneModel: json['hr_zone_model'] as String?,
        effectiveFtpW: apiDouble(json['effective_ftp_w']),
        ftpSource: json['ftp_source'] == null
            ? null
            : FtpSource.parse('${json['ftp_source']}'),
        ftpBasis: FtpBasis.parse(json['ftp_basis'] as String?),
        zones: [
          for (final e in (json['zones'] as List? ?? const []))
            ZoneSeconds.fromJson(e as Map<String, dynamic>),
        ],
      );
}

/// One day's aggregated load. powerLoad/hrLoad are null when that channel has
/// never been recorded — an all-time power load of 0 would be a real claim.
class TrainingLoad {
  final DateTime localDate;
  final double? powerLoad;
  final double? hrLoad;
  final double ctl;
  final double atl;
  final double tsb;
  final String loadVersion;

  const TrainingLoad({
    required this.localDate,
    this.powerLoad,
    this.hrLoad,
    required this.ctl,
    required this.atl,
    required this.tsb,
    required this.loadVersion,
  });

  factory TrainingLoad.fromJson(Map<String, dynamic> json) => TrainingLoad(
    localDate: DateTime.parse('${json['local_date']}'),
    powerLoad: apiDouble(json['power_load']),
    hrLoad: apiDouble(json['hr_load']),
    ctl: apiDoubleOr(json['ctl'], 0),
    atl: apiDoubleOr(json['atl'], 0),
    tsb: apiDoubleOr(json['tsb'], 0),
    loadVersion: '${json['load_version']}',
  );
}

/// A load-change observation with its evidence. Never a readiness or medical
/// claim: the API only reports what the numbers support (ADR-10 §3).
class RecoverySignals {
  final String version;
  final String status; // 'ok' | 'unavailable'
  final String code;
  final List<String> signals;
  final Map<String, dynamic> evidence;

  const RecoverySignals({
    required this.version,
    required this.status,
    required this.code,
    required this.signals,
    required this.evidence,
  });

  bool get isAvailable => status == 'ok';
  String get l10nKey =>
      'training.recovery.${isAvailable ? 'ok' : 'unavailable'}';

  factory RecoverySignals.fromJson(Map<String, dynamic> json) =>
      RecoverySignals(
        version: '${json['version']}',
        status: '${json['status']}',
        code: '${json['code']}',
        signals: [for (final s in (json['signals'] as List? ?? const [])) '$s'],
        evidence:
            (json['evidence'] as Map?)?.cast<String, dynamic>() ?? const {},
      );
}

/// A bounded next-step suggestion, or an explicit refusal to suggest.
class IntensitySuggestion {
  final String version;
  final String status;
  final double? targetLoad;
  final String reason;
  final Map<String, dynamic> evidence;

  const IntensitySuggestion({
    required this.version,
    required this.status,
    this.targetLoad,
    required this.reason,
    required this.evidence,
  });

  bool get isAvailable => status == 'ok';
  String get l10nKey =>
      'training.suggestion.${isAvailable ? 'ok' : 'unavailable'}';

  factory IntensitySuggestion.fromJson(Map<String, dynamic> json) =>
      IntensitySuggestion(
        version: '${json['version']}',
        status: '${json['status']}',
        targetLoad: apiDouble(json['target_load']),
        reason: '${json['reason']}',
        evidence:
            (json['evidence'] as Map?)?.cast<String, dynamic>() ?? const {},
      );
}

/// Everything the training dashboard needs, in one call.
class TrainingSummary {
  final TrainingProfile profile;
  final List<TrainingActivity> recentActivities;
  final List<TrainingLoad> loads;
  final RecoverySignals recovery;
  final IntensitySuggestion suggestion;
  final double weekPowerLoad;

  const TrainingSummary({
    required this.profile,
    required this.recentActivities,
    required this.loads,
    required this.recovery,
    required this.suggestion,
    required this.weekPowerLoad,
  });

  factory TrainingSummary.fromJson(Map<String, dynamic> json) =>
      TrainingSummary(
        profile: TrainingProfile.fromJson(
          json['profile'] as Map<String, dynamic>,
        ),
        recentActivities: [
          for (final e in (json['recent_activities'] as List? ?? const []))
            TrainingActivity.fromJson(e as Map<String, dynamic>),
        ],
        loads: [
          for (final e in (json['loads'] as List? ?? const []))
            TrainingLoad.fromJson(e as Map<String, dynamic>),
        ],
        recovery: RecoverySignals.fromJson(
          json['recovery'] as Map<String, dynamic>,
        ),
        suggestion: IntensitySuggestion.fromJson(
          json['suggestion'] as Map<String, dynamic>,
        ),
        weekPowerLoad: apiDoubleOr(json['week_power_load'], 0),
      );
}

/// A published calculation version. Surfaced so a number can be traced back
/// to the formula that produced it (§31).
class CalculationVersion {
  final String version;
  final String kind;
  final String title;
  final String summary;
  final Map<String, dynamic> params;
  final bool isActive;

  const CalculationVersion({
    required this.version,
    required this.kind,
    required this.title,
    required this.summary,
    required this.params,
    required this.isActive,
  });

  factory CalculationVersion.fromJson(Map<String, dynamic> json) =>
      CalculationVersion(
        version: '${json['version']}',
        kind: '${json['kind']}',
        title: '${json['title']}',
        summary: '${json['summary']}',
        params: (json['params'] as Map?)?.cast<String, dynamic>() ?? const {},
        isActive: json['is_active'] as bool? ?? true,
      );
}

enum WorkoutStatus {
  draft,
  active,
  archived;

  static WorkoutStatus parse(String? raw) => switch (raw) {
    'active' => WorkoutStatus.active,
    'archived' => WorkoutStatus.archived,
    _ => WorkoutStatus.draft,
  };

  String get wire => name;
  String get l10nKey => 'training.workout.status.$wire';
}

enum WorkoutStepType {
  warmup,
  steady,
  interval,
  recovery,
  cooldown;

  static WorkoutStepType parse(String? raw) => switch (raw) {
    'warmup' => WorkoutStepType.warmup,
    'steady' => WorkoutStepType.steady,
    'interval' => WorkoutStepType.interval,
    'recovery' => WorkoutStepType.recovery,
    _ => WorkoutStepType.cooldown,
  };

  String get wire => name;
  String get l10nKey => 'training.workout.stepType.$wire';
}

/// One interval of a workout. Targets are ranges in canonical units; either
/// half may be null for a one-sided target.
class WorkoutStep {
  final int id;
  final int seq;
  final WorkoutStepType stepType;
  final String label;
  final int durationS;
  final int repeatCount;
  final int? targetZone;
  final double? targetPowerLowW;
  final double? targetPowerHighW;
  final double? targetHrLowBpm;
  final double? targetHrHighBpm;

  const WorkoutStep({
    required this.id,
    required this.seq,
    required this.stepType,
    required this.label,
    required this.durationS,
    this.repeatCount = 1,
    this.targetZone,
    this.targetPowerLowW,
    this.targetPowerHighW,
    this.targetHrLowBpm,
    this.targetHrHighBpm,
  });

  /// Total time this step contributes, expansions included.
  int get totalSeconds => durationS * repeatCount;

  /// A target range needs both ends; one end alone is not a range.
  bool get hasPowerRange => targetPowerLowW != null && targetPowerHighW != null;
  bool get hasHrRange => targetHrLowBpm != null && targetHrHighBpm != null;

  factory WorkoutStep.fromJson(Map<String, dynamic> json) => WorkoutStep(
    id: apiIntOr(json['id'], 0),
    seq: apiIntOr(json['seq'], 0),
    stepType: WorkoutStepType.parse(json['step_type'] as String?),
    label: '${json['label']}',
    durationS: apiIntOr(json['duration_s'], 0),
    repeatCount: apiIntOr(json['repeat_count'], 1),
    targetZone: apiInt(json['target_zone']),
    targetPowerLowW: apiDouble(json['target_power_low_w']),
    targetPowerHighW: apiDouble(json['target_power_high_w']),
    targetHrLowBpm: apiDouble(json['target_hr_low_bpm']),
    targetHrHighBpm: apiDouble(json['target_hr_high_bpm']),
  );
}

/// A training session definition. Carries [version] for optimistic locking —
/// an edit must present the version it read (ADR-10 §5).
class Workout {
  final String id;
  final String name;
  final String? description;
  final String discipline;
  final String? goal;
  final WorkoutStatus status;
  final int version;
  final int? targetDurationS;
  final double? targetLoad;
  final String? intensityNote;
  final List<WorkoutStep> steps;
  final DateTime createdAt;
  final DateTime updatedAt;

  const Workout({
    required this.id,
    required this.name,
    this.description,
    required this.discipline,
    this.goal,
    this.status = WorkoutStatus.draft,
    required this.version,
    this.targetDurationS,
    this.targetLoad,
    this.intensityNote,
    this.steps = const [],
    required this.createdAt,
    required this.updatedAt,
  });

  /// Sum of all step durations including repeats, for a progress bar.
  int get plannedSeconds =>
      steps.fold(0, (sum, step) => sum + step.totalSeconds);

  factory Workout.fromJson(Map<String, dynamic> json) => Workout(
    id: '${json['id']}',
    name: '${json['name']}',
    description: json['description'] as String?,
    discipline: '${json['discipline']}',
    goal: json['goal'] as String?,
    status: WorkoutStatus.parse(json['status'] as String?),
    version: apiIntOr(json['version'], 0),
    targetDurationS: apiInt(json['target_duration_s']),
    targetLoad: apiDouble(json['target_load']),
    intensityNote: json['intensity_note'] as String?,
    steps: [
      for (final e in (json['steps'] as List? ?? const []))
        WorkoutStep.fromJson(e as Map<String, dynamic>),
    ],
    createdAt: DateTime.parse('${json['created_at']}'),
    updatedAt: DateTime.parse('${json['updated_at']}'),
  );
}

/// Editable workout draft. Sending this to [TrainingRepository.updateWorkout]
/// requires [expectedVersion] from the copy that was read.
class WorkoutInput {
  final String name;
  final String? description;
  final String discipline;
  final String? goal;
  final WorkoutStatus status;
  final int? targetDurationS;
  final double? targetLoad;
  final String? intensityNote;
  final List<WorkoutStepInput> steps;

  const WorkoutInput({
    required this.name,
    this.description,
    this.discipline = 'road',
    this.goal,
    this.status = WorkoutStatus.draft,
    this.targetDurationS,
    this.targetLoad,
    this.intensityNote,
    this.steps = const [],
  });

  Map<String, dynamic> toJson() => {
    'name': name,
    'description': ?(description != null && description!.isNotEmpty
        ? description
        : null),
    'discipline': discipline,
    'goal': ?(goal != null && goal!.isNotEmpty ? goal : null),
    'status': status.wire,
    'target_duration_s': ?targetDurationS,
    'target_load': ?targetLoad,
    'intensity_note': ?(intensityNote != null && intensityNote!.isNotEmpty
        ? intensityNote
        : null),
    'steps': [for (final s in steps) s.toJson()],
  };
}

class WorkoutStepInput {
  final WorkoutStepType stepType;
  final String label;
  final int durationS;
  final int repeatCount;
  final int? targetZone;
  final double? targetPowerLowW;
  final double? targetPowerHighW;
  final double? targetHrLowBpm;
  final double? targetHrHighBpm;

  const WorkoutStepInput({
    this.stepType = WorkoutStepType.steady,
    required this.label,
    required this.durationS,
    this.repeatCount = 1,
    this.targetZone,
    this.targetPowerLowW,
    this.targetPowerHighW,
    this.targetHrLowBpm,
    this.targetHrHighBpm,
  });

  Map<String, dynamic> toJson() => {
    'step_type': stepType.wire,
    'label': label,
    'duration_s': durationS,
    'repeat_count': repeatCount,
    'target_zone': ?targetZone,
    'target_power_low_w': ?targetPowerLowW,
    'target_power_high_w': ?targetPowerHighW,
    'target_hr_low_bpm': ?targetHrLowBpm,
    'target_hr_high_bpm': ?targetHrHighBpm,
  };
}
