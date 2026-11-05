/// prompt 构造与模型输出解析（移动端与桌面端同语义）。
///
/// 与桌面端保持一致的输出契约：严格 JSON，解析时容忍 markdown 围栏、缺失字段
/// 与无效选项 id。

import 'dart:convert';

class ParsedDecision {
  final String? selectedId;
  final String selectedText;
  final String reason;
  final double confidence;
  final List<Alternative> alternatives;

  ParsedDecision({
    required this.selectedId,
    required this.selectedText,
    required this.reason,
    required this.confidence,
    required this.alternatives,
  });
}

class Alternative {
  final String id;
  final String text;
  final double score;
  Alternative(this.id, this.text, this.score);
  Map<String, dynamic> toJson() => {'id': id, 'text': text, 'score': score};
}

/// 联网触发原因：返回 null 表示不需要联网。
String? needsOnline(
  String question,
  double confidence,
  double threshold,
  List<String> keywords,
) {
  if (confidence < threshold) return '置信度不足';
  final lower = question.toLowerCase();
  for (final kw in keywords) {
    if (lower.contains(kw.toLowerCase())) return '时效性问题';
  }
  return null;
}

/// 隐私开关：默认只发问题，不发选项。
String buildSearchQuery(String question, bool uploadOptions, List<String> optionTexts) {
  if (!uploadOptions) return question;
  return '$question（选项：${optionTexts.join(' / ')}）';
}

String buildDecisionPrompt({
  required String question,
  required List<Map<String, String>> options,
  String? context,
  required String language,
  List<Map<String, String>>? sources,
  required int maxReasonLength,
}) {
  final zh = language.startsWith('zh');
  final buffer = StringBuffer();
  buffer.writeln(zh ? '你是一个决策助手。只输出 JSON。' : 'You are a decision assistant. Output JSON only.');
  buffer.writeln(zh ? '问题：$question' : 'Question: $question');
  if (context != null && context.isNotEmpty) {
    buffer.writeln(zh ? '背景：$context' : 'Context: $context');
  }
  if (sources != null && sources.isNotEmpty) {
    buffer.writeln(zh ? '参考资料：' : 'References:');
    for (final s in sources) {
      buffer.writeln('  - ${s['title'] ?? ''} (${s['domain'] ?? ''}) ${s['snippet'] ?? ''}');
    }
  }
  buffer.writeln(zh ? '选项：' : 'Options:');
  for (var i = 0; i < options.length; i++) {
    buffer.writeln('${i + 1}. [${options[i]['id']}] ${options[i]['text']}');
  }
  buffer.writeln(zh
      ? '只输出 JSON：{"selected":"<id>","reason":"<≤$maxReasonLength 字>","confidence":0.85,"alternatives":[{"id":"<id>","score":0.7}]}'
      : 'Output only JSON: {"selected":"<id>","reason":"<≤$maxReasonLength chars>","confidence":0.85,"alternatives":[{"id":"<id>","score":0.7}]}');
  return buffer.toString();
}

/// 从 DuckDuckGo HTML 里抽出 top N 标题 + 摘要。
List<Map<String, String>> parseDuckDuckGoHtml(String html) {
  final results = <Map<String, String>>[];
  final linkRe = RegExp(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', dotAll: true);
  final snippetRe = RegExp(r'class="result__snippet"[^>]*>(.*?)</a>', dotAll: true);
  final links = linkRe.allMatches(html).take(3).toList();
  final snippets = snippetRe.allMatches(html).take(3).toList();
  for (var i = 0; i < links.length; i++) {
    final href = links[i].group(1) ?? '';
    final title = (links[i].group(2) ?? '').replaceAll(RegExp(r'<[^>]+>'), '').trim();
    final snippet = i < snippets.length
        ? (snippets[i].group(1) ?? '').replaceAll(RegExp(r'<[^>]+>'), '').trim()
        : '';
    final uri = Uri.tryParse(_unwrapRedirect(href));
    results.add({
      'title': title,
      'url': uri?.toString() ?? href,
      'snippet': snippet,
      'domain': uri?.host ?? '',
    });
  }
  return results;
}

String _unwrapRedirect(String href) {
  if (href.startsWith('//')) href = 'https:$href';
  final match = RegExp(r'[?&]uddg=([^&]+)').firstMatch(href);
  if (match != null) {
    return Uri.decodeComponent(match.group(1)!);
  }
  return href;
}

/// 容错解析：先找 {...}，再退化为键值扫描。
ParsedDecision parseDecision(String text, List<Map<String, String>> options, int maxReasonLength) {
  final validIds = options.map((o) => o['id']!).toSet();
  final idToText = {for (final o in options) o['id']!: o['text']!};

  Map<String, dynamic>? payload = _tryJson(text);
  payload ??= _permissiveScan(text);

  String? selected = payload?['selected'] as String?;
  var reason = (payload?['reason'] as String?) ?? '';
  var confidence = _asDouble(payload?['confidence'], 0.0);

  final alternatives = <Alternative>[];
  final rawAlts = payload?['alternatives'];
  if (rawAlts is List) {
    for (final item in rawAlts) {
      if (item is Map && validIds.contains(item['id'])) {
        alternatives.add(Alternative(
          item['id'] as String,
          idToText[item['id']] ?? '',
          _asDouble(item['score'] ?? item['confidence'], 0.0),
        ));
      }
    }
  }

  if (selected == null || !validIds.contains(selected)) {
    if (alternatives.isNotEmpty) {
      alternatives.sort((a, b) => b.score.compareTo(a.score));
      selected = alternatives.first.id;
      if (confidence <= 0) confidence = alternatives.first.score;
    } else {
      selected = options.first['id'];
      confidence = confidence.clamp(0.0, 0.3);
    }
  }

  if (reason.length > maxReasonLength) reason = reason.substring(0, maxReasonLength);

  return ParsedDecision(
    selectedId: selected,
    selectedText: idToText[selected] ?? '',
    reason: reason,
    confidence: confidence.clamp(0.0, 1.0),
    alternatives: alternatives,
  );
}

Map<String, dynamic>? _tryJson(String text) {
  final match = RegExp(r'\{.*\}', dotAll: true).firstMatch(text);
  if (match == null) return null;
  try {
    final decoded = _jsonDecode(match.group(0)!);
    return decoded is Map<String, dynamic> ? decoded : null;
  } catch (_) {
    return null;
  }
}

Map<String, dynamic>? _permissiveScan(String text) {
  final out = <String, dynamic>{};
  for (final key in ['selected', 'choice', 'reason', 'explanation']) {
    final m = RegExp('"$key"\\s*:\\s*"([^"]*)"').firstMatch(text);
    if (m != null) out[key] = m.group(1);
  }
  final c = RegExp(r'"confidence"\s*:\s*(-?\d+(?:\.\d+)?)').firstMatch(text);
  if (c != null) out['confidence'] = c.group(1);
  return out.isEmpty ? null : out;
}

dynamic _jsonDecode(String source) {
  // ignore: avoid_dynamic_calls
  return const JsonCodec().decode(source);
}

double _asDouble(dynamic value, double fallback) {
  if (value is num) return value.toDouble();
  if (value is String) return double.tryParse(value) ?? fallback;
  return fallback;
}
