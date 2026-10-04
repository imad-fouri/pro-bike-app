import 'package:cyclecoach/features/auth/domain/validators.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('email validation', () {
    expect(AuthValidators.email('rider@example.com'), isNull);
    expect(AuthValidators.email('bad'), isNotNull);
    expect(AuthValidators.email('a@b'), isNotNull);
  });

  test('password validation mirrors backend policy', () {
    expect(AuthValidators.password('StrongPass123'), isNull);
    expect(AuthValidators.password('short1'), isNotNull);
    expect(AuthValidators.password('allletterslong'), isNotNull);
    expect(AuthValidators.password('12345678901'), isNotNull);
  });

  test('registration validation: name + confirm', () {
    expect(AuthValidators.displayName('A'), isNotNull);
    expect(AuthValidators.displayName('Imad'), isNull);
    expect(AuthValidators.confirm('a', 'b'), isNotNull);
    expect(AuthValidators.confirm('same', 'same'), isNull);
  });
}
