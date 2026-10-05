import 'package:flutter/material.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import '../../../core/theme/app_colors.dart';
import '../../ride/domain/location_source.dart';
import '../../social/presentation/social_widgets.dart';
import '../domain/group_ride.dart';
import 'ride_location_controller.dart';

/// The one place a Phase 9 error becomes a sentence.
///
/// Mapping is by the backend's stable codes (`RIDE_*`, `ROUTE_*`,
/// `LOCATION_*`), because a bare status is ambiguous in exactly the places a
/// rider will notice: 404 means "no such ride" or "not yours to see", and 409 is
/// a dozen different lifecycle refusals. Anything unrecognized falls through to
/// the shared Phase 8.1 map rather than inventing copy — including a server
/// message, which is English-only and would break the other two locales.
String friendlyRideError(AppLocalizations t, Object error) {
  if (error is! ApiException) return t.get('social.error.generic');
  return friendlyRideCode(t, error.code);
}

/// Same mapping, entered by CODE instead of by exception.
///
/// Split out because some failures are known only as a code — the location
/// controller records `LOCATION_UNAVAILABLE` from a catch block, with no status
/// and no server envelope behind it. Routing that through [friendlyRideError]
/// would mean fabricating an [ApiException] on the client to carry a string the
/// client already had.
String friendlyRideCode(AppLocalizations t, String code) {
  switch (code) {
    // Roster is frozen / the ride is over.
    case 'RIDE_ROSTER_FROZEN':
      return t.get('groupRide.error.rosterFrozen');
    case 'RIDE_CLOSED':
      return t.get('groupRide.error.closed');
    case 'RIDE_NOT_OPEN':
      return t.get('groupRide.error.notOpen');
    case 'RIDE_NOT_STARTED':
      return t.get('groupRide.error.notStarted');
    case 'RIDE_NEEDS_RIDERS':
      return t.get('groupRide.error.needsRiders');

    // The viewer's own standing.
    case 'RIDE_NOT_INVITED':
      return t.get('groupRide.error.notInvited');
    case 'RIDE_ALREADY_MEMBER':
      return t.get('groupRide.error.alreadyMember');
    case 'RIDE_ORGANIZER_CANNOT_LEAVE':
      return t.get('groupRide.error.organizerCannotLeave');
    case 'RIDE_ORGANIZER_IMMUTABLE':
      return t.get('groupRide.error.organizerImmutable');

    // Somebody else's decision, not the viewer's.
    case 'RIDE_BLOCKED':
      return t.get('groupRide.error.blocked');
    case 'RIDE_MEMBER_UNAVAILABLE':
      return t.get('groupRide.error.memberUnavailable');

    // The route pin.
    case 'RIDE_ROUTE_INCOMPLETE':
      return t.get('groupRide.error.routeIncomplete');
    case 'ROUTE_VERSION_NOT_FOUND':
      return t.get('groupRide.error.routeVersionNotFound');

    // Live location.
    case 'LOCATION_UNAVAILABLE':
      return t.get('groupRide.error.locationUnavailable');

    // A ride channel that is over, read succeeded but writing is refused. Its own
    // key so the message can say "ask a rider" rather than "try again".
    case 'CHAT_GROUP_RIDE_CLOSED':
      return t.get('groupRide.error.channelClosed');

    // 404 for "missing" and for "not yours to see" alike: the backend answers
    // identically so the client must not try to tell them apart.
    case 'RIDE_NOT_FOUND':
    case 'ROUTE_NOT_FOUND':
    case 'CHAT_CONVERSATION_NOT_FOUND':
      return t.get('groupRide.error.notFound');
    default:
      return t.get('social.error.generic');
  }
}

/// Status pill for a ride. The wire value is mapped to a key, never rendered
/// raw — a new backend status degrades to the English label rather than leaking
/// a snake_case token into the UI.
class RideStatusBadge extends StatelessWidget {
  final GroupRideStatus status;

  const RideStatusBadge({super.key, required this.status});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final (color, icon) = switch (status) {
      GroupRideStatus.open => (
        AppColors.accentLime,
        Icons.event_available_rounded,
      ),
      GroupRideStatus.started => (AppColors.zone2, Icons.play_circle_fill),
      GroupRideStatus.completed => (AppColors.textMuted, Icons.check_circle),
      GroupRideStatus.cancelled => (AppColors.zone5, Icons.cancel),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 13, color: color),
          const SizedBox(width: 4),
          Text(
            t.get(status.labelKey),
            style: TextStyle(fontSize: 11, color: color),
          ),
        ],
      ),
    );
  }
}

