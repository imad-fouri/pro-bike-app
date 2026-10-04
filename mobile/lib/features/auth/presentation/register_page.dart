import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/l10n/app_localizations.dart';
import '../domain/validators.dart';
import 'auth_state.dart';
import 'login_page.dart';

class RegisterPage extends ConsumerStatefulWidget {
  const RegisterPage({super.key});
  @override
  ConsumerState<RegisterPage> createState() => _RegisterPageState();
}

class _RegisterPageState extends ConsumerState<RegisterPage> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _confirm = TextEditingController();
  final _name = TextEditingController();

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    _confirm.dispose();
    _name.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final t = context.l10n;
    final state = ref.watch(authProvider);
    String? text(String? v, String? Function(String) fn) {
      if (v == null) return t.get('auth.required');
      final key = fn(v);
      return key == null ? null : t.get(key);
    }

    return AuthScaffold(
      titleKey: 'auth.register',
      children: [
        Form(
          key: _form,
          child: Column(
            children: [
              TextFormField(
                controller: _email,
                keyboardType: TextInputType.emailAddress,
                decoration: InputDecoration(labelText: t.get('auth.email')),
                validator: (v) => text(v, AuthValidators.email),
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _name,
                decoration: InputDecoration(
                  labelText: t.get('auth.displayName'),
                ),
                validator: (v) => text(v, AuthValidators.displayName),
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _password,
                obscureText: true,
                decoration: InputDecoration(labelText: t.get('auth.password')),
                validator: (v) => text(v, AuthValidators.password),
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _confirm,
                obscureText: true,
                decoration: InputDecoration(labelText: t.get('auth.confirm')),
                validator: (v) {
                  if (v == null || v.isEmpty) return t.get('auth.required');
                  final key = AuthValidators.confirm(_password.text, v);
                  return key == null ? null : t.get(key);
                },
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        if (state.status == AuthStatus.error)
          Text('${t.get('auth.genericError')} (${state.errorCode})'),
        FilledButton(
          onPressed: state.status == AuthStatus.loading
              ? null
              : () async {
                  if (!_form.currentState!.validate()) return;
                  final ok = await ref
                      .read(authProvider.notifier)
                      .register(
                        _email.text.trim(),
                        _password.text,
                        _name.text.trim(),
                      );
                  if (ok && context.mounted) context.go('/home');
                },
          child: Text(t.get('auth.register')),
        ),
        TextButton(
          onPressed: () => context.go('/login'),
          child: Text(t.get('auth.haveAccount')),
        ),
      ],
    );
  }
}
