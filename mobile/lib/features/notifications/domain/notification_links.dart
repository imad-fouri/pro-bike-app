/// Deep-link parsing and validation (ADR-15 §12).
///
/// A deep link is **navigation intent, never proof of access**. Everything here
/// exists to make that structurally true:
///
/// * only a fixed allowlist of route shapes is accepted, so a hostile or
///   malformed link cannot steer the app somewhere unexpected;
/// * an id must be a UUID, so `/chat/../../etc` is rejected as malformed rather
///   than navigated to;
/// * the parsed result carries no user identity — the destination screen
///   re-authorizes through the API exactly as it would without the link.
///
/// The one thing this file must never do is decide that a caller may view an
/// entity. It decides only whether the link is *well formed*.
library;

/// Where a validated deep link should take the rider.
class NotificationLink {
  final String route;
  final String? id;

  const NotificationLink(this.route, [this.id]);

  const NotificationLink.root(String route) : this(route, null);

  @override
  bool operator ==(Object other) =>
      other is NotificationLink && other.route == route && other.id == id;

  @override
  int get hashCode => Object.hash(route, id);

  @override
  String toString() => id == null ? route : '$route/$id';
}

// `final`, not `const`: `RegExp` has no const constructor. Compiled once at
// load rather than rebuilt on every parse — `parseNotificationLink` runs on
// every notification tap.
final _uuidV4 = RegExp(
  r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$',
);

/// Route shapes a notification may point at.
///
/// Keyed by the **full literal prefix**, and the value is how many trailing id
/// segments follow it. Keying by the first segment alone would break
/// `/friends/requests`: `friends` alone is not an allowed target, and treating
/// `requests` as its id would accept a link the app cannot honour.
const _allowed = <String, int>{
  '/friends/requests': 0,
  '/notifications': 0,
  '/chat': 1,
  '/teams': 1,
  '/users': 1,
};

/// Whether [link] is a well-formed, allowlisted notification target.
///
/// Returns null for anything not accepted, so a caller can fall back to the
/// notification center rather than guessing. A link that fails here is not an
/// error condition the rider should see — it is a link that is not ours.
NotificationLink? parseNotificationLink(String? link) {
  if (link == null || link.isEmpty) return null;

  // A link is an app-internal route, never a URL. Refusing anything with a
  // scheme or an authority means a crafted `https://evil.example/...` or
  // `//evil.example` can never be treated as navigation.
  if (link.contains('://') || link.startsWith('//')) return null;

  final segments = link
      .split('/')
      .where((s) => s.isNotEmpty)
      .toList(growable: false);
  if (segments.isEmpty) return null;

  // Longest-prefix match, so `/friends/requests` is not read as `/friends`
  // with an id of `requests`.
  final path = '/${segments.join('/')}';
  String? matched;
  var expectedIds = -1;
  for (final prefix in _allowed.keys) {
    if (path == prefix || path.startsWith('$prefix/')) {
      if (matched == null || prefix.length > matched.length) {
        matched = prefix;
        expectedIds = _allowed[prefix]!;
      }
    }
  }
  if (matched == null) return null;

  final rest = path
      .substring(matched.length)
      .split('/')
      .where((s) => s.isNotEmpty);
  if (rest.length != expectedIds) return null;

  if (expectedIds == 0) return NotificationLink.root(matched);

  // A trailing id must be a UUID. Rejecting anything else is what stops
  // path traversal or an arbitrary string reaching a route parameter.
  final id = rest.first;
  if (!_uuidV4.hasMatch(id)) return null;
  return NotificationLink(matched, id);
}

/// A notification intent waiting for authentication to resolve.
///
/// A cold start from a tap lands while `AuthStatus` is still `unknown`/`loading`,
/// and the router force-redirects to `/splash` — so the destination would be
/// lost. Holding it here and replaying it once auth settles is what makes a
/// tap work from a terminated app.
class PendingNotificationLink {
  NotificationLink? _link;
  bool _consumed = false;

  /// Record a tap. A second tap before the first is consumed replaces it: the
  /// rider's most recent intent is the one they meant.
  void set(NotificationLink link) {
    _link = link;
    _consumed = false;
  }

  /// Take the pending link, or null when there is none.
  ///
  /// Returns the link exactly once. A link is not replayed on a later rebuild,
  /// because re-running the navigation would bounce the rider back to a screen
  /// they already left.
  NotificationLink? consume() {
    if (_link == null || _consumed) return null;
    _consumed = true;
    return _link;
  }

  bool get hasPending => _link != null && !_consumed;

  void clear() {
    _link = null;
    _consumed = false;
  }
}
