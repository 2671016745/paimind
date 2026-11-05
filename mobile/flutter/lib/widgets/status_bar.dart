import 'package:flutter/material.dart';

import '../core/app_state.dart';
import '../core/hardware.dart';
import '../theme.dart';

/// 顶部状态栏：档位 · 部署模式 · 实际推理后端 · 设备名 · API 状态 · HF 登录状态。
///
/// 手机版不暴露 HTTP API，所以这里只显示档位/后端/设备（API 与 HF 在设置页）。
class StatusBar extends StatelessWidget {
  final AppState state;
  final HardwareProbe hardware;

  const StatusBar({super.key, required this.state, required this.hardware});

  @override
  Widget build(BuildContext context) {
    final chips = <Widget>[
      _chip('档位 ${state.currentTier}', PaimindTheme.text),
      _chip(state.currentMode, PaimindTheme.badgeColor(state.currentMode)),
      _chip('CPU', PaimindTheme.muted),
      if (hardware.probed) _chip('${hardware.availableGb.toStringAsFixed(1)}GB', PaimindTheme.muted),
    ];
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
      decoration: const BoxDecoration(
        color: PaimindTheme.bg,
        border: Border(bottom: BorderSide(color: PaimindTheme.line)),
      ),
      child: Row(
        children: [
          const Text('🍙 应急食品', style: TextStyle(fontWeight: FontWeight.w700)),
          const SizedBox(width: 10),
          Expanded(
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(children: chips),
            ),
          ),
        ],
      ),
    );
  }

  Widget _chip(String text, Color color) => Container(
        margin: const EdgeInsets.only(right: 6),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: PaimindTheme.card,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Text(text,
            style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: color)),
      );
}
