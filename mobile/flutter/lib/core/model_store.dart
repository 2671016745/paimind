import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

/// 已下载档位的磁盘清单。
///
/// 手机端复用桌面端的 `models/<tier>/` 布局与 manifest.json 结构，因此同一个
/// 模型目录可以在桌面与手机之间直接拷贝使用。
class ModelStore {
  static const tiers = ['L0', 'L1', 'L2', 'L3'];

  Directory? _root;

  Future<Directory> root() async {
    if (_root != null) return _root!;
    final docs = await getApplicationDocumentsDirectory();
    final dir = Directory('${docs.path}/models');
    if (!dir.existsSync()) dir.createSync(recursive: true);
    _root = dir;
    return dir;
  }

  Directory tierDirectory(String tier) {
    final base = _root;
    if (base == null) {
      throw StateError('root() 必须先被调用');
    }
    return Directory('${base.path}/$tier');
  }

  Future<void> load() async {
    await root();
  }

  /// 一个档位「已下载」= 目录里至少有一个 .onnx 图文件。
  bool isDownloaded(String tier) {
    final base = _root;
    if (base == null) return false;
    final dir = Directory('${base.path}/$tier');
    if (!dir.existsSync()) return false;
    return dir.listSync().any((f) => f.path.toLowerCase().endsWith('.onnx'));
  }

  List<String> filesOf(String tier) {
    final base = _root;
    if (base == null) return const [];
    final dir = Directory('${base.path}/$tier');
    if (!dir.existsSync()) return const [];
    return dir.listSync().map((f) => f.path.split(Platform.pathSeparator).last).toList();
  }

  /// 从 manifest.json 读该档位的元信息（参数、磁盘、峰值内存、最低要求）。
  Map<String, dynamic> manifestOf(String tier) {
    final base = _root;
    if (base == null) return const {};
    final file = File('${base.path}/$tier/manifest.json');
    if (!file.existsSync()) return const {};
    try {
      return (jsonDecode(file.readAsStringSync()) as Map).cast<String, dynamic>();
    } catch (_) {
      return const {};
    }
  }

  int sizeOf(String tier) {
    final base = _root;
    if (base == null) return 0;
    final dir = Directory('${base.path}/$tier');
    if (!dir.existsSync()) return 0;
    var total = 0;
    for (final f in dir.listSync(recursive: true)) {
      if (f is File) total += f.lengthSync();
    }
    return total;
  }

  /// 自定义导入的 GGUF/ONNX（高级功能，默认折叠）。
  List<Map<String, dynamic>> customModels() {
    final base = _root;
    if (base == null) return const [];
    final dir = Directory('${base.path}/custom');
    if (!dir.existsSync()) return const [];
    return dir
        .listSync()
        .where((f) => f.path.toLowerCase().endsWith('.gguf') ||
            f.path.toLowerCase().endsWith('.onnx'))
        .map((f) => {
              'name': f.path.split(Platform.pathSeparator).last,
              'path': f.path,
              'sizeBytes': f.statSync().size,
            })
        .toList();
  }
}
