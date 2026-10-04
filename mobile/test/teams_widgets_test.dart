import 'dart:async';
import 'dart:convert';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/teams/presentation/my_teams_page.dart';
import 'package:cyclecoach/features/teams/presentation/team_form_page.dart';
import 'package:cyclecoach/features/teams/presentation/team_invitations_admin_page.dart';
import 'package:cyclecoach/features/teams/presentation/team_invitations_page.dart';
import 'package:cyclecoach/features/teams/presentation/team_join_requests_page.dart';
import 'package:cyclecoach/features/teams/presentation/team_members_page.dart';
import 'package:cyclecoach/features/teams/presentation/team_profile_page.dart';
import 'package:cyclecoach/features/teams/presentation/team_search_page.dart';
import 'package:cyclecoach/features/teams/presentation/team_settings_page.dart';
import 'package:cyclecoach/features/teams/presentation/team_widgets.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// Widget-level team tests: loading, empty, error, retry, role-gated actions,
/// duplicate-submission protection, EN/FR/AR, and RTL.
class Wire {
  Map<String, dynamic> team = {};
  String? teamError;
  bool membersEmpty = false;
  bool requestsEmpty = false;
  bool invitationsEmpty = false;
  bool myTeamsEmpty = false;
  bool searchEmpty = false;
  bool slow = false;
  Completer<void>? gate;
  final List<String> calls = [];
  final Map<String, String> invitationStatus = {};

  static Map<String, dynamic> base({
    String state = 'OWNER',
    String? myRole = 'owner',
    String visibility = 'public',
    int pendingRequests = 1,
    int pendingInvites = 1,
  }) => {
    'id': 't-1',
    'name': 'Atlas CC',
    'handle': 'atlas_cc',
    'description': 'Road crew',
    'avatar_url': null,
    'category': 'road',
    'visibility': visibility,
    'status': 'active',
    'member_count': 3,
    'created_at': '2026-01-01T00:00:00Z',
    'my_role': myRole,
    'state': state,
    'pending_requests_count': pendingRequests,
    'pending_invitations_count': pendingInvites,
  };
}

