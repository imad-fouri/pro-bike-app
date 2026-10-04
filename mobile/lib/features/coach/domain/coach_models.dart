/// Coach domain entities. The Coach *interprets* the deterministic engine's
/// output; it never recomputes it. Every number on this screen arrived inside
/// `context.metrics` with its own `source` and `version`, so the UI shows that
/// pair rather than trusting a value it cannot trace (ADR-11 §1, §3).
///
/// Decimal fields arrive as strings (see core/units/api_number.dart) and stay
/// null when the engine could not compute them — "not measured" is never 0, and
/// an unmeasured metric renders as `—` instead of a fabricated zero.
library;

import '../../../core/units/api_number.dart';
import '../../training/domain/training_units.dart';

/// The four explanations the server offers. The client picks an intent and at
/// most one resource pointer; it can never send a metric of its own.
enum CoachIntent {
  explainRide('explain_ride'),
  explainWorkout('explain_workout'),
  weeklySummary('weekly_summary'),
  trainingQuestion('training_question');

  const CoachIntent(this.wire);
  final String wire;

  static CoachIntent parse(String? raw) => switch (raw) {
    'explain_ride' => CoachIntent.explainRide,
    'explain_workout' => CoachIntent.explainWorkout,
    'weekly_summary' => CoachIntent.weeklySummary,
    _ => CoachIntent.trainingQuestion,
  };

  String get l10nKey => 'coach.intent.$wire';

  /// These two read one ride or one workout. The weekly summary and a free
  /// question read the rider's own week instead, so they need no pointer.
  bool get needsResourcePointer =>
      this == CoachIntent.explainRide || this == CoachIntent.explainWorkout;

  /// A free question is the only intent that carries user text worth typing.
  bool get needsMessage => this == CoachIntent.trainingQuestion;
}

/// One statement tied to a metric key, so "why" is traceable to a number.
class CoachObservation {
  final String metric;
  final String text;
  const CoachObservation({required this.metric, required this.text});

  factory CoachObservation.fromJson(Map<String, dynamic> json) =>
      CoachObservation(metric: '${json['metric']}', text: '${json['text']}');
}

/// Advice. The numeric fields are optional, are range-checked server-side
/// against the Phase 6 caps, and are displayed only when present.
class CoachRecommendation {
  final String text;
  final double? loadChangePct;
  final double? loadTarget;
  const CoachRecommendation({
    required this.text,
    this.loadChangePct,
    this.loadTarget,
  });

  factory CoachRecommendation.fromJson(Map<String, dynamic> json) =>
      CoachRecommendation(
        text: '${json['text']}',
        loadChangePct: apiDouble(json['load_change_pct']),
        loadTarget: apiDouble(json['load_target']),
      );
}

/// A required limit or a reason to stop trusting the rest. Always rendered.
///
/// [text] arrives already localized: the server owns the wording of a caution
/// in every language it supports, so the client shows it verbatim rather than
/// keeping a second translation table that could drift out of step. [code] is
/// the stable machine-readable half, and is what the UI keys assertions on.
class CoachCaution {
  final String code;
  final String text;
  const CoachCaution({required this.code, required this.text});

  factory CoachCaution.fromJson(Map<String, dynamic> json) =>
      CoachCaution(code: '${json['code']}', text: '${json['text']}');
}

/// A pointer the explanation leaned on. Identity only — never its metrics.
class CoachEntityRef {
  final String type;
  final String? id;
  final String label;
  const CoachEntityRef({required this.type, this.id, required this.label});

  factory CoachEntityRef.fromJson(Map<String, dynamic> json) => CoachEntityRef(
    type: '${json['type']}',
    id: json['id'] == null ? null : '${json['id']}',
    label: '${json['label'] ?? ''}',
  );
}

/// Where a number came from. The UI shows the source and version so a reader
/// can tell a measured value from an estimate without asking.
class CoachProvenance {
  final String metric;
  final String entity;
  final String? version;
  final String source;
  const CoachProvenance({
    required this.metric,
    required this.entity,
    this.version,
    required this.source,
  });

  factory CoachProvenance.fromJson(Map<String, dynamic> json) =>
      CoachProvenance(
        metric: '${json['metric']}',
        entity: '${json['entity']}',
        version: json['version'] == null ? null : '${json['version']}',
        source: '${json['source']}',
      );
}

/// A deterministic number the Coach was allowed to see, with the formula that
/// produced it. [value] is null when the engine had no basis for it, and the
/// unit is whatever the server says it is — the client never converts.
class CoachMetric {
  final String key;
  final double? value;
  final String unit;
  final String? version;
  final String source;
  const CoachMetric({
    required this.key,
    required this.value,
    required this.unit,
    this.version,
    required this.source,
  });

  /// The value as the server states it, with its own unit. An absent value
  /// shows as unavailable rather than as a zero the rider did not ride.
  String get display => value == null ? unavailable : '$value $unit';

  factory CoachMetric.fromJson(Map<String, dynamic> json) => CoachMetric(
    key: '${json['key']}',
    value: apiDouble(json['value']),
    unit: '${json['unit']}',
    version: json['version'] == null ? null : '${json['version']}',
    source: '${json['source']}',
  );
}

