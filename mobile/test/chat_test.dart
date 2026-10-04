import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:cyclecoach/core/l10n/app_localizations.dart';
import 'package:cyclecoach/core/network/api_client.dart';
import 'package:cyclecoach/features/chat/data/chat_repository.dart';
import 'package:cyclecoach/features/chat/data/chat_transport.dart';
import 'package:cyclecoach/features/chat/domain/chat.dart';
import 'package:cyclecoach/features/chat/domain/chat_validators.dart';
import 'package:cyclecoach/features/chat/presentation/chat_inbox_page.dart';
import 'package:cyclecoach/features/chat/presentation/chat_providers.dart';
import 'package:cyclecoach/features/chat/presentation/chat_widgets.dart';
import 'package:cyclecoach/features/chat/presentation/conversation_page.dart'
    show ConversationScreen;
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

Map<String, dynamic> messageJson({
  String id = 'm-1',
  String conversationId = 'c-1',
  int seq = 1,
  String senderUserId = 'u-1',
  String? senderUsername = 'imad_fouri',
  String? senderDisplayName = 'Imad Fouri',
  String body = 'bonjour',
  String messageType = 'text',
  bool isDeleted = false,
  bool isEdited = false,
  bool isMine = false,
  bool canEdit = false,
  String createdAt = '2026-01-01T10:00:00Z',
  String? editedAt,
}) => {
  'id': id,
  'conversation_id': conversationId,
  'seq': seq,
  'sender_user_id': senderUserId,
  'sender_username': senderUsername,
  'sender_display_name': senderDisplayName,
  'message_type': messageType,
  'body': body,
  'is_deleted': isDeleted,
  'is_edited': isEdited,
  'is_mine': isMine,
  'can_edit': canEdit,
  'created_at': createdAt,
  'edited_at': editedAt,
};

Map<String, dynamic> conversationJson({
  String id = 'c-1',
  String kind = 'direct',
  String? teamId,
  String? teamName,
  String? peerUserId = 'u-2',
  String? peerUsername = 'karim_amrani',
  String? peerDisplayName = 'Karim Amrani',
  String? preview = 'bonjour',
  int? lastSeq = 1,
  int unread = 0,
}) => {
  'id': id,
  'kind': kind,
  'team_id': teamId,
  'team_name': teamName,
  'team_handle': teamId == null ? null : 'atlas_cc',
  'team_visibility': teamId == null ? null : 'public',
  'team_status': teamId == null ? null : 'active',
  'peer_user_id': peerUserId,
  'peer_username': peerUsername,
  'peer_display_name': peerDisplayName,
  'last_message_id': preview == null ? null : 'm-1',
  'last_message_seq': lastSeq,
  'last_message_preview': preview,
  'last_message_at': preview == null ? null : '2026-01-01T10:00:00Z',
  'unread_count': unread,
  'last_read_seq': 0,
  'created_at': '2026-01-01T09:00:00Z',
};

Map<String, dynamic> pageOf(
  List<Map<String, dynamic>> items, {
  bool hasMore = false,
  int? nextBeforeSeq,
}) => {'items': items, 'has_more': hasMore, 'next_before_seq': nextBeforeSeq};

/// Stateful fake. Mutations change what the next read returns, so "the UI
/// followed the server" is a real assertion rather than a snapshot of the first
/// render.
class FakeChat {
  final List<String> calls = [];
  final Map<String, Map<String, dynamic>> messages = {};
  final Map<String, int> lastRead = {};
  final List<String> inboxIds = [];

  String? forcedCode;
  int forcedStatus = 404;

  int seq = 0;
  int sends = 0;

  /// Bodies the fake refuses to accept, keyed by error code.
  final Map<String, String> errorByBody = {
    'blocked': 'CHAT_BLOCKED',
    'frozen team': 'CHAT_TEAM_ARCHIVED',
  };

  void reset() {
    messages.clear();
    lastRead.clear();
    inboxIds.clear();
    calls.clear();
    forcedCode = null;
    seq = 0;
    sends = 0;
  }

  Map<String, dynamic> history(List<Map<String, dynamic>> seed) {
    for (final m in seed) {
      messages[m['id']! as String] = Map<String, dynamic>.from(m);
    }
    return historyOf('c-1');
  }

  /// Seed `count` messages with sequential `seq`, so a multi-page history is
  /// real rather than an artefact of a tiny conversation.
  void seedMessages(int count) {
    for (var i = 1; i <= count; i++) {
      messages['m-$i'] = Map<String, dynamic>.from(
        messageJson(id: 'm-$i', seq: i, body: 'm$i'),
      );
    }
  }

  /// Highest `seq` currently stored, so a send continues the sequence instead of
  /// colliding with seeded history.
  int highestSeq() => messages.values.fold<int>(
    0,
    (max, m) => (m['seq']! as int) > max ? m['seq']! as int : max,
  );

  /// Newest-first page honouring `before_seq`, exactly as the backend does.
  Map<String, dynamic> historyOf(String conversationId) {
    final all =
        messages.values
            .where((m) => m['conversation_id'] == conversationId)
            .toList()
          ..sort((a, b) => (b['seq']! as int).compareTo(a['seq']! as int));
    final before = _pendingBeforeSeq;
    final limit = _pendingLimit;
    var items = all;
    if (before != null) {
      items = all.where((m) => (m['seq']! as int) < before).toList();
    }
    final hasMore = items.length > limit;
    items = items.take(limit).toList();
    return pageOf(
      items,
      hasMore: hasMore,
      nextBeforeSeq: (hasMore && items.isNotEmpty)
          ? items.last['seq']! as int
          : null,
    );
  }

  int? _pendingBeforeSeq;
  int _pendingLimit = 50;
}

