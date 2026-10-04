import 'package:flutter/material.dart';

import '../../core/theme/app_colors.dart';

/// Mountain + rider mark (transparent PNG, cropped from the approved logo).
class LogoMark extends StatelessWidget {
  final double height;
  const LogoMark({super.key, this.height = 40});

  @override
  Widget build(BuildContext context) =>
      Image.asset('assets/logo/cyclecoach-mark.png', height: height);
}

/// Full lockup (mark + CycleCoach + Train • Ride • Explore), transparent.
class LogoFull extends StatelessWidget {
  final double height;
  const LogoFull({super.key, this.height = 160});

  @override
  Widget build(BuildContext context) =>
      Image.asset('assets/logo/cyclecoach-logo.png', height: height);
}

/// Horizontal header lockup: mark + "Cycle" white / "Coach" green.
class BrandLockup extends StatelessWidget {
  final double markHeight;
  final double fontSize;
  const BrandLockup({super.key, this.markHeight = 30, this.fontSize = 22});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        LogoMark(height: markHeight),
        const SizedBox(width: 8),
        Wordmark(fontSize: fontSize),
      ],
    );
  }
}

/// "Cycle" + "Coach" wordmark per docs/11-design-system.md.
class Wordmark extends StatelessWidget {
  final double fontSize;
  const Wordmark({super.key, this.fontSize = 22});

  @override
  Widget build(BuildContext context) {
    final style = TextStyle(
      fontSize: fontSize,
      fontWeight: FontWeight.w800,
      letterSpacing: -0.5,
      height: 1.1,
    );
    return RichText(
      text: TextSpan(
        style: DefaultTextStyle.of(context).style
            .merge(style)
            .copyWith(color: AppColors.textOnDark, shadows: const []),
        children: const [
          TextSpan(text: 'Cycle'),
          TextSpan(
            text: 'Coach',
            style: TextStyle(color: AppColors.primary),
          ),
        ],
      ),
    );
  }
}

/// "Train • Ride • Explore" tagline, letter-spaced.
class Tagline extends StatelessWidget {
  final String text;
  const Tagline({super.key, required this.text});

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      textAlign: TextAlign.center,
      style: const TextStyle(
        fontSize: 13,
        letterSpacing: 3,
        fontWeight: FontWeight.w500,
        color: AppColors.textOnDark,
      ),
    );
  }
}
