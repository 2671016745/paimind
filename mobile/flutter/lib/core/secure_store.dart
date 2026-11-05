import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// 密钥存储：iOS Keychain / Android Keystore。
///
/// 与桌面端 keyring 等价 —— token 只进系统钥匙串，绝不写入配置文件或日志。
class SecureStore {
  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
    iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock),
  );

  static const _kHfToken = 'hf_token';

  static Future<String?> readToken() => _storage.read(key: _kHfToken);

  static Future<void> writeToken(String token) =>
      token.isEmpty ? _storage.delete(key: _kHfToken) : _storage.write(key: _kHfToken, value: token);

  static Future<void> deleteToken() => _storage.delete(key: _kHfToken);
}
