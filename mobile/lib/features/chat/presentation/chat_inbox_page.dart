import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_widgets.dart';
import 'chat_providers.dart';
import 'chat_widgets.dart';

/// The inbox: every conversation the viewer can see, newest activity first.
///
/// Team channels the viewer no longer has standing for are omitted by the server
/// rather than listed-and-refused, so a rider removed from a team does not get a
/// row that opens to an error (ADR-14 §14).
class ChatInboxPage extends ConsumerWidget {
  const ChatInboxPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = context.l10n;
    final inbox = ref.watch(conversationListProvider);
    return Scaffold(
      appBar: AppBar(title: Text(t.get('chat.inbox'))),
      body: inbox.when(
        loading: () => Center(
          child: Text(
            t.get('social.loading'),
            style: const TextStyle(color: AppColors.textMuted),
          ),
        ),
        error: (e, _) => SocialErrorView(
          error: e,
          message: friendlyChatError(t, e),
          onRetry: () => ref.invalidate(conversationListProvider),
        ),
        data: (page) {
          if (page.items.isEmpty) {
            return SocialMessage(
              icon: Icons.forum_outlined,
              message: t.get('chat.noConversations'),
            );
          }
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(conversationListProvider),
            child: ListView.separated(
              itemCount: page.items.length,
              separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.xs),
              padding: const EdgeInsets.all(AppSpacing.md),
              itemBuilder: (context, i) {
                final conversation = page.items[i];
                return ConversationTile(
                  conversation: conversation,
                  onTap: () => context.push(
                    '/chat/${conversation.id}?name=${Uri.encodeComponent(conversation.bestName ?? '')}',
                  ),
                );
              },
            ),
          );
        },
      ),
    );
  }
}
