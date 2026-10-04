import '../../../core/network/api_client.dart';
import '../domain/notification.dart';

/// Real FastAPI backend via the shared [ApiClient]. No mock data.
///
/// Contract source of truth is `backend/app/api/v1/notifications.py`. Every call
/// is `auth: true`; the viewer comes from the access token, never from a body.
///
/// Registration is idempotent server-side on `(user_id, provider, device_id)`,
/// so a repeated call is a token refresh rather than a duplicate row. There is
/// **no** method that returns a token: the API never sends one, so no code here
/// could leak one even by accident.
class NotificationRepository {
  final ApiClient api;
  static const prefix = '/api/v1/notifications';
  static const devicePrefix = '/api/v1/push-devices';

  const NotificationRepository(this.api);

  String _query(Map<String, String> q) =>
      q.entries.map((e) => '${e.key}=${e.value}').join('&');

  // --- notification center ---------------------------------------------------

  Future<NotificationPage> list({
    int page = 1,
    int pageSize = 20,
    bool unreadOnly = false,
  }) async {
    final q = _query({
      'page': '$page',
      'page_size': '$pageSize',
      if (unreadOnly) 'unread_only': 'true',
    });
    final body = await api.get('$prefix?$q', auth: true);
    return NotificationPage.fromJson(body);
  }

  /// Unread total. Kept as its own call because the app bar polls it on every
  /// foreground; fetching a whole page for a number would be wasteful.
  Future<int> unreadCount() async {
    final body = await api.get('$prefix/unread-count', auth: true);
    return (body['unread_count'] as num? ?? 0).toInt();
  }

  /// Mark one read. Idempotent server-side, so a retried tap is safe.
  Future<AppNotification> markRead(String notificationId) async {
    final body = await api.post('$prefix/$notificationId/read', {}, auth: true);
    return AppNotification.fromJson(body);
  }

  /// Mark every unread read. Returns how many changed, which is 0 on a repeat.
  Future<int> markAllRead() async {
    final body = await api.post('$prefix/read-all', {}, auth: true);
    return (body['marked'] as num? ?? 0).toInt();
  }

  // --- devices ---------------------------------------------------------------

  Future<List<PushDevice>> devices() async {
    final body = await api.get(devicePrefix, auth: true);
    return (body['items'] as List? ?? const [])
        .map((e) => PushDevice.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// Register or refresh this device.
  ///
  /// [deviceId] is a stable per-installation id the caller generates and keeps,
  /// so a provider token rotation updates one row rather than adding another.
  /// The [token] is sent once and never stored or logged by the app.
  Future<PushDevice> registerDevice({
    required String deviceId,
    required String token,
    required PushPlatform platform,
    required PushProvider provider,
    String? appVersion,
    String? locale,
  }) async {
    final body = await api.post(devicePrefix, {
      'device_id': deviceId,
      'token': token,
      'platform': platform.wire,
      'provider': provider.wire,
      'app_version': ?appVersion,
      'locale': ?locale,
    }, auth: true);
    return PushDevice.fromJson(body);
  }

  /// Enable or disable one of the rider's own devices.
  Future<PushDevice> setDeviceEnabled(String deviceId, bool enabled) async {
    final body = await api.patch('$devicePrefix/$deviceId', {
      'enabled': enabled,
    }, auth: true);
    return PushDevice.fromJson(body);
  }

  /// Revoke a device entirely. A hard delete server-side: a revoked token has no
  /// reason to remain stored.
  Future<void> revokeDevice(String deviceId) async {
    await api.delete('$devicePrefix/$deviceId', auth: true);
  }
}
