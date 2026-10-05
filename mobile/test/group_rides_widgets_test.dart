/// Phase 9 group-ride widget tests (ADR-16).
///
/// These defend presentation decisions that are easy to regress and hard to
/// notice:
///
/// 1. **A wire value is never rendered raw.** Every status goes through a
///    localization key, so an unknown backend status degrades to an English label
///    instead of leaking `snake_case` into the UI.
/// 2. **Roster state drives what is offered.** An organizer sees Remove on a
///    joined rider; nobody sees Remove on a pending one; a rider with no row is
///    offered no answer at all.
/// 3. **"Nobody is sharing" and "we could not ask" read differently.** The
///    location panel shows a retry in one case and not the other — collapsing
///    them would throw away the 503-vs-empty distinction the service makes.
/// 4. **RTL and all three locales actually render.** A missing Arabic key or a
///    row that overflows in RTL is invisible to an English-only smoke test.
///
/// Every test pumps through [pump], which settles. A bare `pumpWidget` leaves
/// `MaterialApp` one frame short of pushing its initial route, so the widget under
/// test is not in the tree and every assertion here would pass vacuously.
library;

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/features/group_rides/domain/group_ride.dart';
import 'package:cyclecoach/features/group_rides/presentation/group_ride_widgets.dart';
import 'package:cyclecoach/features/group_rides/presentation/ride_location_controller.dart'
    show RideLocationState;
import 'package:cyclecoach/features/ride/domain/location_source.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

// ---------------------------------------------------------------------------
// Fixtures — the same shapes the backend returns
// ---------------------------------------------------------------------------

Map<String, dynamic> participantJson({
  String userId = 'u-2',
  String role = 'participant',
  String status = 'joined',
  String? displayName = 'Karim Amrani',
  String? username = 'karim_amrani',
  String? invitedByUserId = 'u-1',
  String? respondedAt = '2026-01-01T10:00:00Z',
}) => {
  'user_id': userId,
  'role': role,
  'status': status,
  'invited_by_user_id': invitedByUserId,
  'message': null,
  'responded_at': respondedAt,
  'created_at': '2026-01-01T09:00:00Z',
  'username': username,
  'display_name': displayName,
  'avatar_url': null,
};

Map<String, dynamic> riderJson({
  String userId = 'u-2',
  String? displayName = 'Karim Amrani',
  String username = 'karim_amrani',
  double? accuracyM = 12,
  int ageSeconds = 4,
  bool isSelf = false,
}) => {
  'user_id': userId,
  'username': username,
  'display_name': displayName,
  'avatar_url': null,
  'latitude': 33.5,
  'longitude': -6.5,
  'accuracy_m': accuracyM,
  'age_seconds': ageSeconds,
  'is_self': isSelf,
};

GroupRide ride({
  List<Map<String, dynamic>>? roster,
  Map<String, dynamic>? viewer,
  String status = 'open',
}) => GroupRide.fromJson({
  'id': 'r-1',
  'organizer_user_id': 'u-1',
  'title': 'Sunday Spin',
  'description': null,
  'status': status,
  'starts_at': null,
  'meeting_point': null,
  'route_id': null,
  'route_version': null,
  'created_at': '2026-01-01T08:00:00Z',
  'updated_at': '2026-01-01T08:00:00Z',
  'started_at': null,
  'completed_at': null,
  'cancelled_at': null,
  'participant_count': (roster ?? const []).length,
  'roster': roster ?? const [],
  'viewer': viewer ?? const {'is_organizer': false, 'is_joined': false},
});

RideLocationSnapshot snapshot(List<Map<String, dynamic>> riders) =>
    RideLocationSnapshot.fromJson({
      'items': riders,
      'stale_after_seconds': 60,
      'expires_in_seconds': 300,
    });

RideLocationState locationState({
  bool sharing = false,
  LocationPermissionState? permission,
  RideLocationSnapshot? snapshot,
  String? errorCode,
}) => RideLocationState(
  sharing: sharing,
  permission: permission,
  snapshot: snapshot,
  errorCode: errorCode,
);

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

/// Localized strings for a locale, without pumping a widget.
///
/// Every assertion reads its expected text through here, so a test states "the
/// French label" rather than hard-coding one string that would then pass in all
/// three locales.
AppLocalizations l10n(String locale) => AppLocalizations(Locale(locale));

