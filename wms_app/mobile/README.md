# 进销存管理系统 · 移动端（Capacitor）

把现有的网页（`../wms_app/static/index.html`）套一层原生壳，打包成 iOS / Android App。
前端代码与桌面端**完全复用**，改完网页后只需 `npx cap sync` 即可同步进原生工程。

## 前置
- Node.js ≥ 18（本机已装 v24）
- Android 构建需 **Android Studio + SDK**（Windows 可编译 AAB）
- iOS 构建需 **macOS + Xcode + Apple 开发者账号**（iOS 那步必须在 Mac 上做）

## 第一步：安装依赖
```bash
cd mobile
npm install
```

## 第二步：添加原生平台（只需一次）
```bash
npx cap add android      # 需要 Android SDK
npx cap add ios          # 需要 macOS + Xcode
```

## 第三步：把网页同步进原生工程
> 每次修改 `wms_app/static` 后执行：
```bash
npx cap sync
```

## 第四步：配置后端地址（重要）
打开 `wms_app/static/index.html`，把这一行的占位域名改成你的真实服务器：
```js
window.APP_CONFIG = window.APP_CONFIG || { API_BASE: 'https://your-domain.example.com' };
```
改成例如 `https://wms.your-company.com`（必须 https，且后端已开启 CORS）。

## 第五步：构建安装包
### Android（本机可完成）
```bash
npx cap open android     # 用 Android Studio 打开
# Build → Generate Signed Bundle / APK → 选 Android App Bundle(AAB) → 上传 Google Play
```
### iOS（需在 Mac 上）
```bash
npx cap open ios         # 用 Xcode 打开
# 选签名团队 → Product → Archive → 上传 App Store
```

## 增强原生能力（利于过苹果 4.2 审核）
已引入 `@capacitor/barcode-scanner`，可在原生层调用相机扫码，比纯网页扫码更稳、更像原生 App。
如需启用，在前端用 `import { BarcodeScanner } from '@capacitor/barcode-scanner'` 替换现有扫码实现（当前网页扫码在浏览器内已可用）。

## 上架清单
- [ ] 改 `capacitor.config.ts` 的 `appId`
- [ ] 替换 `index.html` 中的 `API_BASE` 为真实域名
- [ ] Apple Developer 账号 + 隐私政策网址
- [ ] Google Play 账号 + 隐私政策网址
- [ ] App 图标（在 android/app/src/main/res 与 iOS Assets.xcassets 替换默认图标）
