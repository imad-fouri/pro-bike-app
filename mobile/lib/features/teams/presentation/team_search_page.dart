import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../social/presentation/social_widgets.dart';
import '../domain/team_validators.dart';
import 'my_teams_page.dart';
import 'team_providers.dart';
import 'team_widgets.dart';

/// Team discovery.
///
/// The backend matches name and handle only — never a member name — so this
/// screen cannot be used to enumerate a team's roster. Private teams the viewer
/// does not belong to are excluded server-side rather than redacted, so nothing
/// here leaks the existence of a private team.
class TeamSearchPage extends ConsumerStatefulWidget {
  const TeamSearchPage({super.key});

  @override
  ConsumerState<TeamSearchPage> createState() => _TeamSearchPageState();
}

class _TeamSearchPageState extends ConsumerState<TeamSearchPage> {
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
      setState(() => _submitted = value.trim());
    });
  }

  void _searchNow() {
    _debounce?.cancel();
    setState(() => _submitted = _controller.text.trim());
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final query = _submitted;
    final invalid = query == null ? null : TeamValidators.searchQuery(query);

    return Scaffold(
      appBar: AppBar(title: Text(t.get('team.searchTeams'))),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(AppSpacing.md),
            child: TextField(
              key: const Key('team.search.field'),
              controller: _controller,
              focusNode: _focus,
              autofocus: true,
              textInputAction: TextInputAction.search,
              onChanged: _onChanged,
              onSubmitted: (_) => _searchNow(),
              decoration: InputDecoration(
                hintText: t.get('team.searchHint'),
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
        message: t.get('team.searchHint'),
      );
    }
    if (invalid != null) return const SizedBox.shrink();

    final page = ref.watch(teamSearchProvider(query));
    return page.when(
      loading: () => Center(
        child: Text(
          t.get('social.loading'),
          style: const TextStyle(color: AppColors.textMuted),
        ),
      ),
      error: (e, _) => SocialErrorView(
        error: e,
        message: friendlyTeamError(t, e),
        onRetry: () => ref.invalidate(teamSearchProvider(query)),
      ),
      data: (result) {
        if (result.items.isEmpty) {
          return SocialMessage(
            icon: Icons.groups_outlined,
            message: t.get('team.noResults'),
          );
        }
        return ListView.separated(
          itemCount: result.items.length,
          separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.xs),
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
          itemBuilder: (context, i) => TeamTile(team: result.items[i]),
        );
      },
    );
  }
}
