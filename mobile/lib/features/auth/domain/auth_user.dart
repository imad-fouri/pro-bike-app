/// Authenticated cyclist (subset of /me needed by the app shell).
class AuthUser {
  final String id;
  final String email;
  final String displayName;
  final bool emailVerified;

  const AuthUser({
    required this.id,
    required this.email,
    required this.displayName,
    required this.emailVerified,
  });

  factory AuthUser.fromMe(Map<String, dynamic> me) {
    final user = me['user'] as Map<String, dynamic>;
    final profile = me['profile'] as Map<String, dynamic>;
    return AuthUser(
      id: '${user['id']}',
      email: '${user['email']}',
      displayName: '${profile['display_name']}',
      emailVerified: user['email_verified'] == true,
    );
  }
}
