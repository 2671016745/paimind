// paimind Flutter 手机版 — 入口。
//
// 手机版与桌面版共享同一套 INT4 ONNX 模型与核心逻辑（同一 prompt、同一置信度
// 规则）。区别只在两端：
//   * 手机版**不暴露 HTTP API**，推理在本机完成（onnxruntime 插件）。
//   * 外部引擎模式（Ollama / vLLM / llama.cpp / Docker）默认隐藏，只保留
//     builtin；开发者选项里可以手动填局域网 Ollama 地址（实验性）。

import 'package:flutter/material.dart';

import 'core/app_state.dart';
import 'core/decision_engine.dart';
import 'core/hardware.dart';
import 'core/model_store.dart';
import 'screens/decision_screen.dart';
import 'screens/settings_screen.dart';
import 'theme.dart';
import 'widgets/status_bar.dart';

void main() {
  runApp(const PaimindApp());
}

class PaimindApp extends StatefulWidget {
  const PaimindApp({super.key});

  @override
  State<PaimindApp> createState() => _PaimindAppState();
}

class _PaimindAppState extends State<PaimindApp> {
  final AppState state = AppState();
  final ModelStore models = ModelStore();
  final HardwareProbe hardware = HardwareProbe();
  late final DecisionEngine engine;

  @override
  void initState() {
    super.initState();
    engine = DecisionEngine(models: models, hardware: hardware, state: state);
    state.onChanged(() => setState(() {}));
    _bootstrap();
  }

  Future<void> _bootstrap() async {
    await models.load();
    await hardware.probe();
    await state.load();
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final theme = PaimindTheme.dark();
    return MaterialApp(
      title: '应急食品',
      debugShowCheckedModeBanner: false,
      theme: theme,
      home: Scaffold(
        backgroundColor: theme.scaffoldBackgroundColor,
        body: SafeArea(
          child: DefaultTabController(
            length: 2,
            child: Column(
              children: [
                StatusBar(state: state, hardware: hardware),
                const TabBar(
                  tabs: [Tab(text: '决策'), Tab(text: '设置')],
                ),
                Expanded(
                  child: TabBarView(
                    children: [
                      DecisionScreen(engine: engine, state: state, models: models),
                      SettingsScreen(
                        state: state,
                        models: models,
                        hardware: hardware,
                        engine: engine,
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
