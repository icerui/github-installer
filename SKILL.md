---
name: github-installer
description: Helps you easily install GitHub projects - detects OS/arch/GPU, parses README, executes steps, fixes errors. Works without Claude via local models or rule-based fallback.
version: 1.0.0
author: icerui
tags: [install, github, automation, cross-platform, tools, devtools]
homepage: https://github.com/icerui/github-installer
---

# 🚀 GitHub Installer

让你轻松安装 GitHub 项目。  
Helps you easily install GitHub projects.

## 触发方式（中英文均可）

以下任意表达都会触发本 Skill：

- "帮我装 ComfyUI"
- "安装 stable-diffusion-webui"
- "我想用 Ollama，帮我装上"
- "install oobabooga/text-generation-webui"
- "https://github.com/AUTOMATIC1111/stable-diffusion-webui 帮我安装"
- "怎么装 Open WebUI？直接帮我装"

## 执行流程（编排逻辑）

```
Step 1: 解析用户输入 → 提取 GitHub owner/repo 或 URL
Step 2: python {baseDir}/tools/main.py detect
        → 获取当前系统环境（OS/架构/GPU/已有工具）
Step 3: python {baseDir}/tools/main.py fetch <owner/repo>
        → 抓取 README，提取依赖声明和安装方法
Step 4: python {baseDir}/tools/main.py plan <owner/repo>
        → 生成适配当前环境的安装计划（JSON）
Step 5: ⚠️ 向用户展示完整安装步骤，请求确认
Step 6: 用户确认后 → python {baseDir}/tools/main.py install <owner/repo>
        → 逐步执行，实时输出，自动捕获并修复报错
Step 7: 安装完成 → 告知启动命令和使用说明
```

## 工具调用示例

```bash
# 检测当前环境
python {baseDir}/tools/main.py detect

# 获取项目信息
python {baseDir}/tools/main.py fetch comfyanonymous/ComfyUI

# 生成安装计划（不执行）
python {baseDir}/tools/main.py plan comfyanonymous/ComfyUI

# 执行安装（在用户确认后调用）
python {baseDir}/tools/main.py install comfyanonymous/ComfyUI

# 指定安装目录
python {baseDir}/tools/main.py install comfyanonymous/ComfyUI --dir ~/AI/ComfyUI

# 强制使用指定 LLM
python {baseDir}/tools/main.py install comfyanonymous/ComfyUI --llm ollama

# 无 LLM 规则模式（最快，无需 API）
python {baseDir}/tools/main.py install comfyanonymous/ComfyUI --llm none

# 依赖安全审计
python {baseDir}/tools/main.py audit comfyanonymous/ComfyUI

# 许可证兼容性检查
python {baseDir}/tools/main.py license comfyanonymous/ComfyUI

# 查看已安装项目
python {baseDir}/tools/main.py updates list

# 检查更新
python {baseDir}/tools/main.py updates check

# 安全卸载
python {baseDir}/tools/main.py uninstall comfyanonymous/ComfyUI --confirm

# 系统诊断
python {baseDir}/tools/main.py doctor

# 查看/搜索 Skills
python {baseDir}/tools/main.py skills list
python {baseDir}/tools/main.py skills search python

# 交互式引导
python {baseDir}/tools/main.py onboard

# 配置管理
python {baseDir}/tools/main.py config show
python {baseDir}/tools/main.py config set llm_provider ollama

# 多平台安设
python {baseDir}/tools/main.py platforms

# 启动 Web 界面
python {baseDir}/tools/main.py web
```

## 安装流程集成

安装过程中自动执行以下检查：
1. **依赖安全审计**：扫描 CVE 漏洞、typosquatting、恶意包
2. **许可证检查**：分析开源协议兼容性和传染性风险
3. **Skills 匹配**：自动查找社区安装策略
4. **安装记录**：成功后记录到 InstallTracker（供 updates/uninstall 使用）

## 安全原则（必须遵守）

1. **确认优先**：永远在执行前向用户展示完整步骤
2. **最小权限**：不主动使用 sudo/管理员权限，除非项目明确要求
3. **命令过滤**：自动拒绝 `rm -rf /`、`format`、`dd if=` 等破坏性命令
4. **网络限制**：只访问 GitHub、PyPI、npm、brew、apt 等可信来源
5. **路径安全**：所有操作限定在用户目录（`~`）内，不修改系统目录

## 输出格式

```
✅ 安装完成！

项目：ComfyUI
位置：~/ComfyUI
启动：cd ~/ComfyUI && python main.py --listen

提示：首次启动会下载模型，需要等待约 10 分钟
```

## 依赖

- Python 3.8+（几乎所有系统都有）
- 无需额外安装其他库（使用 Python 标准库）
- LLM 可选：有 API Key 用云端，有 Ollama/LM Studio 用本地，都没有用规则模式
