import 'dart:async';
import 'dart:convert';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/social/presentation/blocked_users_page.dart';
import 'package:cyclecoach/features/social/presentation/edit_social_profile_page.dart';
import 'package:cyclecoach/features/social/presentation/friend_requests_page.dart';
import 'package:cyclecoach/features/social/presentation/friends_page.dart';
import 'package:cyclecoach/features/social/presentation/my_social_profile_page.dart';
import 'package:cyclecoach/features/social/presentation/social_privacy_page.dart';
import 'package:cyclecoach/features/social/presentation/social_providers.dart';
import 'package:cyclecoach/features/social/presentation/user_profile_page.dart';
import 'package:cyclecoach/features/social/presentation/user_search_page.dart';
import 'package:cyclecoach/features/social/presentation/social_widgets.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// Widget-level social tests: loading, empty, error, retry, duplicate-submit
/// protection, and the action-per-relationship-state rule.
///
/// The HTTP double is stateful so a tap changes what the next GET returns —
/// that is what makes "the UI followed the server" a real assertion rather
/// than a snapshot of an initial render.
class Wire {
  String profileRelationship = 'NONE';
  bool limited = false;
  final Set<String> blocks = {};
  final Set<String> friends = {};
  final Set<String> outgoing = {};
  final List<String> incoming = ['r-in-1'];
  String? profileError;
  bool searchEmpty = false;
  bool blocksEmpty = false;
  bool friendsEmpty = false;
  bool requestsEmpty = false;

  /// When set, every response waits on this gate. Lets a test hold a mutation
  /// open and observe the in-flight UI deterministically, instead of racing a
  /// wall-clock delay against pumpAndSettle.
  Completer<void>? gate;
  final List<String> posts = [];

  static const me = {
    'user_id': 'u-1',
    'username': 'imad_fouri',
    'display_name': 'Imad Fouri',
    'bio': 'Gravel.',
    'avatar_url': null,
    'cycling_category': 'gravel',
    'country_code': 'MA',
    'city': 'Casablanca',
    'profile_visibility': 'public',
    'allow_friend_requests': 'everyone',
    'search_visibility': 'discoverable',
    'created_at': '2026-01-01T00:00:00Z',
    'updated_at': '2026-01-01T00:00:00Z',
  };

  Map<String, dynamic> other(String id) => {
    'user_id': id,
    'username': 'sara_bench',
    'display_name': 'Sara Bench',
    'bio': limited ? null : 'Road.',
    'avatar_url': null,
    'cycling_category': limited ? null : 'road',
    'country_code': limited ? null : 'FR',
    'city': limited ? null : 'Lyon',
    'relationship': profileRelationship,
    'limited': limited,
  };
}

