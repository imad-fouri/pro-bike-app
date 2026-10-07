import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../features/auth/presentation/auth_state.dart';
import '../../features/auth/presentation/forgot_password_page.dart';
import '../../features/auth/presentation/login_page.dart';
import '../../features/auth/presentation/onboarding_page.dart';
import '../../features/auth/presentation/register_page.dart';
import '../../features/auth/presentation/splash_page.dart';
import '../../features/bikes/presentation/bike_detail_page.dart';
import '../../features/bikes/presentation/bike_form_page.dart';
import '../../features/bikes/presentation/bike_list_page.dart';
import '../../features/chat/presentation/chat_inbox_page.dart';
import '../../features/chat/presentation/conversation_page.dart';
import '../../features/competition/presentation/challenge_detail_page.dart';
import '../../features/competition/presentation/challenge_form_page.dart';
import '../../features/competition/presentation/challenges_page.dart';
import '../../features/competition/presentation/rankings_page.dart';
import '../../features/group_rides/domain/group_ride.dart';
import '../../features/group_rides/presentation/group_ride_detail_page.dart';
import '../../features/group_rides/presentation/group_ride_form_page.dart';
import '../../features/group_rides/presentation/group_rides_page.dart';
import '../../features/coach/presentation/coach_page.dart';
import '../../features/notifications/presentation/notification_center_page.dart';
import '../../features/ads/presentation/consent_page.dart';
import '../../features/notifications/presentation/notification_settings_page.dart';
import '../../features/home/home_page.dart';
import '../../features/profile/profile_page.dart';
import '../../features/ride/presentation/ride_recording_page.dart';
import '../../features/ride/presentation/ride_start_page.dart';
import '../../features/ride/presentation/ride_summary_page.dart';
import '../../features/social/presentation/blocked_users_page.dart';
import '../../features/social/presentation/edit_social_profile_page.dart';
import '../../features/social/presentation/friend_requests_page.dart';
import '../../features/social/presentation/friends_page.dart';
import '../../features/social/presentation/my_social_profile_page.dart';
import '../../features/social/presentation/social_privacy_page.dart';
import '../../features/social/presentation/user_profile_page.dart';
import '../../features/social/presentation/user_search_page.dart';
import '../../features/routes/presentation/route_detail_page.dart';
import '../../features/routes/presentation/route_form_page.dart';
import '../../features/routes/presentation/route_list_page.dart';
import '../../features/training/presentation/training_pages.dart';
import '../../features/teams/presentation/my_teams_page.dart';
import '../../features/teams/presentation/team_form_page.dart';
import '../../features/teams/presentation/team_invitations_admin_page.dart';
import '../../features/teams/presentation/team_invitations_page.dart';
import '../../features/teams/presentation/team_invite_page.dart';
import '../../features/teams/presentation/team_join_requests_page.dart';
import '../../features/teams/presentation/team_members_page.dart';
import '../../features/teams/presentation/team_profile_page.dart';
import '../../features/teams/presentation/team_search_page.dart';
import '../../features/teams/presentation/team_settings_page.dart';
import '../../shared/widgets/placeholder_page.dart';

/// Public routes (no session needed). /splash is bootstrap-only: once restore
/// settles, unauthenticated users are sent to /login.
const _public = ['/login', '/register', '/forgot-password', '/onboarding'];

