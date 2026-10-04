import 'dart:ui';

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../../shared/widgets/brand.dart';
import '../domain/validators.dart';
import 'auth_state.dart';
import 'login_background.dart';

/// Shared auth scaffold: CycleCoach dark theme, no hard-coded strings.
class AuthScaffold extends StatelessWidget {
  final String titleKey;
  final List<Widget> children;
  final bool showBrand;
  const AuthScaffold({
    super.key,
    required this.titleKey,
    required this.children,
    this.showBrand = true,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return Scaffold(
      appBar: AppBar(title: Text(t.get(titleKey))),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (showBrand) ...[
              const Center(child: BrandLockup(markHeight: 44, fontSize: 30)),
              const SizedBox(height: AppSpacing.lg),
            ],
            ...children,
          ],
        ),
      ),
    );
  }
}

class LoginPage extends ConsumerStatefulWidget {
  const LoginPage({super.key});
  @override
  ConsumerState<LoginPage> createState() => _LoginPageState();
}

/// Frosted-glass login card over the alpine backdrop, per the approved mockup:
/// logo lockup, white email + password fields, a blue pill button, and text
/// links for reset and registration. No `AppBar` — the card is the screen.
///
/// The identity field stays email-only: `AuthValidators.email` and the backend
/// `LoginIn` both require an email address, so hinting "or username" would
/// promise a login the server cannot honour.
class _LoginPageState extends ConsumerState<LoginPage> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();

  /// The mockup's button blue. Kept local to this screen: it belongs to the
  /// marketing surface, not to the app theme tokens.
  static const _loginBlue = Color(0xFF4A80C0);

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_form.currentState!.validate()) return;
    final ok = await ref
        .read(authProvider.notifier)
        .login(_email.text.trim(), _password.text);
    if (ok && mounted) context.go('/home');
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final state = ref.watch(authProvider);
    final busy = state.status == AuthStatus.loading;

    return Scaffold(
      // The painted scene must reach the screen edges, behind the system bars.
      extendBodyBehindAppBar: true,
      body: Stack(
        children: [
          const LoginBackground(),
          SafeArea(
            child: Center(
              child: SingleChildScrollView(
                padding: const EdgeInsets.symmetric(
                  horizontal: 32,
                  vertical: 24,
                ),
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 420),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(20),
                    child: BackdropFilter(
                      filter: ImageFilter.blur(sigmaX: 18, sigmaY: 18),
                      child: Container(
                        padding: const EdgeInsets.fromLTRB(24, 28, 24, 24),
                        decoration: BoxDecoration(
                          color: Colors.white.withValues(alpha: 0.16),
                          borderRadius: BorderRadius.circular(20),
                          border: Border.all(
                            color: Colors.white.withValues(alpha: 0.35),
                          ),
                        ),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            const LogoFull(height: 110),
                            const SizedBox(height: 4),
                            Tagline(text: t.get('welcome')),
                            const SizedBox(height: AppSpacing.lg),
                            Form(
                              key: _form,
                              child: Column(
                                children: [
                                  _GlassField(
                                    key: const Key('login.email'),
                                    controller: _email,
                                    keyboardType: TextInputType.emailAddress,
                                    hint: t.get('auth.email'),
                                    icon: Icons.person_rounded,
                                    validator: (v) {
                                      final key = AuthValidators.email(v ?? '');
                                      return key == null ? null : t.get(key);
                                    },
                                  ),
                                  const SizedBox(height: 12),
                                  _GlassField(
                                    key: const Key('login.password'),
                                    controller: _password,
                                    obscureText: true,
                                    hint: t.get('auth.password'),
                                    icon: Icons.lock_rounded,
                                    validator: (v) => (v == null || v.isEmpty)
                                        ? t.get('auth.required')
                                        : null,
                                    onSubmitted: (_) => _submit(),
                                  ),
                                ],
                              ),
                            ),
                            const SizedBox(height: 12),
                            SizedBox(
                              height: 52,
                              child: FilledButton(
                                key: const Key('login.submit'),
                                onPressed: busy ? null : _submit,
                                style: FilledButton.styleFrom(
                                  backgroundColor: _loginBlue,
                                  foregroundColor: Colors.white,
                                  disabledBackgroundColor: _loginBlue
                                      .withValues(alpha: 0.55),
                                  textStyle: const TextStyle(
                                    fontSize: 17,
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                                child: busy
                                    ? const SizedBox(
                                        width: 22,
                                        height: 22,
                                        child: CircularProgressIndicator(
                                          strokeWidth: 2.5,
                                          color: Colors.white,
                                        ),
                                      )
                                    : Text(t.get('auth.login')),
                              ),
                            ),
                            if (state.status == AuthStatus.error) ...[
                              const SizedBox(height: 8),
                              Text(
                                '${t.get('auth.genericError')} (${state.errorCode})',
                                key: const Key('login.error'),
                                textAlign: TextAlign.center,
                                style: const TextStyle(
                                  color: Color(0xFFFFDAD4),
                                  fontSize: 12,
                                ),
                              ),
                            ],
                            const SizedBox(height: 12),
                            _Link(
                              key: const Key('login.forgot'),
                              text: t.get('auth.forgot'),
                              onTap: () => context.go('/forgot-password'),
                            ),
                            const SizedBox(height: 8),
                            Center(
                              child: RichText(
                                textAlign: TextAlign.center,
                                text: TextSpan(
                                  style: const TextStyle(
                                    color: Colors.white,
                                    fontSize: 13,
                                  ),
                                  children: [
                                    TextSpan(
                                      text: t.get('auth.noAccountPrefix'),
                                    ),
                                    TextSpan(
                                      text: t.get('auth.signUp'),
                                      style: const TextStyle(
                                        decoration: TextDecoration.underline,
                                        fontWeight: FontWeight.w600,
                                      ),
                                      recognizer: TapGestureRecognizer()
                                        ..onTap = () => context.go('/register'),
                                    ),
                                  ],
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// A white pill field on the glass card: grey icon, grey hint, dark text.
/// Validation messages render in a light error tone that reads on glass.
class _GlassField extends StatelessWidget {
  final TextEditingController controller;
  final String hint;
  final IconData icon;
  final bool obscureText;
  final TextInputType? keyboardType;
  final String? Function(String?) validator;
  final void Function(String)? onSubmitted;
  const _GlassField({
    super.key,
    required this.controller,
    required this.hint,
    required this.icon,
    required this.validator,
    this.obscureText = false,
    this.keyboardType,
    this.onSubmitted,
  });

  @override
  Widget build(BuildContext context) {
    return TextFormField(
      controller: controller,
      obscureText: obscureText,
      keyboardType: keyboardType,
      style: const TextStyle(color: Color(0xFF1A1A1A), fontSize: 15),
      decoration: InputDecoration(
        hintText: hint,
        hintStyle: const TextStyle(color: Color(0xFF8A8A8A)),
        prefixIcon: Icon(icon, color: const Color(0xFF6B6B6B)),
        filled: true,
        fillColor: Colors.white,
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 16,
          vertical: 16,
        ),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide.none,
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide.none,
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: Color(0xFF4A80C0), width: 2),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: Color(0xFFFF8A80), width: 1.5),
        ),
        focusedErrorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: Color(0xFFFF8A80), width: 2),
        ),
        errorStyle: const TextStyle(
          color: Color(0xFFFFDAD4),
          fontSize: 12,
          fontWeight: FontWeight.w600,
        ),
      ),
      validator: validator,
      onFieldSubmitted: onSubmitted,
    );
  }
}

/// A centered white underlined text link, as on the mockup.
class _Link extends StatelessWidget {
  final String text;
  final VoidCallback onTap;
  const _Link({super.key, required this.text, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: GestureDetector(
        onTap: onTap,
        child: Text(
          text,
          style: const TextStyle(
            color: Colors.white,
            fontSize: 13,
            decoration: TextDecoration.underline,
          ),
        ),
      ),
    );
  }
}
