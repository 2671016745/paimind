import 'dart:convert';
import 'dart:io';

import 'app_state.dart';
import 'hardware.dart';
import 'model_store.dart';
import 'onnx_backend.dart';
import 'prompt.dart';

/// 决策结果（与桌面版 / HTTP API 的响应字段一一对应）。
class DecisionResult {
  final String selectedId;
  final String selectedText;
  final double confidence;
  final String reason;
  final String source;
  final String backend;
  final String tier;
  final int latencyMs;
  final List<Alternative> alternatives;
  final List<SourceRef> sources;

  DecisionResult({
    required this.selectedId,
    required this.selectedText,
    required this.confidence,
    required this.reason,
    required this.source,
    required this.backend,
    required this.tier,
    required this.latencyMs,
    required this.alternatives,
    required this.sources,
  });

  Map<String, dynamic> toJson() => {
        'selected': {'id': selectedId, 'text': selectedText, 'confidence': confidence},
        'reason': reason,
        'source': source,
        'backend': backend,
        'tier': tier,
        'latency_ms': latencyMs,
        'alternatives': alternatives.map((a) => a.toJson()).toList(),
        'sources': sources.map((s) => s.toJson()).toList(),
      };
}

class SourceRef {
  final String title;
  final String url;
  final String domain;
  SourceRef(this.title, this.url, this.domain);
  Map<String, dynamic> toJson() => {'title': title, 'url': url, 'domain': domain};
}

/// 手机端决策引擎。
///
/// 手机版**不暴露 HTTP API**：推理在本机 ONNX Runtime 上完成，联网求助走系统
/// HTTP 客户端。外部引擎模式默认隐藏，仅开发者选项里可手动指向局域网 Ollama。
class DecisionEngine {
  final ModelStore models;
  final HardwareProbe hardware;
  final AppState state;
  OnnxBackend? _backend;

  DecisionEngine({required this.models, required this.hardware, required this.state});

  bool get isLoaded => _backend != null;

  /// 加载当前档位；失败时返回 null（由调用方给出下载入口，不静默失败）。
  Future<bool> ensureLoaded(String tier) async {
    if (_backend != null && state.currentTier == tier) return true;
    final dir = models.tierDirectory(tier);
    if (!dir.existsSync()) {
      return false;
    }
    final backend = OnnxBackend(dir);
    final ok = await backend.load();
    if (!ok) return false;
    _backend = backend;
    return true;
  }

  Future<void> unload() async {
    await _backend?.dispose();
    _backend = null;
  }

  /// 本地决策：只输出「选中项 + 一句话理由 + 置信度 + 来源」。
  Future<DecisionResult> decide({
    required String question,
    required List<Map<String, String>> options,
    String? context,
    bool allowOnline = true,
  }) async {
    final tier = state.currentTier;
    final sw = Stopwatch()..start();
    final loaded = await ensureLoaded(tier);
    if (!loaded) {
      throw ModelNotReadyException(tier, models.tierDirectory(tier).path);
    }

    final prompt = buildDecisionPrompt(
      question: question,
      options: options,
      context: context,
      language: state.language,
      maxReasonLength: state.maxReasonLength,
    );
    final raw = await _backend!.complete(prompt, maxTokens: 256);
    var parsed = parseDecision(raw, options, state.maxReasonLength);

    // 联网求助：置信度不足或问题含时效性关键词
    var source = 'local';
    var sources = <SourceRef>[];
    if (allowOnline && state.allowOnline) {
      final trigger = needsOnline(question, parsed.confidence, state.confidenceThreshold,
          state.onlineKeywords);
      if (trigger != null) {
        final results = await _search(question, options);
        if (results.isNotEmpty) {
          final withEvidence = buildDecisionPrompt(
            question: question,
            options: options,
            context: context,
            language: state.language,
            sources: results,
            maxReasonLength: state.maxReasonLength,
          );
          final second = await _backend!.complete(withEvidence, maxTokens: 256);
          final parsed2 = parseDecision(second, options, state.maxReasonLength);
          if (parsed2.selectedId != null) {
            parsed = parsed2;
            source = 'hybrid';
            sources = results
                .map((r) => SourceRef(r['title'] ?? '', r['url'] ?? '', r['domain'] ?? ''))
                .toList();
          }
        }
      }
    }

    sw.stop();
    return DecisionResult(
      selectedId: parsed.selectedId ?? options.first['id']!,
      selectedText: parsed.selectedText,
      confidence: parsed.confidence,
      reason: parsed.reason,
      source: source,
      backend: 'cpu',
      tier: tier,
      latencyMs: sw.elapsedMilliseconds,
      alternatives: parsed.alternatives,
      sources: sources,
    );
  }

  Future<List<Map<String, String>>> _search(
      String question, List<Map<String, String>> options) async {
    // 隐私开关：默认只发送问题，不上传选项。
    final query = state.uploadOptions ? question : '$question（选项：${options.map((o) => o['text']).join(' / ')}）';
    try {
      final uri = Uri.parse(state.searchEndpoint).replace(queryParameters: {'q': query});
      final response = await HttpClient()
          .getUrl(uri)
          .timeout(const Duration(seconds: 5))
          .then((request) => request.close());
      if (response.statusCode != 200) return [];
      final body = await response.transform(utf8.decoder).join();
      return parseDuckDuckGoHtml(body);
    } catch (_) {
      // 联网失败静默回退本地，不弹错误框
      return [];
    }
  }
}

/// 档位尚未下载。
class ModelNotReadyException implements Exception {
  final String tier;
  final String path;
  ModelNotReadyException(this.tier, this.path);
  @override
  String toString() => '档位 $tier 尚未下载：$path';
}
