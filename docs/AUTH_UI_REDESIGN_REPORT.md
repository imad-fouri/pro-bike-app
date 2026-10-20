# Auth UI Redesign Report — Premium Glassmorphism Authentication

Status convention: PASS / FAIL / BLOCKED / NOT TESTED.
Nothing in this work was committed.

## 1. Objective

Give Sign In, Sign Up, and Forgot Password one consistent premium
glassmorphism system in an alpine blue/navy palette, reusing the existing
scenic background and authentication contracts. No auth logic rewrite, no
mock auth, no backend changes.

## 2. Files changed

Created:

```text
mobile/lib/features/auth/presentation/auth_design.dart
  Shared design system: AuthColors tokens, friendlyAuthErrorKey mapper,
  AuthScreenScaffold, AuthGlassCard, AuthBrandHeader, AuthCardHeading,
  AuthTextField (show/hide), AuthPrimaryButton, AuthTextLink,
  AuthAccountSwitch, AuthErrorText, AuthSuccessText, AuthGaps.
mobile/test/auth_screens_test.dart
  11 widget/unit tests for the redesign (see §6).
docs/AUTH_UI_REDESIGN_REPORT.md
  This report.
```

Modified:

```text
mobile/lib/features/auth/presentation/login_page.dart
  Same layout/keys/strings/routes; now built from the shared system.
  Friendly errors instead of raw codes; password show/hide; palette
  lockup instead of the green PNG. AuthScaffold kept byte-identical for
  its non-auth users (bikes/routes/ride-start/profile forms).
mobile/lib/features/auth/presentation/register_page.dart
  Full glass redesign (was a plain themed form): heading, subtitle, four
  fields, loading button, friendly errors, sign-in row. Contract unchanged.
mobile/lib/features/auth/presentation/forgot_password_page.dart
  Full glass redesign: recovery icon, heading, email field, send button
  with loading state, success confirmation, network-error state, back link.
  Existing requestPasswordReset flow used unchanged.
mobile/lib/features/auth/presentation/login_background.dart
  Painted fallback retinted from green slopes to alpine blue-slate
  (also the widget-test rendering path).
mobile/lib/core/l10n/app_localizations.dart
  13 new keys × en/fr/ar (titles, subtitles, links, friendly errors,
  show/hide labels). No existing key altered.
mobile/test/login_page_test.dart
  One assertion updated for the intended behavior change: bad credentials
  now show the friendly message and never the raw code.
mobile/assets/images/auth-road.jpg
  Cropped the top 250 px (logo band baked into the photo) → 896×950.
  Original backed up outside the repo. Keeps green branding off tall
  screens; landscape composition unchanged.
```

## 3. Design decisions

1. Auth-scoped tokens, not a global rebrand. `AppColors.primary` stays
   green for the rest of the app (docs/11, splash, home title, theme);
   rebranding the whole app would break `app_test` brand-token assertions
   and is out of scope. All blue/navy lives in `AuthColors`.
2. Text lockup instead of the logo PNGs on auth cards. Both PNGs are
   green-dominant; redrawing them is explicitly out of scope, and tinting
   would distort them. The blue badge + white/secondary-blue "CycleCoach"
   + tagline keeps brand identity (cycling icon, name, tagline) in-palette.
   Splash/onboarding keep the approved PNGs (pinned by `app_test`).
3. Photo kept; baked-in logo cropped out. The road photo is the approved
   scenic direction; only its composited logo band conflicted.
4. Friendly errors via `friendlyAuthErrorKey`: bad credentials / no
   connection / session ended / generic. Raw codes never render; the code
   goes to `debugPrint` (no PII — code only) in login/register submits.
5. Recovery honesty: success and unknown-email share the confirmation
   (anti-enumeration preserved); a request that never reached the server
   shows the network error instead of faking "sent".
6. Registration contract untouched: display name/email/password to the
   existing `register` flow; confirmation validated locally first (as
   before), so `password_confirm` still matches by construction.
7. No provider or repository changes: same providers, same routes, same
   guards, same token behavior. The `SessionCoordinator` fix from the
   circular-dependency work is preserved and untouched.

## 4. Out of scope / limitations

- Global green theme, home title lockup, and splash/onboarding PNGs are
  unchanged by design (see §3.1–§3.2).
- The rider's jersey in the photo keeps its green accents (photographic
  content, not UI).
- `AuthScaffold` (plain themed form scaffold) remains for its non-auth
  users; auth screens no longer use it but it was not removed.
- No new animations; blur kept at 16px for performance.
- Interactive login on a physical device was verified earlier on Android
  (emulator, pre-redesign UI); this phase verified rendering + mocked
  flows, not a physical-device pass of the new screens.

## 5. Localization

13 keys added to en, fr, and ar side by side; RTL asserted by widget
tests on login and register (Arabic renders RTL with translated strings).
No English-only strings added; no existing translations altered.

## 6. Test results

| Check | Result |
|-------|--------|
| Full mobile suite `flutter test` | PASS — 742 passed (731 baseline + 11 new) |
| `flutter analyze` | PASS — no issues |
| `dart format --set-exit-if-changed lib test` | PASS — 0 changed |
| `flutter build web` (redesign + cropped asset) | PASS |
| New `auth_screens_test.dart` (11) | PASS — mapper table, key parity en/fr/ar, register render/validation/RTL, forgot render/success/network-error, login network message, visibility toggle |
| Updated `login_page_test.dart` | PASS — friendly message asserted, raw code asserted absent |
| Backend tests | NOT RUN — no backend files changed |

One intended assertion change (see §2): bad credentials no longer display
`(INVALID_CREDENTIALS)`. State-level `errorCode` behavior is unchanged
(`auth_state_test` untouched and green).

## 7. Visual verification (Edge, headless screenshots)

Served the production web build locally and captured:

- `/#/login` at 1280×900: glass card, blue lockup, white fields,
  blue pill button, links — PASS, no overflow.
- `/#/register` at 1280×1000: heading, subtitle, four fields, button,
  sign-in row — PASS, consistent with sign in.
- `/#/forgot-password` at 1280×900: icon, heading, subtitle, field,
  button, back link — PASS, consistent.
- `/#/login` at 390×844: card fits, no horizontal scroll, no clipping —
  PASS. This shot caught the baked-in photo logo, which led to the crop
  (§2); re-shot after the crop — PASS, palette fully consistent.

Screenshots are display-only evidence; behavior is covered by the widget
tests above. Headless rendering cannot exercise hover/keyboard focus,
which were implemented (InkWell states, focus rings, tooltips) but marked
NOT TESTED beyond code review.
