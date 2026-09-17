# 进销存管理系统 · 移动端 Android 构建说明

本目录是 Capacitor 7 套壳工程，用于把 `wms_app/static` 的网页版进销存封装成 Android App。

## 方案：直连 NAS（原生摄像头扫码）

- App 启动后在弹窗里输入 NAS 地址（如 `http://192.168.1.10:8000`），`MainActivity` 直接 `loadUrl` 到该 http 页面，**不打包网页本体**，避免把 176MB 的 Tesseract OCR 等资源打进 APK。
- 摄像头扫码走原生 `@capacitor/barcode-scanner` 插件（绕过 WebView 安全上下文对 http 明文页面的限制）。
- 网页层 `wms_app/static/index.html` 已加 📷 按钮：`nativeScan(id)` 检测 `Capacitor.Plugins.BarcodeScanner`，非原生环境提示用扫码枪。
- 登录页"记住用户名"已在前端通用实现（`localStorage.wms_username`，密码不落盘）。

## 环境要求

- Android SDK：本地 `C:\Users\OFFICE\AppData\Local\Android\Sdk`（含 android-34/35、build-tools 34/35）。
- **JDK 17**（本地 `C:\Program Files\Eclipse Adoptium\jdk-17.0.20.101-hotspot`）。

### ⚠️ 关于 Java 工具链

Capacitor 7 官方默认要求 **JDK 21**（`sourceCompatibility VERSION_21` / Kotlin `jvmToolchain(21)`）。
本机只有 JDK 17，因此通过 `scripts/patch-capacitor-jdk17.js`（已挂 `postinstall`）把以下 5 处降到 17：

1. `android/app/capacitor.build.gradle`
2. `android/capacitor-cordova-android-plugins/build.gradle`（gitignore，由 cap sync 生成）
3. `node_modules/@capacitor/android/capacitor/build.gradle`
4. `node_modules/@capacitor/barcode-scanner/android/build.gradle`
5. `node_modules/@capacitor/cli/dist/android/update.js`（cap sync 会据此回写 1、2）

> 若在装有 JDK 21 的机器上构建，可跳过补丁直接编译；否则务必先 `npm install`（触发 postinstall）再 `cap sync`。
> 改完 Capacitor 版本后需复查上述 5 处是否仍被还原成 21。

## 编译步骤

```bat
cd wms_app\mobile
npm install            REM 触发 postinstall 补丁
npx cap sync android   REM 同步插件与配置到 android 工程
set JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-17.0.20.101-hotspot
set ANDROID_SDK_ROOT=C:\Users\OFFICE\AppData\Local\Android\Sdk
cd android
gradlew.bat assembleDebug --no-daemon
```

产物：`android/app/build/outputs/apk/debug/app-debug.apk`
- 包名 `com.kenowong.wms`，minSdk 26（Android 8.0+），targetSdk 35。
- 已含 `CAMERA` / `INTERNET` 权限，Manifest 允许 http 明文流量。

## 已适配清单

- `capacitor.config.ts`：webDir 指向 `./web-placeholder`（极小占位页），`server.cleartext=true`、`allowNavigation:['*']`。
- `android/app/src/main/java/com/kenowong/wms/MainActivity.java`：直连 NAS、http 明文、关闭强制深色、注入 `WmsApp` 桥记住账号。
- `android/app/src/main/AndroidManifest.xml`：`CAMERA` 权限 + 明文流量放行。
- `android/variables.gradle`：`minSdkVersion=26`、`compileSdkVersion=35`、`targetSdkVersion=35`。
- `android/gradle/wrapper/gradle-wrapper.properties`：Gradle 分发改用腾讯云镜像（解决官网下载超时）。
