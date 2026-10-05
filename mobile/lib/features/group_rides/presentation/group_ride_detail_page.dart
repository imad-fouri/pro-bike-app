import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../chat/presentation/conversation_page.dart';
import '../../ride/domain/location_source.dart';
import '../../ride/presentation/ride_providers.dart';
import '../../social/presentation/social_widgets.dart';
import '../domain/group_ride.dart';
import 'group_ride_providers.dart';
import 'group_ride_widgets.dart';
import 'ride_invite_sheet.dart';

/// One group ride: what it is, who is on it, and the one control each viewer has.
///
/// Every action here is gated on BOTH the ride's status and the viewer's roster
/// row, and the gate is the same predicate the backend enforces. That duplication
/// is the point: a button that is visible but always 409s teaches riders that the
/// app does not know its own state, and a button that is missing when it should
/// exist reads as a bug. Where they disagree, the server wins — this only decides
/// what is offered, never what is allowed (ADR-16 §3).
class GroupRideDetailPage extends ConsumerStatefulWidget {
  final String rideId;

  const GroupRideDetailPage({super.key, required this.rideId});

  @override
  ConsumerState<GroupRideDetailPage> createState() =>
      _GroupRideDetailPageState();
}

class _GroupRideDetailPageState extends ConsumerState<GroupRideDetailPage> {
  @override
  void initState() {
    super.initState();
    // Kick the first read after the frame, NOT here. `rideLocationProvider` is an
    // `autoDispose` family: reading it in `initState` would build a controller that
    // nothing is listening to yet, so it would be disposed before the first frame
    // and the poll would start against a dead object. By the next frame this
    // widget is watching it, so the controller survives.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      ref.read(rideLocationProvider(widget.rideId).notifier).startPollingNow();
    });
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final rideId = widget.rideId;
    final detail = ref.watch(rideDetailProvider(rideId));

    return Scaffold(
      appBar: AppBar(
        title: Text(detail.asData?.value.title ?? t.get('groupRide.rideTitle')),
      ),
      body: detail.when(
        loading: () => Center(child: CircularProgressIndicator()),
        error: (e, _) => SocialErrorView(
          error: e,
          message: friendlyRideError(t, e),
          onRetry: () => ref.invalidate(rideDetailProvider(rideId)),
        ),
        data: (ride) => _Body(ride: ride),
      ),
    );
  }
}

class _Body extends ConsumerWidget {
  final GroupRide ride;

  const _Body({required this.ride});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final busy = ref.watch(groupRideActionsProvider);
    final myId = ref.watch(myUserIdProvider).asData?.value;

    return RefreshIndicator(
      onRefresh: () async => ref.invalidate(rideDetailProvider(ride.id)),
      child: ListView(
        padding: const EdgeInsets.all(AppSpacing.md),
        children: [
          Row(
            children: [
              RideStatusBadge(status: ride.status),
              const SizedBox(width: AppSpacing.sm),
              if (ride.viewer.isOrganizer)
                Text(
                  t.get('groupRide.organizer'),
                  style: const TextStyle(fontSize: 12, color: AppColors.gold),
                ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(ride.title, style: const TextStyle(fontSize: 20)),
          if (ride.description != null && ride.description!.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.xs),
            Text(ride.description!),
          ],
          const SizedBox(height: AppSpacing.sm),
          _Fact(
            icon: Icons.event_rounded,
            text: ride.startsAt == null
                ? t.get('groupRide.noDate')
                : formatRideWhen(ride.startsAt!),
          ),
          if (ride.meetingPoint != null && ride.meetingPoint!.isNotEmpty)
            _Fact(
              icon: Icons.place_outlined,
              text: t.getWith('groupRide.meetingAt', {
                'place': ride.meetingPoint!,
              }),
            ),
          if (ride.hasRoute)
            _Fact(
              icon: Icons.alt_route_rounded,
              // The pin is a version, and it is shown as one. A rider who sees
              // only a route name cannot tell that this ride is frozen to v3 while
              // their route is now at v5.
              text: t.getWith('groupRide.pinnedTo', {
                'n': '${ride.routeVersion}',
              }),
              onTap: () => context.push('/routes/${ride.routeId}'),
            ),
          const SizedBox(height: AppSpacing.md),
          _Actions(ride: ride, busy: busy, myUserId: myId),
          const Divider(height: AppSpacing.xl),
          Text(
            t.get('groupRide.roster'),
            style: const TextStyle(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: AppSpacing.xs),
          RideRosterList(
            ride: ride,
            myUserId: myId,
            onRemove: _canRemoveRiders(ride, myId)
                ? (p) => _confirmRemove(context, ref, p)
                : null,
          ),
          const Divider(height: AppSpacing.xl),
          _ChatEntry(ride: ride),
          // Location is only meaningful while the ride is actually running: a
          // map for a ride that has not started would be a screen of "nobody is
          // sharing" that looks broken rather than not-yet-applicable.
          if (ride.status.allowsLiveLocation) ...[
            const Divider(height: AppSpacing.xl),
            RideLocationPanel(
              location: ref.watch(rideLocationProvider(ride.id)),
              onToggleSharing: () => _toggleSharing(context, ref),
              onOpenSettings: () =>
                  ref.read(locationSourceProvider).openSettings(),
              onRetry: () =>
                  ref.read(rideLocationProvider(ride.id).notifier).refresh(),
            ),
          ],
        ],
      ),
    );
  }

  /// A rider can be removed only while the ride is running — the roster is frozen
  /// at start, so before that the organizer can decline to invite them instead.
  static bool _canRemoveRiders(GroupRide ride, String? myId) {
    if (myId == null) return false;
    return ride.viewer.isOrganizer &&
        ride.status == GroupRideStatus.started &&
        ride.viewer.isJoined;
  }

  Future<void> _toggleSharing(BuildContext context, WidgetRef ref) async {
    final t = context.l10n;
    final controller = ref.read(rideLocationProvider(ride.id).notifier);
    if (controller.isSharing) {
      await controller.stopSharing();
      return;
    }
    // Starting is always an explicit tap, and the permission outcome is reported
    // rather than swallowed: a rider who taps share and sees nothing happen would
    // assume they are being tracked and are not.
    final permission = await controller.startSharing();
    if (!context.mounted) return;
    if (permission != LocationPermissionState.granted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(t.get(_permissionKey(permission)))),
      );
    }
  }

