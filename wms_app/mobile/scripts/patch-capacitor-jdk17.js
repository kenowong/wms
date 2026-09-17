/*
 * patch-capacitor-jdk17.js
 * 本机只有 JDK 17，而 Capacitor 7 默认要求 JDK 21 工具链，会导致 assembleDebug 编译失败。
 * 在 npm install 之后把几处 Java 工具链要求从 21 降到 17，使本地可用现有 JDK 17 编译。
 * 仅做字符串替换，对已是 17 的文件无副作用；插件版本变化时若不再含 VERSION_21 则自动跳过。
 */
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');

const targets = [
  // 插件源码（编译进 APK）
  'node_modules/@capacitor/android/capacitor/build.gradle',
  'node_modules/@capacitor/barcode-scanner/android/build.gradle',
  // CLI 模板（cap sync 会据此回写 android/app/capacitor.build.gradle 与 capacitor-cordova-android-plugins/build.gradle）
  'node_modules/@capacitor/cli/dist/android/update.js',
];

let changed = 0;
for (const rel of targets) {
  const p = path.join(root, rel);
  if (!fs.existsSync(p)) {
    console.log('[patch] skip (not found):', rel);
    continue;
  }
  let s = fs.readFileSync(p, 'utf8');
  const before = s;
  s = s.replace(/VERSION_21/g, 'VERSION_17');
  s = s.replace(/jvmToolchain\(21\)/g, 'jvmToolchain(17)');
  if (s !== before) {
    fs.writeFileSync(p, s);
    changed++;
    console.log('[patch] patched:', rel);
  } else {
    console.log('[patch] no-change:', rel);
  }
}
console.log('[patch] done, files changed:', changed);