Future<http.Response> Function(http.Request) chatHandler(FakeChat f) =>
    (req) async {
      final path = req.url.path;
      final method = req.method;
      f.calls.add('$method $path');

      if (f.forcedCode != null) {
        return http.Response(
          jsonEncode({
            'detail': {'code': f.forcedCode, 'message': 'refused'},
          }),
          f.forcedStatus,
        );
      }

      const p = '/api/v1/chat';

      if (method == 'GET' && path == '$p/conversations') {
        // The first inbox row is a DM, the second a team channel: both kinds
        // render differently, so the fake serves both.
        final items = [
          for (final id in f.inboxIds)
            if (id.startsWith('t'))
              conversationJson(
                id: id,
                kind: 'team',
                teamId: 't-1',
                teamName: 'Atlas CC',
                peerUserId: null,
                peerUsername: null,
                peerDisplayName: null,
              )
            else
              conversationJson(id: id),
        ];
        return http.Response(
          jsonEncode({
            'items': items,
            'total': items.length,
            'page': 1,
            'page_size': 20,
          }),
          200,
        );
      }

      if (method == 'GET' &&
          path.startsWith('$p/conversations/') &&
          path.endsWith('/messages')) {
        f._pendingBeforeSeq = int.tryParse(
          req.url.queryParameters['before_seq'] ?? '',
        );
        f._pendingLimit =
            int.tryParse(req.url.queryParameters['limit'] ?? '') ?? 50;
        final id = path.split('/').elementAt(5);
        return http.Response(jsonEncode(f.historyOf(id)), 200);
      }

      if (method == 'POST' &&
          path.startsWith('$p/conversations/') &&
          path.endsWith('/messages')) {
        final body = jsonDecode(req.body) as Map<String, dynamic>;
        final text = '${body['body']}';
        final forced = f.errorByBody[text];
        if (forced != null) {
          return http.Response(
            jsonEncode({
              'detail': {'code': forced, 'message': 'refused'},
            }),
            403,
          );
        }
        final id = path.split('/').elementAt(5);
        final clientId = '${body['client_message_id']}';
        // Idempotency: the same client id is the same message.
        final existing = f.messages.values
            .cast<Map<String, dynamic>?>()
            .firstWhere(
              (m) => m!['client_client_id'] == clientId,
              orElse: () => null,
            );
        if (existing != null) {
          return http.Response(
            jsonEncode({'message': existing, 'duplicate': true}),
            200,
          );
        }
        // `seq` continues past whatever was seeded, so a send lands after the
        // history a test already arranged rather than colliding with it.
        f.seq = f.highestSeq();
        f.seq++;
        f.sends++;
        final message = messageJson(
          id: 's-${f.sends}',
          conversationId: id,
          seq: f.seq,
          body: text,
          isMine: true,
          canEdit: true,
        )..['client_client_id'] = clientId;
        f.messages[message['id']! as String] = message;
        return http.Response(
          jsonEncode({'message': message, 'duplicate': false}),
          201,
        );
      }

      if (method == 'PATCH' && path.startsWith('$p/messages/')) {
        final id = path.split('/').elementAt(5);
        final body = jsonDecode(req.body) as Map<String, dynamic>;
        final message = f.messages[id];
        if (message == null) {
          return http.Response(
            jsonEncode({
              'detail': {'code': 'CHAT_MESSAGE_NOT_FOUND', 'message': 'gone'},
            }),
            404,
          );
        }
        final updated = Map<String, dynamic>.from(message)
          ..['body'] = '${body['body']}'
          ..['is_edited'] = true
          ..['edited_at'] = '2026-01-01T10:05:00Z'
          ..['can_edit'] = false;
        f.messages[id] = updated;
        return http.Response(jsonEncode(updated), 200);
      }

      if (method == 'DELETE' && path.startsWith('$p/messages/')) {
        final id = path.split('/').elementAt(5);
        final message = f.messages[id];
        if (message == null) {
          return http.Response(
            jsonEncode({
              'detail': {'code': 'CHAT_MESSAGE_NOT_FOUND', 'message': 'gone'},
            }),
            404,
          );
        }
        final updated = Map<String, dynamic>.from(message)
          ..['body'] = '[deleted]'
          ..['is_deleted'] = true
          ..['can_edit'] = false;
        f.messages[id] = updated;
        return http.Response(jsonEncode(updated), 200);
      }

      if (method == 'POST' &&
          path.startsWith('$p/conversations/') &&
          path.endsWith('/read')) {
        final id = path.split('/').elementAt(5);
        final seq = int.tryParse(req.url.queryParameters['seq'] ?? '') ?? 0;
        // Never moves backwards.
        final current = f.lastRead[id] ?? 0;
        final next = seq > current ? seq : current;
        f.lastRead[id] = next;
        return http.Response(
          jsonEncode({'conversation_id': id, 'last_read_seq': next}),
          200,
        );
      }

      if (method == 'POST' && path == '$p/direct') {
        return http.Response(jsonEncode(conversationJson()), 201);
      }

      if (method == 'GET' && path.startsWith('$p/teams/')) {
        return http.Response(
          jsonEncode(
            conversationJson(
              kind: 'team',
              teamId: 't-1',
              teamName: 'Atlas CC',
              peerUserId: null,
              peerUsername: null,
              peerDisplayName: null,
            ),
          ),
          200,
        );
      }

      if (method == 'GET' && path.startsWith('$p/conversations/')) {
        final id = path.split('/').elementAt(5);
        return http.Response(jsonEncode(conversationJson(id: id)), 200);
      }

      return http.Response('{}', 404);
    };

ApiClient clientFor(FakeChat fake) => ApiClient(
  baseUrl: 'http://test',
  client: MockClient(chatHandler(fake)),
  accessToken: () async => 'token',
);

/// A transport that emits exactly the pages a test hands it, so widget tests
/// never depend on a wall clock.
class ScriptedChatTransport implements ChatTransport {
  final Map<String, StreamController<MessagePage>> _controllers = {};
  final List<String> watched = [];
  final List<String> refreshed = [];
  bool disposed = false;

  void emit(String conversationId, Map<String, dynamic> json) {
    final controller = _controllers[conversationId];
    if (controller != null && !controller.isClosed) {
      controller.add(MessagePage.fromJson(json));
    }
  }

  @override
  Stream<MessagePage> watch(String conversationId) {
    watched.add(conversationId);
    final existing = _controllers[conversationId];
    if (existing != null) return existing.stream;
    final controller = StreamController<MessagePage>.broadcast();
    _controllers[conversationId] = controller;
    return controller.stream;
  }

  @override
  Future<void> refresh(String conversationId) async =>
      refreshed.add(conversationId);

  @override
  Future<void> dispose() async {
    disposed = true;
    for (final c in _controllers.values) {
      if (!c.isClosed) await c.close();
    }
  }
}

ProviderContainer containerFor(
  FakeChat fake, {
  ChatTransport? transport,
}) => ProviderContainer(
  overrides: [
    chatRepositoryProvider.overrideWithValue(ChatRepository(clientFor(fake))),
    if (transport != null) chatTransportProvider.overrideWithValue(transport),
  ],
);

/// Holds a provider alive for the duration of a test.
///
/// The thread provider is `autoDispose`: awaiting its `future` is enough to get
/// a first value, but with no listener it is disposed immediately afterwards and
/// the next `container.read` returns a fresh, loading state. Tests that drive the
/// notifier directly need a standing subscription, which is what a mounted widget
/// provides in the real app.
ProviderSubscription<AsyncValue<ConversationThread>> keepAlive(
  ProviderContainer container,
  String conversationId,
) => container.listen<AsyncValue<ConversationThread>>(
  conversationThreadProvider(conversationId),
  (_, _) {},
  fireImmediately: true,
);