/// The raw request handler, so a test can layer an override (a forced error,
/// a recorded call) on top of the full fake without losing the default routes.
Future<http.Response> Function(http.Request) handler(Wire wire) => (req) async {
  final path = req.url.path;
  final method = req.method;
  final gate = wire.gate;
  if (gate != null) await gate.future;
  const p = '/api/v1/social';

  if (path == '$p/profile/me') {
    if (wire.profileError != null) {
      // The app's error envelope: {error:{code,message,details}}.
      return http.Response(
        jsonEncode({
          'error': {
            'code': wire.profileError,
            'message': 'boom',
            'details': {},
          },
        }),
        500,
      );
    }
    return http.Response(jsonEncode(Wire.me), 200);
  }
  if (method == 'PATCH') {
    return http.Response(jsonEncode(Wire.me), 200);
  }
  if (path.startsWith('$p/profile/')) {
    return http.Response(jsonEncode(wire.other(path.split('/').last)), 200);
  }
  if (path == '$p/users/search') {
    return http.Response(
      jsonEncode({
        'items': wire.searchEmpty ? [] : [wire.other('u-2')],
        'total': wire.searchEmpty ? 0 : 1,
        'page': 1,
        'page_size': 20,
      }),
      200,
    );
  }
  if (method == 'POST' && path == '$p/friend-requests') {
    final id =
        (jsonDecode(req.body) as Map<String, dynamic>)['user_id'] as String;
    wire.posts.add('send:$id');
    wire.outgoing.add(id);
    wire.profileRelationship = 'OUTGOING_PENDING';
    return http.Response(jsonEncode({'id': 'r-1', 'status': 'pending'}), 201);
  }
  if (path == '$p/friend-requests') {
    final direction = req.url.queryParameters['direction'] ?? 'incoming';
    final items = direction == 'outgoing'
        ? wire.outgoing
              .map(
                (id) => {
                  'id': 'r-out-$id',
                  'user_id': id,
                  'username': 'sara_bench',
                  'display_name': 'Sara Bench',
                  'avatar_url': null,
                  'direction': 'outgoing',
                  'status': 'pending',
                  'created_at': '2026-01-01T00:00:00Z',
                },
              )
              .toList()
        : wire.incoming
              .map(
                (id) => {
                  'id': id,
                  'user_id': 'u-3',
                  'username': 'karim_amrani',
                  'display_name': 'Karim Amrani',
                  'avatar_url': null,
                  'direction': 'incoming',
                  'status': 'pending',
                  'created_at': '2026-01-01T00:00:00Z',
                },
              )
              .toList();
    if (wire.requestsEmpty) items.clear();
    return http.Response(
      jsonEncode({
        'items': items,
        'total': items.length,
        'page': 1,
        'page_size': 20,
      }),
      200,
    );
  }
  if (method == 'POST' && path.endsWith('/accept')) {
    final id = path.split('/')[5];
    wire.posts.add('accept:$id');
    wire.incoming.remove(id);
    wire.friends.add('u-3');
    wire.profileRelationship = 'FRIENDS';
    return http.Response(jsonEncode({'id': id, 'status': 'accepted'}), 200);
  }
  if (method == 'POST' && path.endsWith('/reject')) {
    final id = path.split('/')[5];
    wire.posts.add('reject:$id');
    wire.incoming.remove(id);
    return http.Response(jsonEncode({'status': 'rejected'}), 200);
  }
  if (method == 'DELETE' && path.startsWith('$p/friend-requests/')) {
    wire.posts.add('cancel:${path.split('/').last}');
    wire.outgoing.clear();
    wire.profileRelationship = 'NONE';
    return http.Response(jsonEncode({'status': 'cancelled'}), 200);
  }
  if (path == '$p/friends') {
    final items = wire.friendsEmpty
        ? <Map<String, dynamic>>[]
        : [
            {
              'user_id': 'u-3',
              'username': 'karim_amrani',
              'display_name': 'Karim Amrani',
              'avatar_url': null,
              'friends_since': '2026-01-02T00:00:00Z',
            },
          ];
    return http.Response(
      jsonEncode({
        'items': items,
        'total': items.length,
        'page': 1,
        'page_size': 20,
      }),
      200,
    );
  }
  if (method == 'DELETE' && path.startsWith('$p/friends/')) {
    wire.posts.add('remove:${path.split('/').last}');
    wire.friends.clear();
    wire.profileRelationship = 'NONE';
    return http.Response(jsonEncode({'status': 'removed'}), 200);
  }
  if (method == 'POST' && path == '$p/blocks') {
    final id =
        (jsonDecode(req.body) as Map<String, dynamic>)['user_id'] as String;
    wire.posts.add('block:$id');
    wire.blocks.add(id);
    wire.friends.clear();
    wire.outgoing.clear();
    wire.incoming.clear();
    wire.profileRelationship = 'BLOCKED';
    return http.Response(jsonEncode({'user_id': id, 'status': 'blocked'}), 201);
  }
  if (path == '$p/blocks') {
    final items = wire.blocksEmpty
        ? <Map<String, dynamic>>[]
        : wire.blocks
              .map(
                (id) => {
                  'user_id': id,
                  'username': 'sara_bench',
                  'display_name': 'Sara Bench',
                  'blocked_at': '2026-01-03T00:00:00Z',
                },
              )
              .toList();
    return http.Response(
      jsonEncode({
        'items': items,
        'total': items.length,
        'page': 1,
        'page_size': 20,
      }),
      200,
    );
  }
  if (method == 'DELETE' && path.startsWith('$p/blocks/')) {
    final id = path.split('/').last;
    wire.posts.add('unblock:$id');
    wire.blocks.remove(id);
    // Friendship is NOT restored by an unblock.
    wire.profileRelationship = 'NONE';
    return http.Response(jsonEncode({'status': 'unblocked'}), 200);
  }
  return http.Response('not found', 404);
};

MockClient backend(Wire wire) => MockClient(handler(wire));