/// Router reacts to [authProvider]:
/// unknown/loading → /splash (restore), unauthenticated → /onboarding,
/// authenticated on auth pages → /home.
final routerProvider = Provider<GoRouter>((ref) {
  final auth = ref.watch(authProvider);
  return GoRouter(
    initialLocation: '/splash',
    redirect: (context, state) {
      final loc = state.matchedLocation;
      switch (auth.status) {
        case AuthStatus.unknown:
        case AuthStatus.loading:
          return loc == '/splash' ? null : '/splash';
        case AuthStatus.unauthenticated:
        case AuthStatus.error:
          // Public pages (register, forgot-password, onboarding itself) stay;
          // everything else funnels into the Get Started screen.
          return _public.contains(loc) ? null : '/onboarding';
        case AuthStatus.authenticated:
          if (loc == '/splash' ||
              loc == '/login' ||
              loc == '/register' ||
              loc == '/onboarding') {
            return '/home';
          }
          return null;
      }
    },
    routes: [
      GoRoute(path: '/splash', builder: (c, s) => const SplashPage()),
      GoRoute(path: '/onboarding', builder: (c, s) => const OnboardingPage()),
      GoRoute(path: '/login', builder: (c, s) => const LoginPage()),
      GoRoute(path: '/register', builder: (c, s) => const RegisterPage()),
      GoRoute(
        path: '/forgot-password',
        builder: (c, s) => const ForgotPasswordPage(),
      ),
      GoRoute(path: '/home', builder: (c, s) => const HomePage()),
      GoRoute(path: '/profile', builder: (c, s) => const ProfilePage()),
      GoRoute(path: '/bikes', builder: (c, s) => const BikeListPage()),
      GoRoute(path: '/bikes/new', builder: (c, s) => const BikeFormPage()),
      GoRoute(
        path: '/bikes/:id',
        builder: (c, s) => BikeDetailPage(bikeId: s.pathParameters['id']!),
      ),
      GoRoute(
        path: '/bikes/:id/edit',
        builder: (c, s) => BikeFormPage(bikeId: s.pathParameters['id']!),
      ),
      GoRoute(path: '/ride/start', builder: (c, s) => const RideStartPage()),
      GoRoute(
        path: '/ride/recording',
        builder: (c, s) => const RideRecordingPage(),
      ),
      GoRoute(
        path: '/ride/summary',
        builder: (c, s) => const RideSummaryPage(),
      ),
      GoRoute(
        path: '/ride/recovery',
        builder: (c, s) => const RideRecoveryPage(),
      ),
      // Phase 5: routes (planned geometry) — always registered before the
      // placeholder loop below, which no longer owns '/routes'.
      GoRoute(path: '/routes', builder: (c, s) => const RouteListPage()),
      GoRoute(path: '/routes/new', builder: (c, s) => const RouteFormPage()),
      GoRoute(
        path: '/routes/:id',
        builder: (c, s) => RouteDetailPage(routeId: s.pathParameters['id']!),
      ),
      GoRoute(
        path: '/routes/:id/edit',
        builder: (c, s) => RouteFormPage(routeId: s.pathParameters['id']!),
      ),
      // Phase 6: training replaces the '/training' placeholder. Registered
      // before the loop below, which no longer owns that path.
      GoRoute(path: '/training', builder: (c, s) => const TrainingPage()),
      GoRoute(
        path: '/training/versions',
        builder: (c, s) => const CalculationVersionsPage(),
      ),
      // Phase 7: coach — the placeholder loop below no longer owns '/coach'.
      GoRoute(path: '/coach', builder: (c, s) => const CoachPage()),
      // Phase 8.2: teams and cycling groups. '/teams' leaves the placeholder
      // loop below. '/groups' STAYS a placeholder: group rides are explicitly
      // out of scope for this phase.
      GoRoute(path: '/teams', builder: (c, s) => const MyTeamsPage()),
      GoRoute(path: '/teams/new', builder: (c, s) => const TeamFormPage()),
      GoRoute(path: '/teams/search', builder: (c, s) => const TeamSearchPage()),
      GoRoute(
        path: '/teams/invitations',
        builder: (c, s) => const TeamInvitationsPage(),
      ),
      GoRoute(
        path: '/teams/:id',
        builder: (c, s) => TeamProfilePage(teamId: s.pathParameters['id']!),
      ),
      GoRoute(
        path: '/teams/:id/edit',
        builder: (c, s) => TeamFormPage(teamId: s.pathParameters['id']!),
      ),
      GoRoute(
        path: '/teams/:id/members',
        builder: (c, s) => TeamMembersPage(teamId: s.pathParameters['id']!),
      ),
      GoRoute(
        path: '/teams/:id/join-requests',
        builder: (c, s) =>
            TeamJoinRequestsPage(teamId: s.pathParameters['id']!),
      ),
      GoRoute(
        path: '/teams/:id/invitations',
        builder: (c, s) =>
            TeamInvitationsAdminPage(teamId: s.pathParameters['id']!),
      ),
      GoRoute(
        path: '/teams/:id/invite',
        builder: (c, s) => TeamInvitePage(teamId: s.pathParameters['id']!),
      ),
      GoRoute(
        path: '/teams/:id/settings',
        builder: (c, s) => TeamSettingsPage(teamId: s.pathParameters['id']!),
      ),
      // Phase 8.4: notifications. '/notifications' leaves the placeholder loop
      // below and becomes the real notification center.
      GoRoute(
        path: '/notifications',
        builder: (c, s) => const NotificationCenterPage(),
      ),
      GoRoute(
        path: '/notifications/settings',
        builder: (c, s) => const NotificationSettingsPage(),
      ),
      // Phase 8.1: social identity & relationships. '/friends' leaves the
      // placeholder loop below and becomes the real friend list.
      GoRoute(
        path: '/profile/social',
        builder: (c, s) => const MySocialProfilePage(),
      ),
      GoRoute(
        path: '/profile/social/edit',
        builder: (c, s) => const EditSocialProfilePage(),
      ),
      GoRoute(
        path: '/profile/social/privacy',
        builder: (c, s) => const SocialPrivacyPage(),
      ),
      GoRoute(path: '/friends', builder: (c, s) => const FriendsPage()),
      GoRoute(
        path: '/friends/search',
        builder: (c, s) => const UserSearchPage(),
      ),
      GoRoute(
        path: '/friends/requests',
        builder: (c, s) => const FriendRequestsPage(),
      ),
      GoRoute(
        path: '/friends/blocked',
        builder: (c, s) => const BlockedUsersPage(),
      ),
      GoRoute(
        path: '/users/:userId',
        builder: (c, s) => UserProfilePage(userId: s.pathParameters['userId']!),
      ),
      // Phase 8.3: chat. '/chat' leaves the placeholder loop below. The
      // conversation title rides in as a query parameter rather than being
      // fetched: the name is already known by whatever navigated here (the
      // inbox row, a team profile, a rider profile), and re-fetching it would
      // add a round trip purely to render an app bar.
      GoRoute(path: '/chat', builder: (c, s) => const ChatInboxPage()),
      GoRoute(
        path: '/chat/:id',
        builder: (c, s) {
          // Absent vs. present matters: a notification deep link carries no
          // `rideStatus`, and inventing one would claim the server had told us
          // the status when it never did. Unknown leaves the composer up and lets
          // the server be the authority.
          final rideStatus = s.uri.queryParameters['rideStatus'];
          return ConversationScreen(
            conversationId: s.pathParameters['id']!,
            title: s.uri.queryParameters['name'] ?? '',
            closedBecauseRideStatus: rideStatus == null
                ? null
                : GroupRideStatus.parse(rideStatus),
            readOnly:
                rideStatus != null &&
                GroupRideStatus.parse(rideStatus).isTerminal,
          );
        },
      ),
      // Phase 9: group rides. '/group-rides' leaves the placeholder loop below.
      //
      // Three routes, and the nesting is deliberate: `/group-rides/new` is
      // registered BEFORE `/group-rides/:id` so "new" can never be read as a ride
      // id. The order is load-bearing in go_router — reversing it makes the create
      // form unreachable, which is exactly the kind of bug a router test catches
      // and a manual click-through might not.
      GoRoute(path: '/group-rides', builder: (c, s) => const GroupRidesPage()),
      GoRoute(
        path: '/group-rides/new',
        builder: (c, s) => const GroupRideFormPage(),
      ),
      GoRoute(
        path: '/group-rides/:id',
        builder: (c, s) => GroupRideDetailPage(rideId: s.pathParameters['id']!),
      ),

      // WS-SM: advertising choices. A real screen, not a placeholder: consent
      // needs its own route so profile, settings, and future first-run flows
      // can all link to the same decision point.
      GoRoute(path: '/settings/ads', builder: (c, s) => const ConsentPage()),

      // Phase 10 — WS-RC: rankings and challenges. '/performance' leaves the
      // placeholder loop below; the nav tab that pointed at a placeholder now
      // lands on a real leaderboard.
      GoRoute(path: '/performance', builder: (c, s) => const RankingsPage()),
      GoRoute(path: '/challenges', builder: (c, s) => const ChallengesPage()),
      // Registered BEFORE `/challenges/:id` so "new" can never be read as a
      // challenge id — same load-bearing order as `/group-rides/new`.
      GoRoute(
        path: '/challenges/new',
        builder: (c, s) => const ChallengeFormPage(),
      ),
      GoRoute(
        path: '/challenges/:id',
        builder: (c, s) =>
            ChallengeDetailPage(challengeId: s.pathParameters['id']!),
      ),
      for (final p in ['/rides', '/settings'])
        GoRoute(
          path: p,
          builder: (c, s) => PlaceholderPage(title: p),
        ),
    ],
  );
});