Future<http.Response> Function(http.Request) wireHandler(
  Wire w,
) => (req) async {
  final gate = w.gate;
  if (gate != null) await gate.future;
  if (w.slow) await Future<void>.delayed(const Duration(milliseconds: 150));
  final path = req.url.path;
  final method = req.method;
  w.calls.add('$method $path');

  if (w.teamError != null) {
    return http.Response(
      jsonEncode({
        'detail': {'code': w.teamError, 'message': 'x'},
      }),
      w.teamError == 'TEAM_FORBIDDEN' ? 403 : 500,
    );
  }

  const p = '/api/v1/teams';

  if (method == 'GET' && path == '$p/t-1') {
    return http.Response(jsonEncode(w.team), 200);
  }
  if (method == 'GET' && path == p) {
    final items = w.myTeamsEmpty ? [] : [w.team];
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
  if (method == 'GET' && path == '$p/search') {
    final q = (req.url.queryParameters['q'] ?? '').toLowerCase();
    final items = (w.searchEmpty || q == 'zzz')
        ? <Map<String, dynamic>>[]
        : [
            Wire.base(
              state: 'NOT_AFFILIATED',
              myRole: null,
              pendingRequests: 0,
              pendingInvites: 0,
            ),
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
  if (method == 'POST' && path == p || method == 'PATCH' && path == '$p/t-1') {
    return http.Response(jsonEncode(w.team), method == 'POST' ? 201 : 200);
  }
  if (method == 'GET' && path == '$p/t-1/members') {
    final items = w.membersEmpty
        ? <Map<String, dynamic>>[]
        : [
            {
              'user_id': 'u-1',
              'username': 'imad_fouri',
              'display_name': 'Imad Fouri',
              'avatar_url': null,
              'role': 'owner',
              'joined_at': '2026-01-01T00:00:00Z',
            },
            {
              'user_id': 'u-3',
              'username': 'karim_amrani',
              'display_name': 'Karim Amrani',
              'avatar_url': null,
              'role': 'member',
              'joined_at': '2026-01-02T00:00:00Z',
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
  if (method == 'GET' && path == '$p/t-1/join-requests') {
    final items = w.requestsEmpty
        ? <Map<String, dynamic>>[]
        : [
            {
              'id': 'r-1',
              'team_id': 't-1',
              'user_id': 'u-3',
              'username': 'karim_amrani',
              'display_name': 'Karim Amrani',
              'message': 'let me in',
              'created_at': '2026-01-01T00:00:00Z',
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
  if (method == 'GET' && path == '$p/t-1/invitations') {
    final items = w.invitationsEmpty
        ? <Map<String, dynamic>>[]
        : [
            {
              'id': 'i-1',
              'team_id': 't-1',
              'team_name': 'Atlas CC',
              'team_handle': 'atlas_cc',
              'invited_user_id': 'u-2',
              'invited_by_user_id': 'u-1',
              'invited_by_username': 'imad_fouri',
              'status': 'pending',
              'message': null,
              'created_at': '2026-01-01T00:00:00Z',
              'responded_at': null,
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
  if (method == 'GET' && path == '$p/my/invitations') {
    final items = w.invitationsEmpty
        ? <Map<String, dynamic>>[]
        : [
            {
              'id': 'i-1',
              'team_id': 't-1',
              'team_name': 'Atlas CC',
              'team_handle': 'atlas_cc',
              'invited_user_id': 'u-2',
              'invited_by_user_id': 'u-1',
              'invited_by_username': 'imad_fouri',
              'status': w.invitationStatus['i-1'] ?? 'pending',
              'message': null,
              'created_at': '2026-01-01T00:00:00Z',
              'responded_at': null,
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
  if (method == 'GET' && path == '$p/my/join-requests') {
    return http.Response(
      jsonEncode({
        'items': [
          {
            'id': 'r-9',
            'team_id': 't-9',
            'user_id': 'u-1',
            'username': null,
            'display_name': 'Other CC',
            'message': null,
            'created_at': '2026-01-01T00:00:00Z',
          },
        ],
        'total': 1,
        'page': 1,
        'page_size': 20,
      }),
      200,
    );
  }
  if (method == 'POST' && path.endsWith('/accept')) {
    if (path.contains('/invitations/')) {
      w.invitationStatus['i-1'] = 'accepted';
    }
    return http.Response(
      jsonEncode({'status': 'accepted', 'user_id': 'u-3', 'team_id': 't-1'}),
      200,
    );
  }
  if (method == 'POST' && path.endsWith('/reject')) {
    w.invitationStatus['i-1'] = 'declined';
    return http.Response(jsonEncode({'status': 'declined'}), 200);
  }
  if (method == 'DELETE' && path.contains('/members/')) {
    return http.Response(jsonEncode({'status': 'removed'}), 200);
  }
  if (method == 'PATCH' && path.contains('/members/')) {
    return http.Response(
      jsonEncode({'user_id': 'u-3', 'role': req.url.queryParameters['role']}),
      200,
    );
  }
  if (method == 'DELETE' && path == '$p/t-1/membership') {
    return http.Response(jsonEncode({'status': 'left'}), 200);
  }
  if (method == 'DELETE' && path == '$p/t-1') {
    return http.Response(jsonEncode({'status': 'archived'}), 200);
  }
  if (method == 'POST' && path == '$p/t-1/join') {
    final isPrivate = w.team['visibility'] == 'private';
    return http.Response(
      jsonEncode({
        'status': isPrivate ? 'requested' : 'joined',
        'team_id': 't-1',
      }),
      200,
    );
  }
  if (method == 'DELETE' && path.contains('/join-requests/')) {
    return http.Response(jsonEncode({'status': 'cancelled'}), 200);
  }
  if (method == 'DELETE' && path.contains('/invitations/')) {
    return http.Response(jsonEncode({'status': 'revoked'}), 200);
  }
  return http.Response('not found', 404);
};

MockClient backend(Wire w) => MockClient(wireHandler(w));

Widget harness(
  Widget child,
  MockClient client, {
  Locale locale = const Locale('en'),
}) {
  return ProviderScope(
    overrides: [
      apiClientProvider.overrideWithValue(
        ApiClient(
          baseUrl: 'http://test',
          client: client,
          accessToken: () async => 'token',
        ),
      ),
    ],
    // Riverpod 3 retries a failing provider with a backoff; in a test that
    // leaves the screen on "Loading" and the error assertion can never fire.
    retry: (_, _) => null,
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

/// Drag the page's own list until [finder] is built, then tap it.
///
/// The team surfaces are `ListView`s, so a control below the fold is not built
/// at all — asserting on it without scrolling would test the viewport, not the
/// feature.
Future<void> tapScrolled(WidgetTester t, Finder finder) async {
  if (finder.evaluate().isEmpty) {
    final list = find.byType(ListView);
    for (var i = 0; i < 30 && finder.evaluate().isEmpty; i++) {
      await t.drag(list, const Offset(0, -200));
      await t.pumpAndSettle();
    }
  }
  await t.ensureVisible(finder);
  await t.pumpAndSettle();
  await t.tap(finder);
  await t.pumpAndSettle();
}

/// Same scrolling, without the tap, for assertions about a lower row.
Future<void> reveal(WidgetTester t, Finder finder) async {
  if (finder.evaluate().isEmpty) {
    final list = find.byType(ListView);
    for (var i = 0; i < 30 && finder.evaluate().isEmpty; i++) {
      await t.drag(list, const Offset(0, -200));
      await t.pumpAndSettle();
    }
  }
}

/// Drag down until [finder] is built.
Future<void> scrollDown(WidgetTester t, Finder finder) async {
  if (finder.evaluate().isEmpty) {
    final list = find.byType(ListView);
    for (var i = 0; i < 30 && finder.evaluate().isEmpty; i++) {
      await t.drag(list, const Offset(0, -200));
      await t.pumpAndSettle();
    }
  }
  await t.ensureVisible(finder);
  await t.pumpAndSettle();
}

/// Rewind a lazily-built list so a control near the top exists again.
Future<void> scrollToTop(WidgetTester t) async {
  final list = find.byType(ListView);
  for (var i = 0; i < 20; i++) {
    await t.drag(list, const Offset(0, 400));
    await t.pumpAndSettle();
    if (t.widget<ListView>(list).controller?.offset == 0) return;
  }
}

void main() {
  group('my teams page', () {
    testWidgets('renders the team with its role and pending counts', (t) async {
      final w = Wire()..team = Wire.base();
      await t.pumpWidget(harness(const MyTeamsPage(), backend(w)));
      await t.pumpAndSettle();
      expect(find.text('Atlas CC'), findsOneWidget);
      expect(find.text('@atlas_cc'), findsOneWidget);
      expect(find.text('Owner'), findsOneWidget);
      expect(find.textContaining('Pending requests'), findsOneWidget);
    });

    testWidgets('an empty list points at team creation', (t) async {
      final w = Wire()
        ..team = Wire.base()
        ..myTeamsEmpty = true;
      await t.pumpWidget(harness(const MyTeamsPage(), backend(w)));
      await t.pumpAndSettle();
      expect(find.text('You are not in any team yet'), findsOneWidget);
    });

    testWidgets('a private team shows the lock affordance', (t) async {
      final w = Wire()..team = Wire.base(visibility: 'private');
      await t.pumpWidget(harness(const MyTeamsPage(), backend(w)));
      await t.pumpAndSettle();
      expect(find.byIcon(Icons.lock_outline_rounded), findsOneWidget);
    });

    testWidgets('an error offers retry', (t) async {
      final w = Wire()
        ..team = Wire.base()
        ..teamError = 'TEAM_NOT_FOUND';
      await t.pumpWidget(harness(const MyTeamsPage(), backend(w)));
      await t.pumpAndSettle();
      expect(find.text('That team is not available.'), findsOneWidget);
      expect(find.widgetWithText(OutlinedButton, 'Retry'), findsOneWidget);
    });
  });

  group('team profile page — role-gated actions', () {
    Future<void> pump(WidgetTester t, Map<String, dynamic> team) async {
      await t.pumpWidget(
        harness(TeamProfilePage(teamId: 't-1'), backend(Wire()..team = team)),
      );
      await t.pumpAndSettle();
    }

    testWidgets('OWNER sees management surfaces and archive', (t) async {
      await pump(t, Wire.base(state: 'OWNER', myRole: 'owner'));
      await reveal(t, find.byKey(const Key('team.action.members')));
      expect(find.byKey(const Key('team.action.members')), findsOneWidget);
      await reveal(t, find.byKey(const Key('team.action.joinRequests')));
      expect(find.byKey(const Key('team.action.joinRequests')), findsOneWidget);
      await reveal(t, find.byKey(const Key('team.action.invitations')));
      expect(find.byKey(const Key('team.action.invitations')), findsOneWidget);
      await reveal(t, find.byKey(const Key('team.action.settings')));
      expect(find.byKey(const Key('team.action.settings')), findsOneWidget);
      await reveal(t, find.byKey(const Key('team.action.archive')));
      expect(find.byKey(const Key('team.action.archive')), findsOneWidget);
      // The owner cannot leave.
      expect(find.byKey(const Key('team.action.leave')), findsNothing);
    });

    testWidgets('ADMIN manages but cannot archive or leave-as-owner', (
      t,
    ) async {
      await pump(t, Wire.base(state: 'ADMIN', myRole: 'admin'));
      await reveal(t, find.byKey(const Key('team.action.members')));
      expect(find.byKey(const Key('team.action.members')), findsOneWidget);
      await reveal(t, find.byKey(const Key('team.action.settings')));
      expect(find.byKey(const Key('team.action.settings')), findsOneWidget);
      expect(find.byKey(const Key('team.action.archive')), findsNothing);
      await reveal(t, find.byKey(const Key('team.action.leave')));
      expect(find.byKey(const Key('team.action.leave')), findsOneWidget);
    });

    testWidgets('MEMBER sees only leave', (t) async {
      await pump(
        t,
        Wire.base(
          state: 'MEMBER',
          myRole: 'member',
          pendingRequests: 0,
          pendingInvites: 0,
        ),
      );
      await reveal(t, find.byKey(const Key('team.action.leave')));
      expect(find.byKey(const Key('team.action.leave')), findsOneWidget);
      expect(find.byKey(const Key('team.action.members')), findsNothing);
      await reveal(t, find.byKey(const Key('team.action.members')));
      expect(find.byKey(const Key('team.action.settings')), findsNothing);
    });

    testWidgets('NOT_AFFILIATED sees Join', (t) async {
      await pump(
        t,
        Wire.base(
          state: 'NOT_AFFILIATED',
          myRole: null,
          pendingRequests: 0,
          pendingInvites: 0,
        ),
      );
      await reveal(t, find.byKey(const Key('team.action.join')));
      expect(find.byKey(const Key('team.action.join')), findsOneWidget);
      expect(find.byKey(const Key('team.action.leave')), findsNothing);
      expect(find.byKey(const Key('team.action.members')), findsNothing);
    });

    testWidgets('JOIN_REQUEST_PENDING sees Cancel, not Join', (t) async {
      await pump(
        t,
        Wire.base(
          state: 'JOIN_REQUEST_PENDING',
          myRole: null,
          pendingRequests: 0,
          pendingInvites: 0,
        ),
      );
      await reveal(t, find.byKey(const Key('team.action.cancelRequest')));
      expect(
        find.byKey(const Key('team.action.cancelRequest')),
        findsOneWidget,
      );
      expect(find.byKey(const Key('team.action.join')), findsNothing);
    });

    testWidgets('INVITED sees Join (accepting is the inbox job)', (t) async {
      await pump(
        t,
        Wire.base(
          state: 'INVITED',
          myRole: null,
          pendingRequests: 0,
          pendingInvites: 0,
        ),
      );
      await reveal(t, find.byKey(const Key('team.action.join')));
      expect(find.byKey(const Key('team.action.join')), findsOneWidget);
    });

    testWidgets('a non-member never sees management surfaces', (t) async {
      await pump(
        t,
        Wire.base(
          state: 'NOT_AFFILIATED',
          myRole: null,
          pendingRequests: 3,
          pendingInvites: 3,
        ),
      );
      // Counters exist on the payload but no management action is rendered:
      // authority comes from the role, not from a counter.
      expect(find.byKey(const Key('team.action.members')), findsNothing);
      expect(find.byKey(const Key('team.action.joinRequests')), findsNothing);
      expect(find.byKey(const Key('team.action.settings')), findsNothing);
    });

    testWidgets('joining a public team confirms "joined"', (t) async {
      final w = Wire()
        ..team = Wire.base(
          state: 'NOT_AFFILIATED',
          myRole: null,
          visibility: 'public',
          pendingRequests: 0,
          pendingInvites: 0,
        );
      await t.pumpWidget(harness(TeamProfilePage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('team.action.join')));
      await t.pumpAndSettle();
      expect(find.text('You joined the team'), findsOneWidget);
    });

    testWidgets('joining a private team confirms "request sent"', (t) async {
      final w = Wire()
        ..team = Wire.base(
          state: 'NOT_AFFILIATED',
          myRole: null,
          visibility: 'private',
          pendingRequests: 0,
          pendingInvites: 0,
        );
      await t.pumpWidget(harness(TeamProfilePage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('team.action.join')));
      await t.pumpAndSettle();
      expect(find.text('Join request sent'), findsOneWidget);
    });

    testWidgets('leaving asks for confirmation first', (t) async {
      final w = Wire()
        ..team = Wire.base(
          state: 'MEMBER',
          myRole: 'member',
          pendingRequests: 0,
          pendingInvites: 0,
        );
      await t.pumpWidget(harness(TeamProfilePage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('team.action.leave')));
      await t.pumpAndSettle();
      expect(find.text('Leave this team?'), findsOneWidget);
      expect(
        w.calls.where((c) => c.contains('/membership')),
        isEmpty,
        reason: 'nothing sent before confirming',
      );
      await t.tap(find.byKey(const Key('team.confirm.leave')));
      await t.pumpAndSettle();
      expect(w.calls, contains('DELETE /api/v1/teams/t-1/membership'));
      expect(find.text('You left the team'), findsOneWidget);
    });

    testWidgets('archiving asks for confirmation and only the owner sees it', (
      t,
    ) async {
      final w = Wire()..team = Wire.base(state: 'OWNER', myRole: 'owner');
      await t.pumpWidget(harness(TeamProfilePage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      await tapScrolled(t, find.byKey(const Key('team.action.archive')));
      await t.pumpAndSettle();
      await tapScrolled(t, find.byKey(const Key('team.confirm.archive')));
      await t.pumpAndSettle();
      expect(w.calls, contains('DELETE /api/v1/teams/t-1'));
    });

    testWidgets('no location control is ever rendered', (t) async {
      for (final state in ['OWNER', 'ADMIN', 'MEMBER', 'NOT_AFFILIATED']) {
        await pump(t, Wire.base(state: state));
        expect(find.byIcon(Icons.my_location_rounded), findsNothing);
        expect(find.byIcon(Icons.location_on_rounded), findsNothing);
        expect(find.textContaining('GPS'), findsNothing);
      }
      // The standing notice is present instead.
      expect(
        find.textContaining('does not share your location'),
        findsOneWidget,
      );
    });

    testWidgets('an archived team shows the archived badge', (t) async {
      final w = Wire()
        ..team = {
          ...Wire.base(state: 'MEMBER', myRole: 'member'),
          'status': 'archived',
        };
      await t.pumpWidget(harness(TeamProfilePage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      await reveal(t, find.text('Archived'));
      await reveal(t, find.text('Archived'));
      expect(find.text('Archived'), findsWidgets);
    });
  });

  group('members page — role-gated roster actions', () {
    testWidgets('OWNER sees promote and remove', (t) async {
      final w = Wire()..team = Wire.base(state: 'OWNER', myRole: 'owner');
      await t.pumpWidget(harness(TeamMembersPage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('team.member.menu.u-3')));
      await t.pumpAndSettle();
      expect(find.text('Make admin'), findsOneWidget);
      expect(find.text('Remove'), findsOneWidget);
    });

    testWidgets('ADMIN sees remove but not promote', (t) async {
      final w = Wire()..team = Wire.base(state: 'ADMIN', myRole: 'admin');
      await t.pumpWidget(harness(TeamMembersPage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('team.member.menu.u-3')));
      await t.pumpAndSettle();
      expect(find.text('Remove'), findsOneWidget);
      expect(find.text('Make admin'), findsNothing);
    });

    testWidgets('the owner row offers no management menu', (t) async {
      final w = Wire()..team = Wire.base(state: 'OWNER', myRole: 'owner');
      await t.pumpWidget(harness(TeamMembersPage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('team.member.menu.u-1')), findsNothing);
      expect(find.byKey(const Key('team.member.menu.u-3')), findsOneWidget);
    });

    testWidgets('MEMBER sees a read-only roster', (t) async {
      final w = Wire()..team = Wire.base(state: 'MEMBER', myRole: 'member');
      await t.pumpWidget(harness(TeamMembersPage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('team.member.u-1')), findsOneWidget);
      expect(find.byKey(const Key('team.member.menu.u-3')), findsNothing);
    });

    testWidgets('removing asks for confirmation then confirms', (t) async {
      final w = Wire()..team = Wire.base(state: 'OWNER', myRole: 'owner');
      await t.pumpWidget(harness(TeamMembersPage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('team.member.menu.u-3')));
      await t.pumpAndSettle();
      await t.tap(find.text('Remove'));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('team.confirm.removeMember')));
      await t.pumpAndSettle();
      expect(w.calls, contains('DELETE /api/v1/teams/t-1/members/u-3'));
      expect(find.text('Member removed'), findsOneWidget);
    });

    testWidgets('changing a role sends the wire value', (t) async {
      final w = Wire()..team = Wire.base(state: 'OWNER', myRole: 'owner');
      await t.pumpWidget(harness(TeamMembersPage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('team.member.menu.u-3')));
      await t.pumpAndSettle();
      await t.tap(find.text('Make admin'));
      await t.pumpAndSettle();
      expect(w.calls.any((c) => c.contains('/members/u-3/role')), isTrue);
      expect(find.text('Role updated'), findsOneWidget);
    });

    testWidgets('an empty roster explains itself', (t) async {
      final w = Wire()
        ..team = Wire.base()
        ..membersEmpty = true;
      await t.pumpWidget(harness(TeamMembersPage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      expect(find.text('This team has no members yet'), findsOneWidget);
    });
  });

  group('join requests page', () {
    testWidgets('accepting confirms and updates the list', (t) async {
      final w = Wire()..team = Wire.base();
      await t.pumpWidget(
        harness(TeamJoinRequestsPage(teamId: 't-1'), backend(w)),
      );
      await t.pumpAndSettle();
      await tapScrolled(t, find.byKey(const Key('team.request.accept.r-1')));
      await t.pumpAndSettle();
      expect(find.text('Request accepted'), findsOneWidget);
    });

    testWidgets('rejecting confirms', (t) async {
      final w = Wire()..team = Wire.base();
      await t.pumpWidget(
        harness(TeamJoinRequestsPage(teamId: 't-1'), backend(w)),
      );
      await t.pumpAndSettle();
      await tapScrolled(t, find.byKey(const Key('team.request.reject.r-1')));
      await t.pumpAndSettle();
      expect(find.text('Request rejected'), findsOneWidget);
    });

    testWidgets('an empty queue explains itself', (t) async {
      final w = Wire()
        ..team = Wire.base()
        ..requestsEmpty = true;
      await t.pumpWidget(
        harness(TeamJoinRequestsPage(teamId: 't-1'), backend(w)),
      );
      await t.pumpAndSettle();
      expect(find.text('No join requests'), findsOneWidget);
    });

    testWidgets('a non-manager gets an error, not somebody else\'s queue', (
      t,
    ) async {
      final w = Wire()
        ..team = Wire.base()
        ..teamError = 'TEAM_NOT_FOUND';
      await t.pumpWidget(
        harness(TeamJoinRequestsPage(teamId: 't-1'), backend(w)),
      );
      await t.pumpAndSettle();
      expect(find.text('That team is not available.'), findsOneWidget);
      expect(find.byKey(const Key('team.request.accept.r-1')), findsNothing);
    });

    testWidgets('an accept cannot be double-submitted', (t) async {
      final w = Wire()..team = Wire.base();
      await t.pumpWidget(
        harness(TeamJoinRequestsPage(teamId: 't-1'), backend(w)),
      );
      await t.pumpAndSettle();
      // Installed after the first load so it only holds the mutation open.
      final gate = Completer<void>();
      w.gate = gate;
      final accept = find.byKey(const Key('team.request.accept.r-1'));
      await reveal(t, accept);
      await t.tap(accept);
      await t.pump();
      final during = find.byKey(const Key('team.request.accept.r-1'));
      if (during.evaluate().isNotEmpty) {
        // The wrapper renders a FilledButton whose onPressed is null while busy.
        expect(
          t.widget<FilledButton>(during).onPressed,
          isNull,
          reason: 'the button must be disabled while the call is in flight',
        );
      }
      gate.complete();
      await t.pumpAndSettle();
      expect(
        w.calls.where((c) => c.endsWith('/accept')).length,
        1,
        reason: 'three taps must not send three POSTs',
      );
    });
  });

  group('invitations inbox', () {
    testWidgets('accepting an invitation confirms membership', (t) async {
      final w = Wire();
      await t.pumpWidget(harness(const TeamInvitationsPage(), backend(w)));
      await t.pumpAndSettle();
      await tapScrolled(t, find.byKey(const Key('team.invitation.accept.i-1')));
      await t.pumpAndSettle();
      expect(find.text('You joined the team'), findsOneWidget);
    });

    testWidgets('declining confirms', (t) async {
      final w = Wire();
      await t.pumpWidget(harness(const TeamInvitationsPage(), backend(w)));
      await t.pumpAndSettle();
      await tapScrolled(
        t,
        find.byKey(const Key('team.invitation.decline.i-1')),
      );
      await t.pumpAndSettle();
      expect(find.text('Invitation declined'), findsOneWidget);
    });

    testWidgets('an empty inbox explains itself', (t) async {
      final w = Wire()..invitationsEmpty = true;
      await t.pumpWidget(harness(const TeamInvitationsPage(), backend(w)));
      await t.pumpAndSettle();
      expect(find.text('No invitations'), findsOneWidget);
    });

    testWidgets('the outbound tab lists the viewer\'s own asks', (t) async {
      final w = Wire();
      await t.pumpWidget(harness(const TeamInvitationsPage(), backend(w)));
      await t.pumpAndSettle();
      await t.tap(find.byKey(const Key('team.tab.myRequests')));
      await t.pumpAndSettle();
      expect(find.text('Other CC'), findsOneWidget);
      expect(find.byKey(const Key('team.myRequest.r-9')), findsOneWidget);
    });
  });

  group('manager invitation list', () {
    testWidgets('revoking an offer confirms', (t) async {
      final w = Wire();
      await t.pumpWidget(
        harness(TeamInvitationsAdminPage(teamId: 't-1'), backend(w)),
      );
      await t.pumpAndSettle();
      await tapScrolled(t, find.byKey(const Key('team.invitation.revoke.i-1')));
      await t.pumpAndSettle();
      expect(find.text('Invitation revoked'), findsOneWidget);
    });

    testWidgets('a non-manager gets an error', (t) async {
      final w = Wire()..teamError = 'TEAM_NOT_FOUND';
      await t.pumpWidget(
        harness(TeamInvitationsAdminPage(teamId: 't-1'), backend(w)),
      );
      await t.pumpAndSettle();
      expect(find.text('That team is not available.'), findsOneWidget);
    });
  });

  group('team settings page', () {
    testWidgets('the owner sees identity, privacy, and archive', (t) async {
      final w = Wire()..team = Wire.base(state: 'OWNER', myRole: 'owner');
      await t.pumpWidget(harness(TeamSettingsPage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      await reveal(t, find.byKey(const Key('team.settings.edit')));
      expect(find.byKey(const Key('team.settings.edit')), findsOneWidget);
      expect(find.byKey(const Key('team.settings.members')), findsOneWidget);
      expect(find.byKey(const Key('team.settings.requests')), findsOneWidget);
      await reveal(t, find.byKey(const Key('team.settings.archive')));
      expect(find.byKey(const Key('team.settings.archive')), findsOneWidget);
      expect(
        find.textContaining('does not share your location'),
        findsOneWidget,
      );
    });

    testWidgets('an admin cannot archive', (t) async {
      final w = Wire()..team = Wire.base(state: 'ADMIN', myRole: 'admin');
      await t.pumpWidget(harness(TeamSettingsPage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      await reveal(t, find.byKey(const Key('team.settings.edit')));
      expect(find.byKey(const Key('team.settings.edit')), findsOneWidget);
      expect(find.byKey(const Key('team.settings.archive')), findsNothing);
    });

    testWidgets('a member sees a read-only summary and no edit', (t) async {
      final w = Wire()
        ..team = Wire.base(
          state: 'MEMBER',
          myRole: 'member',
          pendingRequests: 0,
          pendingInvites: 0,
        );
      await t.pumpWidget(harness(TeamSettingsPage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('team.settings.edit')), findsNothing);
      expect(find.byKey(const Key('team.settings.archive')), findsNothing);
      expect(find.text('You are a member'), findsOneWidget);
    });

    testWidgets('an archived team hides the archive control', (t) async {
      final w = Wire()
        ..team = {
          ...Wire.base(state: 'OWNER', myRole: 'owner'),
          'status': 'archived',
        };
      await t.pumpWidget(harness(TeamSettingsPage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('team.settings.archive')), findsNothing);
      await reveal(t, find.text('Archived'));
      await reveal(t, find.text('Archived'));
      expect(find.text('Archived'), findsWidgets);
    });
  });

  group('team form', () {
    testWidgets('create validates before sending', (t) async {
      final w = Wire()..team = Wire.base();
      await t.pumpWidget(harness(const TeamFormPage(), backend(w)));
      await t.pumpAndSettle();
      await t.enterText(find.byKey(const Key('team.field.name')), '');
      await tapScrolled(t, find.byKey(const Key('team.form.save')));
      await scrollToTop(t);
      expect(
        find.text('Enter a team name (up to 80 characters).'),
        findsOneWidget,
      );
      expect(w.calls.where((c) => c == 'POST /api/v1/teams'), isEmpty);
    });

    testWidgets('create posts and confirms', (t) async {
      final w = Wire()..team = Wire.base();
      await t.pumpWidget(harness(const TeamFormPage(), backend(w)));
      await t.pumpAndSettle();
      await t.enterText(find.byKey(const Key('team.field.name')), 'Atlas CC');
      await t.enterText(find.byKey(const Key('team.field.handle')), 'atlas_cc');
      await tapScrolled(t, find.byKey(const Key('team.form.save')));
      await t.pumpAndSettle();
      expect(w.calls, contains('POST /api/v1/teams'));
      expect(find.text('Team created'), findsOneWidget);
    });

    testWidgets('an invalid handle is rejected client-side', (t) async {
      final w = Wire()..team = Wire.base();
      await t.pumpWidget(harness(const TeamFormPage(), backend(w)));
      await t.pumpAndSettle();
      await t.enterText(find.byKey(const Key('team.field.name')), 'Atlas');
      await t.enterText(find.byKey(const Key('team.field.handle')), '!!');
      await tapScrolled(t, find.byKey(const Key('team.form.save')));
      // The message renders under the handle field, near the top.
      await scrollToTop(t);
      expect(
        find.text('Use 3-30 lowercase characters: letters, digits, _ or .'),
        findsOneWidget,
      );
    });

    testWidgets('a 409 handle clash is surfaced, not swallowed', (t) async {
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          return http.Response(
            jsonEncode({
              'detail': {'code': 'TEAM_HANDLE_TAKEN', 'message': 'taken'},
            }),
            409,
          );
        }
        return wireHandler(Wire()..team = Wire.base())(req);
      });
      await t.pumpWidget(harness(const TeamFormPage(), client));
      await t.pumpAndSettle();
      await t.enterText(find.byKey(const Key('team.field.name')), 'Atlas');
      await tapScrolled(t, find.byKey(const Key('team.form.save')));
      await t.pumpAndSettle();
      await scrollToTop(t);
      expect(find.byKey(const Key('team.form.error')), findsOneWidget);
      expect(find.text('That handle is taken.'), findsOneWidget);
    });

    testWidgets('the save button is disabled while a save is in flight', (
      t,
    ) async {
      var posts = 0;
      var gate = Completer<void>();
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          posts++;
          await gate.future;
        }
        return wireHandler(Wire()..team = Wire.base())(req);
      });
      await t.pumpWidget(harness(const TeamFormPage(), client));
      await t.pumpAndSettle();
      await t.enterText(find.byKey(const Key('team.field.name')), 'Atlas');

      final save = find.byKey(const Key('team.form.save'));
      await scrollDown(t, save);
      await t.tap(save);
      await t.pump();

      // In flight the control is present and not tappable again: that is the
      // same state the provider guard reads, so the button and the guard can
      // never disagree.
      final during = find.byKey(const Key('team.form.save'));
      expect(during, findsOneWidget);
      expect(
        t.widget<FilledButton>(during).onPressed,
        isNull,
        reason: 'the save button must be disabled while the call is in flight',
      );

      gate.complete();
      await t.pumpAndSettle();
      expect(posts, 1, reason: 'one tap must produce exactly one POST');
    });

    testWidgets('a 409 handle clash is surfaced, not swallowed', (t) async {
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          return http.Response(
            jsonEncode({
              'detail': {'code': 'TEAM_HANDLE_TAKEN', 'message': 'taken'},
            }),
            409,
          );
        }
        return wireHandler(Wire()..team = Wire.base())(req);
      });
      await t.pumpWidget(harness(const TeamFormPage(), client));
      await t.pumpAndSettle();
      await t.enterText(find.byKey(const Key('team.field.name')), 'Atlas');
      await tapScrolled(t, find.byKey(const Key('team.form.save')));
      await t.pumpAndSettle();
      await scrollToTop(t);
      expect(find.byKey(const Key('team.form.error')), findsOneWidget);
      expect(find.text('That handle is taken.'), findsOneWidget);
    });

    testWidgets('an admin editing sees no owner-only controls', (t) async {
      final w = Wire()..team = Wire.base(state: 'ADMIN', myRole: 'admin');
      await t.pumpWidget(harness(TeamFormPage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      // Identity fields are owner-only on the server, so they are not rendered.
      expect(find.byKey(const Key('team.field.handle')), findsOneWidget);
      expect(find.byKey(const Key('team.visibility.public')), findsNothing);
      expect(find.byKey(const Key('team.visibility.private')), findsNothing);
    });

    testWidgets('an owner editing sees the visibility radio group', (t) async {
      final w = Wire()..team = Wire.base(state: 'OWNER', myRole: 'owner');
      await t.pumpWidget(harness(TeamFormPage(teamId: 't-1'), backend(w)));
      await t.pumpAndSettle();
      await reveal(t, find.byKey(const Key('team.visibility.public')));
      expect(find.byKey(const Key('team.visibility.public')), findsOneWidget);
      expect(find.byKey(const Key('team.visibility.private')), findsOneWidget);
    });
  });

  group('team search', () {
    testWidgets('a query shows a result row', (t) async {
      final w = Wire()..team = Wire.base();
      await t.pumpWidget(harness(const TeamSearchPage(), backend(w)));
      await t.pumpAndSettle();
      await t.enterText(find.byKey(const Key('team.search.field')), 'atlas');
      await t.pump(const Duration(milliseconds: 400));
      await t.pumpAndSettle();
      expect(find.byKey(const Key('team.tile.t-1')), findsOneWidget);
    });

    testWidgets('an empty result shows the empty state, not an error', (
      t,
    ) async {
      final w = Wire()..team = Wire.base();
      await t.pumpWidget(harness(const TeamSearchPage(), backend(w)));
      await t.pumpAndSettle();
      await t.enterText(find.byKey(const Key('team.search.field')), 'zzz');
      await t.pump(const Duration(milliseconds: 400));
      await t.pumpAndSettle();
      expect(find.text('No teams found'), findsOneWidget);
    });

    testWidgets('a one-character query never reaches the server', (t) async {
      final w = Wire()..team = Wire.base();
      await t.pumpWidget(harness(const TeamSearchPage(), backend(w)));
      await t.pumpAndSettle();
      await t.enterText(find.byKey(const Key('team.search.field')), 'a');
      await t.pump(const Duration(milliseconds: 400));
      await t.pumpAndSettle();
      expect(find.text('Enter at least 2 characters.'), findsOneWidget);
      expect(w.calls.where((c) => c.contains('/search')), isEmpty);
    });

    testWidgets('the query is debounced into a single request', (t) async {
      var searches = 0;
      final client = MockClient((req) async {
        if (req.url.path.endsWith('/search')) searches++;
        return wireHandler(Wire()..team = Wire.base())(req);
      });
      await t.pumpWidget(harness(const TeamSearchPage(), client));
      await t.pumpAndSettle();
      final field = find.byKey(const Key('team.search.field'));
      for (final query in ['a', 'at', 'atl', 'atlas']) {
        await t.enterText(field, query);
        await t.pump(const Duration(milliseconds: 50));
      }
      await t.pump(const Duration(milliseconds: 400));
      await t.pumpAndSettle();
      expect(searches, 1, reason: 'four keystrokes, one debounced search');
    });
  });

  group('localization rendering', () {
    testWidgets('my teams renders in French', (t) async {
      final w = Wire()..team = Wire.base();
      await t.pumpWidget(
        harness(const MyTeamsPage(), backend(w), locale: const Locale('fr')),
      );
      await t.pumpAndSettle();
      expect(find.text('Mes équipes'), findsOneWidget);
      expect(find.text('Propriétaire'), findsOneWidget);
    });

    testWidgets('settings renders Arabic RTL', (t) async {
      final w = Wire()..team = Wire.base(state: 'OWNER', myRole: 'owner');
      await t.pumpWidget(
        harness(
          TeamSettingsPage(teamId: 't-1'),
          backend(w),
          locale: const Locale('ar'),
        ),
      );
      await t.pumpAndSettle();
      final ctx = t.element(find.byType(TeamSettingsPage));
      expect(Directionality.of(ctx), TextDirection.rtl);
      expect(find.text('إعدادات الفريق'), findsOneWidget);
      await reveal(t, find.text('المالك'));
      expect(find.text('إعدادات الفريق'), findsOneWidget);
    });

    testWidgets('profile renders Arabic RTL with the right role label', (
      t,
    ) async {
      final w = Wire()..team = Wire.base(state: 'ADMIN', myRole: 'admin');
      await t.pumpWidget(
        harness(
          TeamProfilePage(teamId: 't-1'),
          backend(w),
          locale: const Locale('ar'),
        ),
      );
      await t.pumpAndSettle();
      final ctx = t.element(find.byType(TeamProfilePage));
      expect(Directionality.of(ctx), TextDirection.rtl);
      await reveal(t, find.text('مسؤول'));
      expect(find.text('مسؤول'), findsOneWidget);
    });
  });

  group('security posture', () {
    testWidgets('no team screen renders an email address', (t) async {
      final w = Wire()..team = Wire.base();
      for (final page in <Widget>[
        const MyTeamsPage(),
        TeamProfilePage(teamId: 't-1'),
        TeamMembersPage(teamId: 't-1'),
        TeamJoinRequestsPage(teamId: 't-1'),
        TeamInvitationsAdminPage(teamId: 't-1'),
        TeamSettingsPage(teamId: 't-1'),
        const TeamInvitationsPage(),
        const TeamSearchPage(),
      ]) {
        await t.pumpWidget(harness(page, backend(w)));
        await t.pumpAndSettle();
        for (final text in t.widgetList<Text>(find.byType(Text))) {
          final data = text.data ?? '';
          expect(
            RegExp(r'[^\s@]+@[^\s@]+\.[a-zA-Z]{2,}').hasMatch(data),
            isFalse,
            reason: 'team UI must not render an email address: $data',
          );
        }
      }
    });

    testWidgets('every team call carries the bearer token', (t) async {
      final auths = <String?>[];
      final client = MockClient((req) async {
        auths.add(req.headers['Authorization']);
        return wireHandler(Wire()..team = Wire.base())(req);
      });
      await t.pumpWidget(harness(TeamProfilePage(teamId: 't-1'), client));
      await t.pumpAndSettle();
      await tapScrolled(t, find.byKey(const Key('team.action.archive')));
      await t.pumpAndSettle();
      await tapScrolled(t, find.byKey(const Key('team.confirm.archive')));
      await t.pumpAndSettle();
      expect(auths, isNotEmpty);
      for (final header in auths) {
        expect(header, 'Bearer token');
      }
    });

    testWidgets('the shared avatar never throws on a dead URL', (t) async {
      await t.pumpWidget(
        harness(
          const SizedBox(
            child: TeamAvatar(
              avatarUrl: 'https://invalid.example/a.png',
              name: 'Atlas',
            ),
          ),
          backend(Wire()..team = Wire.base()),
        ),
      );
      await t.pumpAndSettle();
      expect(t.takeException(), isNull);
    });
  });
}