/// Wraps a widget under test in the localization, RTL, and Riverpod scaffolding
/// the chat screens assume.
Widget harness(
  Widget child, {
  required ProviderContainer container,
  String locale = 'en',
  TextDirection? direction,
}) => UncontrolledProviderScope(
  container: container,
  child: MaterialApp(
    locale: Locale(locale),
    localizationsDelegates: const [
      AppLocalizationsDelegate(),
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    supportedLocales: AppLocalizations.supported,
    home: direction == null
        ? child
        : Directionality(textDirection: direction, child: child),
  ),
);

void main() {
  group('domain models', () {
    test('conversation parses a team channel', () {
      final c = Conversation.fromJson(
        conversationJson(
          kind: 'team',
          teamId: 't-1',
          teamName: 'Atlas CC',
          peerUserId: null,
          peerUsername: null,
          peerDisplayName: null,
        ),
      );
      expect(c.kind, ConversationKind.team);
      expect(c.isTeam, isTrue);
      expect(c.bestName, 'Atlas CC');
      expect(c.bestHandle, 'atlas_cc');
      expect(c.peerUserId, isNull);
    });

    test('conversation parses a direct thread and prefers display name', () {
      final c = Conversation.fromJson(conversationJson());
      expect(c.kind, ConversationKind.direct);
      expect(c.isTeam, isFalse);
      expect(c.bestName, 'Karim Amrani');
      expect(c.bestHandle, 'karim_amrani');
      expect(c.unreadCount, 0);
    });

    test('conversation falls back to the username when no display name', () {
      final c = Conversation.fromJson(conversationJson(peerDisplayName: null));
      expect(c.bestName, 'karim_amrani');
    });

    test('message parses every server flag', () {
      final m = ChatMessage.fromJson(
        messageJson(
          isDeleted: true,
          isEdited: true,
          isMine: true,
          canEdit: true,
          editedAt: '2026-01-01T10:05:00Z',
        ),
      );
      expect(m.seq, 1);
      expect(m.messageType, MessageType.text);
      expect(m.isDeleted, isTrue);
      expect(m.isEdited, isTrue);
      expect(m.isMine, isTrue);
      expect(m.canEdit, isTrue);
      expect(m.editedAt, isNotNull);
      expect(m.bestName, 'Imad Fouri');
    });

    test('message tolerates absent optional flags', () {
      final m = ChatMessage.fromJson({
        'id': 'm-1',
        'conversation_id': 'c-1',
        'seq': 3,
        'sender_user_id': 'u-1',
        'body': 'x',
        'created_at': '2026-01-01T10:00:00Z',
      });
      expect(m.isDeleted, isFalse);
      expect(m.isEdited, isFalse);
      expect(m.isMine, isFalse);
      expect(m.canEdit, isFalse);
      expect(m.senderUsername, isNull);
    });

    test('no model carries a location, media, or private account field', () {
      // Phase 8.3 must not make location sharing an implicit capability of
      // messaging (ADR-12 §3, ADR-14 §2). Nothing here can even hold one.
      final message = ChatMessage.fromJson(messageJson());
      final conversation = Conversation.fromJson(conversationJson());
      for (final field in [
        'latitude',
        'longitude',
        'location',
        'media_url',
        'attachment',
        'ride_id',
        'route_id',
        'email',
        'password_hash',
      ]) {
        expect(message.toString().contains(field), isFalse, reason: field);
        expect(conversation.toString().contains(field), isFalse, reason: field);
      }
      expect(MessageType.values.map((e) => e.wire), ['text', 'system']);
    });

    test('unknown enum values degrade instead of throwing', () {
      expect(MessageType.parse('location'), MessageType.text);
      expect(ConversationKind.parse('channel'), ConversationKind.direct);
    });

    test('message page is a cursor envelope with no total', () {
      final page = MessagePage.fromJson(
        pageOf([messageJson()], hasMore: true, nextBeforeSeq: 7),
      );
      expect(page.items, hasLength(1));
      expect(page.hasMore, isTrue);
      expect(page.nextBeforeSeq, 7);
    });

    test('send result reports a duplicate retry', () {
      final result = SendResult.fromJson({
        'message': messageJson(),
        'duplicate': true,
      });
      expect(result.duplicate, isTrue);
      expect(result.message.id, 'm-1');
    });
  });

  group('validators', () {
    test('an empty or whitespace-only body is refused', () {
      expect(validateMessageBody(''), MessageBodyProblem.empty);
      expect(validateMessageBody('   \n\t '), MessageBodyProblem.empty);
      expect(canSendMessage('   '), isFalse);
    });

    test('a body over the server limit is refused', () {
      expect(
        validateMessageBody('x' * (kMessageBodyMaxLength + 1)),
        MessageBodyProblem.tooLong,
      );
      expect(canSendMessage('x' * kMessageBodyMaxLength), isTrue);
    });

    test('the remaining counter goes negative rather than clamping', () {
      expect(remainingMessageChars('abc'), kMessageBodyMaxLength - 3);
      expect(remainingMessageChars('x' * (kMessageBodyMaxLength + 5)), -5);
    });

    test('client message ids are unique and well-formed v4 UUIDs', () {
      final ids = <String>{for (var i = 0; i < 500; i++) newClientMessageId()};
      expect(ids.length, 500, reason: 'generated ids collided');
      final uuidV4 = RegExp(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
      );
      expect(ids.every(uuidV4.hasMatch), isTrue);
    });
  });

  group('repository', () {
    test('history is fetched with the cursor it was given', () async {
      final fake = FakeChat()
        ..history([
          messageJson(id: 'm-1', seq: 1),
          messageJson(id: 'm-2', seq: 2),
          messageJson(id: 'm-3', seq: 3),
        ]);
      final repo = ChatRepository(clientFor(fake));

      // Newest first: the wire serves the head of the thread, not its tail.
      final first = await repo.messages('c-1', limit: 2);
      expect(first.items.map((m) => m.seq), [3, 2]);
      expect(first.hasMore, isTrue);
      expect(first.nextBeforeSeq, 2);

      final second = await repo.messages('c-1', beforeSeq: 2, limit: 2);
      expect(second.items.map((m) => m.seq), [1]);
      expect(second.hasMore, isFalse);
      expect(second.hasMore, isFalse);
      expect(second.nextBeforeSeq, isNull);
    });

    test('a send carries the caller-supplied idempotency key', () async {
      final fake = FakeChat();
      final repo = ChatRepository(clientFor(fake));
      final id = newClientMessageId();

      final first = await repo.send('c-1', 'bonjour', clientMessageId: id);
      final retry = await repo.send('c-1', 'bonjour', clientMessageId: id);

      expect(first.duplicate, isFalse);
      expect(retry.duplicate, isTrue);
      expect(retry.message.id, first.message.id);
      expect(retry.message.seq, first.message.seq);
      expect(fake.sends, 1, reason: 'the retry stored a second message');
    });

    test('a fresh client id stores a second message', () async {
      final fake = FakeChat();
      final repo = ChatRepository(clientFor(fake));
      await repo.send('c-1', 'one', clientMessageId: newClientMessageId());
      await repo.send('c-1', 'two', clientMessageId: newClientMessageId());
      expect(fake.sends, 2);
    });

    test(
      'edit and delete render the server answer, not a local guess',
      () async {
        final fake = FakeChat()..history([messageJson(isMine: true)]);
        final repo = ChatRepository(clientFor(fake));

        final edited = await repo.edit('m-1', 'corrigé');
        expect(edited.body, 'corrigé');
        expect(edited.isEdited, isTrue);
        expect(
          edited.canEdit,
          isFalse,
          reason: 'the window closed server-side',
        );

        final deleted = await repo.delete('m-1');
        expect(deleted.body, '[deleted]');
        expect(deleted.isDeleted, isTrue);
      },
    );

    test('the read mark never moves backwards', () async {
      final fake = FakeChat();
      final repo = ChatRepository(clientFor(fake));
      expect(await repo.markRead('c-1', 5), 5);
      expect(await repo.markRead('c-1', 2), 5);
      expect(await repo.markRead('c-1', 9), 9);
    });

    test('every chat call is authenticated', () async {
      final fake = FakeChat()..history([messageJson()]);
      final repo = ChatRepository(clientFor(fake));
      await repo.conversations();
      await repo.messages('c-1');
      await repo.send('c-1', 'x', clientMessageId: newClientMessageId());
      await repo.markRead('c-1', 1);
      expect(
        fake.calls.every((c) => c.startsWith('GET') || c.startsWith('POST')),
        isTrue,
      );
    });

    test(
      'a blocked target reports the server code, not a local guess',
      () async {
        final fake = FakeChat()
          ..forcedCode = 'CHAT_USER_NOT_FOUND'
          ..forcedStatus = 404;
        final repo = ChatRepository(clientFor(fake));
        await expectLater(
          repo.openDirect('u-2'),
          throwsA(
            isA<ApiException>().having(
              (e) => e.code,
              'code',
              'CHAT_USER_NOT_FOUND',
            ),
          ),
        );
      },
    );
  });

  group('polling transport', () {
    test('a poll failure never reaches the stream', () async {
      final fake = FakeChat();
      final transport = PollingChatTransport(
        repository: ChatRepository(clientFor(fake)),
        pollInterval: const Duration(milliseconds: 20),
      );
      fake.forcedCode = 'NETWORK_ERROR';
      fake.forcedStatus = 0;

      final seen = <MessagePage>[];
      final sub = transport.watch('c-1').listen(seen.add);
      await Future<void>.delayed(const Duration(milliseconds: 120));
      await sub.cancel();
      await transport.dispose();

      expect(
        seen,
        isEmpty,
        reason: 'a dropped poll must not become an error the rider sees',
      );
    });

    test(
      'the first poll emits immediately, without waiting an interval',
      () async {
        final fake = FakeChat()..history([messageJson()]);
        final transport = PollingChatTransport(
          repository: ChatRepository(clientFor(fake)),
          // Long enough that only an immediate first poll can produce the event.
          pollInterval: const Duration(seconds: 30),
        );
        final seen = <MessagePage>[];
        final sub = transport.watch('c-1').listen(seen.add);
        await Future<void>.delayed(const Duration(milliseconds: 30));
        await sub.cancel();
        await transport.dispose();

        expect(seen, hasLength(1));
        expect(seen.single.items.single.seq, 1);
      },
    );

    test('watching one conversation starts exactly one timer', () async {
      final fake = FakeChat();
      final transport = PollingChatTransport(
        repository: ChatRepository(clientFor(fake)),
        pollInterval: const Duration(milliseconds: 20),
      );
      final a = transport.watch('c-1').listen((_) {});
      final b = transport.watch('c-1').listen((_) {});
      await Future<void>.delayed(const Duration(milliseconds: 10));
      await a.cancel();
      await b.cancel();
      await transport.dispose();
      // One request per interval, not one per listener.
      expect(
        fake.calls.where((c) => c.contains('/messages')).length,
        lessThan(4),
      );
    });

    test('dispose stops polling and closes every stream', () async {
      final fake = FakeChat();
      final transport = PollingChatTransport(
        repository: ChatRepository(clientFor(fake)),
        pollInterval: const Duration(milliseconds: 20),
      );
      final sub = transport.watch('c-1').listen((_) {});
      await Future<void>.delayed(const Duration(milliseconds: 10));
      await transport.dispose();
      final before = fake.calls.length;
      await Future<void>.delayed(const Duration(milliseconds: 80));
      await sub.cancel();

      expect(
        fake.calls.length,
        before,
        reason: 'polling continued after dispose',
      );
      expect(transport.watch('c-1'), emitsDone);
    });
  });

  group('thread notifier', () {
    test('initial load is reversed into reading order', () async {
      final fake = FakeChat()
        ..history([
          messageJson(id: 'm-1', seq: 1, body: 'first'),
          messageJson(id: 'm-2', seq: 2, body: 'second'),
          messageJson(id: 'm-3', seq: 3, body: 'third'),
        ]);
      final transport = ScriptedChatTransport();
      final container = containerFor(fake, transport: transport);
      addTearDown(container.dispose);
      final sub = keepAlive(container, 'c-1');
      addTearDown(sub.close);

      await container.read(conversationThreadProvider('c-1').future);
      final thread = container.read(conversationThreadProvider('c-1')).value!;
      expect(thread.messages.map((m) => m.seq), [1, 2, 3]);
      expect(transport.watched, ['c-1']);
    });

    test('a poll merges instead of duplicating or reordering', () async {
      final fake = FakeChat()
        ..history([
          messageJson(id: 'm-1', seq: 1),
          messageJson(id: 'm-2', seq: 2),
        ]);
      final transport = ScriptedChatTransport();
      final container = containerFor(fake, transport: transport);
      addTearDown(container.dispose);
      final sub = keepAlive(container, 'c-1');
      addTearDown(sub.close);
      await container.read(conversationThreadProvider('c-1').future);

      // The same page again: a poll that finds nothing new.
      transport.emit(
        'c-1',
        pageOf([
          messageJson(id: 'm-2', seq: 2),
          messageJson(id: 'm-1', seq: 1),
        ]),
      );
      await Future<void>.delayed(Duration.zero);
      var thread = container.read(conversationThreadProvider('c-1')).value!;
      expect(thread.messages.map((m) => m.seq), [1, 2]);

      // A genuinely new message, newest first as the wire sends it.
      transport.emit(
        'c-1',
        pageOf([
          messageJson(id: 'm-3', seq: 3),
          messageJson(id: 'm-2', seq: 2),
        ]),
      );
      await Future<void>.delayed(Duration.zero);
      thread = container.read(conversationThreadProvider('c-1')).value!;
      expect(
        thread.messages.map((m) => m.seq),
        [1, 2, 3],
        reason: 'a merge duplicated or reordered the thread',
      );
      expect(thread.newestSeq, 3);
    });

    test('a send appends the stored message and marks it read', () async {
      final fake = FakeChat()..history([messageJson(id: 'm-1', seq: 1)]);
      final transport = ScriptedChatTransport();
      final container = containerFor(fake, transport: transport);
      addTearDown(container.dispose);
      final sub = keepAlive(container, 'c-1');
      addTearDown(sub.close);
      await container.read(conversationThreadProvider('c-1').future);

      final sent = await container
          .read(conversationThreadProvider('c-1').notifier)
          .send('hello', newClientMessageId());

      expect(sent, isNotNull);
      final thread = container.read(conversationThreadProvider('c-1')).value!;
      expect(thread.messages.map((m) => m.seq), [1, 2]);
      expect(thread.sending, isFalse);
      expect(fake.lastRead['c-1'], 2);
    });

    test('a retry with the same key does not duplicate the bubble', () async {
      final fake = FakeChat()..history([messageJson(id: 'm-1', seq: 1)]);
      final container = containerFor(fake, transport: ScriptedChatTransport());
      addTearDown(container.dispose);
      final sub = keepAlive(container, 'c-1');
      addTearDown(sub.close);
      await container.read(conversationThreadProvider('c-1').future);
      final notifier = container.read(
        conversationThreadProvider('c-1').notifier,
      );

      final key = newClientMessageId();
      await notifier.send('once', key);
      await notifier.send('once', key);

      final thread = container.read(conversationThreadProvider('c-1')).value!;
      expect(thread.messages, hasLength(2));
      expect(fake.sends, 1);
    });

    test('a failed send leaves the thread intact and rethrows', () async {
      final fake = FakeChat()..history([messageJson(id: 'm-1', seq: 1)]);
      final container = containerFor(fake, transport: ScriptedChatTransport());
      addTearDown(container.dispose);
      final sub = keepAlive(container, 'c-1');
      addTearDown(sub.close);
      await container.read(conversationThreadProvider('c-1').future);

      await expectLater(
        container
            .read(conversationThreadProvider('c-1').notifier)
            .send('blocked', newClientMessageId()),
        throwsA(isA<ApiException>()),
      );
      final thread = container.read(conversationThreadProvider('c-1')).value!;
      expect(thread.messages, hasLength(1));
      expect(thread.sending, isFalse, reason: 'the composer stayed disabled');
    });

    test('loadOlder walks the cursor and merges at the head', () async {
      // More messages than one initial page holds, so the cursor is real rather
      // than an artefact of a tiny conversation.
      final fake = FakeChat();
      fake.seedMessages(60);
      final container = containerFor(fake, transport: ScriptedChatTransport());
      addTearDown(container.dispose);
      final sub = keepAlive(container, 'c-1');
      addTearDown(sub.close);
      await container.read(conversationThreadProvider('c-1').future);
      final notifier = container.read(
        conversationThreadProvider('c-1').notifier,
      );

      var thread = container.read(conversationThreadProvider('c-1')).value!;
      expect(thread.messages, hasLength(kInitialHistoryPage));
      expect(thread.messages.first.seq, 11);
      expect(thread.messages.last.seq, 60);
      expect(thread.hasMoreHistory, isTrue);
      expect(thread.oldestCursor, 11);

      await notifier.loadOlder();
      thread = container.read(conversationThreadProvider('c-1')).value!;
      expect(thread.messages, hasLength(60));
      expect(thread.messages.first.seq, 1);
      expect(thread.messages.last.seq, 60);
      expect(thread.loadingOlder, isFalse);
      expect(thread.hasMoreHistory, isFalse);
      expect(thread.oldestCursor, isNull);
    });

    test(
      'an out-of-order poll sorts into place rather than prepending',
      () async {
        final fake = FakeChat()
          ..history([
            messageJson(id: 'm-1', seq: 1, body: 'old'),
            messageJson(id: 'm-2', seq: 2, body: 'newer'),
          ]);
        final transport = ScriptedChatTransport();
        final container = containerFor(fake, transport: transport);
        addTearDown(container.dispose);
        final sub = keepAlive(container, 'c-1');
        addTearDown(sub.close);
        await container.read(conversationThreadProvider('c-1').future);

        // A poll that returns only the OLDER message, after the newer one is
        // already on screen. Merging naively would put it above.
        transport.emit(
          'c-1',
          pageOf([messageJson(id: 'm-1', seq: 1, body: 'old')]),
        );
        await Future<void>.delayed(Duration.zero);

        final thread = container.read(conversationThreadProvider('c-1')).value!;
        expect(thread.messages.map((m) => m.seq), [1, 2]);
      },
    );

    test('editing adopts the server answer, window and all', () async {
      final fake = FakeChat()
        ..history([
          messageJson(id: 'm-1', seq: 1, isMine: true, canEdit: true),
        ]);
      final container = containerFor(fake, transport: ScriptedChatTransport());
      addTearDown(container.dispose);
      await container.read(conversationThreadProvider('c-1').future);
      final notifier = container.read(
        conversationThreadProvider('c-1').notifier,
      );

      await notifier.edit('m-1', 'corrigé');
      final thread = container.read(conversationThreadProvider('c-1')).value!;
      expect(thread.messages.single.body, 'corrigé');
      expect(thread.messages.single.isEdited, isTrue);
      expect(
        thread.messages.single.canEdit,
        isFalse,
        reason: 'the client kept offering an edit the server refused',
      );
    });

    test('deleting shows the placeholder, not a removed row', () async {
      final fake = FakeChat()
        ..history([
          messageJson(id: 'm-1', seq: 1, body: 'first'),
          messageJson(id: 'm-2', seq: 2, isMine: true),
        ]);
      final container = containerFor(fake, transport: ScriptedChatTransport());
      addTearDown(container.dispose);
      await container.read(conversationThreadProvider('c-1').future);

      await container
          .read(conversationThreadProvider('c-1').notifier)
          .delete('m-2');
      final thread = container.read(conversationThreadProvider('c-1')).value!;
      expect(thread.messages, hasLength(2), reason: 'the row disappeared');
      expect(thread.messages.last.body, '[deleted]');
      expect(thread.messages.last.isDeleted, isTrue);
      expect(thread.messages.last.seq, 2, reason: 'ordering was lost');
    });
  });

  group('inbox', () {
    testWidgets('lists conversations with unread counts', (tester) async {
      final fake = FakeChat()..inboxIds.addAll(['c-1', 't-2']);
      final container = containerFor(fake);
      addTearDown(container.dispose);

      await tester.pumpWidget(
        harness(const ChatInboxPage(), container: container),
      );
      await tester.pumpAndSettle();

      expect(find.text('Karim Amrani'), findsOneWidget);
      expect(find.text('Atlas CC'), findsOneWidget);
      expect(find.text('bonjour'), findsNWidgets(2));
      // Only the team row is badged as a group conversation; the DM row is not.
      expect(find.byIcon(Icons.groups_rounded), findsOneWidget);
      expect(find.byKey(const Key('chat.tile.c-1')), findsOneWidget);
      expect(find.byKey(const Key('chat.tile.t-2')), findsOneWidget);
    });

    testWidgets('shows an empty state when there is nothing yet', (
      tester,
    ) async {
      final container = containerFor(FakeChat());
      addTearDown(container.dispose);
      await tester.pumpWidget(
        harness(const ChatInboxPage(), container: container),
      );
      await tester.pumpAndSettle();

      expect(find.textContaining('No conversations yet'), findsOneWidget);
    });

    testWidgets('a failed load offers a retry', (tester) async {
      final fake = FakeChat()
        ..forcedCode = 'NETWORK_ERROR'
        ..forcedStatus = 0;
      final container = containerFor(fake);
      addTearDown(container.dispose);
      await tester.pumpWidget(
        harness(const ChatInboxPage(), container: container),
      );
      await tester.pumpAndSettle();

      expect(find.text('Retry'), findsOneWidget);
      fake.forcedCode = null;
      fake.inboxIds.add('c-1');
      await tester.tap(find.text('Retry'));
      await tester.pumpAndSettle();
      expect(find.text('Karim Amrani'), findsOneWidget);
    });
  });

  group('conversation screen', () {
    testWidgets('renders history oldest-at-the-top with sender names', (
      tester,
    ) async {
      final fake = FakeChat()
        ..history([
          messageJson(
            id: 'm-1',
            seq: 1,
            senderUserId: 'u-2',
            senderDisplayName: 'Karim Amrani',
            senderUsername: 'karim_amrani',
            body: 'salut',
          ),
          messageJson(
            id: 'm-2',
            seq: 2,
            senderUserId: 'u-1',
            body: 'à toi',
            isMine: true,
            canEdit: true,
          ),
        ]);
      final container = containerFor(fake, transport: ScriptedChatTransport());
      addTearDown(container.dispose);

      await tester.pumpWidget(
        harness(
          const ConversationScreen(conversationId: 'c-1', title: 'Crew thread'),
          container: container,
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('salut'), findsOneWidget);
      expect(find.text('à toi'), findsOneWidget);
      // The other rider is named on their bubble; the viewer's own does not.
      expect(find.text('Karim Amrani'), findsOneWidget);
      expect(find.byKey(const Key('chat.bubble.m-1')), findsOneWidget);
      expect(find.byKey(const Key('chat.bubble.m-2')), findsOneWidget);
    });

    testWidgets('a sent message appears and the composer clears', (
      tester,
    ) async {
      final fake = FakeChat()..history([messageJson(id: 'm-1', seq: 1)]);
      final container = containerFor(fake, transport: ScriptedChatTransport());
      addTearDown(container.dispose);

      await tester.pumpWidget(
        harness(
          const ConversationScreen(conversationId: 'c-1', title: 'Karim'),
          container: container,
        ),
      );
      await tester.pumpAndSettle();

      await tester.enterText(find.byKey(const Key('chat.composer')), 'envoyé');
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('chat.send')));
      await tester.pumpAndSettle();

      expect(find.text('envoyé'), findsOneWidget);
      final field = tester.widget<TextField>(
        find.byKey(const Key('chat.composer')),
      );
      expect(field.controller!.text, isEmpty);
      expect(fake.sends, 1);
    });

    testWidgets('the composer refuses a blank body', (tester) async {
      final fake = FakeChat()..history([messageJson()]);
      final container = containerFor(fake, transport: ScriptedChatTransport());
      addTearDown(container.dispose);

      await tester.pumpWidget(
        harness(
          const ConversationScreen(conversationId: 'c-1', title: 'Karim'),
          container: container,
        ),
      );
      await tester.pumpAndSettle();

      await tester.enterText(find.byKey(const Key('chat.composer')), '   ');
      await tester.pumpAndSettle();
      expect(find.text('Write something first'), findsOneWidget);
      expect(fake.sends, 0);
    });

    testWidgets('the edit affordance follows the server can_edit flag', (
      tester,
    ) async {
      final fake = FakeChat()
        ..history([
          messageJson(id: 'm-1', seq: 1, isMine: true, canEdit: true),
          messageJson(id: 'm-2', seq: 2, isMine: true, canEdit: false),
        ]);
      final container = containerFor(fake, transport: ScriptedChatTransport());
      addTearDown(container.dispose);

      await tester.pumpWidget(
        harness(
          const ConversationScreen(conversationId: 'c-1', title: 'Karim'),
          container: container,
        ),
      );
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('chat.edit.m-1')), findsOneWidget);
      expect(
        find.byKey(const Key('chat.edit.m-2')),
        findsNothing,
        reason: 'an edit was offered past the 15-minute window',
      );
      // Delete is offered for both own messages, edited or not.
      expect(find.byKey(const Key('chat.delete.m-1')), findsOneWidget);
      expect(find.byKey(const Key('chat.delete.m-2')), findsOneWidget);
    });

    testWidgets('an edit round-trips through the server answer', (
      tester,
    ) async {
      final fake = FakeChat()
        ..history([
          messageJson(
            id: 'm-1',
            seq: 1,
            body: 'typo',
            isMine: true,
            canEdit: true,
          ),
        ]);
      final container = containerFor(fake, transport: ScriptedChatTransport());
      addTearDown(container.dispose);

      await tester.pumpWidget(
        harness(
          const ConversationScreen(conversationId: 'c-1', title: 'Karim'),
          container: container,
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const Key('chat.edit.m-1')));
      await tester.pumpAndSettle();
      expect(find.text('Editing a message'), findsOneWidget);

      await tester.enterText(find.byKey(const Key('chat.composer')), 'corrigé');
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('chat.edit.submit')));
      await tester.pumpAndSettle();

      expect(find.text('corrigé'), findsOneWidget);
      expect(find.text('typo'), findsNothing);
      expect(find.text('edited'), findsOneWidget);
      expect(
        find.byKey(const Key('chat.edit.m-1')),
        findsNothing,
        reason: 'the closed window still offered an edit',
      );
    });

    testWidgets('deleting renders the localized placeholder', (tester) async {
      final fake = FakeChat()
        ..history([
          messageJson(id: 'm-1', seq: 1, body: 'regrettable', isMine: true),
        ]);
      final container = containerFor(fake, transport: ScriptedChatTransport());
      addTearDown(container.dispose);

      await tester.pumpWidget(
        harness(
          const ConversationScreen(conversationId: 'c-1', title: 'Karim'),
          container: container,
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const Key('chat.delete.m-1')));
      await tester.pumpAndSettle();

      expect(find.text('Deleted message'), findsOneWidget);
      expect(find.text('regrettable'), findsNothing);
      expect(find.byKey(const Key('chat.bubble.m-1')), findsOneWidget);
    });

    testWidgets('a blocked send reports the server refusal', (tester) async {
      final fake = FakeChat()..history([messageJson()]);
      final container = containerFor(fake, transport: ScriptedChatTransport());
      addTearDown(container.dispose);

      await tester.pumpWidget(
        harness(
          const ConversationScreen(conversationId: 'c-1', title: 'Karim'),
          container: container,
        ),
      );
      await tester.pumpAndSettle();

      await tester.enterText(find.byKey(const Key('chat.composer')), 'blocked');
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('chat.send')));
      await tester.pumpAndSettle();

      expect(find.textContaining('cannot message this rider'), findsOneWidget);
      // The rider's text survives a failed send, so the retry is one tap.
      final field = tester.widget<TextField>(
        find.byKey(const Key('chat.composer')),
      );
      expect(field.controller!.text, 'blocked');
    });

    testWidgets('an archived team refuses a send', (tester) async {
      final fake = FakeChat()..history([messageJson()]);
      final container = containerFor(fake, transport: ScriptedChatTransport());
      addTearDown(container.dispose);

      await tester.pumpWidget(
        harness(
          const ConversationScreen(conversationId: 'c-1', title: 'Atlas CC'),
          container: container,
        ),
      );
      await tester.pumpAndSettle();

      // A body the fake maps to CHAT_TEAM_ARCHIVED — what an archived team
      // answers to a send.
      await tester.enterText(
        find.byKey(const Key('chat.composer')),
        'frozen team',
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('chat.send')));
      await tester.pumpAndSettle();

      expect(
        find.text('This team is archived, so it does not accept new messages.'),
        findsOneWidget,
      );
    });

    testWidgets('a poll merges a newly arrived message', (tester) async {
      final fake = FakeChat()..history([messageJson(id: 'm-1', seq: 1)]);
      final transport = ScriptedChatTransport();
      final container = containerFor(fake, transport: transport);
      addTearDown(container.dispose);

      await tester.pumpWidget(
        harness(
          const ConversationScreen(conversationId: 'c-1', title: 'Karim'),
          container: container,
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('bonjour'), findsOneWidget);

      transport.emit(
        'c-1',
        pageOf([
          messageJson(
            id: 'm-2',
            seq: 2,
            body: 'nouveau',
            senderUserId: 'u-2',
            senderDisplayName: 'Karim Amrani',
          ),
        ]),
      );
      await tester.pumpAndSettle();

      expect(find.text('nouveau'), findsOneWidget);
      expect(find.text('bonjour'), findsOneWidget);
    });

    testWidgets('older history pages in behind a button', (tester) async {
      // More messages than one initial page holds, so the cursor is real.
      final fake = FakeChat()..seedMessages(kInitialHistoryPage + 10);
      final container = containerFor(fake, transport: ScriptedChatTransport());
      addTearDown(container.dispose);

      await tester.pumpWidget(
        harness(
          const ConversationScreen(conversationId: 'c-1', title: 'Karim'),
          container: container,
        ),
      );
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('chat.load.older')), findsOneWidget);

      await tester.tap(find.byKey(const Key('chat.load.older')));
      await tester.pumpAndSettle();

      // The button disappears once the whole history is loaded.
      expect(
        find.byKey(const Key('chat.load.older')),
        findsNothing,
        reason: 'the button stayed after the history was exhausted',
      );

      // The older page merged at the head rather than replacing what was there: the
      // thread now holds every message, still in order, not just the new page.
      await tester.scrollUntilVisible(
        find.text('m1'),
        300,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.text('m1'), findsOneWidget);

      final thread = container.read(conversationThreadProvider('c-1')).value!;
      expect(thread.messages, hasLength(60));
      expect(thread.messages.first.seq, 1);
      expect(thread.messages.last.seq, 60);
    });

    testWidgets('an empty conversation prompts a first message', (
      tester,
    ) async {
      final container = containerFor(
        FakeChat(),
        transport: ScriptedChatTransport(),
      );
      addTearDown(container.dispose);

      await tester.pumpWidget(
        harness(
          const ConversationScreen(conversationId: 'c-1', title: 'Karim'),
          container: container,
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('No messages yet. Say hello.'), findsOneWidget);
    });

    testWidgets('renders right-to-left in Arabic without leaking Latin prose', (
      tester,
    ) async {
      final fake = FakeChat()
        ..history([messageJson(id: 'm-1', seq: 1, body: 'مرحبا')]);
      final container = containerFor(fake, transport: ScriptedChatTransport());
      addTearDown(container.dispose);

      await tester.pumpWidget(
        harness(
          const ConversationScreen(conversationId: 'c-1', title: 'كريم'),
          container: container,
          locale: 'ar',
          direction: TextDirection.rtl,
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('إرسال'), findsOneWidget);
      expect(find.text('اكتب رسالة'), findsOneWidget);
      expect(
        find.textContaining('Edit'),
        findsNothing,
        reason: 'an English affordance leaked into the Arabic screen',
      );
    });

    testWidgets('renders in French', (tester) async {
      final fake = FakeChat()..history([messageJson(id: 'm-1', seq: 1)]);
      final container = containerFor(fake, transport: ScriptedChatTransport());
      addTearDown(container.dispose);

      await tester.pumpWidget(
        harness(
          const ConversationScreen(conversationId: 'c-1', title: 'Karim'),
          container: container,
          locale: 'fr',
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Envoyer'), findsOneWidget);
      expect(find.text('Écrivez un message'), findsOneWidget);
    });
  });

  group('error mapping', () {
    AppLocalizations t(String lang) => AppLocalizations(Locale(lang));

    test('chat codes map to distinct localized sentences', () {
      final en = t('en');
      expect(
        friendlyChatError(en, ApiException(403, 'CHAT_BLOCKED', 'x')),
        contains('cannot message'),
      );
      expect(
        friendlyChatError(en, ApiException(403, 'CHAT_TEAM_ARCHIVED', 'x')),
        contains('archived'),
      );
      expect(
        friendlyChatError(
          en,
          ApiException(409, 'CHAT_EDIT_WINDOW_CLOSED', 'x'),
        ),
        contains('15-minute'),
      );
      expect(
        friendlyChatError(
          en,
          ApiException(422, 'CHAT_CANNOT_TARGET_SELF', 'x'),
        ),
        contains('yourself'),
      );
    });

    test('the not-found family collapses to one sentence', () {
      final en = t('en');
      for (final code in [
        'CHAT_CONVERSATION_NOT_FOUND',
        'CHAT_USER_NOT_FOUND',
        'CHAT_MESSAGE_NOT_FOUND',
      ]) {
        expect(
          friendlyChatError(en, ApiException(404, code, 'x')),
          'That conversation is not available.',
          reason: code,
        );
      }
    });

    test('an unknown error never leaks a raw code', () {
      final message = friendlyChatError(
        t('en'),
        ApiException(418, 'CHAT_TEAPOT', 'raw internal detail'),
      );
      expect(message.contains('TEAPOT'), isFalse);
      expect(message.contains('raw'), isFalse);
    });

    test('a non-API exception degrades to the generic sentence', () {
      expect(
        friendlyChatError(t('en'), StateError('boom')),
        'Something went wrong. Try again.',
      );
    });

    test('every chat error sentence is localized in fr and ar', () {
      const codes = [
        'CHAT_BLOCKED',
        'CHAT_TEAM_ARCHIVED',
        'CHAT_TEAM_UNAVAILABLE',
        'CHAT_FORBIDDEN',
        'CHAT_CANNOT_TARGET_SELF',
        'CHAT_EDIT_WINDOW_CLOSED',
        'CHAT_MESSAGE_DELETED',
        'CHAT_CONVERSATION_NOT_FOUND',
      ];
      for (final lang in ['en', 'fr', 'ar']) {
        final loc = t(lang);
        for (final code in codes) {
          final message = friendlyChatError(loc, ApiException(409, code, 'x'));
          expect(message, isNotEmpty);
          expect(
            message.contains(code),
            isFalse,
            reason: '$code leaked in $lang',
          );
        }
      }
    });
  });

  group('localization', () {
    const chatKeys = [
      'chat.inbox',
      'chat.message',
      'chat.teamChannel',
      'chat.noConversations',
      'chat.noMessages',
      'chat.noMessagesYet',
      'chat.unnamedConversation',
      'chat.deleted',
      'chat.edited',
      'chat.edit',
      'chat.delete',
      'chat.save',
      'chat.cancelEdit',
      'chat.editingMessage',
      'chat.send',
      'chat.sending',
      'chat.saving',
      'chat.hint',
      'chat.loadEarlier',
      'chat.jumpToLatest',
      'chat.error.empty',
      'chat.error.tooLong',
      'chat.error.blocked',
      'chat.error.teamArchived',
      'chat.error.teamUnavailable',
      'chat.error.forbidden',
      'chat.error.cannotTargetSelf',
      'chat.error.editWindowClosed',
      'chat.error.messageDeleted',
      'chat.error.notFound',
    ];

    test('the chat concept set exists in all three languages', () {
      for (final lang in ['en', 'fr', 'ar']) {
        final loc = AppLocalizations(Locale(lang));
        for (final key in chatKeys) {
          expect(loc.get(key), isNotEmpty, reason: '$key missing in $lang');
          expect(
            loc.get(key),
            isNot(key),
            reason: '$key has no translation in $lang',
          );
        }
      }
    });

    test(
      'the three locales translate the same prose, not just the same keys',
      () {
        // A few labels are legitimately identical across languages ("Messages"
        // is the correct French and the correct Arabic sense of the inbox
        // heading), so this checks the sentence-length strings — where a missing
        // translation would show up as an English sentence in a French screen.
        const proseKeys = [
          'chat.noConversations',
          'chat.noMessagesYet',
          'chat.loadEarlier',
          'chat.hint',
          'chat.error.empty',
          'chat.error.tooLong',
          'chat.error.blocked',
          'chat.error.teamArchived',
          'chat.error.editWindowClosed',
          'chat.error.notFound',
        ];
        final en = AppLocalizations(const Locale('en'));
        final fr = AppLocalizations(const Locale('fr'));
        final ar = AppLocalizations(const Locale('ar'));
        for (final key in proseKeys) {
          expect(fr.get(key), isNot(en.get(key)), reason: 'fr reuses en: $key');
          expect(ar.get(key), isNot(en.get(key)), reason: 'ar reuses en: $key');
        }
      },
    );

    test('English renders the expected copy', () {
      final en = AppLocalizations(const Locale('en'));
      expect(en.get('chat.inbox'), 'Messages');
      expect(en.get('chat.teamChannel'), 'Team channel');
      expect(en.get('chat.loadEarlier'), 'Load earlier messages');
    });

    test('French renders the expected copy with escaped apostrophes', () {
      final fr = AppLocalizations(const Locale('fr'));
      expect(fr.get('chat.inbox'), 'Messages');
      expect(fr.get('chat.teamChannel'), "Canal d'équipe");
      expect(fr.get('chat.hint'), 'Écrivez un message');
    });

    test('Arabic renders correct cycling terminology', () {
      final ar = AppLocalizations(const Locale('ar'));
      expect(ar.get('chat.inbox'), 'الرسائل');
      expect(ar.get('chat.teamChannel'), 'قناة الفريق');
      expect(ar.get('chat.send'), 'إرسال');
    });

    test('Arabic chat prose leaks no Latin words', () {
      final ar = AppLocalizations(const Locale('ar'));
      for (final key in chatKeys) {
        expect(
          RegExp(r'[A-Za-z]').hasMatch(ar.get(key)),
          isFalse,
          reason: '$key: ${ar.get(key)}',
        );
      }
    });

    test('French chat prose leaks no Arabic', () {
      final fr = AppLocalizations(const Locale('fr'));
      for (final key in chatKeys) {
        expect(
          RegExp(r'[؀-ۿ]').hasMatch(fr.get(key)),
          isFalse,
          reason: '$key: ${fr.get(key)}',
        );
      }
    });

    test('every message type has a distinct wire value', () {
      // Message types are rendered from the body, not from a localized type
      // label, so what matters is that the two cannot collide on the wire.
      final wires = MessageType.values.map((t) => t.wire).toSet();
      expect(wires.length, MessageType.values.length);
      expect(wires, {'text', 'system'});
    });
  });

  group('routing', () {
    test('chat routes are registered and /group-rides stays a placeholder', () {
      final source = File(
        'lib/core/routing/app_router.dart',
      ).readAsStringSync();
      expect(source.contains("path: '/chat'"), isTrue);
      expect(source.contains("path: '/chat/:id'"), isTrue);
      expect(source.contains('const ChatInboxPage()'), isTrue);
      expect(source.contains('ConversationScreen('), isTrue);

      final placeholderBlock = source.substring(
        source.indexOf('for (final p in ['),
      );
      // Group rides and synchronized rides stay out of scope for Phase 8.3.
      expect(placeholderBlock.contains("'/group-rides'"), isTrue);
      expect(
        placeholderBlock.contains("'/chat'"),
        isFalse,
        reason: '/chat must not be a placeholder any more',
      );

      for (final kept in [
        "'/login'",
        "'/home'",
        "'/friends'",
        "'/users/:userId'",
        "'/teams'",
        "'/teams/:id'",
        "'/routes'",
        "'/training'",
        "'/coach'",
        "'/settings'",
      ]) {
        expect(source.contains(kept), isTrue, reason: '$kept was removed');
      }
    });

    test(
      'a team profile offers its channel and a rider profile offers a DM',
      () {
        final teamSource = File(
          'lib/features/teams/presentation/team_profile_page.dart',
        ).readAsStringSync();
        expect(teamSource.contains("Key('team.action.chat')"), isTrue);
        expect(teamSource.contains('openTeamConversation'), isTrue);

        final userSource = File(
          'lib/features/social/presentation/user_profile_page.dart',
        ).readAsStringSync();
        expect(userSource.contains("Key('social.action.message')"), isTrue);
        expect(userSource.contains('openDirect'), isTrue);
      },
    );
  });
}
