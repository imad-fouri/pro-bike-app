import 'dart:convert';

import 'package:flutter/services.dart';

/// Web has no writable documents directory without extra plugins:
/// the GPX text goes to the clipboard instead (honest, zero-dependency).
/// A real download flow is tracked as a follow-up, not faked here.
Future<String> saveGpx(String filename, List<int> bytes) async {
  await Clipboard.setData(ClipboardData(text: utf8.decode(bytes)));
  return filename;
}