  static String _permissionKey(
    LocationPermissionState permission,
  ) => switch (permission) {
    LocationPermissionState.granted => 'groupRide.location.granted',
    LocationPermissionState.denied => 'groupRide.location.denied',
    LocationPermissionState.deniedForever => 'groupRide.location.deniedForever',
    LocationPermissionState.serviceDisabled => 'groupRide.location.serviceOff',
    LocationPermissionState.imprecise => 'groupRide.location.imprecise',
  };

  Future<void> _confirmRemove(
    BuildContext context,
    WidgetRef ref,
    GroupRideParticipant participant,
  ) async {
    final t = context.l10n;
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        content: Text(
          t.getWith('groupRide.removeConfirm', {'name': participant.label}),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: Text(t.get('routes.cancel')),
          ),
          FilledButton(
            key: const Key('groupRide.remove.confirm'),
            onPressed: () => Navigator.of(context).pop(true),
            child: Text(t.get('groupRide.remove')),
          ),
        ],
      ),
    );
    if (ok != true || !context.mounted) return;
    await _run(
      context,
      ref,
      GroupRideActions.targetKey('remove', ride.id, participant.userId),
      () => ref
          .read(groupRideActionsProvider.notifier)
          .removeParticipant(ride.id, participant.userId),
    );
  }
}

/// The lifecycle controls, exactly as many as this viewer is entitled to.
///
/// Two terminal rides show no controls at all. An organizer looking at a completed
/// ride is not being deprived of "finish" — the finish already happened, and
/// offering it again would be an invitation to a 409.
class _Actions extends ConsumerWidget {
  final GroupRide ride;
  final Set<String> busy;

  /// Nullable while the signed-in id is still loading. Every gate below treats
  /// null as "no authority", so the momentary gap shows no buttons rather than the
  /// wrong set of them.
  final String? myUserId;

  const _Actions({
    required this.ride,
    required this.busy,
    required this.myUserId,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;

    bool isBusy(String action) =>
        busy.contains(GroupRideActions.rideKey(action, ride.id));

    if (ride.status.isTerminal) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
        child: Text(
          t.get(
            ride.status == GroupRideStatus.cancelled
                ? 'groupRide.rideCancelled'
                : 'groupRide.rideFinished',
          ),
          style: const TextStyle(color: AppColors.textMuted),
        ),
      );
    }

    final myStatus = ride.viewerStatus(myUserId);

    // Holding no roster row: nothing at all is offered. This doubles as the "no
    // authority" render while [myUserId] is null, which is the safe direction — a
    // wrong button costs a 409 the rider cannot explain, a missing one costs a
    // refresh.
    if (myStatus == null) return const SizedBox.shrink();

    final buttons = <Widget>[];

