import 'package:flutter/material.dart';

/// Brand tokens from docs/11-design-system.md
abstract final class AppColors {
  static const background = Color(0xFF0A1F1C);
  static const surface = Color(0xFF122B27);
  static const surface2 = Color(0xFF1A3A34);
  static const primary = Color(0xFF7BC043);
  static const primaryDark = Color(0xFF2E7D32);
  static const accentLime = Color(0xFFA8D93A);
  static const gold = Color(0xFFF5B301);
  static const textOnDark = Colors.white;
  static const textMuted = Color(0xFFB0C4BE);

  static const zone5 = Color(0xFFE53935);
  static const zone4 = Color(0xFFEF6C00);
  static const zone3 = Color(0xFFFBC02D);
  static const zone2 = Color(0xFF43A047);
  static const zone1 = Color(0xFFBDBDBD);
}

abstract final class AppRadius {
  static const card = 16.0;
  static const pill = 24.0;
}

abstract final class AppSpacing {
  static const xs = 4.0;
  static const sm = 8.0;
  static const md = 16.0;
  static const lg = 24.0;
  static const xl = 32.0;
}
