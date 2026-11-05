import 'package:flutter/material.dart';

import '../core/app_state.dart';
import '../core/decision_engine.dart';
import '../core/model_store.dart';
import '../theme.dart';

/// 决策页：左输入右结果（竖屏单页，结果卡片在下方）。
class DecisionScreen extends StatefulWidget {
  final DecisionEngine engine;
  final AppState state;
  final ModelStore models;

  const DecisionScreen({
    super.key,
    required this.engine,
    required this.state,
    required this.models,
  });

  @override
  State<DecisionScreen> createState() => _DecisionScreenState();
}

class _DecisionScreenState extends State<DecisionScreen> {
  final TextEditingController _question = TextEditingController();
  final TextEditingController _option = TextEditingController();
  final List<Map<String, String>> _options = [];
  bool _busy = false;
  DecisionResult? _result;
  String? _error;

  @override
  void initState() {
    super.initState();
    _options.addAll([
      {'id': 'opt1', 'text': '近郊露营'},
      {'id': 'opt2', 'text': '周边古镇一日游'},
    ]);
  }

  @override
  void dispose() {
    _question.dispose();
    _option.dispose();
    super.dispose();
  }

  bool get _canDecide => _question.text.trim().isNotEmpty && _options.length >= 2 && !_busy;

  void _addOption() {
    final text = _option.text.trim();
    if (text.isEmpty) return;
    setState(() {
      _options.add({'id': 'opt${_options.length + 1}', 'text': text});
      _option.clear();
    });
  }

  Future<void> _decide({bool forceOnline = false}) async {
    if (!_canDecide) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = await widget.engine.decide(
        question: _question.text.trim(),
        options: List<Map<String, String>>.from(_options),
        allowOnline: forceOnline || widget.state.allowOnline,
      );
      if (!mounted) return;
      setState(() => _result = result);
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(12),
      children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('问题', style: TextStyle(fontSize: 12, color: PaimindTheme.muted)),
                TextField(
                  controller: _question,
                  maxLines: 3,
                  decoration: const InputDecoration(hintText: '例如：周末两天，预算 500，去哪里？'),
                  onChanged: (_) => setState(() {}),
                ),
                const SizedBox(height: 12),
                const Text('选项（至少 2 个）',
                    style: TextStyle(fontSize: 12, color: PaimindTheme.muted)),
                Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _option,
                        decoration: const InputDecoration(hintText: '输入选项'),
                        onSubmitted: (_) => _addOption(),
                      ),
                    ),
                    const SizedBox(width: 8),
                    ElevatedButton(onPressed: _addOption, child: const Text('添加')),
                  ],
                ),
                const SizedBox(height: 8),
                ..._options.map((o) => Padding(
                      padding: const EdgeInsets.only(bottom: 6),
                      child: Row(
                        children: [
                          Expanded(child: Text('${o['text']}')),
                          IconButton(
                            icon: const Icon(Icons.close, size: 18),
                            onPressed: () => setState(() => _options.remove(o)),
                          ),
                        ],
                      ),
                    )),
                if (_question.text.trim().isEmpty || _options.length < 2)
                  Text(
                    _question.text.trim().isEmpty ? '问题不能为空' : '至少需要 2 个选项',
                    style: const TextStyle(fontSize: 12, color: PaimindTheme.warning),
                  ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  children: [
                    FilledButton(
                      onPressed: _canDecide ? () => _decide() : null,
                      child: const Text('开始决策'),
                    ),
                    OutlinedButton(
                      onPressed: _canDecide ? () => _decide(forceOnline: true) : null,
                      child: const Text('强制联网'),
                    ),
                    TextButton(
                      onPressed: _busy
                          ? null
                          : () => setState(() {
                                _question.clear();
                                _options.clear();
                                _result = null;
                                _error = null;
                              }),
                      child: const Text('清空'),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        _buildResult(),
      ],
    );
  }

  Widget _buildResult() {
    if (_busy) {
      return const Card(
        child: Padding(
          padding: EdgeInsets.all(20),
          child: Center(child: CircularProgressIndicator()),
        ),
      );
    }
    if (_error != null) {
      return Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('出错了', style: TextStyle(fontWeight: FontWeight.w700)),
              const SizedBox(height: 8),
              Text(_error!, style: const TextStyle(fontSize: 13, color: PaimindTheme.danger)),
              const SizedBox(height: 8),
              const Text('请到「设置 → 档位」下载模型后重试。',
                  style: TextStyle(fontSize: 12, color: PaimindTheme.muted)),
            ],
          ),
        ),
      );
    }
    final r = _result;
    if (r == null) {
      return const Card(
        child: Padding(
          padding: EdgeInsets.all(20),
          child: Center(child: Text('尚未决策', style: TextStyle(color: PaimindTheme.muted))),
        ),
      );
    }
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(child: Text('结果', style: TextStyle(fontWeight: FontWeight.w700))),
                _sourceBadge(r.source),
              ],
            ),
            const SizedBox(height: 12),
            Text(r.selectedText,
                style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700)),
            const SizedBox(height: 6),
            Text(r.reason, style: const TextStyle(fontSize: 13, color: PaimindTheme.muted)),
            const SizedBox(height: 8),
            Text('置信度 ${(r.confidence * 100).round()}%',
                style: const TextStyle(fontWeight: FontWeight.w600)),
            Text('${r.backend} · ${r.tier} · ${r.latencyMs} ms',
                style: const TextStyle(fontSize: 11, color: PaimindTheme.muted)),
            if (r.alternatives.isNotEmpty) ...[
              const SizedBox(height: 12),
              const Text('候选排序', style: TextStyle(fontSize: 11, color: PaimindTheme.muted)),
              ...r.alternatives.map((a) => Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(a.text, style: const TextStyle(fontSize: 13)),
                        Text('${(a.score * 100).round()}%',
                            style: const TextStyle(
                                fontSize: 13, fontWeight: FontWeight.w600)),
                      ],
                    ),
                  )),
            ],
            if (r.sources.isNotEmpty) ...[
              const SizedBox(height: 12),
              const Text('来源', style: TextStyle(fontSize: 11, color: PaimindTheme.muted)),
              ...r.sources.map((s) => Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text('${s.title} (${s.domain})',
                        style: const TextStyle(fontSize: 12, color: PaimindTheme.muted)),
                  )),
            ],
          ],
        ),
      ),
    );
  }

  Widget _sourceBadge(String source) {
    final online = source != 'local';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
      decoration: BoxDecoration(
        color: online ? PaimindTheme.primary : PaimindTheme.teal,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Text(
        {'local': '本地', 'online': '联网', 'hybrid': '本地+联网'}[source] ?? source,
        style: TextStyle(
            fontSize: 11,
            fontWeight: FontWeight.w700,
            color: online ? Colors.white : const Color(0xFF06281F)),
      ),
    );
  }
}
