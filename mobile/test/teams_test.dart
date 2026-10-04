import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/teams/data/team_repository.dart';
import 'package:cyclecoach/features/teams/domain/team.dart';
import 'package:cyclecoach/features/teams/domain/team_validators.dart';
import 'package:cyclecoach/features/teams/presentation/team_widgets.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _teamJson = {
  'id': 't-1',
  'name': 'Atlas CC',
  'handle': 'atlas_cc',
  'description': 'Road crew',
  'avatar_url': null,
  'category': 'road',
  'visibility': 'public',
  'status': 'active',
  'member_count': 3,
  'created_at': '2026-01-01T00:00:00Z',
  'my_role': 'owner',
  'state': 'OWNER',
  'pending_requests_count': 2,
  'pending_invitations_count': 1,
};

Map<String, dynamic> teamJson({
  String id = 't-1',
  String state = 'OWNER',
  String? myRole = 'owner',
  String visibility = 'public',
  String status = 'active',
  int memberCount = 3,
  int pendingRequests = 0,
  int pendingInvites = 0,
}) => {
  'id': id,
  'name': 'Atlas CC',
  'handle': 'atlas_cc',
  'description': 'Road crew',
  'avatar_url': null,
  'category': 'road',
  'visibility': visibility,
  'status': status,
  'member_count': memberCount,
  'created_at': '2026-01-01T00:00:00Z',
  'my_role': myRole,
  'state': state,
  'pending_requests_count': pendingRequests,
  'pending_invitations_count': pendingInvites,
};

/// Stateful fake so a mutation changes what the next GET returns — that is what
/// makes "the UI followed the server" a real assertion rather than a snapshot of
/// an initial render.
class FakeTeams {
  final Map<String, dynamic> teams = {};
  final Map<String, String> states = {};
  final List<String> calls = [];
  String? forcedError;
  int status = 409;
  Completer<void>? gate;

  int seq = 0;

