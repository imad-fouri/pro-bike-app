import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_providers.dart';
import '../../social/presentation/social_widgets.dart';
import '../domain/group_ride.dart';
import '../domain/ride_validators.dart';
import 'group_ride_providers.dart';
import 'group_ride_widgets.dart';

/// Pick riders to invite, from the organizer's existing friendships.
///
/// Friends-only is a deliberate narrowing, not a missing feature. Phase 9 has no
/// user search on this screen, and inventing one would mean the ride feature
/// carrying its own people-picker — a second, differently-behaving version of
/// something the social feature already owns. An organizer invites people they
/// have already chosen to be connected to.
///
/// People already holding a roster row are shown as disabled rather than hidden:
/// "already invited" is information the organizer needs, and silently dropping
/// the names would make a partially-successful batch look like a total failure.
///
/// The batch is ONE request for all picked riders, so a partial failure is
/// reported per rider by [InviteBatchResult] instead of leaving the organizer
/// guessing which of five taps landed.
class RideInviteSheet extends ConsumerStatefulWidget {
  final GroupRide ride;

  const RideInviteSheet({super.key, required this.ride});

  /// Open the sheet. Returns true when at least one rider was invited.
  static Future<bool?> show(BuildContext context, GroupRide ride) {
    return showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (_) => RideInviteSheet(ride: ride),
    );
  }

  @override
  ConsumerState<RideInviteSheet> createState() => _RideInviteSheetState();
}

class _RideInviteSheetState extends ConsumerState<RideInviteSheet> {
  final _picked = <String>{};
  final _message = TextEditingController();
  String? _errorKey;
  bool _sending = false;

  @override
  void dispose() {
    _message.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final friends = ref.watch(friendListProvider);
    final busy = ref.watch(groupRideActionsProvider);

    return Padding(
      padding: EdgeInsets.only(
        bottom: MediaQuery.of(context).viewInsets.bottom,
      ),
      child: DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.75,
        builder: (context, controller) => Column(
          children: [
            Padding(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      t.get('groupRide.inviteRiders'),
                      style: const TextStyle(
                        fontWeight: FontWeight.w600,
                        fontSize: 16,
                      ),
                    ),
                  ),
                  TextButton(
                    onPressed: () => Navigator.of(context).pop(false),
                    child: Text(t.get('routes.cancel')),
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
              child: TextField(
                key: const Key('groupRide.invite.message'),
                controller: _message,
                maxLength: RideValidators.maxInviteMessage,
                decoration: InputDecoration(
                  labelText: t.get('groupRide.inviteMessage'),
                  border: const OutlineInputBorder(),
                ),
              ),
            ),
            if (_errorKey != null)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
                child: Text(
                  t.get(_errorKey!),
                  key: const Key('groupRide.invite.error'),
                  style: const TextStyle(fontSize: 12, color: AppColors.zone5),
                ),
              ),
            Expanded(
              child: friends.when(
                loading: () => const Center(child: CircularProgressIndicator()),
                error: (e, _) => SocialErrorView(
                  error: e,
                  message: friendlyRideError(t, e),
                  onRetry: () => ref.invalidate(friendListProvider),
                ),
                data: (page) {
                  final already = {
                    for (final p in widget.ride.roster) p.userId,
                  };
                  final candidates = page.items.where(
                    (f) => !already.contains(f.userId),
                  );
                  if (candidates.isEmpty) {
                    return Center(
                      child: Text(
                        t.get('groupRide.nobodyLeftToInvite'),
                        key: const Key('groupRide.invite.empty'),
                        style: const TextStyle(color: AppColors.textMuted),
                      ),
                    );
                  }
                  return ListView(
                    controller: controller,
                    children: [
                      for (final f in candidates)
                        CheckboxListTile(
                          key: Key('groupRide.invite.pick.${f.userId}'),
                          value: _picked.contains(f.userId),
                          title: Text(f.bestName ?? f.userId),
                          subtitle: f.username == null
                              ? null
                              : Text(
                                  '@${f.username}',
                                  style: const TextStyle(
                                    fontSize: 11,
                                    color: AppColors.textMuted,
                                  ),
                                ),
                          onChanged: (v) => setState(() {
                            if (v == true) {
                              _picked.add(f.userId);
                            } else {
                              _picked.remove(f.userId);
                            }
                            _errorKey = null;
                          }),
                        ),
                      for (final p in widget.ride.roster)
                        ListTile(
                          enabled: false,
                          leading: SocialAvatar(
                            avatarUrl: p.avatarUrl,
                            name: p.label,
                            radius: 16,
                          ),
                          title: Text(p.label),
                          subtitle: ParticipantStatusBadge(status: p.status),
                        ),
                    ],
                  );
                },
              ),
            ),
            SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(AppSpacing.md),
                child: SizedBox(
                  width: double.infinity,
                  child: FilledButton(
                    key: const Key('groupRide.invite.send'),
                    onPressed: _sending || _picked.isEmpty
                        ? null
                        : () => _send(busy.contains('invite-batch')),
                    child: Text(
                      t.getWith('groupRide.inviteCount', {
                        'count': '${_picked.length}',
                      }),
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _send(bool alreadyBusy) async {
    if (alreadyBusy) return;
    final t = context.l10n;
    final message = _message.text.trim();
    final error = RideValidators.validateInviteMessage(_message.text);
    if (error != null) {
      setState(() => _errorKey = error);
      return;
    }
    setState(() {
      _sending = true;
      _errorKey = null;
    });
    try {
      final result = await ref
          .read(groupRideActionsProvider.notifier)
          .inviteBatch(
            widget.ride.id,
            userIds: _picked.toList(),
            message: message.isEmpty ? null : message,
          );
      if (!mounted) return;
      final rejected = result?.rejected ?? const <InviteRejection>[];
      if (rejected.isEmpty) {
        Navigator.of(context).pop(true);
        return;
      }
      // Report the misses instead of pretending the batch worked. Rejections are
      // mapped through the same vocabulary the single invite uses, so "blocked"
      // reads identically whether it arrived alone or in a batch.
      setState(() {
        _sending = false;
        _picked.removeAll(rejected.map((r) => r.userId).toSet());
        _errorKey = rejected.every((r) => r.code == 'RIDE_BLOCKED')
            ? 'groupRide.error.blocked'
            : 'groupRide.error.memberUnavailable';
      });
      if (rejected.every((r) => r.code == 'RIDE_BLOCKED')) {
        _snack(t.get('groupRide.error.blocked'));
      }
    } on Object catch (e) {
      if (!mounted) return;
      setState(() => _sending = false);
      _snack(friendlyRideError(t, e));
    }
  }

  void _snack(String message) {
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }
}
