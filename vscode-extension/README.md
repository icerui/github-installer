# GitInstall VS Code Extension

让你轻松安装 GitHub/HuggingFace 项目的 VS Code 插件。

## 功能

- **Quick Install**: 命令面板输入 URL 即可安装
- **剪贴板安装**: 自动识别剪贴板中的 GitHub URL
- **安全审计**: CVE 漏洞扫描、恶意包检测、SBOM 生成
- **AI 模型 VRAM 评估**: 70+ 模型数据库，量化方案推荐
- **CI/CD 配置生成**: GitHub Actions / GitLab CI
- **企业功能**: SSO / RBAC / 审计日志
- **15 级 LLM 降级**: 从 Claude 4 到本地 Ollama 到纯规则

## 安装

```bash
cd vscode-extension
npm install
npm run compile
```

## 开发

```bash
npm run watch
# F5 在 VS Code 中调试
```

## 打包发布

```bash
npm run package
# 会生成 gitinstall-1.0.0.vsix
```

## 命令

| 命令 | 说明 |
|------|------|
| `GitInstall: Install GitHub Project` | 安装 GitHub 项目 |
| `GitInstall: Install from Clipboard URL` | 从剪贴板安装 |
| `GitInstall: Security Audit Current Project` | 安全审计 |
| `GitInstall: Estimate VRAM for AI Model` | VRAM 评估 |
| `GitInstall: Generate SBOM` | 生成软件物料清单 |
| `GitInstall: Generate CI/CD Config` | 生成 CI 配置 |
| `GitInstall: Show Dashboard` | 打开仪表板 |

## 配置

| 设置 | 说明 | 默认值 |
|------|------|--------|
| `gitinstall.pythonPath` | Python 路径 | `python3` |
| `gitinstall.installDirectory` | 默认安装目录 | `~` |
| `gitinstall.autoAudit` | 安装前自动审计 | `true` |
| `gitinstall.llmProvider` | LLM 提供商 | `auto` |
| `gitinstall.confirmBeforeExecute` | 执行前确认 | `true` |
| `gitinstall.mirrorPypi` | PyPI 镜像 | `` |
| `gitinstall.mirrorNpm` | npm 镜像 | `` |

## URI 协议

支持 `vscode://gitinstall.gitinstall/install?repo=owner/repo` 协议。

点击 README 中的 Install 按钮即可直接在 VS Code 中安装。
