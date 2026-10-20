import 'dart:ui';

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import 'login_background.dart';

/// Shared authentication design system: alpine blue/navy glassmorphism.
///
/// Every auth screen (sign in, sign up, password recovery) is built from
/// these tokens and widgets so they read as one professionally designed
/// surface. The palette is deliberately scoped to authentication: the rest
/// of the app keeps its own theme tokens.
///
/// The green brand PNGs stay on the splash/onboarding surfaces; auth cards
/// use the text lockup below so no green enters the auth experience.
abstract final class AuthColors {
  /// Primary interactive blue.
  static const primary = Color(0xFF3978F6);

  /// Primary gradient end (subtle depth on buttons and badges).
  static const primaryDeep = Color(0xFF2B5FD9);

  /// Depth navy for overlays.
  static const navy = Color(0xFF101B32);

  /// Secondary blue for accents and the "Coach" wordmark half.
  static const secondary = Color(0xFF79B8FF);

  /// Frosted-glass surface (translucent white, ~16% opacity).
  static Color get glass => Colors.white.withValues(alpha: 0.16);

  /// Glass border (translucent white, ~35% opacity).
  static Color get glassBorder => Colors.white.withValues(alpha: 0.35);

  static const text = Colors.white;
  static const textSecondary = Color(0xFFD5DFEF);
  static const textMuted = Color(0xFFA7B5CB);

  static const error = Color(0xFFFF6B78);
  static const success = Color(0xFF64D9B0);

  static const fieldFill = Colors.white;
  static const fieldText = Color(0xFF1A2433);
  static const fieldHint = Color(0xFF8A8A8A);
  static const fieldIcon = Color(0xFF6B6B6B);
  static const fieldError = Color(0xFFFF8A80);

  static const cardRadius = 24.0;
  static const fieldRadius = 12.0;
  static const buttonRadius = 26.0;
  static const maxCardWidth = 420.0;
}

/// Maps a backend/API error code to a friendly localized message key.
///
/// Technical codes (notably `NETWORK_ERROR`) are never shown to users; the
/// raw code stays available to callers for `debugPrint` diagnostics.
String friendlyAuthErrorKey(String? code) {
  switch (code) {
    case 'INVALID_CREDENTIALS':
      return 'auth.badCredentials';
    case 'NETWORK_ERROR':
    case 'NETWORK_TIMEOUT':
      return 'auth.networkError';
    case 'SESSION_ENDED':
    case 'UNAUTHORIZED':
      return 'auth.sessionEnded';
    default:
      return 'auth.genericError';
  }
}

/// Full-screen auth scaffold: scenic photo, navy readability overlay, and a
/// centered scrollable column capped at [AuthColors.maxCardWidth].
///
/// Safe on small screens and with the keyboard open: the content scrolls and
/// nothing is clipped. No `AppBar` — navigation uses in-card links and the
/// system back button.
class AuthScreenScaffold extends StatelessWidget {
  final Widget child;
  const AuthScreenScaffold({super.key, required this.child});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      extendBodyBehindAppBar: true,
      body: Stack(
        children: [
          const AuthBackground(),
          // Navy overlay: keeps the photo visible while giving the glass
          // card and white text reliable contrast.
          Positioned.fill(
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [
                    AuthColors.navy.withValues(alpha: 0.42),
                    AuthColors.navy.withValues(alpha: 0.22),
                    AuthColors.navy.withValues(alpha: 0.55),
                  ],
                  stops: const [0.0, 0.45, 1.0],
                ),
              ),
            ),
          ),
          SafeArea(
            child: Center(
              child: SingleChildScrollView(
                padding: const EdgeInsets.symmetric(
                  horizontal: 32,
                  vertical: 24,
                ),
                child: ConstrainedBox(
                  constraints: const BoxConstraints(
                    maxWidth: AuthColors.maxCardWidth,
                  ),
                  child: child,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Frosted-glass card shared by every auth screen.
class AuthGlassCard extends StatelessWidget {
  final Widget child;
  const AuthGlassCard({super.key, required this.child});

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(AuthColors.cardRadius),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 16, sigmaY: 16),
        child: Container(
          padding: const EdgeInsets.fromLTRB(24, 28, 24, 24),
          decoration: BoxDecoration(
            color: AuthColors.glass,
            borderRadius: BorderRadius.circular(AuthColors.cardRadius),
            border: Border.all(color: AuthColors.glassBorder),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withValues(alpha: 0.25),
                blurRadius: 24,
                offset: const Offset(0, 12),
              ),
            ],
          ),
          child: child,
        ),
      ),
    );
  }
}

