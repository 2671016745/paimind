import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

/// 运行时状态：当前档位、部署模式、隐私开关、语言。
///
/// 移动端只保留 builtin（外部引擎默认隐藏），但结构保持一致，便于与桌面版
/// 共享同一份语义。
class AppState {
  static const _kTier = 'runtime.tier';
  static const _kMode = 'runtime.mode';
  static const _kAllowOnline = 'runtime.allowOnline';
  static const _kUploadOptions = 'runtime.uploadOptions';
  static const _kLanguage = 'runtime.language';

  String currentTier = 'L0';
  String currentMode = 'builtin';
  bool allowOnline = true;
  bool uploadOptions = true;
  String language = 'zh';
  double confidenceThreshold = 0.6;
  int maxReasonLength = 20;
  String searchEndpoint = 'https://html.duckduckgo.com/html/';
  String hfToken = '';
  String hfUsername = '';
  String devOllamaUrl = '';
  List<String> onlineKeywords = const [
    '今天', '最新', '现在', '价格', '新闻', '汇率', '版本', '发布',
    'today', 'latest', 'now', 'price', 'news', 'version', 'release',
  ];

  final List<void Function()> _listeners = [];
  void onChanged(void Function() fn) => _listeners.add(fn);
  void _notify() => _listeners.forEach((fn) => fn());

  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    currentTier = prefs.getString(_kTier) ?? 'L0';
    currentMode = prefs.getString(_kMode) ?? 'builtin';
    allowOnline = prefs.getBool(_kAllowOnline) ?? true;
    uploadOptions = prefs.getBool(_kUploadOptions) ?? true;
    language = prefs.getString(_kLanguage) ?? 'zh';
    confidenceThreshold = prefs.getDouble('runtime.confidenceThreshold') ?? 0.6;
    final keywords = prefs.getStringList('runtime.onlineKeywords');
    if (keywords != null && keywords.isNotEmpty) onlineKeywords = keywords;
    hfToken = prefs.getString('runtime.hfToken') ?? '';
    hfUsername = prefs.getString('runtime.hfUsername') ?? '';
    devOllamaUrl = prefs.getString('runtime.devOllamaUrl') ?? '';
  }

  Future<void> save() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_kTier, currentTier);
    await prefs.setString(_kMode, currentMode);
    await prefs.setBool(_kAllowOnline, allowOnline);
    await prefs.setBool(_kUploadOptions, uploadOptions);
    await prefs.setString(_kLanguage, language);
    await prefs.setDouble('runtime.confidenceThreshold', confidenceThreshold);
    await prefs.setStringList('runtime.onlineKeywords', onlineKeywords);
    // token 只存系统钥匙串（flutter_secure_storage），这里只存用户名等非敏感信息
    await prefs.setString('runtime.hfUsername', hfUsername);
    await prefs.setString('runtime.devOllamaUrl', devOllamaUrl);
    _notify();
  }

  Future<void> setTier(String tier) async {
    currentTier = tier;
    await save();
  }

  Future<void> setMode(String mode) async {
    currentMode = mode;
    await save();
  }

  /// 首次联网前的隐私确认状态。
  Future<bool> hasOnlineConsent() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool('runtime.onlineConsent') ?? false;
  }

  Future<void> grantOnlineConsent() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('runtime.onlineConsent', true);
  }

  Map<String, dynamic> describe() => {
        'tier': currentTier,
        'mode': currentMode,
        'allowOnline': allowOnline,
        'uploadOptions': uploadOptions,
        'language': language,
        'confidenceThreshold': confidenceThreshold,
        'onlineKeywords': onlineKeywords,
      };

  String encode() => jsonEncode(describe());
}
