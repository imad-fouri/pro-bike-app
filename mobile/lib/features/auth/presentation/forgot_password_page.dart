import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/l10n/app_localizations.dart';
import '../../../core/network/api_client.dart';
import '../domain/validators.dart';
import 'auth_design.dart';
import 'auth_state.dart';

/// Glass password-recovery card matching sign in: recovery icon, heading,
/// email field, blue send button, and a back to sign in row.
///
/// The existing recovery flow is used unchanged: the backend answers every
/// request identically (anti-enumeration), so success and account-not-found
/// both land on the same confirmation. Only a request that never reached the
/// server (no connection) surfaces an error instead — claiming "sent" then
/// would be dishonest.
class ForgotPasswordPage extends ConsumerStatefulWidget {
  const ForgotPasswordPage({super.key});
  @override
  ConsumerState<ForgotPasswordPage> createState() => _ForgotPasswordPageState();
}

class _ForgotPasswordPageState extends ConsumerState<ForgotPasswordPage> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  bool _sending = false;
  bool _sent = false;
  String? _errorKey;

  @override
  void dispose() {
    _email.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_form.currentState!.validate()) return;
    setState(() {
      _sending = true;
      _errorKey = null;
    });
    try {
      await ref
          .read(authRepositoryProvider)
          .requestPasswordReset(_email.text.trim());
      // Identical UX for existing and unknown emails — no enumeration.
      if (mounted) {
        setState(() {
          _sending = false;
          _sent = true;
        });
      }
    } on ApiException catch (e) {
      // The request never reached the server: say so instead of claiming
      // an email was sent. Any other API answer keeps the anti-enumeration
      // confirmation below.
      if (e.code == 'NETWORK_ERROR' || e.code == 'NETWORK_TIMEOUT') {
        if (mounted) {
          setState(() {
            _sending = false;
            _errorKey = friendlyAuthErrorKey(e.code);
          });
        }
        return;
      }
      if (mounted) {
        setState(() {
          _sending = false;
          _sent = true;
        });
      }
    } on Exception catch (e) {
      // Unexpected client failure: report it honestly rather than faking
      // a sent email, and keep the detail in the log for diagnostics.
      debugPrint('auth password-reset failed: $e');
      if (mounted) {
        setState(() {
          _sending = false;
          _errorKey = 'auth.genericError';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;

    return AuthScreenScaffold(
      child: AuthGlassCard(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Center(
              child: Container(
                width: 56,
                height: 56,
                decoration: BoxDecoration(
                  gradient: const LinearGradient(
                    colors: [AuthColors.primary, AuthColors.primaryDeep],
                  ),
                  borderRadius: BorderRadius.circular(18),
                  border: Border.all(color: AuthColors.glassBorder),
                ),
                child: const Icon(
                  Icons.lock_reset_rounded,
                  color: AuthColors.text,
                  size: 30,
                ),
              ),
            ),
            const SizedBox(height: 16),
            const AuthCardHeading(
              titleKey: 'auth.resetTitle',
              subtitleKey: 'auth.resetSubtitle',
            ),
            AuthGaps.section,
            if (_sent) ...[
              const Icon(
                Icons.check_circle_rounded,
                color: AuthColors.success,
                size: 44,
              ),
              AuthGaps.tight,
              AuthSuccessText(
                key: const Key('forgot.success'),
                messageKey: 'auth.resetSent',
              ),
            ] else ...[
              Form(
                key: _form,
                child: AuthTextField(
                  key: const Key('forgot.email'),
                  controller: _email,
                  keyboardType: TextInputType.emailAddress,
                  textInputAction: TextInputAction.done,
                  hint: t.get('auth.email'),
                  icon: Icons.mail_rounded,
                  validator: (v) {
                    final key = AuthValidators.email(v ?? '');
                    return key == null ? null : t.get(key);
                  },
                  onSubmitted: (_) => _submit(),
                ),
              ),
              AuthGaps.field,
              AuthPrimaryButton(
                key: const Key('forgot.submit'),
                label: t.get('auth.sendResetLink'),
                busy: _sending,
                onPressed: _sending ? null : _submit,
              ),
              if (_errorKey != null) ...[
                AuthGaps.tight,
                AuthErrorText(
                  key: const Key('forgot.error'),
                  messageKey: _errorKey!,
                ),
              ],
            ],
            AuthGaps.field,
            AuthTextLink(
              key: const Key('forgot.back'),
              text: t.get('auth.backToSignIn'),
              onTap: () => context.go('/login'),
            ),
          ],
        ),
      ),
    );
  }
}
