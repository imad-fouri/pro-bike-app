import 'dart:io';

import 'package:path_provider/path_provider.dart';

/// Saves [bytes] as `filename` in the app documents directory.
/// Returns a human-readable location for the confirmation message.
Future<String> saveGpx(String filename, List<int> bytes) async {
  final dir = await getApplicationDocumentsDirectory();
  final file = File('${dir.path}${Platform.pathSeparator}$filename');
  await file.writeAsBytes(bytes, flush: true);
  return file.path;
}