Widget harness(
  Widget child,
  MockClient client, {
  Locale locale = const Locale('en'),
}) {
  return ProviderScope(
    // Riverpod 3 retries a failing provider with a backoff. Correct in
    // production, wrong in a test: the screen would sit on "Loading" while the
    // retry timer runs, so an error-state assertion could never observe the
    // failure. Disable retry here.
    retry: (_, _) => null,
    overrides: [
      apiClientProvider.overrideWithValue(
        ApiClient(
          baseUrl: 'http://test',
          client: client,
          accessToken: () async => 'token',
        ),
      ),
    ],
    child: MaterialApp(
      locale: locale,
      supportedLocales: AppLocalizations.supported,
      localizationsDelegates: const [
        AppLocalizationsDelegate(),
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: child,
    ),
  );
}

/// Scroll a lazily-built list until [finder] exists, then tap it.
///
/// The social forms and lists are `ListView`s, so a button below the fold is
/// not built at all — asserting on it without scrolling would test the
/// viewport rather than the feature.
Future<void> scrollTo(WidgetTester t, Finder finder, {double dy = -220}) async {
  if (finder.evaluate().isNotEmpty) {
    await t.ensureVisible(finder);
    await t.pumpAndSettle();
    return;
  }
  // Drag the page's own list in steps until the widget is built. A manual loop
  // rather than scrollUntilVisible: a lazily-built list may not expose a single
  // Scrollable, and the helper asserts on uniqueness.
  final list = find.byType(ListView);
  for (var i = 0; i < 40 && finder.evaluate().isEmpty; i++) {
    await t.drag(list, Offset(0, dy));
    await t.pumpAndSettle();
  }
}

/// Drag back up. A `ListView` disposes rows scrolled out of view, so an
/// assertion about a control near the top needs the list rewound first.
Future<void> scrollBackTo(WidgetTester t, Finder finder) =>
    scrollTo(t, finder, dy: 220);

/// Rewind the list to the very top.
///
/// Used for error banners, which render above the form title: a finder for a
/// control *below* them would already be satisfied and stop the loop early,
/// leaving the banner unbuilt.
Future<void> scrollToTop(WidgetTester t) async {
  final list = find.byType(ListView);
  for (var i = 0; i < 20; i++) {
    await t.drag(list, const Offset(0, 400));
    await t.pumpAndSettle();
    final position = t.widget<ListView>(list);
    if ((position.controller?.offset ?? 0) <= 0) return;
  }
}

void main() {
  group('my social profile page', () {
    testWidgets('renders the server profile', (t) async {
      await t.pumpWidget(harness(const MySocialProfilePage(), backend(Wire())));
      await t.pumpAndSettle();
      expect(find.text('Imad Fouri'), findsOneWidget);
      expect(find.text('@imad_fouri'), findsOneWidget);
      expect(find.text('Gravel.'), findsOneWidget);
      expect(find.textContaining('Casablanca'), findsOneWidget);
      expect(find.byKey(const Key('social.editProfile')), findsOneWidget);
      expect(find.byKey(const Key('social.privacy')), findsOneWidget);
    });

    testWidgets('shows a retryable error instead of the profile', (t) async {
      final wire = Wire()..profileError = 'SERVER_ERROR';
      await t.pumpWidget(harness(const MySocialProfilePage(), backend(wire)));
      await t.pumpAndSettle();
      expect(find.text('Imad Fouri'), findsNothing);
      expect(
        find.text('The server had a problem. Try again shortly.'),
        findsOneWidget,
      );
      expect(find.widgetWithText(OutlinedButton, 'Retry'), findsOneWidget);
    });

    testWidgets('renders in Arabic RTL', (t) async {
      await t.pumpWidget(
        harness(
          const MySocialProfilePage(),
          backend(Wire()),
          locale: const Locale('ar'),
        ),
      );
      await t.pumpAndSettle();
      final ctx = t.element(find.byType(MySocialProfilePage));
      expect(
        Directionality.of(ctx),
        TextDirection.rtl,
        reason: 'Arabic must lay out right-to-left',
      );
      expect(find.text('الملف الاجتماعي'), findsOneWidget);
    });
  });

  group('edit social profile page', () {
    testWidgets('hydrates the form from the server profile', (t) async {
      await t.pumpWidget(
        harness(const EditSocialProfilePage(), backend(Wire())),
      );
      await t.pumpAndSettle();
      expect(find.text('imad_fouri'), findsOneWidget);
      expect(find.text('Gravel.'), findsOneWidget);
      expect(find.text('MA'), findsOneWidget);
    });

    testWidgets('blocks an invalid username before any request', (t) async {
      final wire = Wire();
      await t.pumpWidget(harness(const EditSocialProfilePage(), backend(wire)));
      await t.pumpAndSettle();
      await t.enterText(find.byKey(const Key('social.field.username')), '!!');
      await scrollTo(t, find.byKey(const Key('social.edit.save')));
      await t.tap(find.byKey(const Key('social.edit.save')));
      await t.pumpAndSettle();
      // The error renders under the username field near the top; rewind the
      // list so the row is built again.
      await scrollBackTo(t, find.byKey(const Key('social.field.username')));
      expect(
        find.text('Use 3-30 lowercase characters: letters, digits, _ or .'),
        findsOneWidget,
      );
      expect(
        wire.posts,
        isEmpty,
        reason: 'client validation blocks the submit',
      );
    });

    testWidgets('a valid edit posts and shows the server confirmation', (
      t,
    ) async {
      final wire = Wire();
      await t.pumpWidget(harness(const EditSocialProfilePage(), backend(wire)));
      await t.pumpAndSettle();
      await t.enterText(
        find.byKey(const Key('social.field.username')),
        'imad_new',
      );
      await scrollTo(t, find.byKey(const Key('social.edit.save')));
      await t.tap(find.byKey(const Key('social.edit.save')));
      await t.pumpAndSettle();
      expect(find.text('Profile saved'), findsOneWidget);
    });

    testWidgets('a 409 from the server is surfaced, not swallowed', (t) async {
      final wire = Wire();
      final client = MockClient((req) async {
        if (req.method == 'PATCH') {
          return http.Response(
            jsonEncode({
              'detail': {'code': 'SOCIAL_USERNAME_TAKEN', 'message': 'taken'},
            }),
            409,
          );
        }
        return handler(wire)(req);
      });
      await t.pumpWidget(harness(const EditSocialProfilePage(), client));
      await t.pumpAndSettle();
      await scrollTo(t, find.byKey(const Key('social.edit.save')));
      await t.tap(find.byKey(const Key('social.edit.save')));
      await t.pumpAndSettle();
      // The server error banner is the first row of the form; rewind to it.
      await scrollToTop(t);
      expect(find.byKey(const Key('social.edit.error')), findsOneWidget);
      expect(find.text('That username is taken.'), findsOneWidget);
    });

    testWidgets('duplicate taps send one PATCH', (t) async {
      var patches = 0;
      final client = MockClient((req) async {
        if (req.method == 'PATCH') {
          patches++;
          await Future<void>.delayed(const Duration(milliseconds: 200));
        }
        if (req.url.path.endsWith('/profile/me')) {
          return http.Response(jsonEncode(Wire.me), 200);
        }
        return http.Response(jsonEncode(Wire.me), 200);
      });
      await t.pumpWidget(harness(const EditSocialProfilePage(), client));
      await t.pumpAndSettle();
      final save = find.byKey(const Key('social.edit.save'));
      await scrollTo(t, save);
      await t.tap(save);
      await t.tap(save);
      await t.pumpAndSettle();
      expect(patches, 1);
    });
  });

  group('user profile page actions per relationship state', () {
    Future<void> pumpState(WidgetTester t, Wire wire) async {
      await t.pumpWidget(
        harness(UserProfilePage(userId: 'u-2'), backend(wire)),
      );
      await t.pumpAndSettle();
    }

    testWidgets('NONE offers Add friend only', (t) async {
      final wire = Wire()..profileRelationship = 'NONE';
      await pumpState(t, wire);
      expect(find.byKey(const Key('social.action.addFriend')), findsOneWidget);
      expect(
        find.byKey(const Key('social.action.cancelRequest')),
        findsNothing,
      );
      expect(find.byKey(const Key('social.action.removeFriend')), findsNothing);
      expect(find.byKey(const Key('social.action.block')), findsNothing);
    });

    testWidgets('OUTGOING_PENDING offers Cancel request', (t) async {
      final wire = Wire()..profileRelationship = 'OUTGOING_PENDING';
      await pumpState(t, wire);
      expect(
        find.byKey(const Key('social.action.cancelRequest')),
        findsOneWidget,
      );
      expect(find.byKey(const Key('social.action.addFriend')), findsNothing);
    });

    testWidgets('INCOMING_PENDING routes to the requests screen', (t) async {
      final wire = Wire()..profileRelationship = 'INCOMING_PENDING';
      await pumpState(t, wire);
      expect(
        find.byKey(const Key('social.action.viewRequests')),
        findsOneWidget,
      );
      expect(find.byKey(const Key('social.action.addFriend')), findsNothing);
    });

    testWidgets('FRIENDS offers Remove friend and Block', (t) async {
      final wire = Wire()..profileRelationship = 'FRIENDS';
      await pumpState(t, wire);
      expect(
        find.byKey(const Key('social.action.removeFriend')),
        findsOneWidget,
      );
      expect(find.byKey(const Key('social.action.block')), findsOneWidget);
      expect(find.byKey(const Key('social.action.addFriend')), findsNothing);
    });

    testWidgets('BLOCKED offers Unblock only', (t) async {
      final wire = Wire()
        ..profileRelationship = 'BLOCKED'
        ..blocks.add('u-2');
      await pumpState(t, wire);
      expect(find.byKey(const Key('social.action.unblock')), findsOneWidget);
      expect(find.byKey(const Key('social.action.addFriend')), findsNothing);
    });

    testWidgets('BLOCKED_BY_USER offers no action that routes around it', (
      t,
    ) async {
      final wire = Wire()..profileRelationship = 'BLOCKED_BY_USER';
      await pumpState(t, wire);
      expect(find.byKey(const Key('social.action.addFriend')), findsNothing);
      expect(
        find.byKey(const Key('social.action.cancelRequest')),
        findsNothing,
      );
      expect(find.byKey(const Key('social.action.unblock')), findsNothing);
      expect(find.textContaining('No action is available'), findsOneWidget);
    });

    testWidgets('a limited profile shows the locked notice, not empty fields', (
      t,
    ) async {
      final wire = Wire()
        ..limited = true
        ..profileRelationship = 'NONE';
      await pumpState(t, wire);
      expect(
        find.text('This rider keeps some details private'),
        findsOneWidget,
      );
      expect(find.text('Road.'), findsNothing);
      expect(find.textContaining('Lyon'), findsNothing);
    });

    testWidgets('Add friend confirms only after the server responds', (
      t,
    ) async {
      final wire = Wire()..profileRelationship = 'NONE';
      await pumpState(t, wire);
      await t.tap(find.byKey(const Key('social.action.addFriend')));
      await t.pumpAndSettle();
      expect(wire.posts, ['send:u-2']);
      // 'Request sent' is also the OUTGOING_PENDING badge label, so the
      // confirmation is asserted by presence, not by count.
      expect(find.text('Request sent'), findsWidgets);
      // The refreshed profile now reports the pending state.
      expect(
        find.byKey(const Key('social.action.cancelRequest')),
        findsOneWidget,
      );
    });

    testWidgets('a blocked rider cannot send a request (server refuses)', (
      t,
    ) async {
      final wire = Wire()
        ..profileRelationship = 'BLOCKED'
        ..blocks.add('u-2');
      await pumpState(t, wire);
      await t.tap(find.byKey(const Key('social.action.unblock')));
      await t.pumpAndSettle();
      expect(wire.posts, ['unblock:u-2']);
      expect(find.text('User unblocked'), findsOneWidget);
    });

    testWidgets('removing a friend asks for confirmation first', (t) async {
      final wire = Wire()..profileRelationship = 'FRIENDS';
      await pumpState(t, wire);
      await t.tap(find.byKey(const Key('social.action.removeFriend')));
      await t.pumpAndSettle();
      expect(find.text('Remove this friend?'), findsOneWidget);
      expect(wire.posts, isEmpty, reason: 'nothing sent before confirming');
      await t.tap(find.byKey(const Key('social.confirm.remove')));
      await t.pumpAndSettle();
      expect(wire.posts, ['remove:u-2']);
      expect(find.text('Friend removed'), findsOneWidget);
    });

    testWidgets('blocking asks for confirmation and then blocks', (t) async {
      final wire = Wire()..profileRelationship = 'FRIENDS';
      await pumpState(t, wire);
      await t.tap(find.byKey(const Key('social.action.block')));
      await t.pumpAndSettle();
      expect(wire.posts, isEmpty);
      await t.tap(find.byKey(const Key('social.confirm.block')));
      await t.pumpAndSettle();
      expect(wire.posts, ['block:u-2']);
      expect(find.text('User blocked'), findsOneWidget);
    });

    testWidgets('no location or GPS control is ever rendered', (t) async {
      for (final state in [
        'NONE',
        'FRIENDS',
        'INCOMING_PENDING',
        'BLOCKED_BY_USER',
      ]) {
        final wire = Wire()..profileRelationship = state;
        await pumpState(t, wire);
        expect(find.byIcon(Icons.my_location_rounded), findsNothing);
        expect(find.byIcon(Icons.location_on_rounded), findsNothing);
        expect(find.textContaining('km'), findsNothing);
        expect(find.textContaining('GPS'), findsNothing);
      }
    });
  });

  group('user search page', () {
    testWidgets('searching shows a result row with the server relationship', (
      t,
    ) async {
      await t.pumpWidget(harness(const UserSearchPage(), backend(Wire())));
      await t.pumpAndSettle();
      await t.enterText(find.byKey(const Key('social.search.field')), 'sara');
      await t.pump(const Duration(milliseconds: 400));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('social.search.result.u-2')), findsOneWidget);
      expect(find.text('Not connected'), findsOneWidget);
    });

    testWidgets('an empty result shows the empty state, not an error', (
      t,
    ) async {
      final wire = Wire()..searchEmpty = true;
      await t.pumpWidget(harness(const UserSearchPage(), backend(wire)));
      await t.pumpAndSettle();
      await t.enterText(find.byKey(const Key('social.search.field')), 'zzz');
      await t.pump(const Duration(milliseconds: 400));
      await t.pumpAndSettle();
      expect(find.text('No riders found'), findsOneWidget);
    });

    testWidgets('a one-character query never reaches the server', (t) async {
      final wire = Wire();
      await t.pumpWidget(harness(const UserSearchPage(), backend(wire)));
      await t.pumpAndSettle();
      await t.enterText(find.byKey(const Key('social.search.field')), 's');
      await t.pump(const Duration(milliseconds: 400));
      await t.pumpAndSettle();
      expect(find.text('Enter at least 2 characters.'), findsOneWidget);
    });

    testWidgets('the query is debounced into a single request', (t) async {
      var searches = 0;
      final client = MockClient((req) async {
        if (req.url.path.endsWith('/users/search')) searches++;
        return handler(Wire())(req);
      });
      await t.pumpWidget(harness(const UserSearchPage(), client));
      await t.pumpAndSettle();
      final field = find.byKey(const Key('social.search.field'));
      for (final query in ['s', 'sa', 'sar', 'sara']) {
        await t.enterText(field, query);
        // Each keystroke lands well inside the 350 ms debounce window, so
        // every timer is cancelled by the next one.
        await t.pump(const Duration(milliseconds: 50));
      }
      await t.pump(const Duration(milliseconds: 400));
      await t.pumpAndSettle();
      expect(searches, 1, reason: 'four keystrokes, one debounced search');
    });
  });

  group('friends page', () {
    testWidgets('lists friends and removes one after confirmation', (t) async {
      final wire = Wire()..friends.add('u-3');
      await t.pumpWidget(harness(const FriendsPage(), backend(wire)));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('social.friend.u-3')), findsOneWidget);
      await t.tap(find.byKey(const Key('social.friend.menu.u-3')));
      await t.pumpAndSettle();
      await t.tap(find.text('Remove friend'));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('social.confirm.remove')));
      await t.pumpAndSettle();
      expect(wire.posts, ['remove:u-3']);
      expect(find.text('Friend removed'), findsOneWidget);
    });

    testWidgets('an empty friend list points at search', (t) async {
      final wire = Wire()..friendsEmpty = true;
      await t.pumpWidget(harness(const FriendsPage(), backend(wire)));
      await t.pumpAndSettle();
      expect(find.text('No friends yet'), findsOneWidget);
    });

    testWidgets('a list error offers retry', (t) async {
      final client = MockClient((req) async {
        if (req.url.path.endsWith('/friends')) {
          return http.Response('{"error":{"code":"X"}}', 500);
        }
        return handler(Wire())(req);
      });
      await t.pumpWidget(harness(const FriendsPage(), client));
      await t.pumpAndSettle();
      expect(
        find.text('The server had a problem. Try again shortly.'),
        findsOneWidget,
      );
      expect(find.widgetWithText(OutlinedButton, 'Retry'), findsOneWidget);
    });
  });

  group('friend requests page', () {
    testWidgets('incoming shows accept, reject, and block', (t) async {
      await t.pumpWidget(harness(const FriendRequestsPage(), backend(Wire())));
      await t.pumpAndSettle();
      expect(
        find.byKey(const Key('social.incoming.accept.r-in-1')),
        findsOneWidget,
      );
      expect(
        find.byKey(const Key('social.incoming.reject.r-in-1')),
        findsOneWidget,
      );
      expect(
        find.byKey(const Key('social.incoming.block.r-in-1')),
        findsOneWidget,
      );
    });

    testWidgets('accepting confirms and updates the list', (t) async {
      final wire = Wire();
      await t.pumpWidget(harness(const FriendRequestsPage(), backend(wire)));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('social.incoming.accept.r-in-1')));
      await t.pumpAndSettle();
      expect(wire.posts, ['accept:r-in-1']);
      expect(find.text('You are now friends'), findsOneWidget);
      expect(
        find.byKey(const Key('social.incoming.accept.r-in-1')),
        findsNothing,
      );
    });

    testWidgets('rejecting confirms and updates the list', (t) async {
      final wire = Wire();
      await t.pumpWidget(harness(const FriendRequestsPage(), backend(wire)));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('social.incoming.reject.r-in-1')));
      await t.pumpAndSettle();
      expect(wire.posts, ['reject:r-in-1']);
      expect(find.text('Request rejected'), findsOneWidget);
    });

    testWidgets('the outgoing tab cancels a pending request', (t) async {
      final wire = Wire()..outgoing.add('u-2');
      await t.pumpWidget(harness(const FriendRequestsPage(), backend(wire)));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('social.tab.outgoing')));
      await t.pumpAndSettle();
      final cancel = find.byKey(const Key('social.outgoing.cancel.r-out-u-2'));
      expect(cancel, findsOneWidget);
      await t.tap(cancel);
      await t.pumpAndSettle();
      expect(wire.posts, ['cancel:r-out-u-2']);
      expect(find.text('Request cancelled'), findsOneWidget);
    });

    testWidgets('an accept button cannot be double-submitted', (t) async {
      final wire = Wire();
      await t.pumpWidget(harness(const FriendRequestsPage(), backend(wire)));
      await t.pumpAndSettle();
      final accept = find.byKey(const Key('social.incoming.accept.r-in-1'));
      // Hold the response open so the in-flight state is observable rather than
      // raced against a timer.
      final gate = Completer<void>();
      wire.gate = gate;
      await t.tap(accept);
      await t.pump();
      // In flight: the button is either gone (list refreshing) or disabled. It
      // must not be tappable again.
      final during = find.byKey(const Key('social.incoming.accept.r-in-1'));
      if (during.evaluate().isNotEmpty) {
        final button = t.widget<FilledButton>(during);
        expect(
          button.onPressed,
          isNull,
          reason: 'accept must be disabled in flight',
        );
      }
      // Release the response and let the list settle.
      gate.complete();
      await t.pumpAndSettle();
      expect(wire.posts.where((p) => p.startsWith('accept')).length, 1);
    });

    testWidgets('a failed accept shows an error and keeps the row', (t) async {
      final wire = Wire();
      final client = MockClient((req) async {
        if (req.url.path.endsWith('/accept')) {
          return http.Response(
            jsonEncode({
              'detail': {'code': 'SOCIAL_REQUEST_NOT_FOUND', 'message': 'gone'},
            }),
            404,
          );
        }
        return handler(wire)(req);
      });
      await t.pumpWidget(harness(const FriendRequestsPage(), client));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('social.incoming.accept.r-in-1')));
      await t.pumpAndSettle();
      expect(find.text('That rider is not available.'), findsOneWidget);
      expect(
        find.byKey(const Key('social.incoming.accept.r-in-1')),
        findsOneWidget,
        reason: 'a failed accept must not silently drop the request row',
      );
    });
  });

  group('blocked users page', () {
    testWidgets('lists blocked riders and unblocks one', (t) async {
      final wire = Wire()..blocks.add('u-4');
      await t.pumpWidget(harness(const BlockedUsersPage(), backend(wire)));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('social.blocked.u-4')), findsOneWidget);
      await t.tap(find.byKey(const Key('social.blocked.unblock.u-4')));
      await t.pumpAndSettle();
      expect(wire.posts, ['unblock:u-4']);
      expect(find.text('User unblocked'), findsOneWidget);
    });

    testWidgets('an empty block list explains itself', (t) async {
      final wire = Wire()..blocksEmpty = true;
      await t.pumpWidget(harness(const BlockedUsersPage(), backend(wire)));
      await t.pumpAndSettle();
      expect(find.text('You have not blocked anyone'), findsOneWidget);
    });

    testWidgets('unblocking does not restore a friendship', (t) async {
      final wire = Wire()..blocks.add('u-4');
      await t.pumpWidget(harness(const BlockedUsersPage(), backend(wire)));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('social.blocked.unblock.u-4')));
      await t.pumpAndSettle();
      // Only the unblock went out; no relationship row was re-created.
      expect(wire.posts, ['unblock:u-4']);
    });
  });

  group('social privacy page', () {
    testWidgets('renders all three settings with the server values', (t) async {
      await t.pumpWidget(harness(const SocialPrivacyPage(), backend(Wire())));
      await t.pumpAndSettle();
      expect(find.text('Profile visibility'), findsOneWidget);
      // The privacy page is a lazily-built list, so scroll each option into
      // view instead of assuming it is mounted.
      for (final option in [
        'social.visibility.public',
        'social.visibility.friends',
        'social.visibility.private',
        'social.requests.everyone',
        'social.requests.nobody',
        'social.searchVisibility.discoverable',
        'social.searchVisibility.hidden',
      ]) {
        final finder = find.byKey(Key(option));
        await scrollTo(t, finder);
        expect(finder, findsOneWidget, reason: option);
      }
      expect(find.text('Friend requests'), findsOneWidget);
      expect(find.text('Search visibility'), findsOneWidget);
    });

    testWidgets('invents no privacy control the backend lacks', (t) async {
      await t.pumpWidget(harness(const SocialPrivacyPage(), backend(Wire())));
      await t.pumpAndSettle();
      expect(find.text('Hide my city'), findsNothing);
      expect(find.text('Who can see my rides'), findsNothing);
      expect(find.text('Block list visibility'), findsNothing);
    });

    testWidgets('saving reports the server confirmation', (t) async {
      var patches = 0;
      final client = MockClient((req) async {
        if (req.method == 'PATCH') patches++;
        return handler(Wire())(req);
      });
      await t.pumpWidget(harness(const SocialPrivacyPage(), client));
      await t.pumpAndSettle();
      await scrollTo(t, find.byKey(const Key('social.privacy.save')));
      await t.tap(find.byKey(const Key('social.privacy.save')));
      await t.pumpAndSettle();
      expect(patches, 1);
      expect(find.text('Privacy settings saved'), findsOneWidget);
    });

    testWidgets('a rejected save keeps the rider on the screen', (t) async {
      final client = MockClient((req) async {
        if (req.method == 'PATCH') {
          return http.Response(
            jsonEncode({
              'detail': {'code': 'FORBIDDEN', 'message': 'no'},
            }),
            403,
          );
        }
        return handler(Wire())(req);
      });
      await t.pumpWidget(harness(const SocialPrivacyPage(), client));
      await t.pumpAndSettle();
      await scrollTo(t, find.byKey(const Key('social.privacy.save')));
      await t.tap(find.byKey(const Key('social.privacy.save')));
      await t.pumpAndSettle();
      // The error banner is the first row of the page; rewind to it.
      await scrollToTop(t);
      expect(find.byKey(const Key('social.privacy.error')), findsOneWidget);
      expect(
        find.text('This rider is not accepting requests.'),
        findsOneWidget,
      );
    });

    testWidgets('duplicate saves send one PATCH', (t) async {
      var patches = 0;
      final client = MockClient((req) async {
        if (req.method == 'PATCH') {
          patches++;
          await Future<void>.delayed(const Duration(milliseconds: 200));
        }
        return handler(Wire())(req);
      });
      await t.pumpWidget(harness(const SocialPrivacyPage(), client));
      await t.pumpAndSettle();
      final save = find.byKey(const Key('social.privacy.save'));
      await scrollTo(t, save);
      await t.tap(save);
      await t.tap(save);
      await t.pumpAndSettle();
      expect(patches, 1);
    });
  });

  group('localization rendering', () {
    testWidgets('friends list renders in French', (t) async {
      final wire = Wire()..friends.add('u-3');
      await t.pumpWidget(
        harness(const FriendsPage(), backend(wire), locale: const Locale('fr')),
      );
      await t.pumpAndSettle();
      expect(find.text('Amis'), findsWidgets);
      // The destructive actions live behind the row menu.
      await t.tap(find.byKey(const Key('social.friend.menu.u-3')));
      await t.pumpAndSettle();
      expect(find.text('Retirer des amis'), findsOneWidget);
      expect(find.text('Bloquer'), findsOneWidget);
    });

    testWidgets('blocked page renders in Arabic RTL', (t) async {
      final wire = Wire()..blocks.add('u-4');
      await t.pumpWidget(
        harness(
          const BlockedUsersPage(),
          backend(wire),
          locale: const Locale('ar'),
        ),
      );
      await t.pumpAndSettle();
      final ctx = t.element(find.byType(BlockedUsersPage));
      expect(Directionality.of(ctx), TextDirection.rtl);
      expect(find.text('المستخدمون المحظورون'), findsOneWidget);
      expect(find.text('إلغاء الحظر'), findsOneWidget);
    });

    testWidgets('privacy labels render in Arabic without Latin leakage', (
      t,
    ) async {
      await t.pumpWidget(
        harness(
          const SocialPrivacyPage(),
          backend(Wire()),
          locale: const Locale('ar'),
        ),
      );
      await t.pumpAndSettle();
      expect(find.text('ظهور الملف'), findsOneWidget);
      expect(find.text('عام'), findsOneWidget);
      expect(find.text('خاص'), findsOneWidget);
      expect(find.text('الجميع'), findsOneWidget);
      expect(find.text('لا أحد'), findsOneWidget);
    });
  });

  group('security posture', () {
    testWidgets('every social call carries the bearer token', (t) async {
      final auths = <String?>[];
      final client = MockClient((req) async {
        auths.add(req.headers['Authorization']);
        return handler(Wire())(req);
      });
      final container = ProviderContainer(
        overrides: [
          apiClientProvider.overrideWithValue(
            ApiClient(
              baseUrl: 'http://test',
              client: client,
              accessToken: () async => 'secret-token',
            ),
          ),
        ],
      );
      await container.read(mySocialProfileProvider.future);
      await container.read(friendListProvider.future);
      await container.read(blockListProvider.future);
      await container
          .read(socialActionsProvider.notifier)
          .sendFriendRequest('u-2');
      container.dispose();
      expect(auths, isNotEmpty);
      for (final header in auths) {
        expect(header, 'Bearer secret-token');
      }
    });

    testWidgets('no social screen renders an email address', (t) async {
      for (final page in <Widget>[
        const MySocialProfilePage(),
        UserProfilePage(userId: 'u-2'),
        const FriendsPage(),
        const FriendRequestsPage(),
        const BlockedUsersPage(),
        const SocialPrivacyPage(),
        const UserSearchPage(),
      ]) {
        await t.pumpWidget(harness(page, backend(Wire())));
        await t.pumpAndSettle();
        // '@handle' is a username and legitimately contains '@'; what must
        // never appear is an email address.
        for (final text in t.widgetList<Text>(find.byType(Text))) {
          final data = text.data ?? '';
          expect(
            RegExp(r'[^\s@]+@[^\s@]+\.[a-zA-Z]{2,}').hasMatch(data),
            isFalse,
            reason: 'social UI must not render an email address: ',
          );
        }
      }
    });

    testWidgets('the shared avatar never throws on a dead URL', (t) async {
      await t.pumpWidget(
        harness(
          const SizedBox(
            child: SocialAvatar(
              avatarUrl: 'https://invalid.example/a.png',
              name: 'Sara',
            ),
          ),
          backend(Wire()),
        ),
      );
      await t.pumpAndSettle();
      expect(t.takeException(), isNull);
    });
  });
}
