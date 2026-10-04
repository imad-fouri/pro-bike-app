import 'package:flutter/material.dart';
import '../../core/l10n/app_localizations.dart';

/// Placeholder proves theme + l10n + routing work. Replaced by real screens later.
class PlaceholderPage extends StatelessWidget {
  final String title;
  const PlaceholderPage({super.key, required this.title});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Text(context.l10n.get('welcome')),
            const SizedBox(height: 16),
            FilledButton(onPressed: () {}, child: const Text('Continue')),
          ],
        ),
      ),
    );
  }
}