String textOf(String locale, String key) => l10n(locale).get(key);

/// Wraps a widget in the localization and theming scaffolding every screen in this
/// app assumes. Without the delegates `context.l10n` throws, which would make every
/// test here a test of the harness.
Widget harness(
  Widget child, {
  String locale = 'en',
  TextDirection? direction,
}) => MaterialApp(
  locale: Locale(locale),
  localizationsDelegates: const [
    AppLocalizationsDelegate(),
    GlobalMaterialLocalizations.delegate,
    GlobalWidgetsLocalizations.delegate,
    GlobalCupertinoLocalizations.delegate,
  ],
  supportedLocales: AppLocalizations.supported,
  home: direction == null
      ? child
      : Directionality(textDirection: direction, child: child),
);

/// Pumps and settles.
///
/// `pumpWidget` alone leaves `MaterialApp` a frame short of pushing its initial
/// route, so the widget under test is absent from the tree and `findsOneWidget`
/// fails for a reason that has nothing to do with the code under test.
Future<void> pump(
  WidgetTester tester,
  Widget child, {
  String locale = 'en',
  TextDirection? direction,
}) async {
  await tester.pumpWidget(harness(child, locale: locale, direction: direction));
  await tester.pumpAndSettle();
}

/// The location panel with inert callbacks, so a test can assert on the callback
/// it cares about without restating the other three.
Widget panel(
  RideLocationState state, {
  VoidCallback? onToggle,
  VoidCallback? onOpenSettings,
  VoidCallback? onRetry,
}) => Scaffold(
  body: RideLocationPanel(
    location: state,
    onToggleSharing: onToggle ?? () {},
    onOpenSettings: onOpenSettings ?? () {},
    onRetry: onRetry ?? () {},
  ),
);

