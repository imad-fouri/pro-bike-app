import 'social_profile.dart';

/// Pure client-side mirrors of the backend rules in
/// `social_service.canonical_username` / `update_profile` / `update_privacy`.
///
/// These exist for UX only: they return an l10n key so the form can complain
/// before a round trip. The backend stays authoritative — a value that passes
/// here can still be rejected with 409 (`SOCIAL_USERNAME_TAKEN`) or 422, and the
/// UI renders that server answer. Never a second source of truth.
class SocialValidators {
  /// Mirrors the backend's fixed vocabularies so the client cannot offer a
  /// value the server would reject.
  static const visibility = ProfileVisibility.values;
  static const requestPolicies = FriendRequestsPolicy.values;
  static const searchVisibilities = SearchVisibility.values;

  /// Cycling category is a free-text label server-side (`String(32)`), so this
  /// is a length bound only. Suggestions are a convenience, not an enum.
  static const suggestedCategories = [
    'road',
    'gravel',
    'mountain_bike',
    'e_bike',
    'commuting',
    'other',
  ];

  /// Username: 3-30 chars, lowercase letters/digits/underscore/dot, at least
  /// one letter, at most one dot, no leading or trailing dot/underscore, and
  /// never an email address. Returns null when the field is left blank — the
  /// backend treats a null username as "unclaimed", not as an error.
  static String? username(String value) {
    final v = value.trim().toLowerCase();
    if (v.isEmpty) return null;
    if (v.length < 3 || v.length > 30) return 'social.invalidUsername';
    if (!RegExp(r'^[a-z0-9_.]+$').hasMatch(v)) return 'social.invalidUsername';
    if (!RegExp(r'[a-z]').hasMatch(v)) return 'social.invalidUsername';
    if ('.'.allMatches(v).length > 1) return 'social.invalidUsername';
    if (v.startsWith('.') ||
        v.startsWith('_') ||
        v.endsWith('.') ||
        v.endsWith('_')) {
      return 'social.invalidUsername';
    }
    if (value.contains('@')) return 'social.invalidUsername';
    return null;
  }

  static String? displayName(String value) {
    final v = value.trim();
    if (v.isEmpty) return 'social.invalidDisplayName';
    if (v.length > 80) return 'social.invalidDisplayName';
    return null;
  }

  static String? bio(String value) {
    if (value.length > 500) return 'social.invalidBio';
    return null;
  }

  static String? avatarUrl(String value) {
    final v = value.trim();
    if (v.isEmpty) return null;
    if (!v.startsWith('https://') && !v.startsWith('http://')) {
      return 'social.invalidAvatar';
    }
    if (v.length > 512) return 'social.invalidAvatar';
    return null;
  }

  static String? cyclingCategory(String value) {
    if (value.length > 32) return 'social.invalidCategory';
    return null;
  }

  static String? countryCode(String value) {
    final v = value.trim();
    if (v.isEmpty) return null;
    if (v.length != 2 || !RegExp(r'^[A-Za-z]{2}$').hasMatch(v)) {
      return 'social.invalidCountry';
    }
    return null;
  }

  static String? city(String value) {
    if (value.length > 120) return 'social.invalidCity';
    return null;
  }

  /// Search box bound mirrors the endpoint's `Query(min_length=2, max_length=64)`.
  static String? searchQuery(String value) {
    final v = value.trim();
    if (v.length < 2) return 'social.searchTooShort';
    if (v.length > 64) return 'social.searchTooLong';
    return null;
  }

  /// Single entry point for the edit form; null means the form may submit.
  static String? profileForm({
    required String username,
    required String displayName,
    required String bio,
    required String cyclingCategory,
    required String countryCode,
    required String city,
  }) {
    return username_(username) ??
        displayName_(displayName) ??
        bio_(bio) ??
        cyclingCategory_(cyclingCategory) ??
        countryCode_(countryCode) ??
        city_(city);
  }

  // Private aliases so `profileForm` can call the rules without the public
  // method names shadowing the parameter names.
  static String? username_(String v) => username(v);
  static String? displayName_(String v) => displayName(v);
  static String? bio_(String v) => bio(v);
  static String? cyclingCategory_(String v) => cyclingCategory(v);
  static String? countryCode_(String v) => countryCode(v);
  static String? city_(String v) => city(v);
}
