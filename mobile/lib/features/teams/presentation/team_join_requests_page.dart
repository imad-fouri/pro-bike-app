import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_widgets.dart';
import '../domain/team.dart';
import 'team_providers.dart';
import 'team_widgets.dart';

/// A team's pending join requests — owner and admin only.
///
/// Each row shows the applicant's PUBLIC social identity (username, display
/// name, avatar) and nothing else: no email, no phone, no location. The
/// backend answers 404 to a non-manager, so an unauthorized viewer gets an
/// error rather than somebody else's queue (ADR-13 §4).
class TeamJoinRequestsPage extends ConsumerWidget {
  final String teamId;
  const TeamJoinRequestsPage({super.key, required this.teamId});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final requests = ref.watch(teamJoinRequestsProvider(teamId));
    return Scaffold(
      appBar: AppBar(title: Text(t.get('team.joinRequests'))),
      body: requests.when(
        loading: () => Center(
          child: Text(
            t.get('social.loading'),
            style: const TextStyle(color: AppColors.textMuted),
          ),
        ),
        error: (e, _) => SocialErrorView(
          error: e,
          message: friendlyTeamError(t, e),
          onRetry: () => ref.invalidate(teamJoinRequestsProvider(teamId)),
        ),
        data: (page) {
          if (page.items.isEmpty) {
            return SocialMessage(
              icon: Icons.inbox_rounded,
              message: t.get('team.noRequests'),
            );
          }
          return RefreshIndicator(
            onRefresh: () async =>
                ref.invalidate(teamJoinRequestsProvider(teamId)),
            child: ListView.separated(
              itemCount: page.items.length,
              separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.xs),
              padding: const EdgeInsets.all(AppSpacing.md),
              itemBuilder: (context, i) =>
                  _RequestTile(teamId: teamId, request: page.items[i]),
            ),
          );
        },
      ),
    );
  }
}

class _RequestTile extends ConsumerWidget {
  final String teamId;
  final TeamJoinRequest request;
  const _RequestTile({required this.teamId, required this.request});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final busy = ref.watch(teamActionsProvider);
    final id = request.id;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                SocialAvatar(avatarUrl: null, name: request.bestName),
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        request.bestName ?? t.get('social.userProfile'),
                        style: const TextStyle(fontWeight: FontWeight.w700),
                      ),
                      if (request.username != null)
                        Text(
                          '@${request.username}',
                          style: const TextStyle(color: AppColors.accentLime),
                        ),
                    ],
                  ),
                ),
              ],
            ),
            if (request.message != null && request.message!.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(request.message!),
            ],
            const SizedBox(height: AppSpacing.md),
            Row(
              children: [
                Expanded(
                  child: SocialActionButton(
                    buttonKey: Key('team.request.accept.$id'),
                    label: t.get('team.accept'),
                    busyLabel: t.get('social.loading'),
                    busy: busy.contains(
                      TeamActions.requestKey('accept', teamId, id),
                    ),
                    primary: true,
                    onPressed: () => _run(
                      context,
                      () => ref
                          .read(teamActionsProvider.notifier)
                          .acceptJoinRequest(teamId, id),
                      t.get('team.requestAccepted'),
                    ),
                  ),
                ),
                const SizedBox(width: AppSpacing.sm),
                Expanded(
                  child: SocialActionButton(
                    buttonKey: Key('team.request.reject.$id'),
                    label: t.get('team.reject'),
                    busyLabel: t.get('social.loading'),
                    busy: busy.contains(
                      TeamActions.requestKey('reject', teamId, id),
                    ),
                    onPressed: () => _run(
                      context,
                      () => ref
                          .read(teamActionsProvider.notifier)
                          .rejectJoinRequest(teamId, id),
                      t.get('team.requestRejected'),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

Future<void> _run(
  BuildContext context,
  Future<void> Function() body,
  String successMessage,
) async {
  final messenger = ScaffoldMessenger.maybeOf(context);
  final t = context.l10n;
  try {
    await body();
    if (messenger != null && messenger.mounted) {
      messenger.showSnackBar(SnackBar(content: Text(successMessage)));
    }
  } on Object catch (e) {
    if (messenger != null && messenger.mounted) {
      messenger.showSnackBar(SnackBar(content: Text(friendlyTeamError(t, e))));
    }
  }
}