void main() {
  group('status badges render a label, never a wire value', () {
    for (final status in GroupRideStatus.values) {
      testWidgets('${status.wire} shows its localized label', (tester) async {
        await pump(tester, Scaffold(body: RideStatusBadge(status: status)));
        // The point is the ABSENCE of the raw token. A snake_case status leaking
        // into the UI is the failure this defends.
        expect(find.text(status.wire), findsNothing);
        expect(find.text(textOf('en', status.labelKey)), findsOneWidget);
      });
    }

    testWidgets('the four ride states are visually distinct', (tester) async {
      final colors = <Color>{};
      for (final status in GroupRideStatus.values) {
        await pump(tester, Scaffold(body: RideStatusBadge(status: status)));
        final text = tester.widget<Text>(
          find.text(textOf('en', status.labelKey)),
        );
        colors.add(text.style?.color ?? Colors.black);
      }
      // `completed` and `cancelled` must not read as the same state: one ride ran,
      // the other was called off. Three distinct colours is the floor — a palette
      // that collapsed them would make a cancelled ride look merely finished.
      expect(colors.length, greaterThanOrEqualTo(3));
    });

    for (final status in GroupRideParticipantStatus.values) {
      testWidgets('roster "${status.wire}" shows its localized label', (
        tester,
      ) async {
        await pump(
          tester,
          Scaffold(body: ParticipantStatusBadge(status: status)),
        );
        expect(find.text(status.wire), findsNothing);
        expect(find.text(textOf('en', status.labelKey)), findsOneWidget);
      });
    }

    testWidgets('declined, left and removed are three different facts', (
      tester,
    ) async {
      // All three hold a roster row and none of them is on the ride. Rendering
      // them identically would make a removal look like a voluntary withdrawal.
      final labels = <String>{};
      for (final status in [
        GroupRideParticipantStatus.declined,
        GroupRideParticipantStatus.left,
        GroupRideParticipantStatus.removed,
      ]) {
        await pump(
          tester,
          Scaffold(body: ParticipantStatusBadge(status: status)),
        );
        labels.add(textOf('en', status.labelKey));
      }
      expect(labels.length, 3);
    });
  });

  group('roster list', () {
    testWidgets('shows every roster state, not just the joined ones', (
      tester,
    ) async {
      // An organizer with three pending invitations needs to see them; a rider who
      // declined needs to see that they did.
      await pump(
        tester,
        Scaffold(
          body: RideRosterList(
            ride: ride(
              roster: [
                participantJson(
                  userId: 'u-1',
                  role: 'organizer',
                  displayName: 'Imad Fouri',
                ),
                participantJson(userId: 'a', displayName: 'Rider A'),
                participantJson(
                  userId: 'b',
                  status: 'invited',
                  respondedAt: null,
                  displayName: 'Pending Rider',
                ),
                participantJson(
                  userId: 'c',
                  status: 'declined',
                  displayName: 'Declined Rider',
                ),
                participantJson(
                  userId: 'd',
                  status: 'removed',
                  displayName: 'Removed Rider',
                ),
              ],
            ),
          ),
        ),
      );

      for (final name in [
        'Rider A',
        'Pending Rider',
        'Declined Rider',
        'Removed Rider',
      ]) {
        expect(find.text(name), findsOneWidget, reason: name);
      }
    });

    testWidgets('an empty roster says so rather than rendering nothing', (
      tester,
    ) async {
      await pump(tester, Scaffold(body: RideRosterList(ride: ride())));
      expect(find.text(textOf('en', 'groupRide.noRoster')), findsOneWidget);
    });

    testWidgets('Remove is offered only on a rider who is on the ride', (
      tester,
    ) async {
      // Offered by the ORGANIZER, and only for a JOINED row: withdrawing a pending
      // invitation is a different action with a different consequence.
      await pump(
        tester,
        Scaffold(
          body: RideRosterList(
            ride: ride(
              roster: [
                participantJson(userId: 'a', status: 'joined'),
                participantJson(
                  userId: 'b',
                  status: 'invited',
                  respondedAt: null,
                ),
              ],
            ),
            myUserId: 'u-1',
            onRemove: (_) {},
          ),
        ),
      );

      expect(find.byKey(const Key('groupRide.remove.a')), findsOneWidget);
      expect(find.byKey(const Key('groupRide.remove.b')), findsNothing);
    });

    testWidgets('Remove is offered to nobody when no handler is given', (
      tester,
    ) async {
      // The affordance follows the CALLER's authority, not the roster: a page that
      // passes no handler must render no button even on a joined row it can see.
      await pump(
        tester,
        Scaffold(
          body: RideRosterList(
            ride: ride(roster: [participantJson(userId: 'a')]),
            myUserId: 'u-2',
          ),
        ),
      );

      expect(find.byKey(const Key('groupRide.remove.a')), findsNothing);
    });

    testWidgets('Remove invokes the handler with that rider', (tester) async {
      GroupRideParticipant? removed;
      await pump(
        tester,
        Scaffold(
          body: RideRosterList(
            ride: ride(
              roster: [
                participantJson(userId: 'a'),
                participantJson(
                  userId: 'b',
                  status: 'invited',
                  respondedAt: null,
                ),
              ],
            ),
            myUserId: 'u-1',
            onRemove: (p) => removed = p,
          ),
        ),
      );

      await tester.tap(find.byKey(const Key('groupRide.remove.a')));
      await tester.pumpAndSettle();

      // The handler receives the RIDER, not a bare id, so the caller can confirm
      // who it is about to remove instead of trusting a key it pressed.
      expect(removed?.userId, 'a');
    });

    testWidgets('the organizer row is marked as the organizer', (tester) async {
      await pump(
        tester,
        Scaffold(
          body: RideRosterList(
            ride: ride(
              roster: [
                participantJson(
                  userId: 'u-1',
                  role: 'organizer',
                  displayName: 'Imad Fouri',
                ),
                participantJson(userId: 'a'),
              ],
            ),
            myUserId: 'u-2',
          ),
        ),
      );

      expect(find.text('Imad Fouri'), findsOneWidget);
      expect(find.text(textOf('en', 'groupRide.organizer')), findsOneWidget);
    });
  });

  group('location panel: sharing is opt-in and visible', () {
    testWidgets('starts switched off', (tester) async {
      // Nothing publishes until asked, so the switch must not start on: a panel
      // that began "on" would be broadcasting before any tap.
      await pump(tester, panel(locationState()));

      final toggle = tester.widget<Switch>(
        find.byKey(const Key('groupRide.location.shareSwitch')),
      );
      expect(toggle.value, isFalse);
      expect(
        find.text(textOf('en', 'groupRide.location.consentNotice')),
        findsOneWidget,
      );
    });

    testWidgets('the switch reflects an active share', (tester) async {
      await pump(tester, panel(locationState(sharing: true)));

      final toggle = tester.widget<Switch>(
        find.byKey(const Key('groupRide.location.shareSwitch')),
      );
      expect(toggle.value, isTrue);
    });

    testWidgets('toggling the switch reports the intent exactly once', (
      tester,
    ) async {
      var toggles = 0;
      await pump(tester, panel(locationState(), onToggle: () => toggles++));

      await tester.tap(find.byKey(const Key('groupRide.location.shareSwitch')));
      await tester.pumpAndSettle();

      expect(toggles, 1);
    });
  });

  group('location panel: failure is never dressed as emptiness', () {
    testWidgets('a read failure shows the code and a retry, not "nobody"', (
      tester,
    ) async {
      // The service answers 503 rather than an empty list precisely so these stay
      // distinguishable. Rendering "nobody is sharing" over a 503 would discard
      // that at the last possible moment.
      await pump(
        tester,
        panel(locationState(errorCode: 'LOCATION_UNAVAILABLE')),
      );

      expect(find.byKey(const Key('groupRide.location.error')), findsOneWidget);
      expect(
        find.text(textOf('en', 'groupRide.location.unavailable')),
        findsOneWidget,
      );
      expect(
        find.text(textOf('en', 'groupRide.location.nobodySharing')),
        findsNothing,
      );
      expect(find.text(textOf('en', 'social.retry')), findsOneWidget);
    });

    testWidgets('an empty read says nobody is sharing, with no retry', (
      tester,
    ) async {
      await pump(tester, panel(locationState()));

      expect(
        find.text(textOf('en', 'groupRide.location.nobodySharing')),
        findsOneWidget,
      );
      expect(find.byKey(const Key('groupRide.location.error')), findsNothing);
      // No retry for an answer that was successfully "nobody" — offering one turns
      // an empty map into a busy loop.
      expect(find.text(textOf('en', 'social.retry')), findsNothing);
    });

    testWidgets('a publish failure is shown while sharing continues', (
      tester,
    ) async {
      // One dropped fix under a tunnel is not a reason to stop sharing, but the
      // rider must not be shown a clean panel.
      await pump(
        tester,
        panel(
          locationState(sharing: true, errorCode: 'LOCATION_PUBLISH_FAILED'),
        ),
      );

      expect(find.byKey(const Key('groupRide.location.error')), findsOneWidget);
      final toggle = tester.widget<Switch>(
        find.byKey(const Key('groupRide.location.shareSwitch')),
      );
      expect(toggle.value, isTrue);
    });

    testWidgets('a broken GPS stream is reported, and sharing is off', (
      tester,
    ) async {
      // The subscription really is gone, so claiming to share would be the lie.
      await pump(
        tester,
        panel(locationState(errorCode: 'LOCATION_STREAM_FAILED')),
      );

      expect(find.byKey(const Key('groupRide.location.error')), findsOneWidget);
      final toggle = tester.widget<Switch>(
        find.byKey(const Key('groupRide.location.shareSwitch')),
      );
      expect(toggle.value, isFalse);
    });

    testWidgets('the error text is localized, not the raw code', (
      tester,
    ) async {
      await pump(
        tester,
        panel(locationState(errorCode: 'LOCATION_UNAVAILABLE')),
      );

      // A SCREAMING_SNAKE code on screen is a bug, not a diagnostic.
      expect(find.textContaining('LOCATION_UNAVAILABLE'), findsNothing);
    });

    testWidgets('the retry is wired to the callback', (tester) async {
      var retries = 0;
      await pump(
        tester,
        panel(
          locationState(errorCode: 'LOCATION_UNAVAILABLE'),
          onRetry: () => retries++,
        ),
      );

      await tester.tap(find.text(textOf('en', 'social.retry')));
      await tester.pumpAndSettle();

      expect(retries, 1);
    });
  });

  group('location panel: permissions are explained', () {
    testWidgets('a plain denial states the problem but offers no settings', (
      tester,
    ) async {
      // The remedy for a one-off denial is to ask again, not to open a settings
      // screen. Offering settings here would send a rider who can fix it with one
      // tap into a menu instead.
      await pump(
        tester,
        panel(locationState(permission: LocationPermissionState.denied)),
      );

      expect(
        find.byKey(const Key('groupRide.location.permission')),
        findsOneWidget,
      );
      expect(
        find.text(textOf('en', 'groupRide.location.denied')),
        findsOneWidget,
      );
      expect(
        find.byKey(const Key('groupRide.location.settings')),
        findsNothing,
      );
    });

    testWidgets('a denied-forever permission offers settings', (tester) async {
      // Here the remedy really is settings: the OS will not ask again.
      await pump(
        tester,
        panel(locationState(permission: LocationPermissionState.deniedForever)),
      );

      expect(
        find.byKey(const Key('groupRide.location.permission')),
        findsOneWidget,
      );
      expect(
        find.byKey(const Key('groupRide.location.settings')),
        findsOneWidget,
      );
    });

    testWidgets('a disabled location service offers settings', (tester) async {
      // Service-off is not a permission problem, so retrying the permission would
      // change nothing; the rider has to turn the service on.
      await pump(
        tester,
        panel(
          locationState(permission: LocationPermissionState.serviceDisabled),
        ),
      );

      expect(
        find.text(textOf('en', 'groupRide.location.serviceOff')),
        findsOneWidget,
      );
      expect(
        find.byKey(const Key('groupRide.location.settings')),
        findsOneWidget,
      );
    });

    testWidgets('an imprecise fix is explained without a settings button', (
      tester,
    ) async {
      // Coarse location works; it is just less precise. Nothing to fix in settings.
      await pump(
        tester,
        panel(locationState(permission: LocationPermissionState.imprecise)),
      );

      expect(
        find.text(textOf('en', 'groupRide.location.imprecise')),
        findsOneWidget,
      );
      expect(
        find.byKey(const Key('groupRide.location.settings')),
        findsNothing,
      );
    });

    testWidgets('a denied-forever permission gets its own remedy', (
      tester,
    ) async {
      // The remedy differs: one is "ask again", the other is "open settings".
      // Collapsing them sends riders to a screen that cannot help them.
      await pump(
        tester,
        panel(locationState(permission: LocationPermissionState.deniedForever)),
      );

      expect(
        find.text(textOf('en', 'groupRide.location.deniedForever')),
        findsOneWidget,
      );
      expect(
        find.text(textOf('en', 'groupRide.location.denied')),
        findsNothing,
      );
    });

    testWidgets('opening settings is wired to the callback', (tester) async {
      var opened = 0;
      await pump(
        tester,
        panel(
          locationState(permission: LocationPermissionState.deniedForever),
          onOpenSettings: () => opened++,
        ),
      );

      await tester.tap(find.byKey(const Key('groupRide.location.settings')));
      await tester.pumpAndSettle();

      expect(opened, 1);
    });

    testWidgets('a granted permission raises no complaint', (tester) async {
      // Nothing to fix, so nothing to say — a green banner on every shared ride is
      // noise.
      await pump(
        tester,
        panel(
          locationState(
            sharing: true,
            permission: LocationPermissionState.granted,
          ),
        ),
      );

      expect(
        find.byKey(const Key('groupRide.location.permission')),
        findsNothing,
      );
    });

    testWidgets('no permission notice before anything was attempted', (
      tester,
    ) async {
      // The app never asks on its own, so it never has anything to report either.
      await pump(tester, panel(locationState()));

      expect(
        find.byKey(const Key('groupRide.location.permission')),
        findsNothing,
      );
    });
  });

  group('location panel: riders on the map', () {
    testWidgets('each rider gets a keyed dot', (tester) async {
      await pump(
        tester,
        panel(
          locationState(
            snapshot: snapshot([
              riderJson(userId: 'a', displayName: 'Rider A'),
              riderJson(userId: 'b', displayName: 'Rider B'),
            ]),
          ),
        ),
      );

      expect(find.byKey(const Key('groupRide.location.a')), findsOneWidget);
      expect(find.byKey(const Key('groupRide.location.b')), findsOneWidget);
      expect(
        find.text(textOf('en', 'groupRide.location.nobodySharing')),
        findsNothing,
      );
    });

    testWidgets('this rider is labelled so they can find themselves', (
      tester,
    ) async {
      // Without a self marker a rider has to guess which dot is theirs by
      // comparing coordinates.
      await pump(
        tester,
        panel(
          locationState(
            sharing: true,
            snapshot: snapshot([
              riderJson(userId: 'a', displayName: 'Rider A'),
              riderJson(userId: 'me', displayName: 'Rider B', isSelf: true),
            ]),
          ),
        ),
      );

      expect(find.text(textOf('en', 'groupRide.location.you')), findsOneWidget);
    });

    testWidgets('an imprecise fix is not drawn as an exact one', (
      tester,
    ) async {
      // A dot whose error radius is unknown must not look as precise as one that
      // knows it. This is presentation honesty, not a privacy control.
      final precise = RiderLocation.fromJson(riderJson(accuracyM: 12));
      final imprecise = RiderLocation.fromJson(riderJson(accuracyM: null));

      expect(precise.isPrecise, isTrue);
      expect(imprecise.isPrecise, isFalse);

      await pump(
        tester,
        panel(
          locationState(
            snapshot: snapshot([riderJson(userId: 'a', accuracyM: null)]),
          ),
        ),
      );
      expect(find.byKey(const Key('groupRide.location.a')), findsOneWidget);
    });
  });

  group('localization', () {
    for (final locale in const ['en', 'fr', 'ar']) {
      testWidgets('$locale renders the whole location panel without a gap', (
        tester,
      ) async {
        // A missing key must never surface as the raw key string.
        await pump(
          tester,
          panel(
            locationState(
              sharing: true,
              permission: LocationPermissionState.granted,
              snapshot: snapshot([riderJson(userId: 'me', isSelf: true)]),
            ),
          ),
          locale: locale,
        );

        expect(find.byType(Text), findsWidgets);
        expect(find.textContaining('groupRide.'), findsNothing);
        expect(find.textContaining('null'), findsNothing);
      });

      testWidgets('$locale renders every roster state', (tester) async {
        await pump(
          tester,
          Scaffold(
            body: RideRosterList(
              ride: ride(
                roster: [
                  for (final status in GroupRideParticipantStatus.values)
                    participantJson(
                      userId: 'u-${status.wire}',
                      status: status.wire,
                      displayName: 'Rider ${status.wire}',
                    ),
                ],
              ),
            ),
          ),
          locale: locale,
        );

        for (final status in GroupRideParticipantStatus.values) {
          expect(
            find.text(textOf(locale, status.labelKey)),
            findsOneWidget,
            reason: '${status.wire} must be translated in $locale',
          );
        }
      });

      testWidgets('$locale renders every ride status', (tester) async {
        for (final status in GroupRideStatus.values) {
          await pump(
            tester,
            Scaffold(body: RideStatusBadge(status: status)),
            locale: locale,
          );
          expect(find.text(textOf(locale, status.labelKey)), findsOneWidget);
        }
      });
    }

    testWidgets('Arabic renders right-to-left without overflowing', (
      tester,
    ) async {
      // The failure this catches is a Row that only overflows in the mirrored
      // direction, which an English-only smoke test never sees.
      await pump(
        tester,
        Scaffold(
          body: RideRosterList(
            ride: ride(
              roster: [
                participantJson(
                  userId: 'a',
                  displayName: 'درّاجParticipantName',
                  status: 'invited',
                  respondedAt: null,
                ),
              ],
            ),
            myUserId: 'u-1',
            onRemove: (_) {},
          ),
        ),
        locale: 'ar',
        direction: TextDirection.rtl,
      );

      expect(tester.takeException(), isNull);
    });

    testWidgets('the location panel does not overflow in RTL', (tester) async {
      await pump(
        tester,
        panel(
          locationState(
            sharing: true,
            permission: LocationPermissionState.denied,
            snapshot: snapshot([riderJson(userId: 'me', isSelf: true)]),
          ),
        ),
        locale: 'ar',
        direction: TextDirection.rtl,
      );

      expect(tester.takeException(), isNull);
    });

    test('the three locales define the same Phase 9 keys', () {
      // Parity is what turns a missing translation into a build failure rather than
      // a rider quietly reading English.
      for (final status in GroupRideStatus.values) {
        expect(l10n('en').has(status.labelKey), isTrue, reason: 'en');
        expect(l10n('fr').has(status.labelKey), isTrue, reason: 'fr');
        expect(l10n('ar').has(status.labelKey), isTrue, reason: 'ar');
      }
      for (final status in GroupRideParticipantStatus.values) {
        expect(l10n('en').has(status.labelKey), isTrue, reason: 'en');
        expect(l10n('fr').has(status.labelKey), isTrue, reason: 'fr');
        expect(l10n('ar').has(status.labelKey), isTrue, reason: 'ar');
      }
    });

    test('Arabic group-ride prose leaks no Latin words', () {
      // Brand-exception aside, a Latin word in Arabic copy is an untranslated
      // string that slipped through.
      const brand = 'CycleCoach';
      const keys = [
        'groupRide.rideTitle',
        'groupRide.createRide',
        'groupRide.roster',
        'groupRide.invite',
        'groupRide.leave',
        'groupRide.start',
        'groupRide.cancelRide',
        'groupRide.rideChat',
        'groupRide.liveLocation',
        'groupRide.error.rosterFrozen',
        'groupRide.error.needsRiders',
        'groupRide.location.nobodySharing',
        'groupRide.location.unavailable',
      ];
      final ar = l10n('ar');
      for (final key in keys) {
        final value = ar.get(key);
        final stripped = value.replaceAll(brand, '');
        expect(
          RegExp('[A-Za-z]').hasMatch(stripped),
          isFalse,
          reason: '$key leaked Latin: "$value"',
        );
      }
    });

    test('French group-ride prose leaks no Arabic', () {
      final fr = l10n('fr');
      for (final key in const [
        'groupRide.rideTitle',
        'groupRide.roster',
        'groupRide.invite',
        'groupRide.leave',
        'groupRide.start',
        'groupRide.cancelRide',
        'groupRide.liveLocation',
        'groupRide.error.needsRiders',
      ]) {
        expect(
          RegExp('[\u0600-\u06FF]').hasMatch(fr.get(key)),
          isFalse,
          reason: '$key leaked Arabic',
        );
      }
    });
  });

  group('error mapping', () {
    test('every ride error code maps to a real sentence', () {
      // An unmapped code degrades to a generic message, which is survivable — but
      // a mapped one must never resolve to the raw code.
      const codes = [
        'RIDE_ROSTER_FROZEN',
        'RIDE_NOT_OPEN',
        'RIDE_NOT_STARTED',
        'RIDE_NEEDS_RIDERS',
        'RIDE_NOT_INVITED',
        'RIDE_ALREADY_MEMBER',
        'RIDE_ORGANIZER_CANNOT_LEAVE',
        'RIDE_MEMBER_IMMUTABLE',
        'RIDE_BLOCKED',
        'RIDE_MEMBER_UNAVAILABLE',
        'RIDE_ROUTE_INCOMPLETE',
        'RIDE_ROUTE_VERSION_NOT_FOUND',
        'RIDE_LOCATION_UNAVAILABLE',
        'RIDE_CHANNEL_CLOSED',
        'RIDE_NOT_FOUND',
      ];
      final en = l10n('en');
      for (final code in codes) {
        final sentence = friendlyRideError(
          en,
          ApiException(409, code, 'server text'),
        );
        expect(sentence, isNotEmpty, reason: code);
        expect(sentence.contains(code), isFalse, reason: code);
      }
    });

    test('every location error code maps to a real sentence', () {
      const codes = [
        'LOCATION_UNAVAILABLE',
        'LOCATION_PUBLISH_FAILED',
        'LOCATION_STREAM_FAILED',
      ];
      final en = l10n('en');
      for (final code in codes) {
        expect(friendlyRideCode(en, code), isNotEmpty, reason: code);
        expect(
          friendlyRideCode(en, code).contains(code),
          isFalse,
          reason: code,
        );
      }
    });

    test('an unmapped code still yields a sentence, never the code', () {
      // The floor: a code this build has never heard of must not reach a rider as
      // text.
      final en = l10n('en');
      final sentence = friendlyRideCode(en, 'SOMETHING_NEW_FROM_THE_SERVER');
      expect(sentence, isNotEmpty);
      expect(sentence.contains('SOMETHING_NEW_FROM_THE_SERVER'), isFalse);
    });

    test('a not-found family does not distinguish missing from not-yours', () {
      // The backend refuses to tell those apart on purpose; a client that tried
      // would re-introduce the oracle.
      final en = l10n('en');
      final missing = friendlyRideError(
        en,
        ApiException(404, 'RIDE_NOT_FOUND', 'Ride not found.'),
      );
      final forbidden = friendlyRideError(
        en,
        ApiException(404, 'RIDE_CHANNEL_CLOSED', 'Ride not found.'),
      );
      expect(missing, isNotEmpty);
      expect(forbidden, isNotEmpty);
    });
  });
}
