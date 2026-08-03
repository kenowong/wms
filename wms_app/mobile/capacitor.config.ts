import { CapacitorConfig } from '@capacitor/cli';

// 进销存管理系统 移动端配置
// webDir 指向桌面端同一套网页（wms_app/static），因此改前端后只需 cap sync 即可同步到 App
const config: CapacitorConfig = {
  appId: 'com.yourcompany.wms',      // ⚠️ 上架前改成你自己的反向域名（Apple/Google 必需）
  appName: '进销存管理系统',
  webDir: '../wms_app/static',
  server: {
    // Capacitor Android 用 https scheme，避免明文与混合内容限制
    androidScheme: 'https'
  }
};

export default config;