/// Palette-matched brand lockup for auth cards: cycling badge, CycleCoach
/// wordmark in white/secondary-blue, and the tagline.
///
/// The green logo PNGs stay on splash/onboarding; this text presentation
/// keeps green out of the auth experience without redrawing any asset.
class AuthBrandHeader extends StatelessWidget {
  final double badgeSize;
  const AuthBrandHeader({super.key, this.badgeSize = 52});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: badgeSize,
          height: badgeSize,
          decoration: BoxDecoration(
            gradient: const LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [AuthColors.primary, AuthColors.primaryDeep],
            ),
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: AuthColors.glassBorder),
            boxShadow: [
              BoxShadow(
                color: AuthColors.primary.withValues(alpha: 0.35),
                blurRadius: 16,
                offset: const Offset(0, 6),
              ),
            ],
          ),
          child: Icon(
            Icons.directions_bike_rounded,
            color: AuthColors.text,
            size: badgeSize * 0.55,
          ),
        ),
        const SizedBox(height: 12),
        RichText(
          textAlign: TextAlign.center,
          text: const TextSpan(
            style: TextStyle(
              fontSize: 26,
              fontWeight: FontWeight.w800,
              letterSpacing: -0.5,
              height: 1.1,
            ),
            children: [
              TextSpan(
                text: 'Cycle',
                style: TextStyle(color: AuthColors.text),
              ),
              TextSpan(
                text: 'Coach',
                style: TextStyle(color: AuthColors.secondary),
              ),
            ],
          ),
        ),
        const SizedBox(height: 6),
        Text(
          t.get('welcome'),
          textAlign: TextAlign.center,
          style: const TextStyle(
            fontSize: 13,
            letterSpacing: 3,
            fontWeight: FontWeight.w500,
            color: AuthColors.textSecondary,
          ),
        ),
      ],
    );
  }
}

/// Card heading + supporting text, used by sign up and recovery.
class AuthCardHeading extends StatelessWidget {
  final String titleKey;
  final String? subtitleKey;
  const AuthCardHeading({super.key, required this.titleKey, this.subtitleKey});

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          t.get(titleKey),
          textAlign: TextAlign.center,
          style: const TextStyle(
            fontSize: 22,
            fontWeight: FontWeight.w700,
            color: AuthColors.text,
          ),
        ),
        if (subtitleKey != null) ...[
          const SizedBox(height: 8),
          Text(
            t.get(subtitleKey!),
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 14, color: AuthColors.textMuted),
          ),
        ],
      ],
    );
  }
}

/// White pill field on the glass card, with icon, focus ring, and an
/// optional show/hide toggle for passwords.
///
/// The widget carries the test key (as the previous per-screen fields did);
/// the inner [TextFormField] stays keyless so key lookups resolve to exactly
/// one widget.
class AuthTextField extends StatefulWidget {
  final TextEditingController controller;
  final String hint;
  final IconData icon;
  final String? Function(String?) validator;
  final TextInputType? keyboardType;
  final TextInputAction? textInputAction;
  final bool password;
  final void Function(String)? onSubmitted;
  const AuthTextField({
    super.key,
    required this.controller,
    required this.hint,
    required this.icon,
    required this.validator,
    this.keyboardType,
    this.textInputAction,
    this.password = false,
    this.onSubmitted,
  });

  @override
  State<AuthTextField> createState() => _AuthTextFieldState();
}

class _AuthTextFieldState extends State<AuthTextField> {
  bool _obscured = true;

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final obscure = widget.password && _obscured;
    return TextFormField(
      controller: widget.controller,
      obscureText: obscure,
      keyboardType: widget.keyboardType,
      textInputAction: widget.textInputAction,
      style: const TextStyle(color: AuthColors.fieldText, fontSize: 15),
      decoration: InputDecoration(
        hintText: widget.hint,
        hintStyle: const TextStyle(color: AuthColors.fieldHint),
        prefixIcon: Icon(widget.icon, color: AuthColors.fieldIcon),
        suffixIcon: widget.password
            ? IconButton(
                tooltip: _obscured
                    ? t.get('auth.showPassword')
                    : t.get('auth.hidePassword'),
                icon: Icon(
                  _obscured
                      ? Icons.visibility_rounded
                      : Icons.visibility_off_rounded,
                  color: AuthColors.fieldIcon,
                ),
                onPressed: () => setState(() => _obscured = !_obscured),
              )
            : null,
        filled: true,
        fillColor: AuthColors.fieldFill,
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 16,
          vertical: 16,
        ),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AuthColors.fieldRadius),
          borderSide: BorderSide.none,
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AuthColors.fieldRadius),
          borderSide: BorderSide.none,
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AuthColors.fieldRadius),
          borderSide: const BorderSide(color: AuthColors.primary, width: 2),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AuthColors.fieldRadius),
          borderSide: const BorderSide(
            color: AuthColors.fieldError,
            width: 1.5,
          ),
        ),
        focusedErrorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AuthColors.fieldRadius),
          borderSide: const BorderSide(color: AuthColors.fieldError, width: 2),
        ),
        errorStyle: const TextStyle(
          color: AuthColors.textSecondary,
          fontSize: 12,
          fontWeight: FontWeight.w600,
        ),
      ),
      validator: widget.validator,
      onFieldSubmitted: widget.onSubmitted,
    );
  }
}

