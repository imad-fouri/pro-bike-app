# 11 — Design System (Logo + App UI from mockups)

## Logo (approved asset)
Green faceted mountains (`#8BC34A` → `#2E7D32` gradient) + white rider silhouette. Wordmark: `Cycle` #FFFFFF, `Coach` #7BC043, bold rounded sans (use Poppins/Montserrat). Tagline `Train • Ride • Explore`, letter-spaced, white/green dots. Variants: on-black (primary), on-dark-green, mono-white. Clearspace = cap-height of "C". Min width 120px. Place files in `assets/logo/`.

## Colors (extracted from screenshots)
- bg: `#0A1F1C` (deep forest), surface: `#122B27` / `#1A3A34`, card: `#FFFFFF` on light sheets, ink: `#0A1F1C`
- primary: `#7BC043`, primary-dark: `#4A9A2B`, accent-lime: `#A8D93A`
- zone5 `#E53935`, zone4 `#EF6C00`, zone3 `#FBC02D`, zone2 `#43A047`, zone1 `#BDBDBD`
- text-on-dark `#FFFFFF` / `#B0C4BE` secondary; gold premium `#F5B301`

## Typography / shape
Headings Poppins SemiBold, body Inter/Plus Jakarta Sans; AR/FR support (Cairo/Tajawal for Arabic). Radius 16 cards, 24 pills/buttons. Dark-first theme (`ThemeData.dark` + light sheets for Training/Performance).

## Screens to build (match mockups)
Splash (logo+valley bg) → Get Started; Home (Today's Workout 45km/2h15/Zone2, Quick Stats 180km +12% / 2400m +18%, Next Event Oued Zem→Martil); Route Details (540km, +6800m, 42% Gravel, difficulty Hard); Live Ride (map+distance/speed/HR/cadence, pause); AI Coach (zones + "Next climb 500m"); Training Plan Week 1-4; Performance (180km, fitness trend, VO2max 48 est., load Moderate); Profile (Imad Fouri, 72kg, FTP 220W est., bikes, Go Premium).

## Flutter tokens (Phase 1)
Define `AppColors`, `AppText`, `AppRadius` in `core/theme/`; RTL via `flutter_localizations` + `ar` arb; logo as SVG.
