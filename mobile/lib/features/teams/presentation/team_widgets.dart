import 'package:flutter/material.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_widgets.dart';
import '../domain/team.dart';
import '../domain/team_validators.dart';

/// Shared presentation atoms for the team surfaces.
///
/// Reuses the Phase 8.1 social atoms (avatar, message, error view, action
/// button, load-more spinner) rather than duplicating them: a rider should not
/// be able to tell that teams shipped in a later phase.

/// Cycling category is free-text server-side, so an unknown value has no
/// translation. Known vocabulary is localized; anything else is rendered as the
/// server's own label rather than dropped.
String teamCategoryLabel(AppLocalizations t, String raw) {
  if (TeamValidators.suggestedCategories.contains(raw)) {
    return t.get('team.category.$raw');
  }
  return raw;
}

/// Team avatar with the same deterministic initial fallback as a rider's.
class TeamAvatar extends StatelessWidget {
  final String? avatarUrl;
  final String name;
  final double radius;

  const TeamAvatar({
    super.key,
    required this.avatarUrl,
    required this.name,
    this.radius = 24,
  });

  @override
  Widget build(BuildContext context) {
    final url = avatarUrl;
    final initial = name.trim();
    if (url != null && url.isNotEmpty) {
      return CircleAvatar(
        radius: radius,
        backgroundColor: AppColors.surface2,
        backgroundImage: NetworkImage(url),
        onBackgroundImageError: (_, _) {},
        child: initial.isEmpty ? null : _initial(onImage: true),
      );
    }
    return CircleAvatar(
      radius: radius,
      backgroundColor: AppColors.primary,
      child: initial.isEmpty
          ? const Icon(Icons.groups_rounded, color: AppColors.background)
          : _initial(onImage: false),
    );
  }

  Widget _initial({required bool onImage}) => Text(
    name.characters.first.toUpperCase(),
    style: TextStyle(
      fontSize: radius * 0.9,
      fontWeight: FontWeight.w800,
      color: onImage ? AppColors.textOnDark : AppColors.background,
    ),
  );
}

/// `@handle`, or a localized "not claimed" marker.
class TeamHandle extends StatelessWidget {
  final String? handle;
  const TeamHandle({super.key, required this.handle});

  @override
  Widget build(BuildContext context) {
    final h = handle;
    final text = (h == null || h.isEmpty)
        ? context.l10n.get('team.noHandle')
        : '@$h';
    return Text(
      text,
      style: TextStyle(
        color: (h == null || h.isEmpty)
            ? AppColors.textMuted
            : AppColors.accentLime,
        fontWeight: FontWeight.w600,
      ),
    );
  }
}

/// Role pill. The label comes from the server's role value.
class TeamRoleBadge extends StatelessWidget {
  final TeamRole? role;
  const TeamRoleBadge({super.key, required this.role});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final r = role ?? TeamRole.member;
    final color = switch (r) {
      TeamRole.owner => AppColors.gold,
      TeamRole.admin => AppColors.accentLime,
      TeamRole.member => AppColors.primary,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.16),
        borderRadius: BorderRadius.circular(AppRadius.pill),
      ),
      child: Text(
        t.get(r.labelKey),
        style: TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w700,
          color: color,
        ),
      ),
    );
  }
}

/// Viewer↔team state badge. Always server-provided; never computed here.
class TeamStateBadge extends StatelessWidget {
  final TeamState state;
  const TeamStateBadge({super.key, required this.state});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final warn = switch (state) {
      TeamState.joinRequestPending || TeamState.invited => true,
      _ => false,
    };
    return RelationshipBadge(stateLabel: t.get(state.labelKey), warn: warn);
  }
}

/// A member-count line, e.g. "12 members". Not a live location indicator and
/// never derived from anything but the server's counter.
class TeamMemberCount extends StatelessWidget {
  final int count;
  const TeamMemberCount({super.key, required this.count});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        const Icon(
          Icons.people_alt_rounded,
          size: 16,
          color: AppColors.textMuted,
        ),
        const SizedBox(width: AppSpacing.xs),
        Text(
          context.l10n.get('team.memberCount'),
          style: const TextStyle(color: AppColors.textMuted),
        ),
        const SizedBox(width: AppSpacing.xs),
        Text('$count', style: const TextStyle(fontWeight: FontWeight.w700)),
      ],
    );
  }
}

/// A manager's actionable queue: "3 pending requests".
class TeamPendingBadge extends StatelessWidget {
  final int requests;
  final int invitations;
  const TeamPendingBadge({
    super.key,
    required this.requests,
    required this.invitations,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    if (requests == 0 && invitations == 0) return const SizedBox.shrink();
    return Wrap(
      spacing: AppSpacing.sm,
      runSpacing: AppSpacing.xs,
      children: [
        if (requests > 0)
          RelationshipBadge(
            stateLabel: '${t.get('team.pendingRequests')} ($requests)',
            warn: true,
          ),
        if (invitations > 0)
          RelationshipBadge(
            stateLabel: '${t.get('team.pendingInvites')} ($invitations)',
          ),
      ],
    );
  }
}

/// Standing reminder that team membership is not location access.
class TeamLocationNotice extends StatelessWidget {
  const TeamLocationNotice({super.key});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Container(
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surface2,
        borderRadius: BorderRadius.circular(AppRadius.card),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(
            Icons.location_off_outlined,
            size: 20,
            color: AppColors.textMuted,
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              t.get('team.locationNotice'),
              style: const TextStyle(color: AppColors.textMuted, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }
}

/// Map a team API error onto a localized sentence.
///
/// Reuses the Phase 8.1 status mapping for the shared cases (401/403/404/409/
/// 422/429/network/5xx) and adds the `TEAM_*` codes that need a specific
/// sentence. The not-found family collapses to one message, matching the
/// backend's deliberate refusal to distinguish "missing" from "not yours".
String friendlyTeamError(AppLocalizations t, Object error) {
  if (error is! ApiException) return t.get('social.error.generic');
  switch (error.code) {
    case 'TEAM_HANDLE_TAKEN':
      return t.get('team.error.handleTaken');
    case 'TEAM_ALREADY_MEMBER':
      return t.get('team.error.alreadyMember');
    case 'TEAM_REQUEST_PENDING':
      return t.get('team.error.requestPending');
    case 'TEAM_INVITE_PENDING':
      return t.get('team.error.invitePending');
    case 'TEAM_OWNER_IMMUTABLE':
      return t.get('team.error.ownerImmutable');
    case 'TEAM_OWNER_CANNOT_LEAVE':
      return t.get('team.error.ownerCannotLeave');
    case 'TEAM_ROLE_UNCHANGED':
      return t.get('team.error.roleUnchanged');
    case 'TEAM_FORBIDDEN':
      return t.get('team.error.forbidden');
    case 'TEAM_CANNOT_TARGET_SELF':
      return t.get('team.error.cannotTargetSelf');
    case 'TEAM_INVALID_HANDLE':
      return t.get('team.error.invalidHandle');
    case 'TEAM_INVALID_AVATAR':
      return t.get('team.error.invalidAvatar');
    case 'TEAM_NOT_FOUND':
    case 'TEAM_USER_NOT_FOUND':
    case 'TEAM_REQUEST_NOT_FOUND':
    case 'TEAM_INVITATION_NOT_FOUND':
      return t.get('team.error.notFound');
    default:
      // Anything else (including social codes) falls through to the shared map.
      return friendlyError(t, error);
  }
}
