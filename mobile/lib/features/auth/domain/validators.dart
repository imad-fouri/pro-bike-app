/// Pure validators — no widgets, fully unit-tested. L10n keys, not messages.
class AuthValidators {
  static final _email = RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$');

  static String? email(String value) {
    if (!_email.hasMatch(value.trim())) return 'auth.invalidEmail';
    return null;
  }

  static String? password(String value) {
    if (value.length < 10) return 'auth.weakPassword';
    if (!RegExp(r'[A-Za-z]').hasMatch(value) ||
        !RegExp(r'[0-9]').hasMatch(value)) {
      return 'auth.weakPassword';
    }
    return null;
  }

  static String? displayName(String value) {
    final v = value.trim();
    if (v.length < 2 || v.length > 80) return 'auth.invalidDisplayName';
    return null;
  }

  static String? confirm(String password, String confirm) {
    if (password != confirm) return 'auth.passwordMismatch';
    return null;
  }
}
