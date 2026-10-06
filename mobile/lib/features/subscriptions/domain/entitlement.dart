/// Server-resolved subscription state, cached for display only.
///
/// The backend owns every access decision. These models describe what the
/// server said at [fetchedAt]; they never authorize anything. A screen may use
/// [hasFeature] to choose copy before the next request, but the request itself
/// is still what succeeds or returns `ENTITLEMENT_REQUIRED`.
library;

/// The product plan reported by `/me/entitlements`.
enum SubscriptionPlan {
  free,
  pro;

  static SubscriptionPlan parse(String? raw) => switch (raw) {
    'pro' => SubscriptionPlan.pro,
    _ => SubscriptionPlan.free,
  };

  String get wire => switch (this) {
    SubscriptionPlan.free => 'free',
    SubscriptionPlan.pro => 'pro',
  };
}

/// Stable product capabilities. The wire values match the backend enum; the
/// client translates them for display and never sends a translation back.
enum EntitlementFeature {
  aiCoach('ai_coach'),
  advancedTraining('advanced_training'),
  advancedAnalytics('advanced_analytics'),
  advancedRoutes('advanced_routes'),
  noAds('no_ads'),
  unknown('unknown');

  const EntitlementFeature(this.wire);
  final String wire;

  static EntitlementFeature parse(String? raw) {
    for (final feature in EntitlementFeature.values) {
      if (feature.wire == raw) return feature;
    }
    // Forward compatibility: a future capability must degrade to an
    // explicitly unknown value rather than break the whole state fetch.
    return EntitlementFeature.unknown;
  }

  String get l10nKey => 'subscription.feature.$wire';
}

/// Where the backend says a grant came from.
enum EntitlementSource {
  subscription,
  manual,
  unknown;

  static EntitlementSource parse(String? raw) => switch (raw) {
    'subscription' => EntitlementSource.subscription,
    'manual' => EntitlementSource.manual,
    _ => EntitlementSource.unknown,
  };
}

/// Row lifecycle reported by the backend. Expiration is derived from the
/// timestamps rather than stored as a separate status.
enum EntitlementStatus {
  active,
  inactive,
  revoked;

  static EntitlementStatus parse(String? raw) => switch (raw) {
    'active' => EntitlementStatus.active,
    'inactive' => EntitlementStatus.inactive,
    _ => EntitlementStatus.revoked,
  };
}

/// One capability grant for the authenticated rider.
class UserEntitlement {
  final EntitlementFeature feature;
  final EntitlementStatus status;
  final EntitlementSource source;
  final DateTime startsAt;
  final DateTime expiresAt;
  final bool effective;

  const UserEntitlement({
    required this.feature,
    required this.status,
    required this.source,
    required this.startsAt,
    required this.expiresAt,
    required this.effective,
  });

  /// The client's advisory view of effectiveness. The server already computed
  /// [effective] at fetch time; this only answers the same question locally
  /// so a screen can avoid an obviously doomed request. It grants nothing.
  bool isEffectiveAt(DateTime now) =>
      status == EntitlementStatus.active &&
      !startsAt.isAfter(now) &&
      expiresAt.isAfter(now);

  factory UserEntitlement.fromJson(Map<String, dynamic> json) {
    return UserEntitlement(
      feature: EntitlementFeature.parse(json['feature'] as String?),
      status: EntitlementStatus.parse(json['status'] as String?),
      source: EntitlementSource.parse(json['source'] as String?),
      startsAt: DateTime.parse('${json['starts_at']}'),
      expiresAt: DateTime.parse('${json['expires_at']}'),
      effective: json['effective'] == true,
    );
  }
}

/// The rider's product state at one instant.
class EntitlementState {
  final SubscriptionPlan plan;
  final List<String> freeCapabilities;
  final List<UserEntitlement> entitlements;
  final DateTime fetchedAt;

  const EntitlementState({
    required this.plan,
    required this.freeCapabilities,
    required this.entitlements,
    required this.fetchedAt,
  });

  bool get isPro => plan == SubscriptionPlan.pro;

  /// Advisory only. A `true` answer means the cached state allows the
  /// feature; the next premium request is still the authority.
  bool hasFeature(EntitlementFeature feature, DateTime now) =>
      entitlements.any((e) => e.feature == feature && e.isEffectiveAt(now));

  factory EntitlementState.fromJson(Map<String, dynamic> json) =>
      EntitlementState(
        plan: SubscriptionPlan.parse(json['plan'] as String?),
        freeCapabilities: [
          for (final c in (json['free_capabilities'] as List? ?? const []))
            '$c',
        ],
        entitlements: [
          for (final e in (json['entitlements'] as List? ?? const []))
            UserEntitlement.fromJson(e as Map<String, dynamic>),
        ],
        fetchedAt: DateTime.now().toUtc(),
      );
}