/// Full-width blue primary button with loading, pressed, and disabled states.
class AuthPrimaryButton extends StatelessWidget {
  final String label;
  final bool busy;
  final VoidCallback? onPressed;
  const AuthPrimaryButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.busy = false,
  });

  @override
  Widget build(BuildContext context) {
    final enabled = onPressed != null && !busy;
    return SizedBox(
      height: 52,
      child: Material(
        type: MaterialType.transparency,
        child: Ink(
          decoration: BoxDecoration(
            gradient: enabled
                ? const LinearGradient(
                    colors: [AuthColors.primary, AuthColors.primaryDeep],
                  )
                : null,
            color: enabled ? null : AuthColors.primary.withValues(alpha: 0.45),
            borderRadius: BorderRadius.circular(AuthColors.buttonRadius),
          ),
          child: InkWell(
            borderRadius: BorderRadius.circular(AuthColors.buttonRadius),
            onTap: enabled ? onPressed : null,
            child: Center(
              child: busy
                  ? const SizedBox(
                      width: 22,
                      height: 22,
                      child: CircularProgressIndicator(
                        strokeWidth: 2.5,
                        color: AuthColors.text,
                      ),
                    )
                  : Text(
                      label,
                      style: const TextStyle(
                        fontSize: 17,
                        fontWeight: FontWeight.w600,
                        color: AuthColors.text,
                      ),
                    ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Centered underlined text link.
class AuthTextLink extends StatelessWidget {
  final String text;
  final VoidCallback onTap;
  const AuthTextLink({super.key, required this.text, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: GestureDetector(
        onTap: onTap,
        child: Text(
          text,
          style: const TextStyle(
            color: AuthColors.text,
            fontSize: 13,
            decoration: TextDecoration.underline,
          ),
        ),
      ),
    );
  }
}

/// "Prefix + link" account-switch row (sign up / back to sign in).
class AuthAccountSwitch extends StatelessWidget {
  final String prefixKey;
  final String linkKey;
  final VoidCallback onTap;
  const AuthAccountSwitch({
    super.key,
    required this.prefixKey,
    required this.linkKey,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Center(
      child: RichText(
        textAlign: TextAlign.center,
        text: TextSpan(
          style: const TextStyle(color: AuthColors.text, fontSize: 13),
          children: [
            TextSpan(text: t.get(prefixKey)),
            TextSpan(
              text: t.get(linkKey),
              style: const TextStyle(
                decoration: TextDecoration.underline,
                fontWeight: FontWeight.w600,
              ),
              recognizer: TapGestureRecognizer()..onTap = onTap,
            ),
          ],
        ),
      ),
    );
  }
}

/// Friendly submission error. Never shows a raw backend code.
class AuthErrorText extends StatelessWidget {
  final String messageKey;
  const AuthErrorText({super.key, required this.messageKey});

  @override
  Widget build(BuildContext context) {
    return Text(
      context.l10n.get(messageKey),
      textAlign: TextAlign.center,
      style: const TextStyle(color: AuthColors.error, fontSize: 13),
    );
  }
}

/// Success confirmation (recovery email state).
class AuthSuccessText extends StatelessWidget {
  final String messageKey;
  const AuthSuccessText({super.key, required this.messageKey});

  @override
  Widget build(BuildContext context) {
    return Text(
      context.l10n.get(messageKey),
      textAlign: TextAlign.center,
      style: const TextStyle(color: AuthColors.success, fontSize: 14),
    );
  }
}

/// Vertical rhythm shared by auth card bodies.
abstract final class AuthGaps {
  static const field = SizedBox(height: 12);
  static const section = SizedBox(height: AppSpacing.lg);
  static const tight = SizedBox(height: AppSpacing.sm);
}