    // Answering an invitation. `invited` is the ONLY state that can answer —
    // once joined, the way out is "leave", which is a different decision and must
    // not sit behind the same button.
    if (myStatus.isPending) {
      buttons.addAll([
        FilledButton(
          key: const Key('groupRide.action.accept'),
          onPressed: isBusy('accept')
              ? null
              : () => _run(
                  context,
                  ref,
                  GroupRideActions.rideKey('accept', ride.id),
                  () => ref
                      .read(groupRideActionsProvider.notifier)
                      .respond(ride.id, accept: true),
                ),
          child: Text(t.get('groupRide.accept')),
        ),
        TextButton(
          key: const Key('groupRide.action.decline'),
          onPressed: isBusy('decline')
              ? null
              : () => _run(
                  context,
                  ref,
                  GroupRideActions.rideKey('decline', ride.id),
                  () => ref
                      .read(groupRideActionsProvider.notifier)
                      .respond(ride.id, accept: false),
                ),
          child: Text(t.get('groupRide.decline')),
        ),
      ]);
    }

    if (ride.viewer.isJoined && !ride.viewer.isOrganizer) {
      buttons.add(
        OutlinedButton(
          key: const Key('groupRide.action.leave'),
          onPressed: isBusy('leave') ? null : () => _confirmLeave(context, ref),
          child: Text(t.get('groupRide.leave')),
        ),
      );
    }

    if (ride.viewer.isOrganizer && ride.viewer.isJoined) {
      buttons.add(
        FilledButton.icon(
          key: const Key('groupRide.action.invite'),
          onPressed: ride.status.acceptsInvites
              ? () => RideInviteSheet.show(context, ride)
              : null,
          icon: const Icon(Icons.person_add_alt, size: 18),
          label: Text(t.get('groupRide.invite')),
        ),
      );
      if (ride.status == GroupRideStatus.open) {
        // Start is disabled — not hidden — when the ride is alone. The organizer
        // needs to know the ride exists but cannot go anywhere, and a vanished
        // button would leave them guessing whether the app is broken.
        final alone = ride.onRoster.length < 2;
        buttons.add(
          FilledButton(
            key: const Key('groupRide.action.start'),
            onPressed: (alone || isBusy('start'))
                ? null
                : () => _run(
                    context,
                    ref,
                    GroupRideActions.rideKey('start', ride.id),
                    () => ref
                        .read(groupRideActionsProvider.notifier)
                        .start(ride.id),
                  ),
            child: Text(t.get('groupRide.start')),
          ),
        );
      }
      if (ride.status == GroupRideStatus.started) {
        buttons.add(
          FilledButton(
            key: const Key('groupRide.action.complete'),
            onPressed: isBusy('complete')
                ? null
                : () => _run(
                    context,
                    ref,
                    GroupRideActions.rideKey('complete', ride.id),
                    () => ref
                        .read(groupRideActionsProvider.notifier)
                        .complete(ride.id),
                  ),
            child: Text(t.get('groupRide.complete')),
          ),
        );
      }
      // Cancel is reachable from both live states and is the one destructive
      // action, so it is the last button and the only outlined-red one.
      buttons.add(
        TextButton(
          key: const Key('groupRide.action.cancel'),
          onPressed: isBusy('cancel')
              ? null
              : () => _confirmCancel(context, ref),
          style: TextButton.styleFrom(foregroundColor: AppColors.zone5),
          child: Text(t.get('groupRide.cancelRide')),
        ),
      );
    }

    if (buttons.isEmpty) return const SizedBox.shrink();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final b in buttons)
          Padding(
            padding: const EdgeInsets.only(bottom: AppSpacing.sm),
            child: b,
          ),
        if (ride.viewer.isOrganizer &&
            ride.status == GroupRideStatus.open &&
            ride.onRoster.length < 2)
          Text(
            t.get('groupRide.needAnotherRider'),
            key: const Key('groupRide.action.needRider'),
            style: const TextStyle(fontSize: 12, color: AppColors.textMuted),
          ),
      ],
    );
  }

  Future<void> _confirmLeave(BuildContext context, WidgetRef ref) async {
    final t = context.l10n;
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        content: Text(t.get('groupRide.leaveConfirm')),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: Text(t.get('routes.cancel')),
          ),
          FilledButton(
            key: const Key('groupRide.leave.confirm'),
            onPressed: () => Navigator.of(context).pop(true),
            child: Text(t.get('groupRide.leave')),
          ),
        ],
      ),
    );
    if (ok != true || !context.mounted) return;
    await _run(
      context,
      ref,
      GroupRideActions.rideKey('leave', ride.id),
      () => ref.read(groupRideActionsProvider.notifier).leave(ride.id),
    );
  }

  Future<void> _confirmCancel(BuildContext context, WidgetRef ref) async {
    final t = context.l10n;
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        content: Text(t.get('groupRide.cancelConfirm')),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: Text(t.get('routes.cancel')),
          ),
          FilledButton(
            key: const Key('groupRide.cancel.confirm'),
            style: FilledButton.styleFrom(backgroundColor: AppColors.zone5),
            onPressed: () => Navigator.of(context).pop(true),
            child: Text(t.get('groupRide.cancelRide')),
          ),
        ],
      ),
    );
    if (ok != true || !context.mounted) return;
    await _run(
      context,
      ref,
      GroupRideActions.rideKey('cancel', ride.id),
      () => ref.read(groupRideActionsProvider.notifier).cancel(ride.id),
    );
  }
}

