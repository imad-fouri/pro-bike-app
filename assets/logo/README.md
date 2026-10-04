# assets/logo — brand source

Approved CycleCoach logo (green faceted mountains + white rider,
`Cycle` white / `Coach` green) was supplied as chat images, not as repo files.
Do NOT redraw it.

Flutter assets (authoritative, referenced by `mobile/pubspec.yaml`):
- `mobile/assets/logo/cyclecoach-mark.png` — mountain + rider mark, transparent
- `mobile/assets/logo/cyclecoach-logo.png` — full lockup with wordmark +
  `Train • Ride • Explore` tagline, transparent

Both were derived from the provided logo PNG (black key removed, tight-cropped).
Source PNG lives outside the repo (`Downloads/ChatGPT Image … 03_13_29 AM.png`);
if an SVG appears later, drop it in `mobile/assets/logo/` and swap the
`Image.asset` calls in `mobile/lib/shared/widgets/brand.dart`.
