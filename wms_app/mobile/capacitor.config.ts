import { CapacitorConfig } from '@capacitor/cli';

// 进销存管理系统 移动端配置（Capacitor 直连 NAS 模式）
// 采用「直连 NAS」方案：App 启动后由用户在弹窗里输入 NAS 地址，MainActivity 直接 loadUrl 到该 http 页面，
// 摄像头扫码走原生 @capacitor/barcode-scanner 插件（不受 WebView 安全上下文限制）。
// 因此 webDir 仅放一个极小占位页（App 启动即被 loadUrl 覆盖），避免把 176MB 的 Tesseract OCR 等打进 APK。
// 改前端网页逻辑（wms_app/static/index.html）后，只需重新 cap sync + 编译，无需改动 webDir。
const config: CapacitorConfig = {
  appId: 'com.kenowong.wms',         // 反向域名（Apple/Google 上架必需）
  appName: '进销存管理系统',
  webDir: './web-placeholder',
  server: {
    // 允许直连 NAS 的 http://IP:端口（明文），手机摄像头扫码走原生插件，不受 WebView 安全上下文限制
    cleartext: true,
    // 放行 WebView 导航到任意地址（NAS 地址由用户在 App 内输入，无法预先枚举）
    allowNavigation: ['*']
  }
};

export default config;
