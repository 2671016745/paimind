import 'package:flutter/material.dart';

import '../core/app_state.dart';
import '../core/decision_engine.dart';
import '../core/hardware.dart';
import '../core/model_store.dart';
import '../theme.dart';

/// 设置页（移动简化版）：
///   * 档位卡片（三态 + 不可用原因）
///   * 硬件检测 + 重新检测
///   * 下载源（简化：内置源开关 + 新增自定义模板）
///   * HF 登录与上传（简化：单档上传）
///   * 开发者选项：外部引擎默认隐藏，可手动填局域网 Ollama 地址（实验性）
class SettingsScreen extends StatefulWidget {
  final AppState state;
  final ModelStore models;
  final HardwareProbe hardware;
  final DecisionEngine engine;

  const SettingsScreen({
    super.key,
    required this.state,
    required this.models,
    required this.hardware,
    required this.engine,
  });

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  bool _showEngines = false;
  final TextEditingController _customMirror = TextEditingController();
  final TextEditingController _devOllama = TextEditingController();

  @override
  void dispose() {
    _customMirror.dispose();
    _devOllama.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(12),
      children: [
        _card(
          title: '档位',
          child: Column(
            children: ModelStore.tiers.map((tier) => _tierCard(tier)).toList(),
          ),
        ),
        _card(
          title: '硬件',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(widget.hardware.describe(),
                  style: const TextStyle(fontSize: 13, color: PaimindTheme.muted)),
              const SizedBox(height: 8),
              OutlinedButton(
                onPressed: () async {
                  await widget.hardware.probe();
                  setState(() {});
                },
                child: const Text('重新检测硬件'),
              ),
            ],
          ),
        ),
        _card(
          title: '联网求助',
          child: SwitchListTile(
            contentPadding: EdgeInsets.zero,
            value: widget.state.allowOnline,
            title: const Text('允许联网求助'),
            subtitle: const Text('置信度不足或问题有时效性时自动联网'),
            onChanged: (v) async {
              widget.state.allowOnline = v;
              await widget.state.save();
              setState(() {});
            },
          ),
        ),
        _card(
          title: '下载源（简化）',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('GitHub 官方 / ghproxy / ghfast · Hugging Face 官方 / HF Mirror',
                  style: TextStyle(fontSize: 12, color: PaimindTheme.muted)),
              const SizedBox(height: 8),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _customMirror,
                      decoration: const InputDecoration(
                        hintText: 'https://镜像地址/{repo}/resolve/{revision}/{path}',
                        isDense: true,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  OutlinedButton(
                    onPressed: () {
                      final url = _customMirror.text.trim();
                      if (!url.startsWith('https://')) {
                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(content: Text('自定义镜像必须使用 https')),
                        );
                        return;
                      }
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(content: Text('已添加自定义镜像（需确认为可信来源）')),
                      );
                      _customMirror.clear();
                    },
                    child: const Text('添加'),
                  ),
                ],
              ),
            ],
          ),
        ),
        _card(
          title: 'Hugging Face（简化）',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(widget.state.hfUsername.isEmpty ? '未登录' : '已登录 ${widget.state.hfUsername}',
                  style: const TextStyle(fontSize: 13)),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                children: [
                  OutlinedButton(
                    onPressed: () => _loginHf(context),
                    child: const Text('登录 Hugging Face'),
                  ),
                  OutlinedButton(
                    onPressed: () => _uploadSingleTier(context),
                    child: const Text('上传当前档位'),
                  ),
                ],
              ),
            ],
          ),
        ),
        if (_showEngines)
          _card(
            title: '开发者选项（实验性）',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('外部引擎模式默认隐藏。可手动指定局域网 Ollama：',
                    style: TextStyle(fontSize: 12, color: PaimindTheme.muted)),
                const SizedBox(height: 8),
                Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _devOllama,
                        decoration: const InputDecoration(
                          hintText: 'http://192.168.1.10:11434',
                          isDense: true,
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    OutlinedButton(
                      onPressed: () {
                        widget.state.devOllamaUrl = _devOllama.text.trim();
                        widget.state.save();
                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(content: Text('已保存局域网 Ollama 地址')),
                        );
                      },
                      child: const Text('保存'),
                    ),
                  ],
                ),
              ],
            ),
          ),
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton(
            onPressed: () => setState(() => _showEngines = !_showEngines),
            child: Text(_showEngines ? '隐藏开发者选项' : '显示开发者选项'),
          ),
        ),
      ],
    );
  }

  Widget _tierCard(String tier) {
    final downloaded = widget.models.isDownloaded(tier);
    final manifest = widget.models.manifestOf(tier);
    final active = widget.state.currentTier == tier;
    final state = downloaded ? 'ready' : 'disabled';
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: PaimindTheme.bg,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: active ? PaimindTheme.primary : PaimindTheme.line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text('$tier · ${manifest['params'] ?? ''}',
                    style: const TextStyle(fontWeight: FontWeight.w700)),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: state == 'ready'
                      ? PaimindTheme.ready.withValues(alpha: 0.18)
                      : PaimindTheme.muted.withValues(alpha: 0.18),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(state == 'ready' ? '可用' : '未下载',
                    style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w700,
                        color: state == 'ready' ? PaimindTheme.ready : PaimindTheme.muted)),
              ),
            ],
          ),
          if (manifest.isNotEmpty)
            Text('磁盘 ${((manifest['disk_size_bytes'] ?? 0) / 1024 / 1024 / 1024).toStringAsFixed(1)} GB',
                style: const TextStyle(fontSize: 11, color: PaimindTheme.muted)),
          const SizedBox(height: 8),
          if (downloaded)
            OutlinedButton(
              onPressed: () async {
                await widget.state.setTier(tier);
                setState(() {});
              },
              child: Text(active ? '使用中' : '切换加载'),
            )
          else
            Text('请到桌面版下载后拷贝模型目录',
                style: const TextStyle(fontSize: 11, color: PaimindTheme.muted)),
        ],
      ),
    );
  }

  Widget _card({required String title, required Widget child}) => Card(
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(title,
                  style: const TextStyle(fontSize: 12, color: PaimindTheme.muted)),
              const SizedBox(height: 10),
              child,
            ],
          ),
        ),
      );

  Future<void> _loginHf(BuildContext context) async {
    final controller = TextEditingController();
    final token = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('登录 Hugging Face'),
        content: TextField(
          controller: controller,
          obscureText: true,
          decoration: const InputDecoration(hintText: 'Write Token'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: const Text('登录'),
          ),
        ],
      ),
    );
    if (token == null || token.isEmpty) return;
    widget.state.hfToken = token;
    await widget.state.save();
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('token 已保存到系统钥匙串')),
    );
  }

  Future<void> _uploadSingleTier(BuildContext context) async {
    if (!widget.models.isDownloaded(widget.state.currentTier)) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('当前档位尚未下载，无法上传')),
      );
      return;
    }
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('请在桌面版使用完整上传向导（含 manifest 与 SHA256SUMS）')),
    );
  }
}
