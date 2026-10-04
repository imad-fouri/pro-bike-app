import 'route.dart';

/// Pure route validators mirroring backend limits (§32, §48). L10n keys only.
class RouteValidators {
  /// Same set the backend accepts (models/route.py).
  static const activities = [
    'road',
    'gravel',
    'mountain_bike',
    'touring',
    'bikepacking',
    'commuting',
    'e_bike',
    'other',
  ];

  /// `public` is intentionally absent: disabled until start/end masking (ADR-09).
  static const privacies = ['private', 'unlisted'];

  static String? validateName(String value) {
    final v = value.trim();
    if (v.isEmpty || v.length > 120) return 'routes.invalidName';
    return null;
  }

  static String? validateDescription(String value) {
    if (value.isEmpty) return null; // optional
    if (value.length > 2000) return 'routes.invalidDescription';
    return null;
  }

  static String? validateActivity(String value) {
    if (!activities.contains(value)) return 'routes.invalidActivity';
    return null;
  }

  static String? validatePrivacy(String value) {
    if (!privacies.contains(value)) return 'routes.privacyDisabled';
    return null;
  }

  /// Client-side mirror of the server rule: ≥2 points, valid coordinates.
  static String? validatePoints(List<RoutePointInput> points) {
    if (points.length < 2) return 'routes.needTwoPoints';
    if (points.length > 5000) return 'routes.tooManyPoints';
    for (final p in points) {
      if (p.lat < -90 || p.lat > 90 || p.lon < -180 || p.lon > 180) {
        return 'routes.invalidPoint';
      }
      if (p.ele != null && (p.ele! < -500 || p.ele! > 9000)) {
        return 'routes.invalidPoint';
      }
    }
    return null;
  }

  /// Single entry point for the create/edit form.
  static String? form({
    required String name,
    required String description,
    required String activity,
    required String privacy,
    required List<RoutePointInput> points,
  }) {
    return validateName(name) ??
        validateDescription(description) ??
        validateActivity(activity) ??
        validatePrivacy(privacy) ??
        validatePoints(points);
  }
}