/// Roster-state pill. A declined rider, a rider who left, and a rider who was
/// removed are three different facts, so they get three different labels.
class ParticipantStatusBadge extends StatelessWidget {
  final GroupRideParticipantStatus status;

  const ParticipantStatusBadge({super.key, required this.status});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final color = switch (status) {
      GroupRideParticipantStatus.joined => AppColors.zone2,
      GroupRideParticipantStatus.invited => AppColors.accentLime,
      GroupRideParticipantStatus.declined => AppColors.textMuted,
      GroupRideParticipantStatus.left => AppColors.textMuted,
      GroupRideParticipantStatus.removed => AppColors.zone5,
    };
    return Text(
      t.get(status.labelKey),
      style: TextStyle(fontSize: 11, color: color),
    );
  }
}

/// The roster, one row per rider, in the server's order.
///
/// Shows every state, not just `joined`: an organizer looking at three pending
/// invitations needs to see them, and a rider who declined needs to see that they
/// did. Visibility of the ride itself is a separate decision, already made.
class RideRosterList extends StatelessWidget {
  final GroupRide ride;
  final String? myUserId;

  /// Offered by the ORGANIZER only, for a rider who is still on the ride.
  final void Function(GroupRideParticipant)? onRemove;

  const RideRosterList({
    super.key,
    required this.ride,
    this.myUserId,
    this.onRemove,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    if (ride.roster.isEmpty) {
      return Text(
        t.get('groupRide.noRoster'),
        style: const TextStyle(color: AppColors.textMuted),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final p in ride.roster)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Row(
              children: [
                SocialAvatar(avatarUrl: p.avatarUrl, name: p.label, radius: 16),
                const SizedBox(width: AppSpacing.sm),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Flexible(child: Text(p.label)),
                          if (p.isOrganizer) ...[
                            const SizedBox(width: 6),
                            Text(
                              t.get('groupRide.organizer'),
                              style: const TextStyle(
                                fontSize: 11,
                                color: AppColors.textMuted,
                              ),
                            ),
                          ],
                        ],
                      ),
                      ParticipantStatusBadge(status: p.status),
                    ],
                  ),
                ),
                if (onRemove != null &&
                    p.status.isOnRide &&
                    !p.isOrganizer &&
                    p.userId != myUserId)
                  IconButton(
                    key: Key('groupRide.remove.${p.userId}'),
                    tooltip: t.get('groupRide.remove'),
                    icon: const Icon(
                      Icons.person_remove_outlined,
                      size: 18,
                      color: AppColors.zone5,
                    ),
                    onPressed: () => onRemove!(p),
                  ),
              ],
            ),
          ),
      ],
    );
  }
}

/// The live-location panel: what is shared, and the one control that starts it.
///
/// Two things are load-bearing about how sharing appears here:
///
/// * The control is a **switch the rider sets**, and it is off on arrival. There
///   is no "share by default", no prompt on entering the ride, and no state
///   restored from a previous ride — the only thing that starts a broadcast is
///   this switch.
/// * A rider whose permission is refused sees WHY, with the settings escape
///   hatch. Silently not sharing would leave them believing they are invisible
///   while their teammates watch a map that has stopped updating.
class RideLocationPanel extends StatelessWidget {
  final RideLocationState location;
  final VoidCallback onToggleSharing;
  final VoidCallback onOpenSettings;
  final VoidCallback onRetry;

