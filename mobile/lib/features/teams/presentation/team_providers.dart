import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_state.dart';
import '../../social/presentation/social_providers.dart';
import '../data/team_repository.dart';
import '../domain/team.dart';

final teamRepositoryProvider = Provider<TeamRepository>(
  (ref) => TeamRepository(ref.watch(apiClientProvider)),
);

/// One page of a listing plus the cursor state the list widgets need.
class PagedTeam<T> {
  final List<T> items;
  final int total;
  final int page;
  final int pageSize;
  final bool loadingMore;

  const PagedTeam({
    required this.items,
    required this.total,
    required this.page,
    required this.pageSize,
    required this.loadingMore,
  });

  bool get hasMore => page * pageSize < total;

  PagedTeam<T> copyWith({
    List<T>? items,
    int? total,
    int? page,
    bool? loadingMore,
  }) => PagedTeam<T>(
    items: items ?? this.items,
    total: total ?? this.total,
    page: page ?? this.page,
    pageSize: pageSize,
    loadingMore: loadingMore ?? this.loadingMore,
  );
}

final myTeamsProvider = FutureProvider.autoDispose.family<TeamPage, bool>((
  ref,
  includeArchived,
) {
  return ref
      .watch(teamRepositoryProvider)
      .myTeams(pageSize: 100, includeArchived: includeArchived);
});

final teamDetailProvider = FutureProvider.autoDispose.family<Team, String>((
  ref,
  teamId,
) {
  return ref.watch(teamRepositoryProvider).team(teamId);
});

/// Team members for one team. `autoDispose` so leaving the screen drops a
/// roster that may have changed while the rider was elsewhere.
final teamMembersProvider = FutureProvider.autoDispose
    .family<TeamMemberPage, String>((ref, teamId) {
      return ref.watch(teamRepositoryProvider).members(teamId, pageSize: 100);
    });

/// Managers only. The server answers 404 to a non-manager, so an unauthorized
/// viewer sees an error rather than somebody else's request queue.
final teamJoinRequestsProvider = FutureProvider.autoDispose
    .family<TeamJoinRequestPage, String>((ref, teamId) {
      return ref
          .watch(teamRepositoryProvider)
          .joinRequests(teamId, pageSize: 100);
    });

final teamInvitationsProvider = FutureProvider.autoDispose
    .family<TeamInvitationPage, String>((ref, teamId) {
      return ref
          .watch(teamRepositoryProvider)
          .teamInvitations(teamId, pageSize: 100);
    });

/// The viewer's own outbound asks.
final myJoinRequestsProvider = FutureProvider.autoDispose<TeamJoinRequestPage>((
  ref,
) {
  return ref.watch(teamRepositoryProvider).myJoinRequests(pageSize: 100);
});

/// The viewer's invitation inbox.
final myInvitationsProvider = FutureProvider.autoDispose<TeamInvitationPage>((
  ref,
) {
  return ref.watch(teamRepositoryProvider).myInvitations(pageSize: 100);
});

/// Team search, keyed by the exact query string. Debounced by the page before
/// it asks for a provider, and `autoDispose` so each keystroke's result is
/// dropped rather than cached.
final teamSearchProvider = FutureProvider.autoDispose.family<TeamPage, String>((
  ref,
  query,
) {
  return ref.watch(teamRepositoryProvider).search(query, pageSize: 50);
});

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/// Server-confirmed team mutations with duplicate-submission protection.
///
/// Mirrors the Phase 8.1 rules exactly:
/// 1. A key already in flight short-circuits, and each button reads its busy
///    flag from this same set, so the guard and the disabled button cannot
///    disagree.
/// 2. Nothing is applied optimistically. The call awaits the server, then
///    invalidates every provider whose contents the server may have changed.
///
/// Team state is never computed locally. After a mutation the roster, the
/// team detail, and the member lists are re-read so a stale role badge cannot
/// outlive the change that removed it.
class TeamActions extends Notifier<Set<String>> {
  @override
  Set<String> build() => const <String>{};

  TeamRepository get repo => ref.read(teamRepositoryProvider);

  bool isBusy(String key) => state.contains(key);

  static String teamKey(String action, String teamId) => '$action:$teamId';
  static String memberKey(String action, String teamId, String userId) =>
      '$action:$teamId:$userId';
  static String inviteKey(String teamId, String userId) =>
      'invite:$teamId:$userId';
  static String requestKey(String action, String teamId, String requestId) =>
      '$action:$teamId:$requestId';

  /// Invalidate everything whose truth the server just decided.
  ///
  /// Social providers are invalidated too: accepting a join request or
  /// removing a member changes what `search` should show, and the social
  /// surfaces do not otherwise know a team changed. Friendship rows are NOT
  /// touched — no method here writes one (ADR-13 §5).
  void _afterMutation({String? teamId, bool touchSocial = false}) {
    ref.invalidate(myTeamsProvider);
    ref.invalidate(myInvitationsProvider);
    ref.invalidate(myJoinRequestsProvider);
    if (teamId != null) {
      ref.invalidate(teamDetailProvider(teamId));
      ref.invalidate(teamMembersProvider(teamId));
      ref.invalidate(teamJoinRequestsProvider(teamId));
      ref.invalidate(teamInvitationsProvider(teamId));
    }
    ref.invalidate(teamSearchProvider);
    if (touchSocial) {
      ref.invalidate(friendListProvider);
      ref.invalidate(userSearchProvider);
    }
  }

