import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../features/ads/domain/ad_policy.dart';
import '../../features/ads/presentation/ad_policy_providers.dart';

/// The one and only advertising surface in the app.
///
/// Screens place `AdSlotWidget(slot: ...)` where the slot taxonomy allows a
/// slot; everything else — eligibility, consent, ride safety, provider
/// mechanics, failure handling — happens here and in [AdPolicyService].
/// There is deliberately no other way to show an ad: no screen reads
/// entitlements, consent, or provider state for advertising purposes.
///
/// Behavior contract, all tested:
/// - ineligible slot → renders nothing ([Key] `ad.slot.empty`);
/// - eligible but unfilled/unavailable provider → renders nothing;
/// - any provider throw, at any point → renders nothing;
/// - never blocks, never navigates, never intercepts gestures: the only
///   non-empty output is the provider's own rendering, and the provider owns
///   every pixel of it.
class AdSlotWidget extends ConsumerStatefulWidget {
  final AdSlot slot;
  const AdSlotWidget({super.key, required this.slot});

  @override
  ConsumerState<AdSlotWidget> createState() => _AdSlotWidgetState();
}

class _AdSlotWidgetState extends ConsumerState<AdSlotWidget> {
  /// Whether a load has been attempted in this widget lifecycle.
  bool _loadRequested = false;

  /// Whether the provider reported a fill. Null until a load completes.
  bool? _filled;

  @override
  Widget build(BuildContext context) {
    final decision = ref
        .watch(adPolicyServiceProvider)
        .decisionFor(widget.slot, DateTime.now().toUtc());
    if (!decision.eligible) {
      // A slot that stops being eligible forgets its load: re-eligibility
      // later retries rather than serving a stale fill.
      _loadRequested = false;
      _filled = null;
      return const SizedBox.shrink(key: Key('ad.slot.empty'));
    }
    if (_filled == true) {
      try {
        final rendered = ref
            .read(adProviderProvider)
            .renderSlot(context, widget.slot);
        if (rendered != null) {
          return KeyedSubtree(
            key: const Key('ad.slot.filled'),
            child: rendered,
          );
        }
      } catch (_) {
        // Provider rendering failed: fall through to nothing.
      }
      return const SizedBox.shrink(key: Key('ad.slot.empty'));
    }
    if (!_loadRequested) {
      _loadRequested = true;
      _requestLoad();
    }
    return const SizedBox.shrink(key: Key('ad.slot.empty'));
  }

  Future<void> _requestLoad() async {
    // Snapshot everything context-derived BEFORE the first await: using a
    // BuildContext across an async gap is both a lint and a correctness
    // hazard (the widget may move in the tree while the provider loads).
    final locale = _localeOf(context);
    final appVersion = ref.read(adAppVersionProvider);
    bool filled = false;
    try {
      final provider = ref.read(adProviderProvider);
      if (!provider.isAvailable) return;
      await provider.initialize();
      final result = await provider.load(
        AdContext(
          slot: widget.slot,
          locale: locale,
          appVersion: appVersion,
          // The slot's own coarse category — about the screen, never the
          // rider. See AdContentCategory.
          contentCategory: widget.slot.defaultContentCategory.wire,
        ),
      );
      filled = result.filled;
    } catch (_) {
      // Ad failures are non-fatal by design: the slot stays dark and the
      // screen never learns anything broke.
      filled = false;
    } finally {
      if (mounted) {
        setState(() => _filled = filled);
      } else {
        _filled = filled;
      }
    }
  }

  /// The UI locale for ad context. Defaults to `en` when no localizations
  /// are in scope rather than crashing — a missing delegate must never take
  /// down a screen.
  String _localeOf(BuildContext context) {
    try {
      return Localizations.localeOf(context).languageCode;
    } catch (_) {
      return 'en';
    }
  }
}