  Map<String, dynamic> invitations = {
    'i-1': {
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
  };

  final Map<String, Map<String, dynamic>> joinRequests = {
    'r-1': {
      'id': 'r-1',
      'team_id': 't-1',
      'user_id': 'u-3',
      'username': 'karim_amrani',
      'display_name': 'Karim Amrani',
      'message': 'let me in',
      'created_at': '2026-01-01T00:00:00Z',
    },
  };
}

Future<http.Response> Function(http.Request) handler(
  FakeTeams f,
) => (req) async {
  final gate = f.gate;
  if (gate != null) await gate.future;
  final path = req.url.path;
  final method = req.method;
  f.calls.add('$method $path');

  if (f.forcedError != null) {
    return http.Response(
      jsonEncode({
        'detail': {'code': f.forcedError, 'message': 'x'},
      }),
      f.status,
    );
  }

  const p = '/api/v1/teams';
  final myProfile = jsonEncode(_teamJson);

  if (method == 'POST' && path == p) {
    f.seq++;
    final id = 't-${f.seq}';
    f.teams[id] = teamJson(id: id);
    return http.Response(
      jsonEncode({
        ...teamJson(id: id),
        'owner_user_id': 'u-1',
        'updated_at': '2026-01-01T00:00:00Z',
      }),
      201,
    );
  }
  if (method == 'GET' && path == p) {
    return http.Response(
      jsonEncode({
        'items': [teamJson()],
        'total': 1,
        'page': 1,
        'page_size': 20,
      }),
      200,
    );
  }
  if (method == 'GET' && path == '$p/search') {
    final q = (req.url.queryParameters['q'] ?? '').toLowerCase();
    final empty = q == 'nothing';
    return http.Response(
      jsonEncode({
        'items': empty
            ? []
            : [
                teamJson(
                  id: 't-2',
                  state: f.states['t-2'] ?? 'NOT_AFFILIATED',
                  myRole: null,
                  memberCount: 7,
                ),
              ],
        'total': empty ? 0 : 1,
        'page': 1,
        'page_size': 20,
      }),
      200,
    );
  }
  if (method == 'GET' && path.startsWith('$p/my/join-requests')) {
    return http.Response(
      jsonEncode({
        'items': f.joinRequests.values.toList(),
        'total': f.joinRequests.length,
        'page': 1,
        'page_size': 20,
      }),
      200,
    );
  }
  if (method == 'GET' && path.startsWith('$p/my/invitations')) {
    final status = req.url.queryParameters['status'];
    final items = f.invitations.values
        .where((i) => status == null || i['status'] == status)
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
  if (method == 'POST' &&
      path.startsWith('$p/invitations/') &&
      path.endsWith('/accept')) {
    final id = path.split('/').elementAt(5);
    f.invitations.remove(id);
    f.states['t-1'] = 'MEMBER';
    return http.Response(
      jsonEncode({'team_id': 't-1', 'status': 'accepted'}),
      200,
    );
  }
  if (method == 'POST' &&
      path.startsWith('$p/invitations/') &&
      path.endsWith('/reject')) {
    final id = path.split('/').elementAt(5);
    final inv = f.invitations[id];
    if (inv != null) inv['status'] = 'declined';
    return http.Response(jsonEncode({'status': 'declined'}), 200);
  }
  if (method == 'GET' && path == '$p/t-1/members') {
    return http.Response(
      jsonEncode({
        'items': [
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
        ],
        'total': 2,
        'page': 1,
        'page_size': 20,
      }),
      200,
    );
  }
  if (method == 'GET' && path == '$p/t-1/join-requests') {
    return http.Response(
      jsonEncode({
        'items': f.joinRequests.values.toList(),
        'total': f.joinRequests.length,
        'page': 1,
        'page_size': 20,
      }),
      200,
    );
  }
  if (method == 'POST' && path.endsWith('/accept')) {
    f.joinRequests.remove(path.split('/').elementAt(5));
    return http.Response(
      jsonEncode({'status': 'accepted', 'user_id': 'u-3'}),
      200,
    );
  }
  if (method == 'POST' && path.endsWith('/reject')) {
    f.joinRequests.remove(path.split('/').elementAt(5));
    return http.Response(jsonEncode({'status': 'rejected'}), 200);
  }
  if (method == 'DELETE' && path.contains('/join-requests/')) {
    f.joinRequests.clear();
    return http.Response(jsonEncode({'status': 'cancelled'}), 200);
  }
  if (method == 'POST' && path == '$p/t-1/join') {
    final status = f.states['t-1'] == 'private' ? 'requested' : 'joined';
    return http.Response(jsonEncode({'status': status, 'team_id': 't-1'}), 200);
  }
  if (method == 'POST' && path == '$p/t-1/invitations') {
    f.seq++;
    final id = 'i-${f.seq}';
    final inv = {
      'id': id,
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
    };
    f.invitations[id] = inv;
    return http.Response(jsonEncode(inv), 201);
  }
  if (method == 'DELETE' && path.contains('/invitations/')) {
    final id = path.split('/').last;
    final inv = f.invitations[id];
    if (inv != null) inv['status'] = 'revoked';
    return http.Response(jsonEncode({'status': 'revoked'}), 200);
  }
  if (method == 'GET' && path == '$p/t-1/invitations') {
    return http.Response(
      jsonEncode({
        'items': f.invitations.values.toList(),
        'total': f.invitations.length,
        'page': 1,
        'page_size': 20,
      }),
      200,
    );
  }
  if (method == 'DELETE' && path.contains('/members/')) {
    return http.Response(jsonEncode({'status': 'removed'}), 200);
  }
  if (method == 'PATCH' &&
      path.contains('/members/') &&
      path.endsWith('/role')) {
    return http.Response(
      jsonEncode({'user_id': 'u-3', 'role': req.url.queryParameters['role']}),
      200,
    );
  }
  if (method == 'DELETE' && path == '$p/t-1/membership') {
    return http.Response(jsonEncode({'status': 'left'}), 200);
  }
  if (method == 'PATCH' && path == '$p/t-1') {
    return http.Response(jsonEncode(teamJson()), 200);
  }
  if (method == 'DELETE' && path == '$p/t-1') {
    return http.Response(jsonEncode({'status': 'archived'}), 200);
  }
  if (method == 'GET' && path == '$p/t-1') {
    return http.Response(
      jsonEncode(
        teamJson(
          state: f.states['t-1'] ?? 'OWNER',
          pendingRequests: f.joinRequests.length,
          pendingInvites: f.invitations.length,
        ),
      ),
      200,
    );
  }
  return http.Response(myProfile, 200);
};

MockClient backend(FakeTeams f) => MockClient(handler(f));

ApiClient api(MockClient backend) => ApiClient(
  baseUrl: 'http://test',
  client: backend,
  accessToken: () async => 'test-token',
);

ProviderContainer container(MockClient backend) => ProviderContainer(
  overrides: [apiClientProvider.overrideWithValue(api(backend))],
  retry: (_, _) => null,
);

void main() {
  group('domain models', () {
    test('team parses the server payload including role and state', () {
      final team = Team.fromJson(_teamJson);
      expect(team.id, 't-1');
      expect(team.name, 'Atlas CC');
      expect(team.handleLabel, '@atlas_cc');
      expect(team.visibility, TeamVisibility.public_);
      expect(team.status, TeamStatus.active);
      expect(team.memberCount, 3);
      expect(team.myRole, TeamRole.owner);
      expect(team.state, TeamState.owner);
      expect(team.pendingRequestsCount, 2);
      expect(team.pendingInvitationsCount, 1);
      expect(team.hasPendingWork, isTrue);
    });

    test('a non-member team has a null role and an explicit state', () {
      final team = Team.fromJson(
        teamJson(state: 'NOT_AFFILIATED', myRole: null, memberCount: 7),
      );
      expect(team.myRole, isNull);
      expect(team.state, TeamState.notAffiliated);
      expect(team.isManager, isFalse);
      expect(team.isOwner, isFalse);
      expect(team.state.canJoin, isTrue);
    });

    test('role authority matches the backend ladder', () {
      expect(TeamRole.owner.canManage, isTrue);
      expect(TeamRole.admin.canManage, isTrue);
      expect(TeamRole.member.canManage, isFalse);
      expect(TeamRole.owner.isOwner, isTrue);
      expect(TeamRole.admin.isOwner, isFalse);
    });

    test('membership state wins over a pending request', () {
      expect(TeamState.member.isMember, isTrue);
      expect(TeamState.owner.isMember, isTrue);
      expect(TeamState.admin.isMember, isTrue);
      expect(TeamState.invited.isMember, isFalse);
      expect(TeamState.joinRequestPending.isMember, isFalse);
    });

    test('unknown wire values degrade instead of crashing', () {
      expect(TeamState.parse('SOMETHING_NEW'), TeamState.notAffiliated);
      expect(TeamRole.parse(null), TeamRole.member);
      expect(TeamVisibility.parse(null), TeamVisibility.public_);
      expect(TeamStatus.parse(null), TeamStatus.active);
      expect(InvitationStatus.parse(null), InvitationStatus.pending);
    });

    test('private and archived flags read from the wire', () {
      final team = Team.fromJson(
        teamJson(visibility: 'private', status: 'archived'),
      );
      expect(team.isPrivate, isTrue);
      expect(team.isArchived, isTrue);
      expect(team.handleLabel, '@atlas_cc');
    });

    test('an unclaimed handle renders as null, not an empty @', () {
      final team = Team.fromJson({...teamJson(), 'handle': null});
      expect(team.handleLabel, isNull);
    });

    test('no team model carries email, phone, or location', () {
      // A guard, not a style check: if a future field adds telemetry or
      // credentials, this fails rather than shipping.
      const banned = {
        'email',
        'password',
        'token',
        'lat',
        'lon',
        'lng',
        'gps',
        'location',
        'position',
        'coordinate',
        'phone',
      };
      final payloads = <Map<String, dynamic>>[
        _teamJson,
        teamJson(),
        {
          'user_id': 'u-2',
          'username': 'sara',
          'display_name': 'Sara',
          'avatar_url': null,
          'role': 'member',
          'joined_at': '2026-01-01T00:00:00Z',
        },
        {
          'id': 'r-1',
          'team_id': 't-1',
          'user_id': 'u-3',
          'username': 'karim',
          'display_name': 'Karim',
          'message': null,
          'created_at': '2026-01-01T00:00:00Z',
        },
        {
          'id': 'i-1',
          'team_id': 't-1',
          'team_name': 'Atlas',
          'team_handle': 'atlas',
          'invited_user_id': 'u-2',
          'invited_by_user_id': 'u-1',
          'invited_by_username': 'imad',
          'status': 'pending',
          'message': null,
          'created_at': '2026-01-01T00:00:00Z',
          'responded_at': null,
        },
      ];
      for (final json in payloads) {
        for (final key in json.keys) {
          for (final part in key.toLowerCase().split('_')) {
            expect(
              banned.contains(part),
              isFalse,
              reason: 'team payload must not carry $key',
            );
          }
        }
      }
    });
  });

  group('validators mirror the backend', () {
    test('handle rules', () {
      expect(TeamValidators.handle('atlas_cc'), isNull);
      expect(TeamValidators.handle('Atlas_CC'), isNull);
      expect(TeamValidators.handle(''), isNull); // blank = unclaimed
      expect(TeamValidators.handle('ab'), isNotNull);
      expect(TeamValidators.handle('a' * 31), isNotNull);
      expect(TeamValidators.handle('12345'), isNotNull);
      expect(TeamValidators.handle('a.b.c'), isNotNull);
      expect(TeamValidators.handle('.leading'), isNotNull);
      expect(TeamValidators.handle('trailing_'), isNotNull);
      expect(TeamValidators.handle('user@example.com'), isNotNull);
      expect(TeamValidators.handle('with space'), isNotNull);
    });

    test('name, description, avatar, category bounds', () {
      expect(TeamValidators.name('Atlas CC'), isNull);
      expect(TeamValidators.name('   '), isNotNull);
      expect(TeamValidators.name('a' * 81), isNotNull);
      expect(TeamValidators.description('x' * 500), isNull);
      expect(TeamValidators.description('x' * 501), isNotNull);
      expect(TeamValidators.avatarUrl('https://x.dev/a.png'), isNull);
      expect(TeamValidators.avatarUrl('javascript:alert(1)'), isNotNull);
      expect(TeamValidators.category('x' * 32), isNull);
      expect(TeamValidators.category('x' * 33), isNotNull);
      expect(TeamValidators.message('x' * 280), isNull);
      expect(TeamValidators.message('x' * 281), isNotNull);
    });

    test('search bounds match the endpoint', () {
      expect(TeamValidators.searchQuery('ab'), isNull);
      expect(TeamValidators.searchQuery('a'), isNotNull);
      expect(TeamValidators.searchQuery('a' * 65), isNotNull);
    });

    test('the combined form returns the first failure', () {
      expect(
        TeamValidators.form(
          name: '',
          handle: '!!',
          description: '',
          avatarUrl: '',
          category: '',
        ),
        'team.invalidName',
      );
    });

    test('owner is not an assignable role (no transfer in 8.2)', () {
      expect(TeamValidators.assignableRoles, isNot(contains(TeamRole.owner)));
      expect(TeamValidators.assignableRoles, contains(TeamRole.admin));
      expect(TeamValidators.assignableRoles, contains(TeamRole.member));
    });
  });

  group('repository', () {
    test('every team endpoint maps to the real path', () async {
      final f = FakeTeams();
      final repo = TeamRepository(api(backend(f)));
      await repo.create(name: 'Atlas CC', handle: 'atlas_cc');
      await repo.myTeams();
      await repo.team('t-1');
      await repo.search('atlas');
      await repo.members('t-1');
      await repo.join('t-1');
      await repo.joinRequests('t-1');
      await repo.myJoinRequests();
      await repo.acceptJoinRequest('t-1', 'r-1');
      await repo.invite('t-1', 'u-2');
      await repo.teamInvitations('t-1');
      await repo.myInvitations();
      await repo.acceptInvitation('i-1');
      await repo.declineInvitation('i-1');
      await repo.revokeInvitation('t-1', 'i-1');
      await repo.setRole('t-1', 'u-3', TeamRole.admin);
      await repo.removeMember('t-1', 'u-3');
      await repo.leave('t-1');
      await repo.update('t-1', name: 'Atlas CC 2');
      await repo.archive('t-1');

      for (final expected in [
        'POST /api/v1/teams',
        'GET /api/v1/teams',
        'GET /api/v1/teams/search',
        'GET /api/v1/teams/t-1',
        'PATCH /api/v1/teams/t-1',
        'DELETE /api/v1/teams/t-1',
        'GET /api/v1/teams/t-1/members',
        'POST /api/v1/teams/t-1/join',
        'GET /api/v1/teams/t-1/join-requests',
        'GET /api/v1/teams/my/join-requests',
        'GET /api/v1/teams/my/invitations',
        'GET /api/v1/teams/t-1/invitations',
      ]) {
        expect(f.calls, contains(expected));
      }
    });

    test('search sends only q, page, page_size', () async {
      final f = FakeTeams();
      final repo = TeamRepository(api(backend(f)));
      await repo.search('atlas_cc');
      final query = f.calls.last;
      expect(query, 'GET /api/v1/teams/search');
    });

    test('a search query is url-encoded', () async {
      final captured = <String>[];
      final client = MockClient((req) async {
        captured.add(req.url.query);
        return handler(FakeTeams())(req);
      });
      await TeamRepository(api(client)).search('a b&c=1');
      expect(captured.single, contains('q=a+b%26c%3D1'));
    });

    test('setting a role sends the wire value, never owner', () async {
      final captured = <String>[];
      final client = MockClient((req) async {
        captured.add(req.url.query);
        return handler(FakeTeams())(req);
      });
      await TeamRepository(api(client)).setRole('t-1', 'u-3', TeamRole.admin);
      expect(captured.single, contains('role=admin'));
    });

    test('a server error surfaces as ApiException', () async {
      final f = FakeTeams()
        ..forcedError = 'TEAM_HANDLE_TAKEN'
        ..status = 409;
      final repo = TeamRepository(api(backend(f)));
      try {
        await repo.create(name: 'X', handle: 'taken');
        fail('expected ApiException');
      } on ApiException catch (e) {
        expect(e.status, 409);
        expect(e.code, 'TEAM_HANDLE_TAKEN');
      }
    });

    test('clearing a handle sends an explicit null', () async {
      final captured = <Map<String, dynamic>>[];
      final client = MockClient((req) async {
        if (req.method == 'PATCH') {
          captured.add(jsonDecode(req.body) as Map<String, dynamic>);
        }
        return handler(FakeTeams())(req);
      });
      await TeamRepository(
        api(client),
      ).update('t-1', name: 'X', handle: 'x', clearHandle: true);
      expect(captured.single['handle'], isNull);
    });
  });

  group('error mapping', () {
    AppLocalizations t(String lang) => AppLocalizations(Locale(lang));

    test('team error codes map to distinct localized sentences', () {
      final en = t('en');
      expect(
        friendlyTeamError(en, ApiException(409, 'TEAM_HANDLE_TAKEN', 'x')),
        'That handle is taken.',
      );
      expect(
        friendlyTeamError(en, ApiException(409, 'TEAM_ALREADY_MEMBER', 'x')),
        'You are already a member of this team.',
      );
      expect(
        friendlyTeamError(en, ApiException(409, 'TEAM_REQUEST_PENDING', 'x')),
        'A join request is already pending.',
      );
      expect(
        friendlyTeamError(en, ApiException(409, 'TEAM_INVITE_PENDING', 'x')),
        'An invitation is already pending.',
      );
      expect(
        friendlyTeamError(en, ApiException(409, 'TEAM_OWNER_IMMUTABLE', 'x')),
        'The owner role cannot be changed or transferred.',
      );
      expect(
        friendlyTeamError(
          en,
          ApiException(409, 'TEAM_OWNER_CANNOT_LEAVE', 'x'),
        ),
        'The owner cannot leave. Archive the team.',
      );
      expect(
        friendlyTeamError(en, ApiException(403, 'TEAM_FORBIDDEN', 'x')),
        'Only the owner can change this.',
      );
    });

    test('the not-found family collapses to one message', () {
      final en = t('en');
      for (final code in [
        'TEAM_NOT_FOUND',
        'TEAM_USER_NOT_FOUND',
        'TEAM_REQUEST_NOT_FOUND',
        'TEAM_INVITATION_NOT_FOUND',
      ]) {
        expect(
          friendlyTeamError(en, ApiException(404, code, 'x')),
          'That team is not available.',
          reason: code,
        );
      }
    });

    test('status codes without a team code fall through to the shared map', () {
      final en = t('en');
      expect(
        friendlyTeamError(en, ApiException(401, 'UNAUTHORIZED', 'x')),
        'Please sign in again.',
      );
      expect(
        friendlyTeamError(en, ApiException(429, 'RATE_LIMITED', 'x')),
        'Too many attempts. Wait a moment.',
      );
      expect(
        friendlyTeamError(en, ApiException(0, 'NETWORK_ERROR', 'x')),
        'No connection. Check your network and retry.',
      );
    });

    test('an unknown error never leaks a raw code', () {
      final message = friendlyTeamError(
        t('en'),
        ApiException(418, 'TEAM_TEAPOT', 'raw internal detail'),
      );
      expect(message.contains('TEAPOT'), isFalse);
      expect(message.contains('raw'), isFalse);
    });

    test('a non-API exception degrades to the generic sentence', () {
      expect(
        friendlyTeamError(t('en'), StateError('boom')),
        'Something went wrong. Try again.',
      );
    });

    test('every team error sentence is localized in fr and ar', () {
      const codes = [
        'TEAM_HANDLE_TAKEN',
        'TEAM_ALREADY_MEMBER',
        'TEAM_REQUEST_PENDING',
        'TEAM_INVITE_PENDING',
        'TEAM_OWNER_IMMUTABLE',
        'TEAM_OWNER_CANNOT_LEAVE',
        'TEAM_ROLE_UNCHANGED',
        'TEAM_FORBIDDEN',
        'TEAM_CANNOT_TARGET_SELF',
        'TEAM_INVALID_HANDLE',
        'TEAM_INVALID_AVATAR',
        'TEAM_NOT_FOUND',
      ];
      for (final lang in ['en', 'fr', 'ar']) {
        final loc = t(lang);
        for (final code in codes) {
          final message = friendlyTeamError(loc, ApiException(409, code, 'x'));
          expect(message, isNotEmpty);
          expect(
            message.contains(code),
            isFalse,
            reason: '$code leaked in $lang',
          );
        }
      }
    });
  });

  group('localization', () {
    test('the team concept set exists in all three languages', () {
      const keys = [
        'team.myTeams',
        'team.searchTeams',
        'team.createTeam',
        'team.editTeam',
        'team.teamMembers',
        'team.joinRequests',
        'team.invitations',
        'team.name',
        'team.handle',
        'team.description',
        'team.category',
        'team.visibility',
        'team.role.owner',
        'team.role.admin',
        'team.role.member',
        'team.state.OWNER',
        'team.state.ADMIN',
        'team.state.MEMBER',
        'team.state.JOIN_REQUEST_PENDING',
        'team.state.INVITED',
        'team.state.NOT_AFFILIATED',
        'team.visibility.public',
        'team.visibility.private',
        'team.join',
        'team.requestToJoin',
        'team.cancelRequest',
        'team.accept',
        'team.reject',
        'team.leave',
        'team.remove',
        'team.noTeams',
        'team.noResults',
        'team.noMembers',
        'team.noRequests',
        'team.noInvitations',
        'team.error.handleTaken',
        'team.error.alreadyMember',
        'team.error.notFound',
      ];
      for (final lang in ['en', 'fr', 'ar']) {
        final loc = AppLocalizations(Locale(lang));
        for (final key in keys) {
          expect(loc.get(key), isNotEmpty, reason: '$key missing in $lang');
        }
      }
    });

    test('English renders the expected copy', () {
      final en = AppLocalizations(const Locale('en'));
      expect(en.get('team.myTeams'), 'My Teams');
      expect(en.get('team.role.owner'), 'Owner');
      expect(en.get('team.state.JOIN_REQUEST_PENDING'), 'Request pending');
    });

    test('French renders the expected copy', () {
      final fr = AppLocalizations(const Locale('fr'));
      expect(fr.get('team.myTeams'), 'Mes équipes');
      expect(fr.get('team.role.owner'), 'Propriétaire');
      expect(fr.get('team.visibility.private'), 'Privée');
    });

    test('Arabic renders correct cycling terminology', () {
      final ar = AppLocalizations(const Locale('ar'));
      expect(ar.get('team.myTeams'), 'فرقي');
      expect(ar.get('team.role.owner'), 'المالك');
      expect(ar.get('team.visibility.private'), 'خاص');
      expect(ar.get('team.join'), 'انضم إلى الفريق');
    });

    test('Arabic team prose leaks no Latin words', () {
      final ar = AppLocalizations(const Locale('ar'));
      const keys = [
        'team.myTeams',
        'team.searchTeams',
        'team.createTeam',
        'team.editTeam',
        'team.teamMembers',
        'team.joinRequests',
        'team.invitations',
        'team.name',
        'team.handle',
        'team.description',
        'team.category',
        'team.visibility',
        'team.role.owner',
        'team.role.admin',
        'team.role.member',
        'team.state.OWNER',
        'team.state.ADMIN',
        'team.state.MEMBER',
        'team.state.JOIN_REQUEST_PENDING',
        'team.state.INVITED',
        'team.state.NOT_AFFILIATED',
        'team.visibility.public',
        'team.visibility.private',
        'team.join',
        'team.cancelRequest',
        'team.accept',
        'team.reject',
        'team.leave',
        'team.remove',
        'team.noTeams',
        'team.noResults',
        'team.noMembers',
        'team.noRequests',
        'team.noInvitations',
        'team.error.handleTaken',
        'team.error.notFound',
      ];
      for (final key in keys) {
        expect(
          RegExp(r'[A-Za-z]').hasMatch(ar.get(key)),
          isFalse,
          reason: '$key: ${ar.get(key)}',
        );
      }
    });

    test('French team prose leaks no Arabic', () {
      final fr = AppLocalizations(const Locale('fr'));
      for (final key in [
        'team.myTeams',
        'team.join',
        'team.accept',
        'team.reject',
        'team.leave',
      ]) {
        expect(
          RegExp(r'[\u0600-\u06FF]').hasMatch(fr.get(key)),
          isFalse,
          reason: key,
        );
      }
    });

    test('role and state labels resolve for every server value', () {
      for (final role in TeamRole.values) {
        for (final lang in ['en', 'fr', 'ar']) {
          expect(
            AppLocalizations(Locale(lang)).get(role.labelKey),
            isNotEmpty,
            reason: '${role.wire} in $lang',
          );
        }
      }
      for (final state in TeamState.values) {
        for (final lang in ['en', 'fr', 'ar']) {
          expect(
            AppLocalizations(Locale(lang)).get(state.labelKey),
            isNotEmpty,
            reason: '${state.wire} in $lang',
          );
        }
      }
    });
  });

  group('routing', () {
    test('team routes are registered and /groups stays a placeholder', () {
      final source = File(
        'lib/core/routing/app_router.dart',
      ).readAsStringSync();
      for (final path in [
        "'/teams'",
        "'/teams/new'",
        "'/teams/search'",
        "'/teams/invitations'",
        "'/teams/:id'",
        "'/teams/:id/edit'",
        "'/teams/:id/members'",
        "'/teams/:id/join-requests'",
        "'/teams/:id/invitations'",
        "'/teams/:id/settings'",
      ]) {
        expect(source.contains('path: $path'), isTrue, reason: path);
      }
      expect(source.contains('const MyTeamsPage()'), isTrue);

      // /groups must remain a placeholder: group rides are out of scope.
      final placeholderBlock = source.substring(
        source.indexOf('for (final p in ['),
      );
      expect(placeholderBlock.contains("'/groups'"), isFalse);
      expect(placeholderBlock.contains("'/group-rides'"), isTrue);
      expect(
        placeholderBlock.contains("'/teams'"),
        isFalse,
        reason: '/teams must not be a placeholder any more',
      );

      // Pre-existing routes must survive.
      for (final kept in [
        "'/login'",
        "'/register'",
        "'/onboarding'",
        "'/home'",
        "'/profile'",
        "'/bikes'",
        "'/friends'",
        "'/users/:userId'",
        "'/routes'",
        "'/training'",
        "'/coach'",
        "'/settings'",
      ]) {
        expect(source.contains(kept), isTrue, reason: '$kept was removed');
      }
    });
  });
}
