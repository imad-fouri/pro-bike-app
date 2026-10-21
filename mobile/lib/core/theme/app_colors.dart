import 'package:flutter/material.dart';

/// Brand tokens — Bike Pro Mobile restyle (dark-first, amber accent).
abstract final class AppColors {
  static const background = Color(0xFF0B0D11);
  static const surface = Color(0xFF11151C);
  static const surface2 = Color(0xFF1C2330);
  static const primary = Color(0xFFE9A23B);
  static const primaryDark = Color(0xFF7F6232);
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
  static const pill = 999.0;
}

abstract final class AppSpacing {
  static const xs = 4.0;
  static const sm = 8.0;
  static const md = 12.0;
  static const lg = 16.0;
  static const xl = 24.0;
}