  Future<void> createTeam({
    required String name,
    required String handle,
    required String description,
    required String avatarUrl,
    required String category,
    required TeamVisibility visibility,
  }) => _run('create', () async {
    await repo.create(
      name: name,
      handle: handle,
      description: description,
      avatarUrl: avatarUrl,
      category: category,
      visibility: visibility,
    );
    _afterMutation();
  });

  Future<void> updateTeam({
    required String teamId,
    required String name,
    required String handle,
    required String description,
    required String avatarUrl,
    required String category,
    required TeamVisibility visibility,
    required bool clearHandle,
  }) => _run(teamKey('update', teamId), () async {
    await repo.update(
      teamId,
      name: name,
      handle: handle,
      clearHandle: clearHandle,
      description: description,
      avatarUrl: avatarUrl,
      category: category,
      visibility: visibility,
    );
    _afterMutation(teamId: teamId);
  });

  Future<void> archiveTeam(String teamId) =>
      _run(teamKey('archive', teamId), () async {
        await repo.archive(teamId);
        _afterMutation(teamId: teamId);
      });

  /// Returns the server's `status` so a caller can report "joined" vs
  /// "requested" without guessing from team visibility.
  Future<String> join(String teamId) async {
    var status = '';
    await _run(teamKey('join', teamId), () async {
      final result = await repo.join(teamId);
      status = result.status;
      _afterMutation(teamId: teamId);
    });
    return status;
  }

  Future<void> cancelJoinRequest(String teamId, String requestId) =>
      _run(requestKey('cancel', teamId, requestId), () async {
        await repo.cancelJoinRequest(teamId, requestId);
        _afterMutation(teamId: teamId);
      });

  /// Withdraw the viewer's own outstanding ask from a team profile, where only
  /// the state (not the request id) is known.
  ///
  /// The id is resolved by listing the viewer's own requests rather than
  /// constructed locally — a guessed id would be a 404, and worse, an id that
  /// happens to exist for a different team.
  Future<void> cancelMyJoinRequest(String teamId) =>
      _run(teamKey('join', teamId), () async {
        final mine = await repo.myJoinRequests(pageSize: 100);
        final match = mine.items.where((r) => r.teamId == teamId).firstOrNull;
        if (match != null) {
          await repo.cancelJoinRequest(teamId, match.id);
        }
        _afterMutation(teamId: teamId);
      });

  Future<void> acceptJoinRequest(String teamId, String requestId) =>
      _run(requestKey('accept', teamId, requestId), () async {
        await repo.acceptJoinRequest(teamId, requestId);
        _afterMutation(teamId: teamId);
      });

  Future<void> rejectJoinRequest(String teamId, String requestId) =>
      _run(requestKey('reject', teamId, requestId), () async {
        await repo.rejectJoinRequest(teamId, requestId);
        _afterMutation(teamId: teamId);
      });

  Future<void> invite(String teamId, String userId, {String? message}) =>
      _run(inviteKey(teamId, userId), () async {
        await repo.invite(teamId, userId, message: message);
        _afterMutation(teamId: teamId);
      });

  Future<void> acceptInvitation(String invitationId) =>
      _run(requestKey('acceptinv', '', invitationId), () async {
        await repo.acceptInvitation(invitationId);
        _afterMutation(touchSocial: true);
      });

  Future<void> declineInvitation(String invitationId) =>
      _run(requestKey('declineinv', '', invitationId), () async {
        await repo.declineInvitation(invitationId);
        _afterMutation();
      });

  Future<void> revokeInvitation(String teamId, String invitationId) =>
      _run(requestKey('revoke', teamId, invitationId), () async {
        await repo.revokeInvitation(teamId, invitationId);
        _afterMutation(teamId: teamId);
      });

  Future<void> removeMember(String teamId, String userId) =>
      _run(memberKey('remove', teamId, userId), () async {
        await repo.removeMember(teamId, userId);
        _afterMutation(teamId: teamId);
      });

  Future<void> setRole(String teamId, String userId, TeamRole role) =>
      _run(memberKey('role', teamId, userId), () async {
        await repo.setRole(teamId, userId, role);
        _afterMutation(teamId: teamId);
      });

  Future<void> leave(String teamId) => _run(teamKey('leave', teamId), () async {
    await repo.leave(teamId);
    // touchSocial is false: leaving a team does not touch friendships
    // (ADR-13 §5). Only the social *identity* surfaces are re-read, and only
    // because they may show the viewer's own team count.
    _afterMutation(teamId: teamId);
  });

  Future<void> _run(String key, Future<void> Function() body) async {
    if (state.contains(key)) return;
    state = {...state, key};
    try {
      await body();
    } finally {
      final next = {...state};
      next.remove(key);
      state = next;
    }
  }
}

final teamActionsProvider = NotifierProvider<TeamActions, Set<String>>(
  TeamActions.new,
);
