import 'package:flutter/material.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import '../../../core/theme/app_colors.dart';
import '../domain/social_validators.dart';

/// Shared presentation atoms for the social surfaces. Every user-visible
/// string comes from l10n; every error message comes from [friendlyError],
/// which maps the existing CycleCoach [ApiException] contract onto a localized
/// sentence instead of leaking a raw code.

/// Cycling category is a free-text label on the server (`String(32)`), so an
/// unrecognized value has no translation. Render the known vocabulary in the
/// active locale and fall back to the server's own label rather than dropping
/// it or crashing on a missing key.
String categoryLabel(AppLocalizations t, String raw) {
  final key = 'social.category.$raw';
  if (SocialValidators.suggestedCategories.contains(raw)) return t.get(key);
  return raw;
}

/// Avatar with a deterministic initial fallback. A missing or empty avatar
/// URL renders the initial — never a broken image, never a network fetch to an
/// arbitrary host that the profile owner supplied.
class SocialAvatar extends StatelessWidget {
  final String? avatarUrl;
  final String? name;
  final double radius;

  const SocialAvatar({
    super.key,
    required this.avatarUrl,
    required this.name,
    this.radius = 24,
  });

  @override
  Widget build(BuildContext context) {
    final url = avatarUrl;
    final initial = (name ?? '').trim();
    if (url != null && url.isNotEmpty) {
      return CircleAvatar(
        radius: radius,
        backgroundColor: AppColors.surface2,
        backgroundImage: NetworkImage(url),
        // A dead avatar URL falls back to the initial instead of rendering the
        // framework's red error box.
        onBackgroundImageError: (_, _) {},
        child: initial.isEmpty
            ? null
            : _initial(context, initial, onImage: true),
      );
    }
    return CircleAvatar(
      radius: radius,
      backgroundColor: AppColors.primary,
      child: initial.isEmpty
          ? const Icon(Icons.person_rounded, color: AppColors.background)
          : _initial(context, initial, onImage: false),
    );
  }

  Widget _initial(BuildContext context, String name, {required bool onImage}) =>
      Text(
        name.characters.first.toUpperCase(),
        style: TextStyle(
          fontSize: radius * 0.9,
          fontWeight: FontWeight.w800,
          color: onImage ? AppColors.textOnDark : AppColors.background,
        ),
      );
}

/// Handle rendered as `@name`, or a localized "not set" marker. A rider who has
/// not claimed a username is a normal state, not an error.
class SocialHandle extends StatelessWidget {
  final String? username;
  const SocialHandle({super.key, required this.username});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final u = username;
    final text = (u == null || u.isEmpty) ? t.get('social.noUsername') : '@$u';
    return Text(
      text,
      style: TextStyle(
        color: (u == null || u.isEmpty)
            ? AppColors.textMuted
            : AppColors.accentLime,
        fontWeight: FontWeight.w600,
      ),
    );
  }
}

/// Relationship badge. The label comes from the server's state value, never
/// from a locally computed guess.
class RelationshipBadge extends StatelessWidget {
  final String stateLabel;
  final bool warn;
  const RelationshipBadge({
    super.key,
    required this.stateLabel,
    this.warn = false,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: (warn ? AppColors.gold : AppColors.primary).withValues(
          alpha: 0.16,
        ),
        borderRadius: BorderRadius.circular(AppRadius.pill),
      ),
      child: Text(
        stateLabel,
        style: TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w700,
          color: warn ? AppColors.gold : AppColors.primary,
        ),
      ),
    );
  }
}

/// The one place an [ApiException] becomes a sentence.
///
/// Mapping is by HTTP status first, then by the backend's stable error codes
/// (`SOCIAL_*`) for the cases where the status alone is ambiguous — a 409 is a
/// taken username, a pending request, or an existing friendship, and the rider
/// deserves to know which. Anything unrecognized degrades to a generic
/// localized sentence: no raw status line, no stack trace, no internal code.
String friendlyError(AppLocalizations t, Object error) {
  if (error is! ApiException) return t.get('social.error.generic');
  if (error.code.startsWith('SOCIAL_')) {
    switch (error.code) {
      case 'SOCIAL_USERNAME_TAKEN':
        return t.get('social.error.usernameTaken');
      case 'SOCIAL_REQUEST_PENDING':
        return t.get('social.error.requestPending');
      case 'SOCIAL_ALREADY_FRIENDS':
        return t.get('social.error.alreadyFriends');
      case 'SOCIAL_REQUESTS_NOT_ALLOWED':
        return t.get('social.error.requestsNotAllowed');
      case 'SOCIAL_INVALID_USERNAME':
        return t.get('social.error.invalidUsername');
      case 'SOCIAL_CANNOT_TARGET_SELF':
        return t.get('social.error.cannotTargetSelf');
      case 'SOCIAL_USER_NOT_FOUND':
      case 'SOCIAL_REQUEST_NOT_FOUND':
      case 'SOCIAL_FRIENDSHIP_NOT_FOUND':
      case 'SOCIAL_BLOCK_NOT_FOUND':
        // The backend deliberately answers identically for "missing" and
        // "not yours to act on", so the client must not try to distinguish.
        return t.get('social.error.notFound');
      default:
        break;
    }
  }
  switch (error.status) {
    case 401:
      return t.get('social.error.unauthorized');
    case 403:
      return t.get('social.error.forbidden');
    case 404:
      return t.get('social.error.notFound');
    case 409:
      return t.get('social.error.conflict');
    case 422:
      return t.get('social.error.validation');
    case 429:
      return t.get('social.error.rateLimited');
    case 0:
      return t.get('social.error.network');
    default:
      if (error.status >= 500) return t.get('social.error.server');
      return t.get('social.error.generic');
  }
}

