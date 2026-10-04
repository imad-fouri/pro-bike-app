import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_widgets.dart';
import '../data/push_registrar.dart';
import '../domain/notification.dart';
import 'notification_providers.dart';
import 'notification_widgets.dart';

/// Notification settings: what this device is registered for, and how to stop.
///
/// Phase 8.4 ships no provider, so the primary state is "not configured yet".
/// The screen says that plainly rather than showing a dead toggle: a switch that
/// cannot do anything is worse than an honest explanation (ADR-15 §11).
class NotificationSettingsPage extends ConsumerWidget {
  const NotificationSettingsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final registrar = ref.watch(pushRegistrarProvider);
    final devices = ref.watch(pushDeviceListProvider);

    return Scaffold(
      appBar: AppBar(title: Text(t.get('notifications.settings.title'))),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.md),
        children: [
          if (!registrar.isAvailable)
            _Notice(message: t.get('notifications.settings.unavailable'))
          else
            _Notice(message: t.get('notifications.settings.registered')),
          const SizedBox(height: AppSpacing.md),
          Text(
            t.get('notifications.settings.devices'),
            style: const TextStyle(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: AppSpacing.xs),
          Text(
            t.get('notifications.settings.devicesHint'),
            style: const TextStyle(color: AppColors.textMuted, fontSize: 13),
          ),
          const SizedBox(height: AppSpacing.md),
          devices.when(
            loading: () => Center(
              child: Text(
                t.get('social.loading'),
                style: const TextStyle(color: AppColors.textMuted),
              ),
            ),
            error: (e, _) => SocialErrorView(
              error: e,
              message: friendlyNotificationError(t, e),
              onRetry: () => ref.invalidate(pushDeviceListProvider),
            ),
            data: (rows) {
              if (rows.isEmpty) {
                return Padding(
                  padding: const EdgeInsets.symmetric(vertical: AppSpacing.lg),
                  child: Center(
                    child: Text(
                      t.get('notifications.settings.noDevices'),
                      style: const TextStyle(color: AppColors.textMuted),
                    ),
                  ),
                );
              }
              return Column(
                children: [
                  for (final device in rows) _DeviceTile(device: device),
                ],
              );
            },
          ),
        ],
      ),
    );
  }
}

class _Notice extends StatelessWidget {
  final String message;
  const _Notice({required this.message});

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.all(AppSpacing.md),
    decoration: BoxDecoration(
      color: AppColors.surface2,
      borderRadius: BorderRadius.circular(AppRadius.card),
    ),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Icon(
          Icons.info_outline_rounded,
          size: 20,
          color: AppColors.textMuted,
        ),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: Text(
            message,
            style: const TextStyle(color: AppColors.textMuted, fontSize: 13),
          ),
        ),
      ],
    ),
  );
}

/// One registered device, with a disable toggle and a revoke action.
///
/// No token is displayed, because the API never returns one — a device row can
/// therefore not leak a credential no matter what this widget does.
class _DeviceTile extends ConsumerWidget {
  final PushDevice device;
  const _DeviceTile({required this.device});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final busy = ref
        .watch(pushDeviceActionsProvider)
        .contains(PushDeviceActions.deviceKey(device.id));

    return Card(
      margin: const EdgeInsets.only(bottom: AppSpacing.xs),
      child: ListTile(
        key: Key('device.tile.${device.id}'),
        leading: Icon(
          device.platform == PushPlatform.ios
              ? Icons.phone_iphone_rounded
              : Icons.smartphone_rounded,
          color: device.enabled ? AppColors.accentLime : AppColors.textMuted,
        ),
        title: Text(device.deviceId.isEmpty ? '—' : device.deviceId),
        subtitle: Text(
          [
            device.platform.wire,
            device.provider.wire,
            if (device.appVersion != null) 'v${device.appVersion}',
          ].join(' · '),
          style: const TextStyle(color: AppColors.textMuted, fontSize: 12),
        ),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Switch(
              key: Key('device.toggle.${device.id}'),
              value: device.enabled,
              onChanged: busy
                  ? null
                  : (value) => ref
                        .read(pushDeviceActionsProvider.notifier)
                        .setEnabled(device, value),
            ),
            IconButton(
              key: Key('device.revoke.${device.id}'),
              tooltip: t.get('notifications.settings.devices'),
              icon: const Icon(Icons.logout_rounded, size: 18),
              onPressed: busy
                  ? null
                  : () => ref
                        .read(pushDeviceActionsProvider.notifier)
                        .revoke(device),
            ),
          ],
        ),
      ),
    );
  }
}
