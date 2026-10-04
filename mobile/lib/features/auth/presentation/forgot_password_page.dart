import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/l10n/app_localizations.dart';
import '../domain/validators.dart';
import 'auth_state.dart';
import 'login_page.dart';

/// Forgot password: requests a reset email. Always shows the same
/// confirmation (anti-enumeration), mirroring the backend.
class ForgotPasswordPage extends ConsumerStatefulWidget {
  const ForgotPasswordPage({super.key});
  @override
  ConsumerState<ForgotPasswordPage> createState() => _ForgotPasswordPageState();
}

class _ForgotPasswordPageState extends ConsumerState<ForgotPasswordPage> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  bool _sent = false;

  @override
  void dispose() {
    _email.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    return AuthScaffold(
      titleKey: 'auth.forgot',
      children: [
        if (_sent)
          Text(t.get('auth.resetSent'))
        else ...[
          Form(
            key: _form,
            child: TextFormField(
              controller: _email,
              keyboardType: TextInputType.emailAddress,
              decoration: InputDecoration(labelText: t.get('auth.email')),
              validator: (v) {
                final key = AuthValidators.email(v ?? '');
                return key == null ? null : t.get(key);
              },
            ),
          ),
          const SizedBox(height: 16),
          FilledButton(
            onPressed: () async {
              if (!_form.currentState!.validate()) return;
              try {
                await ref
                    .read(authRepositoryProvider)
                    .requestPasswordReset(_email.text.trim());
              } on Exception {
                // Identical UX either way — no enumeration.
              }
              if (mounted) setState(() => _sent = true);
            },
            child: Text(t.get('auth.sendReset')),
          ),
        ],
      ],
    );
  }
}
