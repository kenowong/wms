# 进销存管理系统 · App 上架与云端部署规划

> 目标：上架 iOS（App Store）与 Android（Google Play），公开下载；后端部署到云服务器，手机随时随地外网访问。

---

## 一、账号与上架流程（你问的 Q2）

### Apple（iOS）
1. **Apple ID**：已有则跳过。
2. **加入 Apple Developer Program**：https://developer.apple.com
   - 个人（Individual）$99/年，注册最简单，适合个人/小项目
   - 组织（Organization）需邓白氏码（D-U-N-S），适合公司主体
3. **Mac 环境（三选一）**：
   - 买 Mac mini M4（一次性 ~¥4000，完全可控，推荐长期做 App）
   - 云 Mac 构建 **Codemagic**（按构建分钟计费，连 GitHub 自动出 IPA，免维护）
   - 借/用现有 Mac
4. **Apple Developer 后台创建**：App ID（bundle id，如 `com.yourcompany.wms`）、Distribution 证书、App Store 类型 Provisioning Profile
5. **App Store Connect 建 App**：名称 / 截图（iPhone 6.7"、6.5"，可选 iPad）/ 描述 / 关键词 / **隐私政策网址（必须）** / 数据收集声明
6. **打包上传**：Xcode Archive → Validate → Upload，或 Codemagic 自动构建上传
7. **审核（约 24–48h）常见驳回与应对**：
   - 4.2 功能单薄 → 用原生扫码 + 离线缓存 + 实在业务功能证明价值
   - 5.1.1 隐私 → 提供隐私政策 + 权限用途说明

### Google Play（Android）
1. **Google Play Console** $25 一次性
2. **准备 AAB**（我编译，需 Android SDK；或你本地 / Codemagic 构建）
3. **建应用**：名称 / 截图 / 描述 / **隐私政策网址（必须）** / Data Safety 表单
4. **轨道**：内部测试 → 封闭测试 → 公开，上传 AAB，审核几小时~几天

---

## 二、云端部署（Q1：上云服务器）

- **服务器**：腾讯云轻量应用服务器 / 阿里云 ECS，2核2G Ubuntu 22.04，约 ¥70–100/月（年付更省）
- **域名**：买一个域名（¥几十/年），解析到服务器公网 IP
- **HTTPS**：用 **Caddy** 反代 + Let's Encrypt 自动证书（零配置），转发到 FastAPI 容器
- **数据库**：SQLite 开 **WAL** 模式；数据卷挂云盘 + 每日备份
- **我交付**：云版 `docker-compose.yml`（含 Caddy）、WAL 开启、备份脚本、部署文档
- **你需**：买服务器 + 域名、开放 22/80/443、按步骤跑（或给我 SSH 我远程执行）

---

## 三、App 工程方案：Capacitor 套壳（复用现有网页）

- **阶段1 前端改造**：API 地址**可配置**（不再依赖 `location.host`，否则 App 里会指向手机自身）；加 PWA（manifest + service worker，离线缓存 + 可安装）
- **阶段2 Capacitor 工程**：Android 工程可构建、iOS 工程待 Mac 构建
- **安卓 AAB 实际编译**需 Android SDK（本机可能未装）；届时装 SDK 或你本地 / Codemagic 构建
- **增强原生能力**：接入 `@capacitor/barcode-scanner`（你已有扫码业务，正好满足苹果"要有原生功能"的审核要求，降低 4.2 驳回风险）

---

## 四、你需要做的（外部依赖，我无法代劳）

- [ ] 买云服务器 + 域名，开放 22/80/443 端口
- [ ] 注册 Apple Developer（$99/年）+ 准备 Mac 或 Codemagic 账号
- [ ] 准备隐私政策（我可给模板）
- [ ] 注册 Google Play（$25）
- [ ] **提供真实域名**（我把它写进 App 配置，替换占位 `https://your-domain.example.com`）

---

## 五、当前进度

- [x] 方案规划（本文件）
- [x] 阶段1：前端 API 可配置 + PWA 化（已验证 /manifest、/icon、首页关联均 200/True）
- [x] 阶段2：Capacitor 工程脚手架（package.json + capacitor.config.ts + README，npm install 通过；cap add 待你本地/云 Mac 执行）
- [ ] 云端部署文件：docker-compose + Caddy HTTPS + WAL + 备份
- [ ] 编译 Android AAB
- [ ] iOS 构建与上架（需 Mac/云Mac + Apple 账号）
- [ ] 隐私政策模板 + 商店素材清单
