import 'dart:convert';
import 'dart:io';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/features/notifications/data/notification_repository.dart';
import 'package:cyclecoach/features/notifications/data/push_registrar.dart';
import 'package:cyclecoach/features/notifications/domain/notification.dart';
import 'package:cyclecoach/features/notifications/domain/notification_links.dart';
import 'package:cyclecoach/features/notifications/presentation/notification_center_page.dart';
import 'package:cyclecoach/features/notifications/presentation/notification_providers.dart';
import 'package:cyclecoach/features/notifications/presentation/notification_widgets.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const secretToken = 'SUPER-SECRET-PUSH-TOKEN-XYZ';

Map<String, dynamic> notificationJson({
  String id = 'n-1',
  String type = 'friend_request',
  String? deepLink = '/friends/requests',
  bool isRead = false,
  Map<String, String>? params,
  String? actorDisplayName = 'Karim Amrani',
}) => {
  'id': id,
  'type': type,
  'entity_type': 'friend_request',
  'entity_id': 'e-1',
  'l10n_key': 'notifications.type.$type',
  'params': params ?? {'actorName': actorDisplayName ?? ''},
  'deep_link': deepLink,
  'is_read': isRead,
  'is_unread': !isRead,
  'created_at': '2026-01-01T10:00:00Z',
  'read_at': isRead ? '2026-01-01T11:00:00Z' : null,
  'actor_user_id': 'u-2',
  'actor_username': 'karim_amrani',
  'actor_display_name': actorDisplayName,
  'entity_name': null,
};

/// Stateful fake so a mutation changes what the next read returns.
class FakeNotifications {
  final Map<String, Map<String, dynamic>> rows = {};
  final List<String> calls = [];
  String? forcedCode;
  int forcedStatus = 404;

  /// Device rows the fake API returns for `/push-devices`.
  List<Map<String, dynamic>>? deviceRows;

  /// The last token the client SENT. Tracked so a test can prove the token went
  /// up without ever being returned down.
  String? registeredToken;

  void seed(List<Map<String, dynamic>> seed) {
    for (final row in seed) {
      rows[row['id']! as String] = Map<String, dynamic>.from(row);
    }
  }

  List<Map<String, dynamic>> _page(int page, int pageSize, bool unreadOnly) {
    var all = rows.values.toList();
    if (unreadOnly) {
      all = all.where((r) => r['is_unread'] == true).toList();
    }
    all.sort((a, b) => '${b['created_at']}'.compareTo('${a['created_at']}'));
    return all.skip((page - 1) * pageSize).take(pageSize).toList();
  }
}

Future<http.Response> Function(http.Request) handler(FakeNotifications f) =>
    (req) async {
      final path = req.url.path;
      final method = req.method;
      f.calls.add('$method $path');

      if (f.forcedCode != null) {
        return http.Response(
          jsonEncode({
            'error': {'code': f.forcedCode, 'message': 'x'},
          }),
          f.forcedStatus,
        );
      }

      if (path.startsWith('/api/v1/chat/')) {
        // Guard: the notification feature must never issue a chat call.
        return http.Response(
          jsonEncode({
            'error': {'code': 'LEAK'},
          }),
          500,
        );
      }
      if (path.startsWith('/api/v1/teams/')) {
        return http.Response(
          jsonEncode({
            'error': {'code': 'LEAK'},
          }),
          500,
        );
      }

      if (method == 'GET' && path == '/api/v1/notifications') {
        final page = int.tryParse(req.url.queryParameters['page'] ?? '1') ?? 1;
        final size =
            int.tryParse(req.url.queryParameters['page_size'] ?? '20') ?? 20;
        final unread = req.url.queryParameters['unread_only'] == 'true';
        final items = f._page(page, size, unread);
        return http.Response(
          jsonEncode({
            'items': items,
            'total': f.rows.length,
            'page': page,
            'page_size': size,
          }),
          200,
        );
      }

      if (method == 'GET' && path == '/api/v1/notifications/unread-count') {
        final unread = f.rows.values
            .where((r) => r['is_unread'] == true)
            .length;
        return http.Response(jsonEncode({'unread_count': unread}), 200);
      }

      if (method == 'POST' && path == '/api/v1/notifications/read-all') {
        var marked = 0;
        f.rows.forEach((_, row) {
          if (row['is_unread'] == true) {
            row['is_unread'] = false;
            row['is_read'] = true;
            row['read_at'] = '2026-01-01T12:00:00Z';
            marked++;
          }
        });
        return http.Response(jsonEncode({'marked': marked}), 200);
      }

      if (method == 'POST' &&
          path.startsWith('/api/v1/notifications/') &&
          path.endsWith('/read')) {
        final id = path.split('/').elementAt(4);
        final row = f.rows[id];
        if (row == null) {
          return http.Response(
            jsonEncode({
              'error': {'code': 'NOTIFICATION_NOT_FOUND', 'message': 'x'},
            }),
            404,
          );
        }
        row['is_unread'] = false;
        row['is_read'] = true;
        row['read_at'] = '2026-01-01T12:00:00Z';
        return http.Response(jsonEncode(row), 200);
      }

      if (path == '/api/v1/push-devices') {
        if (method == 'GET') {
          return http.Response(
            jsonEncode({
              'items': f.deviceRows ?? const [],
              'total': (f.deviceRows ?? const []).length,
            }),
            200,
          );
        }
        if (method == 'POST') {
          final body = jsonDecode(req.body) as Map<String, dynamic>;
          f.registeredToken = '${body['token']}';
          final row = {
            'id': 'd-1',
            'device_id': '${body['device_id']}',
            'platform': '${body['platform']}',
            'provider': '${body['provider']}',
            'app_version': body['app_version'],
            'locale': body['locale'],
            'enabled': true,
            'last_seen_at': '2026-01-01T10:00:00Z',
            'created_at': '2026-01-01T10:00:00Z',
          };
          f.deviceRows = [row];
          return http.Response(jsonEncode(row), 201);
        }
      }

      if (method == 'PATCH' && path.startsWith('/api/v1/push-devices/')) {
        final id = path.split('/').elementAt(4);
        final body = jsonDecode(req.body) as Map<String, dynamic>;
        final rows = f.deviceRows ?? const [];
        if (rows.isEmpty || rows.first['id'] != id) {
          return http.Response(
            jsonEncode({
              'error': {'code': 'PUSH_DEVICE_NOT_FOUND', 'message': 'x'},
            }),
            404,
          );
        }
        final updated = {...rows.first, 'enabled': body['enabled']};
        f.deviceRows = [updated];
        return http.Response(jsonEncode(updated), 200);
      }

      if (method == 'DELETE' && path.startsWith('/api/v1/push-devices/')) {
        f.deviceRows = const [];
        return http.Response(jsonEncode({'status': 'revoked'}), 200);
      }

      return http.Response('{}', 404);
    };

