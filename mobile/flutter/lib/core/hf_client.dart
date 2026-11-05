import 'dart:convert';
import 'dart:io';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:path_provider/path_provider.dart';

/// 手机端 Hugging Face 客户端（简化版：登录 + 单档上传）。
///
/// 完整的多档上传向导、manifest 生成与「设为下载源」闭环在桌面端；手机端只做
/// 最小可用路径。上传一律走 huggingface.co 官方 API，镜像不参与上传。
class HfClient {
  static const _storage = FlutterSecureStorage();
  static const _kToken = 'hf_token';
  static const _api = 'https://huggingface.co/api';

  static Future<String?> _token() => _storage.read(key: _kToken);

  static Future<bool> isLoggedIn() async => (await _token())?.isNotEmpty ?? false;

  /// 校验 token 并存入钥匙串；返回用户名。
  static Future<String> login(String token) async {
    final response = await HttpClient()
        .getUrl(Uri.parse('$_api/whoami-v2'))
        .then((r) {
      r.headers.set('Authorization', 'Bearer $token');
      return r.close();
    });
    if (response.statusCode == 401 || response.statusCode == 403) {
      throw Exception('token 无效或缺少 write 权限');
    }
    final body = jsonDecode(await response.transform(utf8.decoder).join());
    await _storage.write(key: _kToken, value: token);
    return (body['name'] ?? '') as String;
  }

  static Future<void> logout() => _storage.delete(key: _kToken);

  /// 上传单个档位：主模型 + tokenizer + manifest.json + SHA256SUMS。
  static Future<String> uploadTier(String tier, String repoId,
      {void Function(double progress)? onProgress}) async {
    final token = await _token();
    if (token == null) throw Exception('未登录 Hugging Face');

    final docs = await getApplicationDocumentsDirectory();
    final dir = Directory('${docs.path}/models/$tier');
    if (!dir.existsSync()) throw Exception('档位 $tier 尚未下载');

    final files = dir.listSync().whereType<File>().toList();
    if (files.isEmpty) throw Exception('档位 $tier 目录为空');

    var done = 0;
    String lastUrl = '';
    for (final file in files) {
      final relative = file.path.split(Platform.pathSeparator).last;
      final request = await HttpClient()
          .postUrl(Uri.parse('$_api/models/$repoId/upload/main/$relative'));
      request.headers.set('Authorization', 'Bearer $token');
      request.headers.set('Content-Type', 'application/octet-stream');
      final bytes = await file.readAsBytes();
      request.add(bytes);
      final response = await request.close();
      if (response.statusCode >= 400) {
        throw Exception('上传 $relative 失败：HTTP ${response.statusCode}');
      }
      lastUrl = response.headers.value('location') ?? lastUrl;
      done++;
      onProgress?.call(done / files.length);
    }
    return 'https://huggingface.co/$repoId';
  }
}
