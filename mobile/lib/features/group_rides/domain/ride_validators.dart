/// Pure validators mirroring the Phase 9 backend limits.
///
/// Kept out of the widget so the rules are testable without a screen, and out of
/// the backend because the client still has to be able to say "this title is too
/// long" without a round trip. The server remains the authority; these mirror it
/// and never replace it.
class RideValidators {
  /// `app/schemas/group_ride.py` — `RideCreate`.
  static const maxTitle = 120;
  static const maxDescription = 1000;
  static const maxMeetingPoint = 160;
  static const maxInviteMessage = 280;

  static String? validateTitle(String value) {
    final v = value.trim();
    if (v.isEmpty || v.length > maxTitle) return 'groupRide.invalidTitle';
    return null;
  }

  static String? validateDescription(String value) {
    if (value.isEmpty) return null; // optional
    if (value.length > maxDescription) return 'groupRide.invalidDescription';
    return null;
  }

  static String? validateMeetingPoint(String value) {
    if (value.isEmpty) return null; // optional
    if (value.length > maxMeetingPoint) return 'groupRide.invalidMeetingPoint';
    return null;
  }

  static String? validateInviteMessage(String value) {
    if (value.isEmpty) return null; // optional
    if (value.length > maxInviteMessage) {
      return 'groupRide.invalidInviteMessage';
    }
    return null;
  }

  /// The route pin is BOTH-OR-NEITHER, and the client refuses to submit half a
  /// pair rather than letting the server 422 it.
  ///
  /// There is no "latest version" option here on purpose: a ride pins the exact
  /// geometry that was agreed, so a version selector with a default of "newest"
  /// would silently change what riders ride once somebody edits the route
  /// (ADR-16 §4).
  static String? validateRoutePin(String? routeId, int? routeVersion) {
    if (routeId == null && routeVersion == null) return null;
    if (routeId == null || routeVersion == null) {
      return 'groupRide.invalidRoutePin';
    }
    if (routeVersion < 1) return 'groupRide.invalidRoutePin';
    return null;
  }

  /// Single entry point for the create form.
  static String? form({
    required String title,
    required String description,
    required String meetingPoint,
    String? routeId,
    int? routeVersion,
  }) {
    return validateTitle(title) ??
        validateDescription(description) ??
        validateMeetingPoint(meetingPoint) ??
        validateRoutePin(routeId, routeVersion);
  }
}
