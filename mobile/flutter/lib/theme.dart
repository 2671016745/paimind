import 'package:flutter/material.dart';

/// 视觉规范：圆角卡片 + 主色 #4F7CFF + 深色模式。
class PaimindTheme {
  static const Color primary = Color(0xFF4F7CFF);
  static const Color bg = Color(0xFF14161B);
  static const Color card = Color(0xFF1D2027);
  static const Color line = Color(0xFF2A2E37);
  static const Color text = Color(0xFFE8EAED);
  static const Color muted = Color(0xFF9AA0A6);
  static const Color ready = Color(0xFF34A853);
  static const Color warning = Color(0xFFF9AB00);
  static const Color danger = Color(0xFFFB923C);
  static const Color teal = Color(0xFF2DD4BF);
  static const Color purple = Color(0xFFA78BFA);
  static const Color hfOrange = Color(0xFFFF9D00);

  /// 部署模式徽标色：内置蓝灰、Ollama 青、vLLM 紫、llama.cpp 墨绿、Docker 深蓝。
  static Color badgeColor(String mode) {
    switch (mode) {
      case 'builtin':
        return const Color(0xFF8A94A6);
      case 'ollama':
        return teal;
      case 'vllm':
        return purple;
      case 'llamacpp':
        return const Color(0xFF1F7A5C);
      case 'docker':
        return const Color(0xFF1E40AF);
      default:
        return muted;
    }
  }

  static ThemeData dark() {
    final base = ThemeData.dark(useMaterial3: true);
    return base.copyWith(
      scaffoldBackgroundColor: bg,
      colorScheme: base.colorScheme.copyWith(
        primary: primary,
        surface: card,
        secondary: teal,
      ),
      cardTheme: CardThemeData(
        color: card,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: line),
        ),
      ),
      tabBarTheme: const TabBarThemeData(
        labelColor: text,
        unselectedLabelColor: muted,
        indicatorColor: primary,
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: bg,
        elevation: 0,
        foregroundColor: text,
      ),
      dividerColor: line,
      textTheme: base.textTheme.apply(bodyColor: text, displayColor: text),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: bg,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(10),
          borderSide: const BorderSide(color: line),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(10),
          borderSide: const BorderSide(color: line),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(10),
          borderSide: const BorderSide(color: primary),
        ),
      ),
    );
  }
}