ProviderContainer containerFor(
  FakeNotifications fake, {
  PushRegistrar registrar = const UnconfiguredPushRegistrar(),
}) => ProviderContainer(
  overrides: [
    notificationRepositoryProvider.overrideWithValue(
      NotificationRepository(
        ApiClient(
          baseUrl: 'http://test',
          client: MockClient(handler(fake)),
          accessToken: () async => 'token',
        ),
      ),
    ),
    pushRegistrarProvider.overrideWithValue(registrar),
  ],
);

/// A registrar that hands back a token, so registration can be exercised.
class ScriptedPushRegistrar implements PushRegistrar {
  final PushRegistration? result;
  int registerCalls = 0;
  int unregisterCalls = 0;

  ScriptedPushRegistrar({this.result});

  @override
  bool get isAvailable => result != null;

  @override
  Future<PushRegistration?> register() async {
    registerCalls++;
    return result;
  }

  @override
  Future<void> unregister() async => unregisterCalls++;
}

/// Keeps a provider alive for the duration of a test.
///
/// `autoDispose` providers are disposed as soon as nothing reads them, so
/// awaiting `future` once is not enough for a test that then drives the notifier
/// directly. A standing subscription is what a mounted widget provides in the
/// real app.
ProviderSubscription<AsyncValue<NotificationListState>> keepList(
  ProviderContainer container,
) => container.listen<AsyncValue<NotificationListState>>(
  notificationListProvider,
  (_, _) {},
  fireImmediately: true,
);

ProviderSubscription<AsyncValue<int>> keepUnread(ProviderContainer container) =>
    container.listen<AsyncValue<int>>(
      unreadCountProvider,
      (_, _) {},
      fireImmediately: true,
    );

