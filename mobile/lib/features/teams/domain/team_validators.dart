import 'team.dart';

/// Pure client-side mirrors of the backend rules in
/// `team_service.canonical_handle` / `create_team` / `update_team`.
///
/// UX only. The backend stays authoritative: a handle that passes here can still
/// be rejected with 409 `TEAM_HANDLE_TAKEN`, and the UI renders that answer.
class TeamValidators {
  /// Mirrors the backend's fixed vocabularies so the client cannot offer a
  /// value the server would reject.
  static const visibilities = TeamVisibility.values;

  /// Roles a client may render. Owner transfer is out of scope (Phase 8.3), so
  /// owner is never an assignable target.
  static const assignableRoles = [TeamRole.admin, TeamRole.member];

  /// Categories mirror `social_profiles.cycling_category`: a free-text label,
  /// not an enum, so this is a length bound plus a convenience vocabulary.
  static const suggestedCategories = [
    'road',
    'gravel',
    'mountain_bike',
    'cyclocross',
    'e_bike',
    'commuting',
    'bikepacking',
    'touring',
    'other',
  ];

  /// Same rules as `canonical_handle`: 3-30 lowercase chars, at least one
  /// letter, at most one dot, no leading/trailing dot or underscore, never an
  /// email. Blank means "unclaimed", which the backend accepts.
  static String? handle(String value) {
    final v = value.trim().toLowerCase();
    if (v.isEmpty) return null;
    if (v.length < 3 || v.length > 30) return 'team.invalidHandle';
    if (!RegExp(r'^[a-z0-9_.]+$').hasMatch(v)) return 'team.invalidHandle';
    if (!RegExp(r'[a-z]').hasMatch(v)) return 'team.invalidHandle';
    if ('.'.allMatches(v).length > 1) return 'team.invalidHandle';
    if (v.startsWith('.') ||
        v.startsWith('_') ||
        v.endsWith('.') ||
        v.endsWith('_')) {
      return 'team.invalidHandle';
    }
    if (value.contains('@')) return 'team.invalidHandle';
    return null;
  }

  static String? name(String value) {
    final v = value.trim();
    if (v.isEmpty) return 'team.invalidName';
    if (v.length > 80) return 'team.invalidName';
    return null;
  }

  static String? description(String value) {
    if (value.length > 500) return 'team.invalidDescription';
    return null;
  }

  static String? avatarUrl(String value) {
    final v = value.trim();
    if (v.isEmpty) return null;
    if (!v.startsWith('https://') && !v.startsWith('http://')) {
      return 'team.invalidAvatar';
    }
    if (v.length > 512) return 'team.invalidAvatar';
    return null;
  }

  static String? category(String value) {
    if (value.length > 32) return 'team.invalidCategory';
    return null;
  }

  static String? message(String value) {
    if (value.length > 280) return 'team.invalidMessage';
    return null;
  }

  /// Mirrors the endpoint's `q` constraint (2-64).
  static String? searchQuery(String value) {
    final v = value.trim();
    if (v.length < 2) return 'team.searchTooShort';
    if (v.length > 64) return 'team.searchTooLong';
    return null;
  }

  /// Single entry point for the create/edit form; null means it may submit.
  static String? form({
    required String name,
    required String handle,
    required String description,
    required String avatarUrl,
    required String category,
  }) {
    return name_(name) ??
        handle_(handle) ??
        description_(description) ??
        avatarUrl_(avatarUrl) ??
        category_(category);
  }

  // Aliases so `form` can reference the rules without its parameter names
  // shadowing the methods.
  static String? name_(String v) => name(v);
  static String? handle_(String v) => handle(v);
  static String? description_(String v) => description(v);
  static String? avatarUrl_(String v) => avatarUrl(v);
  static String? category_(String v) => category(v);
}
