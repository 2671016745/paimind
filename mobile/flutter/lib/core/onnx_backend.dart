import 'dart:io';
import 'dart:typed_data';

import 'package:onnxruntime/onnxruntime.dart';

/// 手机端的内置 ONNX Runtime 推理封装。
///
/// 与桌面端同一套 INT4 ONNX 图、同一个 prompt；这里只做最小必要的封装：
/// 优先 NNAPI / CoreML 执行提供者，回退 CPU。模型缺失时返回「未就绪」而不是
/// 静默失败。
class OnnxBackend {
  final Directory directory;
  OrtSession? _session;
  bool _loaded = false;

  OnnxBackend(this.directory);

  bool get isLoaded => _loaded;

  Future<bool> load() async {
    try {
      OrtEnv.instance.init();
      final onnx = directory
          .listSync()
          .map((f) => f.path)
          .where((p) => p.toLowerCase().endsWith('.onnx'))
          .toList();
      if (onnx.isEmpty) return false;

      final options = OrtSessionOptions();
      // 移动端优先硬件加速：Android NNAPI，iOS CoreML，最后 CPU。
      try {
        final providers = OrtEnv.instance.availableProviders();
        if (providers.contains(OrtProvider.nnapi)) {
          options.appendNnapiProvider(NnapiFlags.useNone);
        } else if (providers.contains(OrtProvider.coreml)) {
          options.appendCoreMLProvider(CoreMLFlags.useNone);
        }
      } catch (_) {
        // 提供者不可用时保持默认 CPU
      }

      _session = OrtSession.fromFile(File(onnx.first), options);
      options.release();
      _loaded = true;
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<String> complete(String prompt, {int maxTokens = 256}) async {
    final session = _session;
    if (session == null) {
      throw StateError('ONNX 会话未加载：${directory.path}');
    }
    final ids = _encode(prompt);
    var input = OrtValueTensor.createTensorWithDataList(
        Int64List.fromList(ids), [1, ids.length]);
    var attention = OrtValueTensor.createTensorWithDataList(
        Int64List.fromList(List<int>.filled(ids.length, 1)), [1, ids.length]);
    final runOptions = OrtRunOptions();
    final inputs = <String, OrtValue>{
      'input_ids': input,
      'attention_mask': attention,
    };
    const outputNames = ['logits'];

    final buffer = StringBuffer();
    try {
      for (var i = 0; i < maxTokens; i++) {
        final outputs = session.run(runOptions, inputs, outputNames);
        final logits = outputs.first;
        if (logits == null) break;
        final next = _argmax(logits);
        if (next == _eosId) break;
        buffer.write(_decode([next]));

        // 释放上一轮的输出与张量，避免 native 内存累积。
        _releaseAll(outputs);
        input.release();
        attention.release();

        input = OrtValueTensor.createTensorWithDataList(
            Int64List.fromList([next]), [1, 1]);
        attention = OrtValueTensor.createTensorWithDataList(
            Int64List.fromList([1]), [1, 1]);
        inputs['input_ids'] = input;
        inputs['attention_mask'] = attention;
      }
    } finally {
      input.release();
      attention.release();
      runOptions.release();
    }
    return buffer.toString();
  }

  /// `OrtValue` 基类没有公开的 `release()`，只有具体子类有。
  static void _releaseAll(List<OrtValue?> values) {
    for (final v in values) {
      try {
        // ignore: avoid_dynamic_calls
        (v as dynamic).release();
      } catch (_) {
        // 非张量输出（sequence/map）释放方式不同，忽略即可
      }
    }
  }

  List<int> _encode(String text) {
    final bytes = text.codeUnits;
    // 基础分词：按字符/字节映射，保证无词表时仍可运行（质量低于真实 BPE）
    return bytes.take(1024).toList();
  }

  String _decode(List<int> ids) => String.fromCharCodes(ids.where((i) => i >= 0 && i < 65536));

  int _argmax(OrtValue tensor) {
    final data = tensor.value;
    if (data is! List || data.isEmpty) return 0;
    final row = data.last;
    if (row is! List || row.isEmpty) return 0;
    var best = 0;
    for (var i = 1; i < row.length; i++) {
      final a = row[i] is num ? (row[i] as num).toDouble() : 0.0;
      final b = row[best] is num ? (row[best] as num).toDouble() : 0.0;
      if (a > b) best = i;
    }
    return best;
  }

  int get _eosId => 2;

  Future<void> dispose() async {
    _session?.release();
    _session = null;
    _loaded = false;
  }
}
