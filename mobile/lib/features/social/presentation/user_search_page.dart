import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../domain/social_profile.dart';
import '../domain/social_validators.dart';
import 'social_providers.dart';
import 'social_widgets.dart';

/// Rider search over the backend's username/display-name index.
///
/// Notes on the contract:
/// - The query is debounced (350 ms) so a fast typist sends one search, not
///   ten, against an endpoint that is rate-limited at 60/min per user.
/// - Only `username` and `display name` are searched. Email, phone, and ids
///   are not matchable: the repository sends exactly one `q` parameter and the
///   server matches two columns (ADR-12 §4).
/// - Results carry the server's relationship state, so each row shows the
///   action the rider is actually allowed to take.
class UserSearchPage extends ConsumerStatefulWidget {
  const UserSearchPage({super.key});

  @override
  ConsumerState<UserSearchPage> createState() => _UserSearchPageState();
}

class _UserSearchPageState extends ConsumerState<UserSearchPage> {
  final _controller = TextEditingController();
  final _focus = FocusNode();
  Timer? _debounce;
  String? _submitted;

  static const debounce = Duration(milliseconds: 350);

  @override
  void dispose() {
    _debounce?.cancel();
    _controller.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _onChanged(String value) {
    _debounce?.cancel();
    _debounce = Timer(debounce, () {
      if (!mounted) return;
      setState(() {
        _submitted = value.trim();
      });
    });
  }

  void _searchNow() {
    _debounce?.cancel();
    setState(() {
      _submitted = _controller.text.trim();
    });
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final query = _submitted;
    final invalid = query == null ? null : SocialValidators.searchQuery(query);

    return Scaffold(
      appBar: AppBar(title: Text(t.get('social.searchUsers'))),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(AppSpacing.md),
            child: TextField(
              key: const Key('social.search.field'),
              controller: _controller,
              focusNode: _focus,
              autofocus: true,
              textInputAction: TextInputAction.search,
              onChanged: _onChanged,
              onSubmitted: (_) => _searchNow(),
              decoration: InputDecoration(
                hintText: t.get('social.searchHint'),
                prefixIcon: const Icon(Icons.search_rounded),
                errorText: invalid == null ? null : t.get(invalid),
              ),
            ),
          ),
          Expanded(child: _results(query, invalid)),
        ],
      ),
    );
  }

  Widget _results(String? query, String? invalid) {
    final t = context.l10n;
    if (query == null || query.isEmpty) {
      return SocialMessage(
        icon: Icons.search_rounded,
        message: t.get('social.searchHint'),
      );
    }
    if (invalid != null) {
      return const SizedBox.shrink();
    }
    final results = ref.watch(userSearchProvider(query));
    return results.when(
      loading: () => Center(
        child: Text(
          t.get('social.loading'),
          style: const TextStyle(color: AppColors.textMuted),
        ),
      ),
      error: (e, _) => SocialErrorView(
        error: e,
        onRetry: () => ref.invalidate(userSearchProvider(query)),
      ),
      data: (page) {
        if (page.items.isEmpty) {
          return SocialMessage(
            icon: Icons.person_search_rounded,
            message: t.get('social.noResults'),
          );
        }
        return ListView.separated(
          itemCount: page.items.length,
          separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.xs),
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
          itemBuilder: (context, i) {
            final rider = page.items[i];
            return Card(
              child: ListTile(
                key: Key('social.search.result.${rider.userId}'),
                leading: SocialAvatar(
                  avatarUrl: rider.avatarUrl,
                  name: rider.bestName,
                ),
                title: Text(rider.bestName ?? t.get('social.userProfile')),
                subtitle: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SocialHandle(username: rider.username),
                    const SizedBox(height: 2),
                    RelationshipBadge(
                      stateLabel: t.get(rider.relationship.labelKey),
                      warn: rider.relationship != RelationshipState.none,
                    ),
                  ],
                ),
                isThreeLine: true,
                trailing: const Icon(Icons.chevron_right_rounded),
                onTap: () => context.push('/users/${rider.userId}'),
              ),
            );
          },
        );
      },
    );
  }
}