/// Shared tail for every lifecycle call: report the server's refusal in the
/// rider's language instead of an exception trace.
///
/// Every action funnels through here, so no button can accidentally swallow its
/// own error and leave the UI looking like the tap did nothing.
///
/// No invalidation happens here. [GroupRideActions] already invalidates the
/// detail, the list, and the invitation inbox on success, and doing it again from
/// the widget would produce a second identical read for every tap.
Future<void> _run(
  BuildContext context,
  WidgetRef ref,
  String busyKey,
  Future<void> Function() body,
) async {
  final t = context.l10n;
  try {
    await body();
  } on Object catch (e) {
    if (!context.mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(friendlyRideError(t, e))));
  }
}

/// The ride's channel, opened through the existing chat screen.
///
/// A ride channel is not a special screen. It is one conversation with a group
/// of people who happen to share a ride, and reusing [ConversationScreen] means
/// ride messages get the same history paging, unread handling, and sending as
/// every other conversation — with the authorization rules living entirely on the
/// server.
class _ChatEntry extends ConsumerWidget {
  final GroupRide ride;

  const _ChatEntry({required this.ride});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    // A cancelled ride's channel is read-only, so it is not offered at all. The
    // messages are still readable through the chat inbox; what is gone is the
    // ability to start typing into a conversation nobody will answer.
    if (ride.status == GroupRideStatus.cancelled) {
      return const SizedBox.shrink();
    }
    final conversation = ref.watch(rideConversationProvider(ride.id));
    return conversation.when(
      loading: () => const SizedBox.shrink(),
      error: (e, _) => Text(
        friendlyRideError(t, e),
        style: const TextStyle(fontSize: 12, color: AppColors.textMuted),
      ),
      data: (c) => ListTile(
        key: const Key('groupRide.chat.entry'),
        contentPadding: EdgeInsets.zero,
        leading: const Icon(Icons.forum_outlined),
        title: Text(t.get('groupRide.rideChat')),
        subtitle: Text(
          c.unreadCount == 0
              ? t.get('groupRide.noNewMessages')
              : t.getWith('groupRide.unreadMessages', {
                  'count': '${c.unreadCount}',
                }),
          style: const TextStyle(fontSize: 12),
        ),
        trailing: const Icon(Icons.chevron_right_rounded),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => ConversationScreen(
              conversationId: c.id,
              title: c.title.isEmpty ? ride.title : c.title,
              // The ride was just loaded on this screen, so its status is the
              // freshest thing available and there is nothing to guess: a terminal
              // ride gets a read-only channel instead of a composer whose every
              // send would come back `CHAT_GROUP_RIDE_CLOSED`.
              closedBecauseRideStatus: ride.status,
              readOnly: ride.status.isTerminal,
            ),
          ),
        ),
      ),
    );
  }
}

/// Zero-padded schedule time, in the rider's LOCAL zone.
///
/// The server stores UTC and hands back an offset-aware ISO string; it is
/// converted here so a rider scheduling a 07:00 meetup in their own timezone reads
/// back 07:00, not the UTC hour they typed in another one.
String formatRideWhen(DateTime utc) {
  final local = utc.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${local.year}-${two(local.month)}-${two(local.day)} '
      '${two(local.hour)}:${two(local.minute)}';
}

class _Fact extends StatelessWidget {
  final IconData icon;
  final String text;
  final VoidCallback? onTap;

  const _Fact({required this.icon, required this.text, this.onTap});

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 2),
        child: Row(
          children: [
            Icon(icon, size: 16, color: AppColors.textMuted),
            const SizedBox(width: AppSpacing.sm),
            Expanded(child: Text(text, style: const TextStyle(fontSize: 13))),
          ],
        ),
      ),
    );
  }
}
