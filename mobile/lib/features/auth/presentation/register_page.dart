import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/l10n/app_localizations.dart';
import '../domain/validators.dart';
import 'auth_design.dart';
import 'auth_state.dart';

/// Glass sign-up card matching sign in: brand lockup, heading, display name +
/// email + password + confirmation fields, blue primary button, and a back to
/// sign in row. No `AppBar` — the card is the screen.
///
/// The registration contract is unchanged: display name, email, and password
/// go to the existing `register` flow, and the confirmation is validated
/// locally before anything is sent. Failures show a friendly localized
/// message, never a raw backend code.
class RegisterPage extends ConsumerStatefulWidget {
  const RegisterPage({super.key});
  @override
  ConsumerState<RegisterPage> createState() => _RegisterPageState();
}

class _RegisterPageState extends ConsumerState<RegisterPage> {
  final _form = GlobalKey<FormState>();
  final _name = TextEditingController();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _confirm = TextEditingController();

  @override
  void dispose() {
    _name.dispose();
    _email.dispose();
    _password.dispose();
    _confirm.dispose();
    super.dispose();
  }

  String? _text(String? v, String? Function(String) fn) {
    if (v == null || v.isEmpty) {
      return context.l10n.get('auth.required');
    }
    final key = fn(v);
    return key == null ? null : context.l10n.get(key);
  }

  Future<void> _submit() async {
    if (!_form.currentState!.validate()) return;
    final ok = await ref
        .read(authProvider.notifier)
        .register(_email.text.trim(), _password.text, _name.text.trim());
    if (!mounted) return;
    if (!ok) {
      // Diagnostics stay in the log; users see the friendly message below.
      debugPrint(
        'auth register failed: code=${ref.read(authProvider).errorCode}',
      );
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
            const AuthBrandHeader(badgeSize: 44),
            AuthGaps.section,
            const AuthCardHeading(
              titleKey: 'auth.createAccountTitle',
              subtitleKey: 'auth.createAccountSubtitle',
            ),
            AuthGaps.section,
            Form(
              key: _form,
              child: Column(
                children: [
                  AuthTextField(
                    key: const Key('register.name'),
                    controller: _name,
                    textInputAction: TextInputAction.next,
                    hint: t.get('auth.displayName'),
                    icon: Icons.badge_rounded,
                    validator: (v) => _text(v, AuthValidators.displayName),
                  ),
                  AuthGaps.field,
                  AuthTextField(
                    key: const Key('register.email'),
                    controller: _email,
                    keyboardType: TextInputType.emailAddress,
                    textInputAction: TextInputAction.next,
                    hint: t.get('auth.email'),
                    icon: Icons.person_rounded,
                    validator: (v) => _text(v, AuthValidators.email),
                  ),
                  AuthGaps.field,
                  AuthTextField(
                    key: const Key('register.password'),
                    controller: _password,
                    password: true,
                    textInputAction: TextInputAction.next,
                    hint: t.get('auth.password'),
                    icon: Icons.lock_rounded,
                    validator: (v) => _text(v, AuthValidators.password),
                  ),
                  AuthGaps.field,
                  AuthTextField(
                    key: const Key('register.confirm'),
                    controller: _confirm,
                    password: true,
                    textInputAction: TextInputAction.done,
                    hint: t.get('auth.confirm'),
                    icon: Icons.lock_outline_rounded,
                    validator: (v) {
                      if (v == null || v.isEmpty) {
                        return t.get('auth.required');
                      }
                      final key = AuthValidators.confirm(_password.text, v);
                      return key == null ? null : t.get(key);
                    },
                    onSubmitted: (_) => _submit(),
                  ),
                ],
              ),
            ),
            AuthGaps.field,
            AuthPrimaryButton(
              key: const Key('register.submit'),
              label: t.get('auth.register'),
              busy: busy,
              onPressed: busy ? null : _submit,
            ),
            if (state.status == AuthStatus.error) ...[
              AuthGaps.tight,
              AuthErrorText(
                key: const Key('register.error'),
                messageKey: friendlyAuthErrorKey(state.errorCode),
              ),
            ],
            AuthGaps.field,
            AuthAccountSwitch(
              key: const Key('register.signin'),
              prefixKey: 'auth.haveAccountPrefix',
              linkKey: 'auth.login',
              onTap: () => context.go('/login'),
            ),
          ],
        ),
      ),
    );
  }
}
