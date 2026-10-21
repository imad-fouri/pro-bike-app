# 11 — Design System (Logo + App UI from mockups)

## Logo (approved asset)
Green faceted mountains (`#E9A23B` → `#7F6232` gradient) + white rider silhouette. Wordmark: `Cycle` #FFFFFF, `Coach` #E9A23B, bold rounded sans (use Poppins/Montserrat). Tagline `Train • Ride • Explore`, letter-spaced, white/green dots. Variants: on-black (primary), on-dark-green, mono-white. Clearspace = cap-height of "C". Min width 120px. Place files in `assets/logo/`.

## Colors (extracted from screenshots)
- bg: `#0B0D11`, surface: `#11151C` / `#1C2330`, card: `#FFFFFF` on light sheets, ink: `#0B0D11`
- primary: `#E9A23B`, primary-dark: `#7F6232`, accent-lime: `#A8D93A`
- zone5 `#E53935`, zone4 `#EF6C00`, zone3 `#FBC02D`, zone2 `#43A047`, zone1 `#BDBDBD`
- text-on-dark `#FFFFFF` / `#B0C4BE` secondary; gold premium `#F5B301`

## Typography / shape
Headings Poppins SemiBold, body Inter/Plus Jakarta Sans; AR/FR support (Cairo/Tajawal for Arabic). Radius 16 cards, pill (999) buttons. Dark-first theme (`ThemeData.dark` + light sheets for Training/Performance).

## Screens to build (match mockups)
Splash (logo+valley bg) → Get Started; Home (Today's Workout 45km/2h15/Zone2, Quick Stats 180km +12% / 2400m +18%, Next Event Oued Zem→Martil); Route Details (540km, +6800m, 42% Gravel, difficulty Hard); Live Ride (map+distance/speed/HR/cadence, pause); AI Coach (zones + "Next climb 500m"); Training Plan Week 1-4; Performance (180km, fitness trend, VO2max 48 est., load Moderate); Profile (Imad Fouri, 72kg, FTP 220W est., bikes, Go Premium).

## Flutter tokens (Phase 1)
Define `AppColors`, `AppText`, `AppRadius` in `core/theme/`; RTL via `flutter_localizations` + `ar` arb; logo as SVG.
