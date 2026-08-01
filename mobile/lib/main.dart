import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app.dart';

/// Application entry point.
///
/// Loads configuration before the first frame so no screen ever reads an
/// uninitialised [AppConfig].
Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // `.env` is bundled as an asset (see pubspec.yaml). Tolerate its absence so a
  // fresh clone still runs before anyone has copied .env.example across.
  try {
    await dotenv.load();
  } on Exception catch (_) {
    debugPrint(
      'WARNING: .env not found — falling back to defaults. '
      'Copy .env.example to .env to configure the API base URL.',
    );
  }

  runApp(
    // ProviderScope is Riverpod's root. Tests override providers here to inject
    // fakes without touching widget code.
    const ProviderScope(child: SathifyApp()),
  );
}
