import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_widgets.dart';
import '../domain/group_ride.dart';
import 'group_ride_providers.dart';
import 'group_ride_widgets.dart';

/// My group rides: every ride the viewer holds a roster row on, plus the
/// invitations waiting for an answer.
///
/// Invitations are NOT folded into the ride list. They are the only rides this
/// viewer may read without being on them, so keeping them as their own section
/// makes it obvious which rows are answerable and which are history.
///
/// No status filter is applied client-side: a cancelled ride stays in the list,
/// because the server's record and the rider's memory should agree about what
/// happened (ADR-16 §3).
class GroupRidesPage extends ConsumerWidget {
  const GroupRidesPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final rides = ref.watch(myRidesProvider);
    final invitations = ref.watch(rideInvitationsProvider);

    return Scaffold(
      appBar: AppBar(title: Text(t.get('groupRide.myRides'))),
      body: rides.when(
        loading: () => Center(
          child: Text(
            t.get('social.loading'),
            style: const TextStyle(color: AppColors.textMuted),
          ),
        ),
        error: (e, _) => SocialErrorView(
          error: e,
          message: friendlyRideError(t, e),
          onRetry: () => ref.invalidate(myRidesProvider),
        ),
        data: (list) {
          return RefreshIndicator(
            onRefresh: () async {
              ref.invalidate(myRidesProvider);
              ref.invalidate(rideInvitationsProvider);
            },
            child: ListView(
              padding: const EdgeInsets.all(AppSpacing.md),
              children: [
                _InvitationSection(invitations: invitations),
                if (list.items.isEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: AppSpacing.xl),
                    child: SocialMessage(
                      icon: Icons.directions_bike_rounded,
                      message: t.get('groupRide.noRides'),
                      actionLabel: t.get('groupRide.createRide'),
                      onAction: () => context.push('/group-rides/new'),
                    ),
                  )
                else
                  for (final ride in list.items)
                    Padding(
                      padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                      child: GroupRideTile(ride: ride),
                    ),
              ],
            ),
          );
        },
      ),
      floatingActionButton: FloatingActionButton(
        key: const Key('groupRide.create.fab'),
        tooltip: t.get('groupRide.createRide'),
        onPressed: () => context.push('/group-rides/new'),
        child: const Icon(Icons.add),
      ),
    );
  }
}

/// Pending invitations, with accept/decline on the row.
///
/// Answering is offered here and nowhere else, because answering is the one thing
/// a rider can do on a ride they cannot otherwise read. The buttons are the same
/// busy-guarded actions the rest of the feature uses, so a double tap cannot
/// produce two requests.
class _InvitationSection extends ConsumerWidget {
  final AsyncValue<GroupRideInvitationList> invitations;

  const _InvitationSection({required this.invitations});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    // `asData?.value` rather than a nullable accessor: while the invitations load
    // the section renders nothing, and a ride that IS readable must never be
    // hidden behind a slow invitation fetch.
    final items =
        invitations.asData?.value.items ?? const <GroupRideInvitation>[];
    if (items.isEmpty) return const SizedBox.shrink();

    final busy = ref.watch(groupRideActionsProvider);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          t.get('groupRide.invitations'),
          style: const TextStyle(fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: AppSpacing.sm),
        for (final invite in items)
          Card(
            key: Key('groupRide.invite.${invite.participantId}'),
            child: Padding(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    invite.title,
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                  Text(
                    t.getWith('groupRide.invitedBy', {
                      'name': invite.organizerLabel,
                    }),
                    style: const TextStyle(
                      fontSize: 12,
                      color: AppColors.textMuted,
                    ),
                  ),
                  if (invite.message != null && invite.message!.isNotEmpty) ...[
                    const SizedBox(height: AppSpacing.xs),
                    Text(invite.message!),
                  ],
                  const SizedBox(height: AppSpacing.sm),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.end,
                    children: [
                      TextButton(
                        key: Key(
                          'groupRide.invite.decline.${invite.participantId}',
                        ),
                        onPressed:
                            busy.contains(
                              GroupRideActions.rideKey(
                                'decline',
                                invite.groupRideId,
                              ),
                            )
                            ? null
                            : () => _respond(context, ref, invite, false),
                        child: Text(t.get('groupRide.decline')),
                      ),
                      const SizedBox(width: AppSpacing.sm),
                      FilledButton(
                        key: Key(
                          'groupRide.invite.accept.${invite.participantId}',
                        ),
                        onPressed:
                            busy.contains(
                              GroupRideActions.rideKey(
                                'accept',
                                invite.groupRideId,
                              ),
                            )
                            ? null
                            : () => _respond(context, ref, invite, true),
                        child: Text(t.get('groupRide.accept')),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        const Divider(height: AppSpacing.xl),
      ],
    );
  }

  Future<void> _respond(
    BuildContext context,
    WidgetRef ref,
    GroupRideInvitation invite,
    bool accept,
  ) async {
    final t = context.l10n;
    try {
      await ref
          .read(groupRideActionsProvider.notifier)
          .respond(invite.groupRideId, accept: accept);
      // Answering only makes the ride readable; going there is the natural next
      // step and there is nothing else on this page to show for it.
      if (accept && context.mounted) {
        context.push('/group-rides/${invite.groupRideId}');
      }
    } on Object catch (e) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(friendlyRideError(t, e))));
    }
  }
}

/// A ride row: title, when, how many riders are on it, and where it stands.
///
/// A ride the viewer once answered but is no longer on is listed and NOT
/// tappable. `GET /group-rides/{id}` authorizes on a `joined` row, so opening one
/// would be a guaranteed 404 — offering the tap would teach the rider that the
/// app has rows it cannot open. They keep the row in their history (that is what
/// `left`/`removed`/`declined` mean) without being offered a door into it.
class GroupRideTile extends StatelessWidget {
  final GroupRide ride;

  const GroupRideTile({super.key, required this.ride});

  /// Whether the server will answer a detail read for this viewer.
  bool get _canOpen => ride.viewer.isJoined || ride.viewer.isOrganizer;

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Card(
      child: ListTile(
        key: Key('groupRide.tile.${ride.id}'),
        leading: const Icon(Icons.directions_bike_rounded),
        title: Text(ride.title),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (ride.meetingPoint != null && ride.meetingPoint!.isNotEmpty)
              Text(
                t.getWith('groupRide.meetingAt', {'place': ride.meetingPoint!}),
                style: const TextStyle(fontSize: 12),
              ),
            Row(
              children: [
                Text(
                  t.getWith('groupRide.ridersCount', {
                    'count': '${ride.participantCount}',
                  }),
                  style: const TextStyle(
                    fontSize: 12,
                    color: AppColors.textMuted,
                  ),
                ),
                const SizedBox(width: AppSpacing.sm),
                RideStatusBadge(status: ride.status),
                if (ride.viewer.isOrganizer) ...[
                  const SizedBox(width: AppSpacing.sm),
                  Text(
                    t.get('groupRide.organizer'),
                    style: const TextStyle(fontSize: 11, color: AppColors.gold),
                  ),
                ],
              ],
            ),
            if (!_canOpen)
              Text(
                t.get('groupRide.notOnThisRide'),
                key: Key('groupRide.tile.closed.${ride.id}'),
                style: const TextStyle(
                  fontSize: 11,
                  color: AppColors.textMuted,
                ),
              ),
          ],
        ),
        isThreeLine: true,
        trailing: _canOpen ? const Icon(Icons.chevron_right_rounded) : null,
        onTap: _canOpen ? () => context.push('/group-rides/${ride.id}') : null,
      ),
    );
  }
}