/// Centered message + optional action, used for every empty/error surface.
class SocialMessage extends StatelessWidget {
  final IconData icon;
  final String message;
  final String? actionLabel;
  final VoidCallback? onAction;

  const SocialMessage({
    super.key,
    required this.icon,
    required this.message,
    this.actionLabel,
    this.onAction,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, size: 56, color: AppColors.textMuted),
            const SizedBox(height: AppSpacing.md),
            Text(
              message,
              textAlign: TextAlign.center,
              style: const TextStyle(color: AppColors.textMuted),
            ),
            if (actionLabel != null && onAction != null) ...[
              const SizedBox(height: AppSpacing.md),
              OutlinedButton(
                onPressed: onAction,
                child: Text(actionLabel ?? t.get('social.retry')),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Error surface with a retry affordance, matching the bikes/routes pattern.
class SocialErrorView extends StatelessWidget {
  final Object error;
  final VoidCallback onRetry;

  /// Lets a feature with its own error vocabulary (teams, via
  /// `friendlyTeamError`) supply a pre-mapped sentence. Omitted means the
  /// shared [friendlyError] mapping is used, which stays the Phase 8.1 default.
  final String? message;

  const SocialErrorView({
    super.key,
    required this.error,
    required this.onRetry,
    this.message,
  });

  @override
  Widget build(BuildContext context) {
    return SocialMessage(
      icon: Icons.cloud_off_rounded,
      message: message ?? friendlyError(context.l10n, error),
      actionLabel: context.l10n.get('social.retry'),
      onAction: onRetry,
    );
  }
}

/// Trailing spinner + label for a list that is appending its next page.
class SocialLoadMoreIndicator extends StatelessWidget {
  final bool visible;
  const SocialLoadMoreIndicator({super.key, required this.visible});

  @override
  Widget build(BuildContext context) {
    if (!visible) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.md),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const SizedBox(
            width: 18,
            height: 18,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
          const SizedBox(width: AppSpacing.sm),
          Text(
            context.l10n.get('social.loadingMore'),
            style: const TextStyle(color: AppColors.textMuted),
          ),
        ],
      ),
    );
  }
}

/// A mutation button that cannot be double-submitted.
///
/// [busy] comes from [SocialActions], the same state the guard uses, so the
/// disabled button and the guard can never disagree. The label swaps to
/// [busyLabel] while in flight so the rider sees the action is running rather
/// than assuming the tap was lost.
class SocialActionButton extends StatelessWidget {
  final String label;
  final String busyLabel;
  final bool busy;
  final VoidCallback? onPressed;
  final bool primary;
  final bool destructive;
  final Key? buttonKey;

  const SocialActionButton({
    super.key,
    required this.label,
    required this.busyLabel,
    required this.busy,
    required this.onPressed,
    this.primary = false,
    this.destructive = false,
    this.buttonKey,
  });

  @override
  Widget build(BuildContext context) {
    final child = Text(busy ? busyLabel : label);
    final style = destructive
        ? TextStyle(color: Theme.of(context).colorScheme.error)
        : null;
    if (primary) {
      return FilledButton(
        key: buttonKey,
        onPressed: busy ? null : onPressed,
        child: busy
            ? const SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : child,
      );
    }
    return OutlinedButton(
      key: buttonKey,
      onPressed: busy ? null : onPressed,
      style: style == null
          ? null
          : ButtonStyle(foregroundColor: WidgetStatePropertyAll(style.color)),
      child: busy
          ? const SizedBox(
              width: 18,
              height: 18,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : child,
    );
  }
}
