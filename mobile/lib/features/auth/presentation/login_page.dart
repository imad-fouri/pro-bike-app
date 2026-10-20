import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/l10n/app_localizations.dart';
import '../../../core/theme/app_colors.dart';
import '../../../shared/widgets/brand.dart';
import '../domain/validators.dart';
import 'auth_design.dart';
import 'auth_state.dart';
import 'login_background.dart';

/// Shared auth scaffold: CycleCoach dark theme, no hard-coded strings.
///
/// Also used by non-auth form screens (bikes, routes, ride start, profile),
/// so it stays on the app theme and is intentionally NOT part of the glass
/// auth redesign.
class AuthScaffold extends StatelessWidget {
  final String titleKey;
  final List<Widget> children;
  final bool showBrand;
  final bool photoBackground;
  const AuthScaffold({
    super.key,
    required this.titleKey,
    required this.children,
    this.showBrand = true,
    this.photoBackground = false,
  });

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final content = SingleChildScrollView(
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
    );

    // The photo screens keep their standard AppBar for navigation, but let
    // the shared road backdrop show through it and the page body.
    return Scaffold(
      extendBodyBehindAppBar: photoBackground,
      backgroundColor: photoBackground ? Colors.transparent : null,
      appBar: AppBar(
        title: Text(t.get(titleKey)),
        backgroundColor: photoBackground ? Colors.transparent : null,
        foregroundColor: photoBackground ? Colors.white : null,
        elevation: 0,
      ),
      body: photoBackground
          ? Stack(
              children: [
                const AuthBackground(),
                SafeArea(child: content),
              ],
            )
          : content,
    );
  }
}

class LoginPage extends ConsumerStatefulWidget {
  const LoginPage({super.key});
  @override
  ConsumerState<LoginPage> createState() => _LoginPageState();
}

/// Glass sign-in card over the alpine backdrop: brand lockup, email +
/// password fields with show/hide, a blue primary button, and text links for
/// reset and registration. No `AppBar` — the card is the screen.
///
/// The identity field stays email-only: `AuthValidators.email` and the backend
/// `LoginIn` both require an email address, so hinting "or username" would
/// promise a login the server cannot honour.
///
/// Failures show a friendly localized message, never a raw backend code; the
/// code is kept for diagnostics in the debug log.
class _LoginPageState extends ConsumerState<LoginPage> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();

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
    if (!mounted) return;
    if (!ok) {
      // Diagnostics stay in the log; users see the friendly message below.
      debugPrint('auth login failed: code=${ref.read(authProvider).errorCode}');
      return;
    }
    context.go('/home');
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final state = ref.watch(authProvider);
    final busy = state.status == AuthStatus.loading;

    return AuthScreenScaffold(
      child: AuthGlassCard(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const AuthBrandHeader(),
            AuthGaps.section,
            Form(
              key: _form,
              child: Column(
                children: [
                  AuthTextField(
                    key: const Key('login.email'),
                    controller: _email,
                    keyboardType: TextInputType.emailAddress,
                    textInputAction: TextInputAction.next,
                    hint: t.get('auth.email'),
                    icon: Icons.person_rounded,
                    validator: (v) {
                      final key = AuthValidators.email(v ?? '');
                      return key == null ? null : t.get(key);
                    },
                  ),
                  AuthGaps.field,
                  AuthTextField(
                    key: const Key('login.password'),
                    controller: _password,
                    password: true,
                    textInputAction: TextInputAction.done,
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
            AuthGaps.field,
            AuthPrimaryButton(
              key: const Key('login.submit'),
              label: t.get('auth.login'),
              busy: busy,
              onPressed: busy ? null : _submit,
            ),
            if (state.status == AuthStatus.error) ...[
              AuthGaps.tight,
              AuthErrorText(
                key: const Key('login.error'),
                messageKey: friendlyAuthErrorKey(state.errorCode),
              ),
            ],
            AuthGaps.field,
            AuthTextLink(
              key: const Key('login.forgot'),
              text: t.get('auth.forgot'),
              onTap: () => context.go('/forgot-password'),
            ),
            AuthGaps.tight,
            AuthAccountSwitch(
              prefixKey: 'auth.noAccountPrefix',
              linkKey: 'auth.signUp',
              onTap: () => context.go('/register'),
            ),
          ],
        ),
      ),
    );
  }
}
