import 'dart:io';

import 'package:flutter/services.dart';

/// 硬件检测（移动端口径）。
///
/// `Abi` 只在部分 Dart SDK 的 `dart:io` 中可用，这里退回 `Platform` 信息，
/// 保证在旧 SDK 上也能通过分析。
/// 关键差异：移动端**必须**用系统 available 接口，不能用总内存：
///   * Android — `ActivityManager.MemoryInfo.availMem`
///   * iOS     — `os_proc_available_memory()`
/// 这里通过 MethodChannel 向原生侧取真实值，取不到时退回保守估计。
class HardwareProbe {
  static const MethodChannel _channel = MethodChannel('paimind/hardware');

  bool probed = false;
  int totalBytes = 0;
  int availableBytes = 0;
  String arch = '';
  int cores = 1;
  List<String> abis = const [];
  String? gpuName;
  bool appleSilicon = false;

  double get availableGb => availableBytes / 1024 / 1024 / 1024;
  double get totalGb => totalBytes / 1024 / 1024 / 1024;

  Future<void> probe() async {
    try {
      final Map<dynamic, dynamic> info =
          (await _channel.invokeMethod('getHardware'))?.cast<String, dynamic>() ?? {};
      totalBytes = (info['totalBytes'] as num?)?.toInt() ?? totalBytes;
      availableBytes = (info['availableBytes'] as num?)?.toInt() ?? availableBytes;
      arch = (info['arch'] as String?) ?? '';
      cores = (info['cores'] as num?)?.toInt() ?? 1;
      abis = (info['abis'] as List?)?.cast<String>() ?? const [];
      gpuName = info['gpuName'] as String?;
      appleSilicon = (info['appleSilicon'] as bool?) ?? false;
    } on PlatformException {
      // 原生侧不可用时退回 platform 信息，保证 UI 仍能渲染（不阻塞启动）
      arch = Platform.operatingSystem;
      cores = Platform.numberOfProcessors;
    } on MissingPluginException {
      arch = Platform.operatingSystem;
      cores = Platform.numberOfProcessors;
    }
    probed = true;
  }

  String describe() {
    final gpu = gpuName ?? '未知 GPU';
    return '可用 ${availableGb.toStringAsFixed(1)} GB · $cores 核 · $arch · $gpu';
  }
}

/// Android 侧在 MainActivity 里注册该 handler（见 android/app 说明）。
class HardwareChannel {
  static const MethodChannel channel = MethodChannel('paimind/hardware');
}

typedef HardwareCallback = Future<Map<String, dynamic>> Function();