Widget harness(
  Widget child, {
  required ProviderContainer container,
  String locale = 'en',
  TextDirection? direction,
}) => UncontrolledProviderScope(
  container: container,
  child: MaterialApp(
    locale: Locale(locale),
    localizationsDelegates: const [
      AppLocalizationsDelegate(),
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    supportedLocales: AppLocalizations.supported,
    home: direction == null
        ? child
        : Directionality(textDirection: direction, child: child),
  ),
);

void main() {
  group('domain models', () {
    test('a notification parses every server field', () {
      final n = AppNotification.fromJson(notificationJson());
      expect(n.id, 'n-1');
      expect(n.type, NotificationType.friendRequest);
      expect(n.l10nKey, 'notifications.type.friend_request');
      expect(n.params['actorName'], 'Karim Amrani');
      expect(n.deepLink, '/friends/requests');
      expect(n.isUnread, isTrue);
      expect(n.bestActor, 'Karim Amrani');
    });

    test('non-string params are dropped rather than rendered', () {
      // A numeric param would render as "3.0" inside a localized sentence.
      final n = AppNotification.fromJson({
        ...notificationJson(),
        'params': {'count': 3, 'flag': true, 'name': 'Ada', 'nil': null},
      });
      expect(n.params, {'name': 'Ada'});
    });

    test('an unknown type degrades instead of throwing', () {
      final n = AppNotification.fromJson(
        // Deliberately a Phase 9 sibling that does not exist. Before Phase 9 this
        // test used `group_ride_invitation`, which was genuinely unknown then and
        // is a real type now - keeping that input would have turned a meaningful
        // assertion into a false pass once the enum grew. The property under test
        // is "a type THIS BUILD has never heard of", so the input has to stay
        // ahead of the enum rather than behind it.
        notificationJson(type: 'group_ride_scheduled'),
      );
      // A newer server must not be able to crash an older client.
      expect(n.type, NotificationType.system);
    });

    test('every Phase 9 group-ride type is recognized, not degraded', () {
      // The complement of the test above, and the reason it needs one: a type
      // added to the enum but left out of `parse` would silently render as
      // `system` and lose its deep link, which is exactly the failure the previous
      // version of that test would have hidden.
      for (final wire in const [
        'group_ride_invitation',
        'group_ride_accepted',
        'group_ride_started',
        'chat_message_group_ride',
      ]) {
        expect(
          AppNotification.fromJson(notificationJson(type: wire)).type,
          isNot(NotificationType.system),
          reason: '$wire must parse to its own type',
        );
      }
    });

    test('a team notification names its team', () {
      final n = AppNotification.fromJson(
        notificationJson(
          type: 'chat_message_team',
          deepLink: '/teams/2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f',
          params: {'actorName': 'Imad', 'teamName': 'Atlas CC'},
        ),
      );
      expect(n.type, NotificationType.chatMessageTeam);
      expect(n.params['teamName'], 'Atlas CC');
      expect(n.type.isBulk, isTrue);
    });

    test('no model can hold a token', () {
      // The API never returns one, so the type has nowhere to put it.
      expect(
        AppNotification.fromJson({'id': 'x', 'token': secretToken}).toString(),
        isNot(contains(secretToken)),
      );
      expect(
        PushDevice.fromJson({'id': 'd', 'token': secretToken}).toString(),
        isNot(contains(secretToken)),
      );
    });

    test('push device parses without a token field', () {
      final d = PushDevice.fromJson({
        'id': 'd-1',
        'device_id': 'phone',
        'platform': 'ios',
        'provider': 'apns',
        'app_version': '1.2.3',
        'locale': 'fr',
        'enabled': true,
        'last_seen_at': null,
        'created_at': '2026-01-01T10:00:00Z',
      });
      expect(d.platform, PushPlatform.ios);
      expect(d.provider, PushProvider.apns);
      expect(d.enabled, isTrue);
      expect(d.copyWith(enabled: false).enabled, isFalse);
    });

    test('notification page is offset-paged', () {
      final page = NotificationPage.fromJson({
        'items': [notificationJson()],
        'total': 40,
        'page': 1,
        'page_size': 20,
      });
      expect(page.items, hasLength(1));
      expect(page.hasMore, isTrue);
    });
  });

  group('deep links', () {
    test('an allowlisted route with a UUID id is accepted', () {
      final id = '2c3d4e5f-6a7b-4c8d-9e0f-1a2b3c4d5e6f';
      expect(parseNotificationLink('/chat/$id'), NotificationLink('/chat', id));
      expect(parseNotificationLink('/teams/$id')?.route, '/teams');
      expect(parseNotificationLink('/users/$id')?.id, id);
    });

    test('an id-less allowlisted route is accepted', () {
      expect(
        parseNotificationLink('/friends/requests'),
        const NotificationLink.root('/friends/requests'),
      );
      expect(parseNotificationLink('/notifications')?.route, '/notifications');
    });

    test('a malformed id is rejected rather than navigated to', () {
      expect(parseNotificationLink('/chat/not-a-uuid'), isNull);
      expect(parseNotificationLink('/chat/../../etc/passwd'), isNull);
      expect(parseNotificationLink('/chat/'), isNull);
      expect(parseNotificationLink('/teams/12345'), isNull);
    });

    test('a route outside the allowlist is rejected', () {
      expect(parseNotificationLink('/settings'), isNull);
      expect(parseNotificationLink('/rides/1'), isNull);
      expect(parseNotificationLink('/'), isNull);
      expect(parseNotificationLink(null), isNull);
      expect(parseNotificationLink(''), isNull);
    });

    test('an external URL is never treated as navigation', () {
      // A crafted link must not be able to steer the app off-route.
      expect(parseNotificationLink('https://evil.example/chat'), isNull);
      expect(parseNotificationLink('//evil.example/chat'), isNull);
    });

    test('a pending link is handed out exactly once', () {
      final pending = PendingNotificationLink();
      expect(pending.hasPending, isFalse);
      expect(pending.consume(), isNull);

      pending.set(const NotificationLink.root('/notifications'));
      expect(pending.hasPending, isTrue);
      expect(pending.consume()?.route, '/notifications');
      // Replaying a consumed link would bounce the rider back to a screen they
      // already left.
      expect(pending.consume(), isNull);
    });

    test('a newer tap replaces an unconsumed one', () {
      final pending = PendingNotificationLink();
      pending.set(const NotificationLink.root('/notifications'));
      pending.set(const NotificationLink.root('/friends/requests'));
      expect(pending.consume()?.route, '/friends/requests');
    });

    test('clear drops a pending intent', () {
      final pending = PendingNotificationLink();
      pending.set(const NotificationLink.root('/notifications'));
      pending.clear();
      expect(pending.consume(), isNull);
    });

    test('the pending-link provider ignores a malformed link', () {
      final container = ProviderContainer();
      addTearDown(container.dispose);
      container
          .read(pendingLinkProvider.notifier)
          .open('https://evil.example/chat');
      expect(container.read(pendingLinkProvider.notifier).take(), isNull);

      container.read(pendingLinkProvider.notifier).open('/notifications');
      expect(
        container.read(pendingLinkProvider.notifier).take()?.route,
        '/notifications',
      );
    });
  });

  group('repository', () {
    test('lists and parses a page', () async {
      final fake = FakeNotifications()..seed([notificationJson()]);
      final repo = NotificationRepository(
        ApiClient(
          baseUrl: 'http://test',
          client: MockClient(handler(fake)),
          accessToken: () async => 'token',
        ),
      );
      final page = await repo.list();
      expect(page.items.single.type, NotificationType.friendRequest);
      expect(page.total, 1);
    });

    test('reads the unread count from its own endpoint', () async {
      final fake = FakeNotifications()
        ..seed([notificationJson(), notificationJson(id: 'n-2')]);
      final repo = NotificationRepository(
        ApiClient(
          baseUrl: 'http://test',
          client: MockClient(handler(fake)),
          accessToken: () async => 'token',
        ),
      );
      expect(await repo.unreadCount(), 2);
      expect(fake.calls.where((c) => c.contains('unread-count')), hasLength(1));
    });

    test('marking read adopts the server answer', () async {
      final fake = FakeNotifications()..seed([notificationJson()]);
      final repo = NotificationRepository(
        ApiClient(
          baseUrl: 'http://test',
          client: MockClient(handler(fake)),
          accessToken: () async => 'token',
        ),
      );
      final updated = await repo.markRead('n-1');
      expect(updated.isRead, isTrue);
      expect(updated.isUnread, isFalse);
    });

    test('a raw token is sent but never returned to the caller', () async {
      final fake = FakeNotifications();
      final repo = NotificationRepository(
        ApiClient(
          baseUrl: 'http://test',
          client: MockClient(handler(fake)),
          accessToken: () async => 'token',
        ),
      );
      final device = await repo.registerDevice(
        deviceId: 'phone',
        token: secretToken,
        platform: PushPlatform.android,
        provider: PushProvider.fcm,
      );
      expect(fake.registeredToken, secretToken);
      // The response has nowhere to carry the token back.
      expect(device.toString(), isNot(contains(secretToken)));
      expect((await repo.devices()).toString(), isNot(contains(secretToken)));
    });

    test('registration is idempotent server-side', () async {
      final fake = FakeNotifications();
      final repo = NotificationRepository(
        ApiClient(
          baseUrl: 'http://test',
          client: MockClient(handler(fake)),
          accessToken: () async => 'token',
        ),
      );
      await repo.registerDevice(
        deviceId: 'phone',
        token: secretToken,
        platform: PushPlatform.android,
        provider: PushProvider.fcm,
      );
      await repo.registerDevice(
        deviceId: 'phone',
        token: 'rotated',
        platform: PushPlatform.android,
        provider: PushProvider.fcm,
      );
      expect(fake.registeredToken, 'rotated');
    });

    test('the notification feature never calls chat or teams', () async {
      final fake = FakeNotifications()..seed([notificationJson()]);
      final repo = NotificationRepository(
        ApiClient(
          baseUrl: 'http://test',
          client: MockClient(handler(fake)),
          accessToken: () async => 'token',
        ),
      );
      await repo.list();
      await repo.unreadCount();
      await repo.markRead('n-1');
      expect(fake.calls.any((c) => c.contains('/chat/')), isFalse);
      expect(fake.calls.any((c) => c.contains('/teams/')), isFalse);
    });
  });

  group('push registrar', () {
    test(
      'the default registrar reports unavailable and yields no token',
      () async {
        const registrar = UnconfiguredPushRegistrar();
        expect(registrar.isAvailable, isFalse);
        // Phase 8.4 ships no provider: this is the correct, expected outcome.
        expect(await registrar.register(), isNull);
      },
    );

    test('a scripted registrar yields a registration', () async {
      final registrar = ScriptedPushRegistrar(
        result: const PushRegistration(
          token: secretToken,
          deviceId: 'phone',
          platform: PushPlatform.android,
          provider: PushProvider.fcm,
        ),
      );
      final result = await registrar.register();
      expect(result?.token, secretToken);
      expect(result?.deviceId, 'phone');
    });

    test('registration state tracks device ids', () {
      const state = DeviceRegistrationState();
      expect(state.has('phone'), isFalse);
      final after = state.having('phone');
      expect(after.has('phone'), isTrue);
      expect(after.without('phone').has('phone'), isFalse);
    });

    test(
      'an unavailable registrar yields no device rather than failing',
      () async {
        final fake = FakeNotifications();
        final container = containerFor(fake);
        addTearDown(container.dispose);
        final device = await container
            .read(pushDeviceActionsProvider.notifier)
            .register(
              deviceId: 'phone',
              registrar: const UnconfiguredPushRegistrar(),
            );
        expect(device, isNull);
      },
    );

    test('an available registrar registers the device', () async {
      final fake = FakeNotifications();
      final container = containerFor(
        fake,
        registrar: ScriptedPushRegistrar(
          result: const PushRegistration(
            token: secretToken,
            deviceId: 'phone',
            platform: PushPlatform.android,
            provider: PushProvider.fcm,
          ),
        ),
      );
      addTearDown(container.dispose);
      final device = await container
          .read(pushDeviceActionsProvider.notifier)
          .register(
            deviceId: 'phone',
            registrar: ScriptedPushRegistrar(
              result: const PushRegistration(
                token: secretToken,
                deviceId: 'phone',
                platform: PushPlatform.android,
                provider: PushProvider.fcm,
              ),
            ),
          );
      expect(device?.deviceId, 'phone');
      expect(container.read(deviceRegistrationProvider).has('phone'), isTrue);
    });

    test('unregister is best-effort and never throws', () async {
      final fake = FakeNotifications();
      final container = containerFor(fake);
      addTearDown(container.dispose);
      final registrar = ScriptedPushRegistrar();
      await container
          .read(pushDeviceActionsProvider.notifier)
          .bestEffortUnregister(registrar);
      expect(registrar.unregisterCalls, 1);
    });
  });

  group('notification list notifier', () {
    test('loads the first page', () async {
      final fake = FakeNotifications()..seed([notificationJson()]);
      final container = containerFor(fake);
      addTearDown(container.dispose);
      final state = await container.read(notificationListProvider.future);
      expect(state.items, hasLength(1));
      expect(state.isEmpty, isFalse);
    });

    test('an empty inbox is not an error', () async {
      final container = containerFor(FakeNotifications());
      addTearDown(container.dispose);
      final state = await container.read(notificationListProvider.future);
      expect(state.isEmpty, isTrue);
    });

    test('mark read updates the row and the unread count', () async {
      final fake = FakeNotifications()..seed([notificationJson()]);
      final container = containerFor(fake);
      addTearDown(container.dispose);
      keepList(container);
      keepUnread(container);
      await container.read(notificationListProvider.future);
      expect(await container.read(unreadCountProvider.future), 1);

      final updated = await container
          .read(notificationListProvider.notifier)
          .markRead('n-1');
      expect(updated?.isRead, isTrue);
      final state = container.read(notificationListProvider).value!;
      expect(state.items.single.isRead, isTrue);
      expect(await container.read(unreadCountProvider.future), 0);
    });

    test('a failed mark read restores the row', () async {
      final fake = FakeNotifications()..seed([notificationJson()]);
      final container = containerFor(fake);
      addTearDown(container.dispose);
      keepList(container);
      await container.read(notificationListProvider.future);

      fake.forcedCode = 'INTERNAL_ERROR';
      fake.forcedStatus = 500;
      await expectLater(
        container.read(notificationListProvider.notifier).markRead('n-1'),
        throwsA(isA<ApiException>()),
      );
      fake.forcedCode = null;
      // A false "read" dot would be worse than no dot at all.
      final state = container.read(notificationListProvider).value!;
      expect(state.items.single.isUnread, isTrue);
    });

    test('mark all read clears the inbox', () async {
      final fake = FakeNotifications()
        ..seed([notificationJson(), notificationJson(id: 'n-2')]);
      final container = containerFor(fake);
      addTearDown(container.dispose);
      keepList(container);
      keepUnread(container);
      await container.read(notificationListProvider.future);

      final marked = await container
          .read(notificationListProvider.notifier)
          .markAllRead();
      expect(marked, 2);
      expect(await container.read(unreadCountProvider.future), 0);
    });

    test('load more appends a page', () async {
      final fake = FakeNotifications();
      for (var i = 1; i <= 25; i++) {
        fake.seed([notificationJson(id: 'n-$i')]);
      }
      final container = containerFor(fake);
      addTearDown(container.dispose);
      keepList(container);
      await container.read(notificationListProvider.future);
      var state = container.read(notificationListProvider).value!;
      expect(state.items, hasLength(20));
      expect(state.hasMore, isTrue);

      await container.read(notificationListProvider.notifier).loadMore();
      state = container.read(notificationListProvider).value!;
      expect(state.items, hasLength(25));
      expect(state.hasMore, isFalse);
    });
  });

  group('notification center', () {
    testWidgets('renders notifications with localized copy', (tester) async {
      final fake = FakeNotifications()..seed([notificationJson()]);
      final container = containerFor(fake);
      addTearDown(container.dispose);

      await tester.pumpWidget(
        harness(const NotificationCenterPage(), container: container),
      );
      await tester.pumpAndSettle();

      expect(find.text('New friend request'), findsOneWidget);
      expect(find.textContaining('sent you a friend request'), findsOneWidget);
      expect(find.byKey(const Key('notification.unread.n-1')), findsOneWidget);
      expect(find.byKey(const Key('notification.tile.n-1')), findsOneWidget);
    });

    testWidgets('shows an empty state', (tester) async {
      final container = containerFor(FakeNotifications());
      addTearDown(container.dispose);
      await tester.pumpWidget(
        harness(const NotificationCenterPage(), container: container),
      );
      await tester.pumpAndSettle();

      expect(find.byIcon(Icons.notifications_none_rounded), findsWidgets);
      expect(find.byType(NotificationTile), findsNothing);
    });

    testWidgets('a failed load offers a retry', (tester) async {
      final fake = FakeNotifications()
        ..forcedCode = 'NETWORK_ERROR'
        ..forcedStatus = 0;
      final container = containerFor(fake);
      addTearDown(container.dispose);
      await tester.pumpWidget(
        harness(const NotificationCenterPage(), container: container),
      );
      await tester.pumpAndSettle();

      expect(find.text('Retry'), findsOneWidget);
      fake.forcedCode = null;
      fake.seed([notificationJson()]);
      await tester.tap(find.text('Retry'));
      await tester.pumpAndSettle();
      expect(find.byType(NotificationTile), findsOneWidget);
    });

    testWidgets('marking read clears the unread dot', (tester) async {
      final fake = FakeNotifications()..seed([notificationJson()]);
      final container = containerFor(fake);
      addTearDown(container.dispose);
      await tester.pumpWidget(
        harness(const NotificationCenterPage(), container: container),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const Key('notification.markRead.n-1')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('notification.unread.n-1')), findsNothing);
    });

    testWidgets('mark all read is offered when something is unread', (
      tester,
    ) async {
      final fake = FakeNotifications()..seed([notificationJson()]);
      final container = containerFor(fake);
      addTearDown(container.dispose);
      await tester.pumpWidget(
        harness(const NotificationCenterPage(), container: container),
      );
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('notification.readAll')), findsOneWidget);
      await tester.tap(find.byKey(const Key('notification.readAll')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('notification.unread.n-1')), findsNothing);
    });

    testWidgets('renders in French with interpolated copy', (tester) async {
      final fake = FakeNotifications()
        ..seed([
          notificationJson(params: {'actorName': 'Karim'}),
        ]);
      final container = containerFor(fake);
      addTearDown(container.dispose);
      await tester.pumpWidget(
        harness(
          const NotificationCenterPage(),
          container: container,
          locale: 'fr',
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Notifications'), findsOneWidget);
      expect(find.textContaining('Karim vous a envoyé'), findsOneWidget);
    });

    testWidgets('renders right-to-left in Arabic with no Latin leakage', (
      tester,
    ) async {
      final fake = FakeNotifications()
        ..seed([
          notificationJson(params: {'actorName': 'كريم'}),
        ]);
      final container = containerFor(fake);
      addTearDown(container.dispose);
      await tester.pumpWidget(
        harness(
          const NotificationCenterPage(),
          container: container,
          locale: 'ar',
          direction: TextDirection.rtl,
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('الإشعارات'), findsOneWidget);
      expect(
        find.textContaining('Edit'),
        findsNothing,
        reason: 'an English affordance leaked into the Arabic screen',
      );
    });

    testWidgets('an unknown l10n key degrades to a generic sentence', (
      tester,
    ) async {
      final fake = FakeNotifications()
        ..seed([
          {
            ...notificationJson(),
            // A key this build has never heard of — a newer server.
            'l10n_key': 'notifications.type.from_the_future',
          },
        ]);
      final container = containerFor(fake);
      addTearDown(container.dispose);
      await tester.pumpWidget(
        harness(const NotificationCenterPage(), container: container),
      );
      await tester.pumpAndSettle();

      expect(find.text('New activity'), findsOneWidget);
      expect(
        find.textContaining('from_the_future'),
        findsNothing,
        reason: 'an unknown key must never be rendered as itself',
      );
    });
  });

  group('bell', () {
    testWidgets('the badge renders only when something is unread', (
      tester,
    ) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: NotificationBell(unreadCount: 3, onTap: () {})),
        ),
      );
      expect(find.byKey(const Key('notification.badge')), findsOneWidget);
      expect(find.text('3'), findsOneWidget);

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: NotificationBell(unreadCount: 0, onTap: () {})),
        ),
      );
      expect(find.byKey(const Key('notification.badge')), findsNothing);
    });

    testWidgets('a large count is capped at 99+', (tester) async {
      await tester.pumpWidget(
        MaterialApp(home: Scaffold(body: NotificationBadge(count: 250))),
      );
      expect(find.text('99+'), findsOneWidget);
    });

    test('the home page bell points at the notification center', () {
      final source = File(
        'lib/features/home/home_page.dart',
      ).readAsStringSync();
      // It used to navigate to '/friends', which became misleading the moment a
      // real notification center existed.
      expect(source.contains("context.go('/notifications')"), isTrue);
      expect(source.contains('NotificationBell('), isTrue);
      expect(source.contains("context.go('/friends')"), isFalse);
    });
  });

  group('error mapping', () {
    AppLocalizations t(String lang) => AppLocalizations(Locale(lang));

    test('notification errors map to localized sentences', () {
      final en = t('en');
      expect(
        friendlyNotificationError(
          en,
          ApiException(404, 'NOTIFICATION_NOT_FOUND', 'x'),
        ),
        isNotEmpty,
      );
      expect(
        friendlyNotificationError(en, ApiException(429, 'RATE_LIMITED', 'x')),
        contains('Too many'),
      );
      expect(
        friendlyNotificationError(en, ApiException(0, 'NETWORK_ERROR', 'x')),
        contains('No connection'),
      );
    });

    test('a non-API exception degrades to the generic sentence', () {
      expect(
        friendlyNotificationError(t('en'), StateError('boom')),
        'Something went wrong. Try again.',
      );
    });

    test('an unknown error never leaks a raw code', () {
      final message = friendlyNotificationError(
        t('en'),
        ApiException(418, 'NOTIF_TEAPOT', 'raw internal detail'),
      );
      expect(message.contains('TEAPOT'), isFalse);
      expect(message.contains('raw'), isFalse);
    });

    test('every notification error sentence is localized in fr and ar', () {
      const codes = [
        'NOTIFICATION_NOT_FOUND',
        'PUSH_DEVICE_NOT_FOUND',
        'PUSH_DEVICE_CONFLICT',
        'PUSH_DEVICE_INVALID',
        'PUSH_DEVICE_PLATFORM_UNSUPPORTED',
      ];
      for (final lang in ['en', 'fr', 'ar']) {
        final loc = t(lang);
        for (final code in codes) {
          final message = friendlyNotificationError(
            loc,
            ApiException(404, code, 'x'),
          );
          expect(message, isNotEmpty);
          expect(message.contains(code), isFalse, reason: '$code in $lang');
        }
      }
    });
  });

  group('localization', () {
    const notificationKeys = [
      'notifications.title',
      'notifications.none',
      'notifications.noneHint',
      'notifications.unreadBadge',
      'notifications.markAllRead',
      'notifications.allRead',
      'notifications.loadMore',
      'notifications.error.load',
      'notifications.error.read',
      'notifications.action.read',
      'notifications.action.open',
      'notifications.type.friend_request.title',
      'notifications.type.friend_request.body',
      'notifications.type.friend_request_accepted.title',
      'notifications.type.friend_request_accepted.body',
      'notifications.type.team_invitation.title',
      'notifications.type.team_invitation.body',
      'notifications.type.team_join_request.title',
      'notifications.type.team_join_request.body',
      'notifications.type.team_member_removed.title',
      'notifications.type.team_member_removed.body',
      'notifications.type.team_archived.title',
      'notifications.type.team_archived.body',
      'notifications.type.chat_message.title',
      'notifications.type.chat_message.body',
      'notifications.type.chat_message_team.title',
      'notifications.type.chat_message_team.body',
      'notifications.type.system.title',
      'notifications.type.system.body',
      'notifications.generic.title',
      'notifications.generic.body',
      'notifications.settings.title',
      'notifications.settings.devices',
      'notifications.settings.devicesHint',
      'notifications.settings.noDevices',
      'notifications.settings.current',
      'notifications.settings.registered',
      'notifications.settings.unavailable',
      'notifications.settings.retry',
      'notifications.time.justNow',
      'notifications.time.minutes',
      'notifications.time.hours',
    ];

    test('the notification concept set exists in all three languages', () {
      for (final lang in ['en', 'fr', 'ar']) {
        final loc = AppLocalizations(Locale(lang));
        for (final key in notificationKeys) {
          expect(loc.get(key), isNotEmpty, reason: '$key missing in $lang');
          expect(
            loc.get(key),
            isNot(key),
            reason: '$key has no translation in $lang',
          );
        }
      }
    });

    test('interpolation substitutes every placeholder', () {
      final en = AppLocalizations(const Locale('en'));
      expect(
        en.getWith('notifications.type.friend_request.body', {
          'actorName': 'Karim',
        }),
        'Karim sent you a friend request.',
      );
      expect(
        en.getWith('notifications.time.minutes', {'count': '5'}),
        '5 min ago',
      );
    });

    test('interpolation leaves an unmatched placeholder in place', () {
      final en = AppLocalizations(const Locale('en'));
      // Better a visible placeholder than a sentence with a hole in it.
      expect(
        en.getWith('notifications.type.team_invitation.body', {
          'actorName': 'A',
        }),
        contains('{teamName}'),
      );
    });

    test('interpolation does not re-expand an argument containing braces', () {
      final en = AppLocalizations(const Locale('en'));
      final out = en.getWith('notifications.type.chat_message.body', {
        'actorName': '{actorName}',
      });
      // A single pass, not a loop: an argument cannot inject a placeholder.
      expect(out, '{actorName} sent you a message.');
    });

    test('a missing key no longer crashes the app', () {
      final en = AppLocalizations(const Locale('en'));
      // The old implementation ended in a non-null assertion, so a key absent
      // from every locale threw instead of degrading.
      expect(() => en.get('nope.not.a.key'), returnsNormally);
      expect(en.get('nope.not.a.key'), 'nope.not.a.key');
      expect(en.has('nope.not.a.key'), isFalse);
      expect(en.has('chat.inbox'), isTrue);
    });

    test('a key missing in one locale falls back to English', () {
      const partial = AppLocalizations(Locale('fr'));
      expect(partial.get('chat.inbox'), isNotEmpty);
    });

    test('Arabic notification prose leaks no Latin words', () {
      final ar = AppLocalizations(const Locale('ar'));
      // Two things legitimately contain Latin and must stay that way:
      //  * `{placeholder}` — a substitution token, not prose; the letters are
      //    required for the argument to be substituted at all.
      //  * the brand name `CycleCoach` — a proper noun is not transliterated
      //    into Arabic any more than "Nike" would be.
      final placeholder = RegExp(r'\{[a-zA-Z]+\}');
      const brand = 'CycleCoach';
      for (final key in notificationKeys) {
        final text = ar
            .get(key)
            .replaceAll(placeholder, '')
            .replaceAll(brand, '');
        expect(
          RegExp(r'[A-Za-z]').hasMatch(text),
          isFalse,
          reason: '$key: ${ar.get(key)}',
        );
      }
    });

    test('French notification prose leaks no Arabic', () {
      final fr = AppLocalizations(const Locale('fr'));
      final placeholder = RegExp(r'\{[a-zA-Z]+\}');
      for (final key in notificationKeys) {
        final text = fr.get(key).replaceAll(placeholder, '');
        expect(
          RegExp(r'[؀-ۿ]').hasMatch(text),
          isFalse,
          reason: '$key: ${fr.get(key)}',
        );
      }
    });

    test('every notification type has a stable, translated key', () {
      for (final type in NotificationType.values) {
        final key = 'notifications.type.${type.wire}';
        expect(key, startsWith('notifications.type.'));
        for (final lang in ['en', 'fr', 'ar']) {
          final loc = AppLocalizations(Locale(lang));
          expect(loc.get('$key.title'), isNotEmpty, reason: '$key in $lang');
          expect(loc.get('$key.body'), isNotEmpty, reason: '$key in $lang');
        }
      }
    });
  });

  group('routing', () {
    test('notification routes are registered', () {
      final source = File(
        'lib/core/routing/app_router.dart',
      ).readAsStringSync();
      expect(source.contains("path: '/notifications'"), isTrue);
      expect(source.contains("path: '/notifications/settings'"), isTrue);
      expect(source.contains('NotificationCenterPage'), isTrue);
      expect(source.contains('NotificationSettingsPage'), isTrue);

      final placeholderBlock = source.substring(
        source.indexOf('for (final p in ['),
      );
      expect(
        placeholderBlock.contains("'/notifications'"),
        isFalse,
        reason: '/notifications must not be a placeholder any more',
      );

      for (final kept in [
        "'/login'",
        "'/home'",
        "'/friends'",
        "'/teams'",
        "'/chat'",
        "'/routes'",
        "'/training'",
        "'/coach'",
        "'/settings'",
        "'/group-rides'",
      ]) {
        expect(source.contains(kept), isTrue, reason: '$kept was removed');
      }
    });

    test('the cold-start replay is wired into the app shell', () {
      final source = File('lib/main.dart').readAsStringSync();
      expect(source.contains('pendingLinkReplayProvider'), isTrue);
    });
  });
}
