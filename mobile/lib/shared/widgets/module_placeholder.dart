import 'package:flutter/material.dart';

/// Stand-in screen for a module that has not been built yet.
///
/// Keeps the navigation graph complete and compiling from day one, so routing
/// and role guards can be tested before any feature screen exists. Each
/// placeholder names the module that will replace it.
class ModulePlaceholder extends StatelessWidget {
  const ModulePlaceholder({
    required this.title,
    required this.moduleName,
    this.icon = Icons.construction_outlined,
    super.key,
  });

  final String title;
  final String moduleName;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(icon, size: 72, color: theme.colorScheme.primary),
              const SizedBox(height: 24),
              Text(
                moduleName,
                style: theme.textTheme.titleLarge,
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 8),
              Text(
                'This module has not been built yet.',
                style: theme.textTheme.bodyMedium,
                textAlign: TextAlign.center,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
