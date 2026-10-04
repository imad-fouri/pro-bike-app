import 'dart:convert';
import 'dart:io';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/features/auth/presentation/auth_state.dart';
import 'package:cyclecoach/features/social/data/social_repository.dart';
import 'package:cyclecoach/features/social/domain/social_profile.dart';
import 'package:cyclecoach/features/social/domain/social_validators.dart';
import 'package:cyclecoach/features/social/presentation/social_providers.dart';
import 'package:cyclecoach/features/social/presentation/social_widgets.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

// Wire shapes copied from backend/app/schemas/social.py. No field here that the
// backend does not send, and no field it sends that this client ignores for a
// reason (relationship/limited are both load-bearing).
const _myProfileJson = {
  'user_id': 'u-1',
  'username': 'imad_fouri',
  'display_name': 'Imad Fouri',
  'bio': 'Gravel most weekends.',
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

Map<String, dynamic> publicProfileJson({
  String userId = 'u-2',
  String relationship = 'NONE',
  bool limited = false,
  String? displayName = 'Sara Bench',
  String? username = 'sara_bench',
}) => <String, dynamic>{
  'user_id': userId,
  'username': username,
  'display_name': displayName,
  'bio': limited ? null : 'Road racing.',
  'avatar_url': null,
  'cycling_category': limited ? null : 'road',
  'country_code': limited ? null : 'FR',
  'city': limited ? null : 'Lyon',
  'relationship': relationship,
  'limited': limited,
};

/// In-memory social backend. Holds relationship state so tests can assert that
/// the client follows the server rather than its own assumptions.
class FakeSocial {
  final Map<String, dynamic> byUser = {};
  final Map<String, String> relationships = {}; // otherId -> state
  final Map<String, String> pendingIds = {}; // otherId -> request id
  final Set<String> blocks = {};
  final List<Map<String, dynamic>> calls = [];
  String? forcedError;
  int status = 409;
  bool slow = false;

  int requestSeq = 0;

  /// Mirrors the backend: a block in the viewer's direction reports BLOCKED,
  /// otherwise the stored relationship state, otherwise NONE.
  Map<String, dynamic> profile(String userId, {String? relation}) {
    final state =
        relation ??
        (blocks.contains(userId) ? 'BLOCKED' : relationships[userId] ?? 'NONE');
    return publicProfileJson(userId: userId, relationship: state);
  }
}

FakeSocial fakeState() => FakeSocial();

MockClient socialBackend(FakeSocial state, {String meId = 'u-1'}) {
  return MockClient((req) async {
    final path = req.url.path;
    final method = req.method;
    state.calls.add({'method': method, 'path': path, 'query': req.url.query});

    if (state.slow) {
      await Future<void>.delayed(const Duration(milliseconds: 120));
    }
    if (state.forcedError != null) {
      return http.Response(
        jsonEncode({
          'detail': {'code': state.forcedError, 'message': 'failed'},
        }),
        state.status,
      );
    }

    const prefix = '/api/v1/social';

    // --- profile ---------------------------------------------------------
    if (method == 'GET' && path == '$prefix/profile/me') {
      return http.Response(jsonEncode(_myProfileJson), 200);
    }
    if (method == 'PATCH' && path == '$prefix/profile') {
      return http.Response(jsonEncode(_myProfileJson), 200);
    }
    if (method == 'PATCH' && path == '$prefix/profile/privacy') {
      return http.Response(jsonEncode(_myProfileJson), 200);
    }
    if (method == 'GET' && path.startsWith('$prefix/profile/')) {
      final id = path.substring('$prefix/profile/'.length);
      return http.Response(jsonEncode(state.profile(id)), 200);
    }

    // --- search ----------------------------------------------------------
    if (method == 'GET' && path == '$prefix/users/search') {
      final q = (req.url.queryParameters['q'] ?? '').toLowerCase();
      final page = int.parse(req.url.queryParameters['page'] ?? '1');
      final items = q == 'nobody'
          ? <Map<String, dynamic>>[]
          : [state.profile('u-2')];
      return http.Response(
        jsonEncode({
          'items': page > 1 ? [] : items,
          'total': items.length,
          'page': page,
          'page_size': 20,
        }),
        200,
      );
    }

    // --- friend requests --------------------------------------------------
    if (method == 'POST' && path == '$prefix/friend-requests') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      final id = body['user_id'] as String;
      if (state.relationships[id] == 'FRIENDS') {
        return http.Response(
          jsonEncode({
            'detail': {'code': 'SOCIAL_ALREADY_FRIENDS', 'message': 'already'},
          }),
          409,
        );
      }
      state.requestSeq++;
      state.pendingIds[id] = 'r-${state.requestSeq}';
      state.relationships[id] = 'OUTGOING_PENDING';
      return http.Response(
        jsonEncode({'id': 'r-${state.requestSeq}', 'status': 'pending'}),
        201,
      );
    }
    if (method == 'GET' && path == '$prefix/friend-requests') {
      final direction = req.url.queryParameters['direction'] ?? 'incoming';
      if (direction == 'outgoing') {
        final items = state.pendingIds.entries
            .map(
              (e) => {
                'id': e.value,
                'user_id': e.key,
                'username': 'sara_bench',
                'display_name': 'Sara Bench',
                'avatar_url': null,
                'direction': 'outgoing',
                'status': 'pending',
                'created_at': '2026-01-01T00:00:00Z',
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
      return http.Response(
        jsonEncode({
          'items': [
            {
              'id': 'r-in-1',
              'user_id': 'u-3',
              'username': 'karim_amrani',
              'display_name': 'Karim Amrani',
              'avatar_url': null,
              'direction': 'incoming',
              'status': 'pending',
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
      final id = path.split('/').elementAt(4);
      state.relationships['u-3'] = 'FRIENDS';
      return http.Response(jsonEncode({'id': id, 'status': 'accepted'}), 200);
    }
    if (method == 'POST' && path.endsWith('/reject')) {
      return http.Response(jsonEncode({'status': 'rejected'}), 200);
    }
    if (method == 'DELETE' && path.startsWith('$prefix/friend-requests/')) {
      final id = path.split('/').last;
      state.pendingIds.removeWhere((_, v) => v == id);
      state.relationships.removeWhere((_, v) => v == 'OUTGOING_PENDING');
      return http.Response(jsonEncode({'status': 'cancelled'}), 200);
    }

    // --- friends ----------------------------------------------------------
    if (method == 'GET' && path == '$prefix/friends') {
      return http.Response(
        jsonEncode({
          'items': [
            {
              'user_id': 'u-3',
              'username': 'karim_amrani',
              'display_name': 'Karim Amrani',
              'avatar_url': null,
              'friends_since': '2026-01-02T00:00:00Z',
            },
          ],
          'total': 1,
          'page': 1,
          'page_size': 20,
        }),
        200,
      );
    }
    if (method == 'DELETE' && path.startsWith('$prefix/friends/')) {
      final id = path.substring('$prefix/friends/'.length);
      state.relationships.remove(id);
      return http.Response(jsonEncode({'status': 'removed'}), 200);
    }

    // --- blocks ------------------------------------------------------------
    if (method == 'POST' && path == '$prefix/blocks') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      final id = body['user_id'] as String;
      state.blocks.add(id);
      // A block annihilates the relationship server-side.
      state.relationships.remove(id);
      state.pendingIds.remove(id);
      return http.Response(
        jsonEncode({'user_id': id, 'status': 'blocked'}),
        201,
      );
    }
    if (method == 'GET' && path == '$prefix/blocks') {
      return http.Response(
        jsonEncode({
          'items': [
            {
              'user_id': 'u-4',
              'username': 'nadia_idrissi',
              'display_name': 'Nadia Idrissi',
              'blocked_at': '2026-01-03T00:00:00Z',
            },
          ],
          'total': 1,
          'page': 1,
          'page_size': 20,
        }),
        200,
      );
    }
    if (method == 'DELETE' && path.startsWith('$prefix/blocks/')) {
      final id = path.substring('$prefix/blocks/'.length);
      state.blocks.remove(id);
      state.relationships.remove(id); // unblock restores nothing
      return http.Response(jsonEncode({'status': 'unblocked'}), 200);
    }

    return http.Response('not found', 404);
  });
}

ApiClient api(MockClient backend) => ApiClient(
  baseUrl: 'http://test',
  client: backend,
  accessToken: () async => 'test-token',
);

ProviderContainer container(MockClient backend) => ProviderContainer(
  overrides: [apiClientProvider.overrideWithValue(api(backend))],
);

void main() {
  group('domain models', () {
    test('relationship states parse the server wire values exactly', () {
      expect(RelationshipState.parse('NONE'), RelationshipState.none);
      expect(
        RelationshipState.parse('OUTGOING_PENDING'),
        RelationshipState.outgoingPending,
      );
      expect(
        RelationshipState.parse('INCOMING_PENDING'),
        RelationshipState.incomingPending,
      );
      expect(RelationshipState.parse('FRIENDS'), RelationshipState.friends);
      expect(RelationshipState.parse('BLOCKED'), RelationshipState.blocked);
      expect(
        RelationshipState.parse('BLOCKED_BY_USER'),
        RelationshipState.blockedByUser,
      );
      expect(RelationshipState.parse('SELF'), RelationshipState.self);
    });

    test('an unknown state degrades to NONE, never a crash', () {
      expect(RelationshipState.parse('TELEPATHY'), RelationshipState.none);
      expect(RelationshipState.parse(null), RelationshipState.none);
    });

    test('a block in either direction permits no social action', () {
      expect(RelationshipState.blocked.allowsSocialAction, isFalse);
      expect(RelationshipState.blockedByUser.allowsSocialAction, isFalse);
      expect(RelationshipState.self.allowsSocialAction, isFalse);
      expect(RelationshipState.friends.allowsSocialAction, isTrue);
      expect(RelationshipState.none.allowsSocialAction, isTrue);
    });

    test('privacy enums use the backend wire values', () {
      expect(ProfileVisibility.parse('private'), ProfileVisibility.private_);
      expect(FriendRequestsPolicy.parse('nobody'), FriendRequestsPolicy.nobody);
      expect(SearchVisibility.parse('hidden'), SearchVisibility.hidden);
      expect(ProfileVisibility.private_.wire, 'private');
      expect(FriendRequestsPolicy.nobody.wire, 'nobody');
      expect(SearchVisibility.hidden.wire, 'hidden');
    });

    test('public profile keeps relationship + limited off the wire', () {
      final p = PublicProfile.fromJson(publicProfileJson());
      expect(p.relationship, RelationshipState.none);
      expect(p.limited, isFalse);
      expect(p.placeLabel, 'Lyon, FR');
      expect(p.bestName, 'Sara Bench');
    });

    test('a limited profile withholds detail without losing identity', () {
      final p = PublicProfile.fromJson(
        publicProfileJson(
          limited: true,
          displayName: null,
          username: 'ghost_rider',
        ),
      );
      expect(p.limited, isTrue);
      expect(p.bio, isNull);
      expect(p.cyclingCategory, isNull);
      expect(p.placeLabel, isNull);
      expect(p.bestName, 'ghost_rider');
    });

    test('page knows when the server has more', () {
      final page = SocialPage.fromJson({
        'items': [publicProfileJson()],
        'total': 45,
        'page': 2,
        'page_size': 20,
      }, PublicProfile.fromJson);
      expect(page.hasMore, isTrue);
      expect(page.items.length, 1);
    });

    test('no social model carries email, tokens, or location', () {
      // A guard, not a style check: if a future field adds GPS or credentials,
      // this fails rather than shipping.
      // Compared per underscore-separated token, not as substrings: 'lat'
      // appears inside 'relationship', which is a required field.
      const banned = {
        'email',
        'password',
        'token',
        'tokens',
        'access',
        'refresh',
        'lat',
        'lon',
        'lng',
        'latitude',
        'longitude',
        'location',
        'locations',
        'gps',
        'position',
        'coordinate',
        'coordinates',
      };
      for (final json in [
        _myProfileJson,
        publicProfileJson(),
        {
          'id': 'r-1',
          'user_id': 'u-2',
          'username': null,
          'display_name': null,
          'avatar_url': null,
          'direction': 'incoming',
          'status': 'pending',
          'created_at': '2026-01-01T00:00:00Z',
        },
        {
          'user_id': 'u-2',
          'username': null,
          'display_name': null,
          'avatar_url': null,
          'friends_since': '2026-01-01T00:00:00Z',
        },
        {
          'user_id': 'u-2',
          'username': null,
          'display_name': null,
          'blocked_at': '2026-01-01T00:00:00Z',
        },
      ]) {
        for (final key in json.keys) {
          // Compared per underscore-separated token, not as a substring:
          // 'lat' occurs inside 'relationship', which is a required field.
          for (final part in key.toLowerCase().split('_')) {
            expect(
              banned.contains(part),
              isFalse,
              reason: 'social payload must not carry $key',
            );
          }
        }
      }
    });
  });

  group('validators mirror the backend', () {
    test('username rules', () {
      expect(SocialValidators.username('imad_fouri'), isNull);
      expect(SocialValidators.username('Imad.Fouri'), isNull); // lowercased
      expect(SocialValidators.username(''), isNull); // blank = unclaimed
      expect(SocialValidators.username('ab'), isNotNull);
      expect(SocialValidators.username('a' * 31), isNotNull);
      expect(SocialValidators.username('12345'), isNotNull); // needs a letter
      expect(SocialValidators.username('a.b.c'), isNotNull); // JWT-shaped
      expect(SocialValidators.username('.imad'), isNotNull);
      expect(SocialValidators.username('imad_'), isNotNull);
      expect(SocialValidators.username('user@example.com'), isNotNull);
      expect(SocialValidators.username('imad fouri'), isNotNull);
    });

    test('display name, bio, avatar, country, city bounds', () {
      expect(SocialValidators.displayName('Imad'), isNull);
      expect(SocialValidators.displayName('   '), isNotNull);
      expect(SocialValidators.displayName('a' * 81), isNotNull);
      expect(SocialValidators.bio('x' * 500), isNull);
      expect(SocialValidators.bio('x' * 501), isNotNull);
      expect(SocialValidators.avatarUrl('https://x.dev/a.png'), isNull);
      expect(SocialValidators.avatarUrl('ftp://x/a.png'), isNotNull);
      expect(SocialValidators.avatarUrl('javascript:alert(1)'), isNotNull);
      expect(SocialValidators.countryCode('MA'), isNull);
      expect(SocialValidators.countryCode('MAR'), isNotNull);
      expect(SocialValidators.countryCode('M1'), isNotNull);
      expect(SocialValidators.city('x' * 120), isNull);
      expect(SocialValidators.city('x' * 121), isNotNull);
    });

    test('search bounds match the endpoint query constraints', () {
      expect(SocialValidators.searchQuery('ab'), isNull);
      expect(SocialValidators.searchQuery('a'), isNotNull);
      expect(SocialValidators.searchQuery('a' * 65), isNotNull);
    });

    test('the combined form returns the first failure', () {
      expect(
        SocialValidators.profileForm(
          username: '!!',
          displayName: '',
          bio: '',
          cyclingCategory: '',
          countryCode: '',
          city: '',
        ),
        'social.invalidUsername',
      );
    });
  });

  group('repository', () {
    test('every social endpoint maps to the real path', () async {
      final state = FakeSocial();
      final repo = SocialRepository(api(socialBackend(state)));
      expect((await repo.myProfile()).username, 'imad_fouri');
      await repo.updateProfile(username: 'imad_fouri', displayName: 'Imad');
      await repo.updatePrivacy(profileVisibility: ProfileVisibility.private_);
      expect((await repo.userProfile('u-2')).userId, 'u-2');
      expect((await repo.searchUsers('sara')).items.length, 1);
      await repo.sendFriendRequest('u-2');
      expect((await repo.friendRequests()).items.length, 1);
      await repo.cancelFriendRequest('r-1');
      await repo.acceptFriendRequest('r-in-1');
      await repo.rejectFriendRequest('r-in-1');
      expect((await repo.friends()).items.length, 1);
      await repo.removeFriend('u-3');
      await repo.blockUser('u-4');
      expect((await repo.blocks()).items.length, 1);
      await repo.unblockUser('u-4');

      final paths = state.calls
          .map((c) => '${c['method']} ${c['path']}')
          .toList();
      expect(paths, contains('GET /api/v1/social/profile/me'));
      expect(paths, contains('PATCH /api/v1/social/profile'));
      expect(paths, contains('PATCH /api/v1/social/profile/privacy'));
      expect(paths, contains('GET /api/v1/social/profile/u-2'));
      expect(paths, contains('POST /api/v1/social/friend-requests'));
      expect(paths, contains('GET /api/v1/social/friend-requests'));
      expect(paths, contains('DELETE /api/v1/social/friend-requests/r-1'));
      expect(
        paths,
        contains('POST /api/v1/social/friend-requests/r-in-1/accept'),
      );
      expect(
        paths,
        contains('POST /api/v1/social/friend-requests/r-in-1/reject'),
      );
      expect(paths, contains('GET /api/v1/social/friends'));
      expect(paths, contains('DELETE /api/v1/social/friends/u-3'));
      expect(paths, contains('POST /api/v1/social/blocks'));
      expect(paths, contains('GET /api/v1/social/blocks'));
      expect(paths, contains('DELETE /api/v1/social/blocks/u-4'));
    });

    test('search sends only q, page, page_size — never email or id', () async {
      final state = FakeSocial();
      final repo = SocialRepository(api(socialBackend(state)));
      await repo.searchUsers('sara_bench');
      final query = state.calls.last['query'] as String;
      expect(query, contains('q=sara_bench'));
      expect(query, contains('page=1'));
      expect(query, contains('page_size=20'));
      expect(query.contains('email'), isFalse);
      expect(query.contains('user_id'), isFalse);
    });

    test(
      'a search query is url-encoded so spaces do not split the query',
      () async {
        final state = FakeSocial();
        final repo = SocialRepository(api(socialBackend(state)));
        await repo.searchUsers('sara bench&admin=1');
        final query = state.calls.last['query'] as String;
        expect(query, contains('q=sara+bench%26admin%3D1'));
      },
    );

    test('an empty search result is an empty page, not an error', () async {
      final repo = SocialRepository(api(socialBackend(FakeSocial())));
      final page = await repo.searchUsers('nobody');
      expect(page.items, isEmpty);
      expect(page.total, 0);
    });

    test(
      'a server error surfaces as ApiException, not a raw exception',
      () async {
        final state = FakeSocial()
          ..forcedError = 'SOCIAL_REQUEST_PENDING'
          ..status = 409;
        final repo = SocialRepository(api(socialBackend(state)));
        try {
          await repo.sendFriendRequest('u-2');
          fail('expected ApiException');
        } on ApiException catch (e) {
          expect(e.status, 409);
          expect(e.code, 'SOCIAL_REQUEST_PENDING');
        }
      },
    );

    test(
      'clearing a username sends an explicit null, not an empty string',
      () async {
        final captured = <Map<String, dynamic>>[];
        final backend = MockClient((req) async {
          if (req.method == 'PATCH' &&
              req.url.path.endsWith('/social/profile')) {
            captured.add(jsonDecode(req.body) as Map<String, dynamic>);
          }
          return http.Response(jsonEncode(_myProfileJson), 200);
        });
        final repo = SocialRepository(api(backend));
        await repo.updateProfile(
          username: 'imad_fouri',
          clearUsername: true,
          displayName: 'Imad',
        );
        expect(captured.single.containsKey('username'), isTrue);
        expect(captured.single['username'], isNull);
      },
    );
  });

  group('providers', () {
    test('my profile provider reads the server profile', () async {
      final c = container(socialBackend(FakeSocial()));
      final profile = await c.read(mySocialProfileProvider.future);
      expect(profile.displayName, 'Imad Fouri');
      expect(profile.profileVisibility, ProfileVisibility.public_);
      c.dispose();
    });

    test('search provider pages and reports hasMore', () async {
      final c = container(socialBackend(FakeSocial()));
      final page = await c.read(userSearchProvider('sara').future);
      expect(page.items.length, 1);
      expect(page.items.first.relationship, RelationshipState.none);
      c.dispose();
    });

    test('friends, requests, and blocks each load their own page', () async {
      final c = container(socialBackend(FakeSocial()));
      expect((await c.read(friendListProvider.future)).items.length, 1);
      expect((await c.read(incomingRequestsProvider.future)).items.length, 1);
      expect((await c.read(outgoingRequestsProvider.future)).items.length, 0);
      expect((await c.read(blockListProvider.future)).items.length, 1);
      c.dispose();
    });

    test('loadMore stops at the last page instead of looping', () async {
      final state = FakeSocial();
      final c = container(socialBackend(state));
      await c.read(friendListProvider.future);
      await c.read(friendListProvider.notifier).loadMore();
      await c.read(friendListProvider.notifier).loadMore();
      final friendGets = state.calls
          .where(
            (c2) =>
                c2['method'] == 'GET' && '${c2['path']}'.endsWith('/friends'),
          )
          .length;
      // 1 initial + at most 1 extra attempt; the page has no more rows.
      expect(friendGets, lessThanOrEqualTo(2));
      c.dispose();
    });
  });

  group('relationship transitions follow the server', () {
    test('none → outgoing pending', () async {
      final state = FakeSocial();
      final c = container(socialBackend(state));
      await c.read(userProfileProvider('u-2').future);
      await c.read(socialActionsProvider.notifier).sendFriendRequest('u-2');
      final after = await c.read(userProfileProvider('u-2').future);
      expect(after.relationship, RelationshipState.outgoingPending);
      c.dispose();
    });

    test('outgoing pending → cancelled', () async {
      final state = FakeSocial();
      final c = container(socialBackend(state));
      state.relationships['u-2'] = 'OUTGOING_PENDING';
      state.pendingIds['u-2'] = 'r-1';
      await c.read(userProfileProvider('u-2').future);
      await c.read(socialActionsProvider.notifier).cancelOutgoingRequest('u-2');
      final after = await c.read(userProfileProvider('u-2').future);
      expect(after.relationship, RelationshipState.none);
      expect(state.pendingIds.containsKey('u-2'), isFalse);
      c.dispose();
    });

    test('incoming pending → accepted', () async {
      final state = FakeSocial();
      final c = container(socialBackend(state));
      state.relationships['u-3'] = 'INCOMING_PENDING';
      await c.read(userProfileProvider('u-3').future);
      await c
          .read(socialActionsProvider.notifier)
          .acceptFriendRequest('r-in-1', 'u-3');
      final after = await c.read(userProfileProvider('u-3').future);
      expect(after.relationship, RelationshipState.friends);
      c.dispose();
    });

    test('incoming pending → rejected', () async {
      final state = FakeSocial();
      final c = container(socialBackend(state));
      state.relationships['u-3'] = 'INCOMING_PENDING';
      await c
          .read(socialActionsProvider.notifier)
          .rejectFriendRequest('r-in-1', 'u-3');
      expect(
        state.calls.any((x) => '${x['path']}'.endsWith('/reject')),
        isTrue,
      );
      c.dispose();
    });

    test('accepted → removed', () async {
      final state = FakeSocial();
      final c = container(socialBackend(state));
      state.relationships['u-3'] = 'FRIENDS';
      await c.read(userProfileProvider('u-3').future);
      await c.read(socialActionsProvider.notifier).removeFriend('u-3');
      final after = await c.read(userProfileProvider('u-3').future);
      expect(after.relationship, RelationshipState.none);
      c.dispose();
    });

    test('block destroys the friendship server-side', () async {
      final state = FakeSocial();
      final c = container(socialBackend(state));
      state.relationships['u-3'] = 'FRIENDS';
      await c.read(userProfileProvider('u-3').future);
      await c.read(socialActionsProvider.notifier).blockUser('u-3');
      expect(state.blocks.contains('u-3'), isTrue);
      final after = await c.read(userProfileProvider('u-3').future);
      expect(after.relationship, RelationshipState.blocked);
      c.dispose();
    });

    test('unblock does NOT restore the friendship', () async {
      final state = FakeSocial();
      final c = container(socialBackend(state));
      state.blocks.add('u-3');
      state.relationships['u-3'] = 'BLOCKED';
      await c.read(userProfileProvider('u-3').future);
      await c.read(socialActionsProvider.notifier).unblockUser('u-3');
      final after = await c.read(userProfileProvider('u-3').future);
      expect(after.relationship, RelationshipState.none);
      expect(
        after.relationship == RelationshipState.friends,
        isFalse,
        reason: 'unblocking must never silently re-create a friendship',
      );
      c.dispose();
    });

    test(
      'a mutation refreshes friends, requests, blocks, and search',
      () async {
        final state = FakeSocial();
        final c = container(socialBackend(state));
        await c.read(friendListProvider.future);
        await c.read(incomingRequestsProvider.future);
        await c.read(blockListProvider.future);
        await c.read(userSearchProvider('sara').future);
        state.calls.clear();
        await c.read(socialActionsProvider.notifier).sendFriendRequest('u-2');
        // Every invalidated provider re-runs its read on next access.
        await c.read(friendListProvider.future);
        await c.read(incomingRequestsProvider.future);
        await c.read(blockListProvider.future);
        await c.read(userSearchProvider('sara').future);
        expect(
          state.calls.any((x) => '${x['path']}'.endsWith('/friends')),
          isTrue,
        );
        expect(
          state.calls.any((x) => '${x['path']}'.endsWith('/friend-requests')),
          isTrue,
        );
        expect(
          state.calls.any((x) => '${x['path']}'.endsWith('/blocks')),
          isTrue,
        );
        expect(
          state.calls.any((x) => '${x['path']}'.endsWith('/users/search')),
          isTrue,
        );
        c.dispose();
      },
    );
  });

  group('duplicate-submission protection', () {
    test('a second send while the first is in flight is dropped', () async {
      final state = FakeSocial()..slow = true;
      final c = container(socialBackend(state));
      final actions = c.read(socialActionsProvider.notifier);
      await Future.wait([
        actions.sendFriendRequest('u-2'),
        actions.sendFriendRequest('u-2'),
        actions.sendFriendRequest('u-2'),
      ]);
      final sends = state.calls
          .where(
            (x) =>
                x['method'] == 'POST' &&
                '${x['path']}'.endsWith('/friend-requests'),
          )
          .length;
      expect(sends, 1, reason: 'three taps must produce one POST');
      c.dispose();
    });

    test(
      'the busy set is exposed while a mutation runs and cleared after',
      () async {
        final state = FakeSocial()..slow = true;
        final c = container(socialBackend(state));
        final key = SocialActions.userKey('request', 'u-2');
        final future = c
            .read(socialActionsProvider.notifier)
            .sendFriendRequest('u-2');
        expect(c.read(socialActionsProvider), contains(key));
        await future;
        expect(c.read(socialActionsProvider), isNot(contains(key)));
        c.dispose();
      },
    );

    test('distinct actions on the same rider do not share a lock', () async {
      final state = FakeSocial()..slow = true;
      final c = container(socialBackend(state));
      final actions = c.read(socialActionsProvider.notifier);
      await Future.wait([
        actions.sendFriendRequest('u-2'),
        actions.blockUser('u-2'),
      ]);
      final posts = state.calls.where((x) => x['method'] == 'POST').length;
      expect(posts, 2);
      c.dispose();
    });

    test(
      'two different riders are not serialized against each other',
      () async {
        final state = FakeSocial()..slow = true;
        final c = container(socialBackend(state));
        final actions = c.read(socialActionsProvider.notifier);
        await Future.wait([
          actions.sendFriendRequest('u-2'),
          actions.sendFriendRequest('u-3'),
        ]);
        final sends = state.calls
            .where(
              (x) =>
                  x['method'] == 'POST' &&
                  '${x['path']}'.endsWith('/friend-requests'),
            )
            .length;
        expect(sends, 2);
        c.dispose();
      },
    );

    test(
      'the busy flag clears even when the server rejects the call',
      () async {
        final state = FakeSocial()
          ..forcedError = 'SOCIAL_REQUEST_PENDING'
          ..status = 409;
        final c = container(socialBackend(state));
        final actions = c.read(socialActionsProvider.notifier);
        await expectLater(
          actions.sendFriendRequest('u-2'),
          throwsA(isA<ApiException>()),
        );
        expect(c.read(socialActionsProvider), isEmpty);
        c.dispose();
      },
    );
  });

  group('error mapping', () {
    AppLocalizations t(String lang) => AppLocalizations(Locale(lang));

    test('status codes map to distinct localized sentences', () {
      final en = t('en');
      final cases = {
        401: 'Please sign in again.',
        403: 'This rider is not accepting requests.',
        404: 'That rider is not available.',
        409: 'That action conflicts with the current state.',
        422: 'Please check the details and try again.',
        429: 'Too many attempts. Wait a moment.',
        500: 'The server had a problem. Try again shortly.',
      };
      cases.forEach((status, message) {
        expect(
          friendlyErrorForTest(en, ApiException(status, 'X', 'raw')),
          message,
          reason: 'status $status',
        );
      });
    });

    test('a network failure is status 0 and reads as offline', () {
      expect(
        friendlyErrorForTest(t('en'), ApiException(0, 'NETWORK_ERROR', 'x')),
        'No connection. Check your network and retry.',
      );
    });

    test('social error codes win over the bare status', () {
      final en = t('en');
      expect(
        friendlyErrorForTest(
          en,
          ApiException(409, 'SOCIAL_USERNAME_TAKEN', 'x'),
        ),
        'That username is taken.',
      );
      expect(
        friendlyErrorForTest(
          en,
          ApiException(409, 'SOCIAL_ALREADY_FRIENDS', 'x'),
        ),
        'You are already friends.',
      );
      expect(
        friendlyErrorForTest(
          en,
          ApiException(409, 'SOCIAL_REQUEST_PENDING', 'x'),
        ),
        'A request is already pending.',
      );
      expect(
        friendlyErrorForTest(
          en,
          ApiException(403, 'SOCIAL_REQUESTS_NOT_ALLOWED', 'x'),
        ),
        'This rider is not accepting requests.',
      );
    });

    test('not-found codes collapse to one message (no existence oracle)', () {
      final en = t('en');
      for (final code in [
        'SOCIAL_USER_NOT_FOUND',
        'SOCIAL_REQUEST_NOT_FOUND',
        'SOCIAL_FRIENDSHIP_NOT_FOUND',
        'SOCIAL_BLOCK_NOT_FOUND',
      ]) {
        expect(
          friendlyErrorForTest(en, ApiException(404, code, 'x')),
          'That rider is not available.',
        );
      }
    });

    test('an unknown error never leaks a raw code to the user', () {
      final message = friendlyErrorForTest(
        t('en'),
        ApiException(418, 'SOCIAL_TEAPOT', 'raw internal detail'),
      );
      expect(message.contains('TEAPOT'), isFalse);
      expect(message.contains('raw internal detail'), isFalse);
      expect(message, 'Something went wrong. Try again.');
    });

    test('a non-API exception degrades to the generic sentence', () {
      expect(
        friendlyErrorForTest(t('en'), StateError('boom')),
        'Something went wrong. Try again.',
      );
    });

    test('every error sentence is localized in fr and ar', () {
      final statuses = [401, 403, 404, 409, 422, 429, 500, 0];
      for (final lang in ['en', 'fr', 'ar']) {
        final loc = t(lang);
        for (final status in statuses) {
          final message = friendlyErrorForTest(
            loc,
            ApiException(status, 'X', 'x'),
          );
          expect(message, isNotEmpty);
          expect(
            message,
            isNot(loc.get('social.error.generic')),
            reason: 'unmapped $status in $lang',
          );
        }
      }
    });
  });

  group('localization', () {
    test('the minimum concept set exists in all three languages', () {
      const keys = [
        'social.myProfile',
        'social.editProfile',
        'social.username',
        'social.displayName',
        'social.bio',
        'social.cyclingCategory',
        'social.country',
        'social.city',
        'social.friends',
        'social.friendRequests',
        'social.incoming',
        'social.outgoing',
        'social.addFriend',
        'social.cancelRequest',
        'social.accept',
        'social.reject',
        'social.removeFriend',
        'social.block',
        'social.unblock',
        'social.blockedUsers',
        'social.privacy',
        'social.visibility.public',
        'social.visibility.friends',
        'social.visibility.private',
        'social.requests.everyone',
        'social.requests.nobody',
        'social.search.discoverable',
        'social.search.hidden',
        'social.search',
        'social.noResults',
        'social.noFriends',
        'social.noRequests',
        'social.loading',
        'social.retry',
        'social.error',
        'social.requestSent',
        'social.requestAccepted',
        'social.requestRejected',
        'social.friendRemoved',
        'social.userBlocked',
        'social.userUnblocked',
        'social.state.SELF',
        'social.state.NONE',
        'social.state.OUTGOING_PENDING',
        'social.state.INCOMING_PENDING',
        'social.state.FRIENDS',
        'social.state.BLOCKED',
        'social.state.BLOCKED_BY_USER',
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
      expect(en.get('social.friends'), 'Friends');
      expect(en.get('social.visibility.friends'), 'Friends only');
      expect(en.get('social.state.INCOMING_PENDING'), 'Wants to connect');
    });

    test('French renders the expected copy', () {
      final fr = AppLocalizations(const Locale('fr'));
      expect(fr.get('social.friends'), 'Amis');
      expect(fr.get('social.visibility.private'), 'Privé');
      expect(fr.get('social.blockedUsers'), 'Utilisateurs bloqués');
    });

    test('Arabic renders correct cycling terminology', () {
      final ar = AppLocalizations(const Locale('ar'));
      expect(ar.get('social.friends'), 'الأصدقاء');
      expect(ar.get('social.addFriend'), 'إضافة صديق');
      expect(ar.get('social.visibility.private'), 'خاص');
      // "رحلة" is a ride; the app must not use the driving word.
      expect(ar.get('social.addFriend').contains('قيادة'), isFalse);
    });

    test('Arabic social copy leaks no Latin words', () {
      final ar = AppLocalizations(const Locale('ar'));
      const keys = [
        'social.myProfile',
        'social.userProfile',
        'social.editProfile',
        'social.privacy',
        'social.username',
        'social.displayName',
        'social.bio',
        'social.cyclingCategory',
        'social.country',
        'social.city',
        'social.friends',
        'social.friendRequests',
        'social.incoming',
        'social.outgoing',
        'social.addFriend',
        'social.cancelRequest',
        'social.accept',
        'social.reject',
        'social.removeFriend',
        'social.block',
        'social.unblock',
        'social.blockedUsers',
        'social.search',
        'social.noResults',
        'social.noFriends',
        'social.noRequests',
        'social.retry',
        'social.requestSent',
        'social.requestAccepted',
        'social.requestRejected',
        'social.friendRemoved',
        'social.userBlocked',
        'social.userUnblocked',
        'social.state.SELF',
        'social.state.NONE',
        'social.state.OUTGOING_PENDING',
        'social.state.INCOMING_PENDING',
        'social.state.FRIENDS',
        'social.state.BLOCKED',
        'social.state.BLOCKED_BY_USER',
      ];
      for (final key in keys) {
        final value = ar.get(key);
        // Country codes and URLs are legitimate Latin; prose must not be.
        expect(
          RegExp(r'[A-Za-z]').hasMatch(value),
          isFalse,
          reason: '$key: $value',
        );
      }
    });

    test('French social copy leaks no Arabic', () {
      final fr = AppLocalizations(const Locale('fr'));
      for (final key in [
        'social.friends',
        'social.block',
        'social.accept',
        'social.reject',
      ]) {
        expect(
          RegExp(r'[\u0600-\u06FF]').hasMatch(fr.get(key)),
          isFalse,
          reason: key,
        );
      }
    });

    test('relationship labels resolve for every server state', () {
      for (final state in RelationshipState.values) {
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
    test(
      'social routes are registered and /friends is no longer a placeholder',
      () {
        final source = _readRouterSource();
        expect(source.contains("path: '/profile/social'"), isTrue);
        expect(source.contains("path: '/profile/social/edit'"), isTrue);
        expect(source.contains("path: '/profile/social/privacy'"), isTrue);
        expect(source.contains("path: '/friends'"), isTrue);
        expect(source.contains("path: '/friends/search'"), isTrue);
        expect(source.contains("path: '/friends/requests'"), isTrue);
        expect(source.contains("path: '/friends/blocked'"), isTrue);
        expect(source.contains("path: '/users/:userId'"), isTrue);
        expect(
          source.contains("const FriendsPage()"),
          isTrue,
          reason: '/friends must resolve to the real page',
        );
        // The placeholder loop must no longer own /friends.
        final placeholderBlock = source.substring(
          source.indexOf('for (final p in ['),
        );
        expect(placeholderBlock.contains("'/friends'"), isFalse);
        // Pre-existing routes must survive.
        for (final kept in [
          "'/login'",
          "'/register'",
          "'/onboarding'",
          "'/home'",
          "'/profile'",
          "'/bikes'",
          "'/rides'",
          "'/routes'",
          "'/training'",
          "'/coach'",
          "'/settings'",
          "'/chat'",
        ]) {
          expect(source.contains(kept), isTrue, reason: '$kept was removed');
        }
      },
    );
  });
}

/// The error mapper is private to the widgets library; re-expose it for the
/// unit tests through the public surface the screens actually use.
String friendlyErrorForTest(AppLocalizations t, Object e) =>
    friendlyError(t, e);

String _readRouterSource() {
  // The router is a pure Dart file; reading it keeps this a static contract
  // check rather than a widget pump.
  return File('lib/core/routing/app_router.dart').readAsStringSync();
}
