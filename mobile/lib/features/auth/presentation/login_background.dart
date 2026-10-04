import 'package:flutter/material.dart';

/// Login backdrop: the alpine photo when it is bundled, the painted scene
/// otherwise.
///
/// The photo lives at `assets/images/login-hero.jpg` (declared in
/// `pubspec.yaml`). If it is ever missing from a build, `errorBuilder` falls
/// back to the painted scene instead of rendering a broken image — so the
/// screen is never blank, in production or in widget tests.
class LoginBackground extends StatelessWidget {
  const LoginBackground({super.key});

  static const photo = 'assets/images/login-hero.jpg';

  @override
  Widget build(BuildContext context) {
    return Positioned.fill(
      child: Image.asset(
        photo,
        fit: BoxFit.cover,
        // The photo is bright; a light scrim keeps the glass card readable.
        color: Colors.black.withValues(alpha: 0.12),
        colorBlendMode: BlendMode.darken,
        errorBuilder: (context, error, stackTrace) =>
            const CustomPaint(painter: _AlpinePainter()),
      ),
    );
  }
}

class _AlpinePainter extends CustomPainter {
  const _AlpinePainter();

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;

    // Sky: alpine blue melting into haze at the horizon.
    canvas.drawRect(
      Offset.zero & size,
      Paint()
        ..shader = LinearGradient(
          begin: Alignment.topCenter,
          end: const Alignment(0, 0.55),
          colors: const [Color(0xFF2F7AC2), Color(0xFFA8CFE8)],
        ).createShader(Offset.zero & size),
    );

    // Sun haze, upper right.
    canvas.drawCircle(
      Offset(w * 0.78, h * 0.16),
      w * 0.35,
      Paint()..color = Colors.white.withValues(alpha: 0.18),
    );

    // Far snowy ridge, then white caps on its tallest peaks.
    const peaks = [
      0.10,
      0.34,
      0.24,
      0.46,
      0.20,
      0.62,
      0.36,
      0.80,
      0.22,
      1.0,
      0.18,
    ];
    _ridge(
      canvas,
      w,
      h,
      base: 0.52,
      peaks: peaks,
      fill: const Color(0xFF8FA3B8),
    );
    _snowcaps(canvas, w, h, base: 0.52, peaks: peaks);

    // Rocky cliff on the right, like the mockup's.
    final cliff = Path()
      ..moveTo(w * 0.72, h * 0.30)
      ..lineTo(w, h * 0.18)
      ..lineTo(w, h * 0.62)
      ..lineTo(w * 0.80, h * 0.60)
      ..close();
    canvas.drawPath(cliff, Paint()..color = const Color(0xFF7A6A58));
    final cliffShade = Path()
      ..moveTo(w * 0.86, h * 0.24)
      ..lineTo(w, h * 0.18)
      ..lineTo(w, h * 0.62)
      ..lineTo(w * 0.88, h * 0.60)
      ..close();
    canvas.drawPath(cliffShade, Paint()..color = const Color(0xFF5E5347));

    // Rolling green slopes left and right.
    final leftSlope = Path()
      ..moveTo(0, h * 0.52)
      ..quadraticBezierTo(w * 0.25, h * 0.50, w * 0.45, h * 0.62)
      ..lineTo(w * 0.35, h)
      ..lineTo(0, h)
      ..close();
    canvas.drawPath(leftSlope, Paint()..color = const Color(0xFF3E7A3E));
    final rightSlope = Path()
      ..moveTo(w, h * 0.55)
      ..quadraticBezierTo(w * 0.78, h * 0.56, w * 0.62, h * 0.66)
      ..lineTo(w * 0.72, h)
      ..lineTo(w, h)
      ..close();
    canvas.drawPath(rightSlope, Paint()..color = const Color(0xFF4C8A44));

    // Meadow between the slopes.
    final meadow = Path()
      ..moveTo(w * 0.30, h * 0.60)
      ..quadraticBezierTo(w * 0.55, h * 0.58, w * 0.75, h * 0.64)
      ..lineTo(w * 0.72, h)
      ..lineTo(w * 0.35, h)
      ..close();
    canvas.drawPath(meadow, Paint()..color = const Color(0xFF69A854));

    // The road: a winding pass climbing from the bottom to the right ridge.
    final road = Path()
      ..moveTo(w * 0.30, h)
      ..cubicTo(w * 0.42, h * 0.86, w * 0.34, h * 0.80, w * 0.52, h * 0.74)
      ..cubicTo(w * 0.68, h * 0.69, w * 0.62, h * 0.64, w * 0.80, h * 0.60);
    canvas.drawPath(
      road,
      Paint()
        ..color = const Color(0xFF9AA0A3)
        ..style = PaintingStyle.stroke
        ..strokeWidth = w * 0.075
        ..strokeCap = StrokeCap.round,
    );
    canvas.drawPath(
      road,
      Paint()
        ..color = Colors.white.withValues(alpha: 0.85)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2
        ..strokeCap = StrokeCap.round,
    );

    // Foreground grass darkening the corners, and a light scrim so the glass
    // card always has contrast behind it.
    canvas.drawRect(
      Offset.zero & size,
      Paint()
        ..shader = LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [Colors.transparent, Colors.black.withValues(alpha: 0.28)],
          stops: const [0.55, 1.0],
        ).createShader(Offset.zero & size),
    );
  }

  /// A jagged ridge: [peaks] alternates peak height and valley depth, all as
  /// fractions of the height above [base], spread evenly across the width.
  void _ridge(
    Canvas canvas,
    double w,
    double h, {
    required double base,
    required List<double> peaks,
    required Color fill,
  }) {
    final path = Path()..moveTo(0, h * base);
    final n = peaks.length;
    for (var i = 0; i < n; i += 2) {
      path.lineTo(w * (i / (n - 1)), h * (base - peaks[i] * 0.45));
      if (i + 1 < n) {
        path.lineTo(w * ((i + 1) / (n - 1)), h * (base - peaks[i + 1]));
      }
    }
    path
      ..lineTo(w, h * base)
      ..lineTo(w, h * base + 1)
      ..lineTo(0, h * base + 1)
      ..close();
    canvas.drawPath(path, Paint()..color = fill);
  }

  /// A white triangle on every peak tall enough to hold snow.
  void _snowcaps(
    Canvas canvas,
    double w,
    double h, {
    required double base,
    required List<double> peaks,
  }) {
    final paint = Paint()..color = Colors.white.withValues(alpha: 0.92);
    final n = peaks.length;
    for (var i = 0; i < n; i += 2) {
      if (peaks[i] < 0.3) continue;
      final peakX = w * (i / (n - 1));
      final tipY = h * (base - peaks[i] * 0.45);
      final half = w * 0.045;
      final drop = h * 0.05;
      canvas.drawPath(
        Path()
          ..moveTo(peakX - half, tipY + drop)
          ..lineTo(peakX, tipY)
          ..lineTo(peakX + half, tipY + drop)
          ..close(),
        paint,
      );
    }
  }

  @override
  bool shouldRepaint(_AlpinePainter oldDelegate) => false;
}