  const RideLocationPanel({
    super.key,
    required this.location,
    required this.onToggleSharing,
    required this.onOpenSettings,
    required this.onRetry,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(
                  Icons.my_location_rounded,
                  size: 18,
                  color: AppColors.textMuted,
                ),
                const SizedBox(width: AppSpacing.sm),
                Expanded(
                  child: Text(
                    t.get('groupRide.liveLocation'),
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                ),
                Switch(
                  key: const Key('groupRide.location.shareSwitch'),
                  value: location.sharing,
                  onChanged: (_) => onToggleSharing(),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.xs),
            Text(
              t.get('groupRide.location.consentNotice'),
              style: const TextStyle(fontSize: 12, color: AppColors.textMuted),
            ),
            if (location.permission != null &&
                location.permission != LocationPermissionState.granted)
              _PermissionNotice(
                permission: location.permission!,
                onOpenSettings: onOpenSettings,
              ),
            if (location.errorCode != null) ...[
              const SizedBox(height: AppSpacing.sm),
              Row(
                children: [
                  Expanded(
                    child: Text(
                      friendlyRideCode(t, location.errorCode!),
                      key: const Key('groupRide.location.error'),
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppColors.zone5,
                      ),
                    ),
                  ),
                  TextButton(
                    onPressed: onRetry,
                    child: Text(t.get('social.retry')),
                  ),
                ],
              ),
            ],
            const SizedBox(height: AppSpacing.sm),
            if (location.errorCode != null && !location.hasRiders)
              Text(
                t.get('groupRide.location.unavailable'),
                style: const TextStyle(
                  fontSize: 12,
                  color: AppColors.textMuted,
                ),
              )
            else if (!location.hasRiders)
              Text(
                t.get('groupRide.location.nobodySharing'),
                style: const TextStyle(
                  fontSize: 12,
                  color: AppColors.textMuted,
                ),
              )
            else
              for (final r in location.riders) RiderDot(rider: r),
          ],
        ),
      ),
    );
  }
}

/// One rider's position, with its age.
///
/// The age is rendered rather than hidden because "last seen" is the difference
/// between a live rider and one who stopped answering three minutes ago, and the
/// server's cutoff is 60s — so a dot that is present but old means something
/// specific. Self is labelled so a rider can find themselves without guessing
/// which dot is theirs.
class RiderDot extends StatelessWidget {
  final RiderLocation rider;

  const RiderDot({super.key, required this.rider});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return ListTile(
      key: Key('groupRide.location.${rider.userId}'),
      dense: true,
      contentPadding: EdgeInsets.zero,
      leading: Icon(
        // Accuracy the fix does not know is drawn hollow: a precise-looking dot
        // for a 500-metre fix is a lie about how exact the position is.
        Icons.location_on_rounded,
        color: rider.isSelf ? AppColors.zone2 : AppColors.accentLime,
        size: 20,
      ),
      title: Row(
        children: [
          Flexible(
            child: Text(
              rider.isSelf ? t.get('groupRide.location.you') : rider.label,
            ),
          ),
          if (!rider.isPrecise) ...[
            const SizedBox(width: 6),
            const Icon(
              Icons.blur_on_rounded,
              size: 14,
              color: AppColors.textMuted,
            ),
          ],
        ],
      ),
      subtitle: Text(
        t.getWith('groupRide.location.lastSeen', {
          'age': '${rider.ageSeconds}',
        }),
        style: const TextStyle(fontSize: 11, color: AppColors.textMuted),
      ),
    );
  }
}

class _PermissionNotice extends StatelessWidget {
  final LocationPermissionState permission;
  final VoidCallback onOpenSettings;

  const _PermissionNotice({
    required this.permission,
    required this.onOpenSettings,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final key = switch (permission) {
      LocationPermissionState.denied => 'groupRide.location.denied',
      LocationPermissionState.deniedForever =>
        'groupRide.location.deniedForever',
      LocationPermissionState.serviceDisabled =>
        'groupRide.location.serviceOff',
      LocationPermissionState.imprecise => 'groupRide.location.imprecise',
      LocationPermissionState.granted => 'groupRide.location.granted',
    };
    final needsSettings =
        permission == LocationPermissionState.deniedForever ||
        permission == LocationPermissionState.serviceDisabled;
    return Padding(
      padding: const EdgeInsets.only(top: AppSpacing.sm),
      child: Row(
        children: [
          const Icon(
            Icons.warning_amber_rounded,
            size: 16,
            color: AppColors.gold,
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              t.get(key),
              key: const Key('groupRide.location.permission'),
              style: const TextStyle(fontSize: 12, color: AppColors.gold),
            ),
          ),
          if (needsSettings)
            TextButton(
              key: const Key('groupRide.location.settings'),
              onPressed: onOpenSettings,
              child: Text(t.get('groupRide.location.openSettings')),
            ),
        ],
      ),
    );
  }
}