/// The bounded context the explanation was built from: what it saw, what it
/// was told about the rider, and what it could not measure. [unavailable] is
/// rendered prominently — a missing measurement is a fact, not a blank.
class CoachContext {
  final List<CoachMetric> metrics;
  final List<String> notes;
  final List<String> unavailable;
  const CoachContext({
    required this.metrics,
    required this.notes,
    required this.unavailable,
  });

  factory CoachContext.fromJson(Map<String, dynamic> json) => CoachContext(
    metrics: [
      for (final m in (json['metrics'] as List? ?? const []))
        CoachMetric.fromJson(m as Map<String, dynamic>),
    ],
    notes: [for (final n in (json['notes'] as List? ?? const [])) '$n'],
    unavailable: [
      for (final u in (json['unavailable'] as List? ?? const [])) '$u',
    ],
  );
}

/// One explanation. Deliberately not a transcript: the screen holds a single
/// answer and asking again replaces it (ADR-11 §6).
class CoachMessage {
  final CoachIntent intent;
  final String summary;
  final List<CoachObservation> observations;
  final List<CoachRecommendation> recommendations;
  final List<CoachCaution> cautions;
  final List<CoachEntityRef> referencedEntities;
  final List<CoachProvenance> provenance;
  final bool fallbackUsed;
  final String? promptVersion;
  final CoachContext context;

  const CoachMessage({
    required this.intent,
    required this.summary,
    required this.observations,
    required this.recommendations,
    required this.cautions,
    required this.referencedEntities,
    required this.provenance,
    required this.fallbackUsed,
    this.promptVersion,
    required this.context,
  });

  /// A caution the model was supposed to produce and did not. The server drops
  /// such a response for the deterministic answer, so anything still arriving
  /// without one is surfaced here rather than hidden.
  bool get hasAdvice => recommendations.isNotEmpty;

  factory CoachMessage.fromJson(Map<String, dynamic> json) => CoachMessage(
    intent: CoachIntent.parse(json['intent'] as String?),
    summary: '${json['summary']}',
    observations: [
      for (final o in (json['observations'] as List? ?? const []))
        CoachObservation.fromJson(o as Map<String, dynamic>),
    ],
    recommendations: [
      for (final r in (json['recommendations'] as List? ?? const []))
        CoachRecommendation.fromJson(r as Map<String, dynamic>),
    ],
    cautions: [
      for (final c in (json['cautions'] as List? ?? const []))
        CoachCaution.fromJson(c as Map<String, dynamic>),
    ],
    referencedEntities: [
      for (final e in (json['referenced_entities'] as List? ?? const []))
        CoachEntityRef.fromJson(e as Map<String, dynamic>),
    ],
    provenance: [
      for (final p in (json['provenance'] as List? ?? const []))
        CoachProvenance.fromJson(p as Map<String, dynamic>),
    ],
    fallbackUsed: json['fallback_used'] == true,
    promptVersion: json['prompt_version'] == null
        ? null
        : '${json['prompt_version']}',
    context: CoachContext.fromJson(
      json['context'] as Map<String, dynamic>? ?? const {},
    ),
  );
}

/// The server-declared limits. Shown so the user can see the budget they are
/// spending, and so the client never invents a cap of its own.
class CoachLimits {
  final int maxMessageChars;
  final int maxInputTokens;
  final int maxOutputTokens;
  final int maxRequestsPerUser;
  final int timeoutSeconds;
  const CoachLimits({
    required this.maxMessageChars,
    required this.maxInputTokens,
    required this.maxOutputTokens,
    required this.maxRequestsPerUser,
    required this.timeoutSeconds,
  });

  factory CoachLimits.fromJson(Map<String, dynamic> json) => CoachLimits(
    maxMessageChars: apiIntOr(json['max_message_chars'], 1000),
    maxInputTokens: apiIntOr(json['max_input_tokens'], 4000),
    maxOutputTokens: apiIntOr(json['max_output_tokens'], 600),
    maxRequestsPerUser: apiIntOr(json['max_requests_per_user'], 20),
    timeoutSeconds: apiIntOr(json['timeout_seconds'], 20),
  );
}

/// Whether the Coach can explain or will only restate the deterministic
/// answer. [fallbackOnly] is not an error: it is the honest state of a build
/// with no provider key, and the screen says so instead of looking broken.
class CoachStatus {
  final bool enabled;
  final String provider;
  final String model;
  final bool fallbackOnly;
  final Map<String, String> promptVersions;
  final CoachLimits limits;
  const CoachStatus({
    required this.enabled,
    required this.provider,
    required this.model,
    required this.fallbackOnly,
    required this.promptVersions,
    required this.limits,
  });

  factory CoachStatus.fromJson(Map<String, dynamic> json) => CoachStatus(
    enabled: json['enabled'] == true,
    provider: '${json['provider'] ?? 'none'}',
    model: json['model'] ?? '',
    fallbackOnly: json['fallback_only'] == true,
    promptVersions: {
      for (final e
          in (json['prompt_versions'] as Map<String, dynamic>? ?? const {})
              .entries)
        e.key: e.value,
    },
    limits: CoachLimits.fromJson(
      json['limits'] as Map<String, dynamic>? ?? const {},
    ),
  );
}
