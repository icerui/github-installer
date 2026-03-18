"""
planner_known_projects.py - 已知热门项目安装数据库
=====================================================

从 planner.py 拆分出来的已知项目数据库。
新增项目时只需编辑此文件，不影响核心逻辑。
"""

from __future__ import annotations

# ─────────────────────────────────────────────
#  AI 项目硬件需求数据库
#
#  每个 AI 项目标注：
#    category     → 项目类别
#    min_vram_gb  → 最低 GPU 显存需求
#    rec_vram_gb  → 推荐 GPU 显存
#    gpu_required → 是否必须 GPU
#    gpu_backends → 支持的 GPU 后端列表
#    disk_gb      → 预估磁盘空间需求
#    ram_gb       → 最低内存需求
# ─────────────────────────────────────────────

_AI_HARDWARE_REQS: dict[str, dict] = {
    "ollama/ollama": {
        "category": "llm_inference",
        "min_vram_gb": 0,      # CPU 可跑小模型
        "rec_vram_gb": 8,
        "gpu_required": False,
        "gpu_backends": ["cuda", "mps", "rocm", "cpu"],
        "disk_gb": 5,          # 基础安装
        "ram_gb": 8,
    },
    "ggerganov/llama.cpp": {
        "category": "llm_inference",
        "min_vram_gb": 0,
        "rec_vram_gb": 8,
        "gpu_required": False,
        "gpu_backends": ["cuda", "metal", "rocm", "vulkan", "cpu"],
        "disk_gb": 2,
        "ram_gb": 8,
    },
    "comfyanonymous/comfyui": {
        "category": "image_gen",
        "min_vram_gb": 4,
        "rec_vram_gb": 8,
        "gpu_required": True,
        "gpu_backends": ["cuda", "mps", "rocm"],
        "disk_gb": 15,
        "ram_gb": 16,
    },
    "automatic1111/stable-diffusion-webui": {
        "category": "image_gen",
        "min_vram_gb": 4,
        "rec_vram_gb": 8,
        "gpu_required": True,
        "gpu_backends": ["cuda", "mps"],
        "disk_gb": 15,
        "ram_gb": 16,
    },
    "lllyasviel/stable-diffusion-webui-forge": {
        "category": "image_gen",
        "min_vram_gb": 4,
        "rec_vram_gb": 8,
        "gpu_required": True,
        "gpu_backends": ["cuda", "mps"],
        "disk_gb": 15,
        "ram_gb": 16,
    },
    "open-webui/open-webui": {
        "category": "llm_ui",
        "min_vram_gb": 0,
        "rec_vram_gb": 0,
        "gpu_required": False,
        "gpu_backends": ["cpu"],  # UI 本身不需要 GPU
        "disk_gb": 2,
        "ram_gb": 4,
    },
    "oobabooga/text-generation-webui": {
        "category": "llm_ui",
        "min_vram_gb": 4,
        "rec_vram_gb": 8,
        "gpu_required": False,
        "gpu_backends": ["cuda", "mps", "rocm", "cpu"],
        "disk_gb": 10,
        "ram_gb": 16,
    },
    "lobehub/lobe-chat": {
        "category": "llm_ui",
        "min_vram_gb": 0,
        "rec_vram_gb": 0,
        "gpu_required": False,
        "gpu_backends": ["cpu"],
        "disk_gb": 2,
        "ram_gb": 4,
    },
    "nomic-ai/gpt4all": {
        "category": "llm_inference",
        "min_vram_gb": 0,
        "rec_vram_gb": 8,
        "gpu_required": False,
        "gpu_backends": ["cuda", "metal", "cpu"],
        "disk_gb": 5,
        "ram_gb": 8,
    },
    "mudler/localai": {
        "category": "llm_inference",
        "min_vram_gb": 0,
        "rec_vram_gb": 8,
        "gpu_required": False,
        "gpu_backends": ["cuda", "metal", "cpu"],
        "disk_gb": 5,
        "ram_gb": 8,
    },
    "hiyouga/llama-factory": {
        "category": "llm_training",
        "min_vram_gb": 8,
        "rec_vram_gb": 24,
        "gpu_required": True,
        "gpu_backends": ["cuda"],
        "disk_gb": 30,
        "ram_gb": 32,
    },
    "vllm-project/vllm": {
        "category": "llm_inference",
        "min_vram_gb": 8,
        "rec_vram_gb": 24,
        "gpu_required": True,
        "gpu_backends": ["cuda", "rocm"],
        "disk_gb": 10,
        "ram_gb": 16,
    },
    "lm-sys/fastchat": {
        "category": "llm_inference",
        "min_vram_gb": 8,
        "rec_vram_gb": 16,
        "gpu_required": False,
        "gpu_backends": ["cuda", "mps", "cpu"],
        "disk_gb": 5,
        "ram_gb": 16,
    },
    "mckaywrigley/chatbot-ui": {
        "category": "llm_ui",
        "min_vram_gb": 0,
        "rec_vram_gb": 0,
        "gpu_required": False,
        "gpu_backends": ["cpu"],
        "disk_gb": 1,
        "ram_gb": 4,
    },
    "microsoft/autogen": {
        "category": "ai_agent",
        "min_vram_gb": 0,
        "rec_vram_gb": 0,
        "gpu_required": False,
        "gpu_backends": ["cpu"],
        "disk_gb": 2,
        "ram_gb": 8,
    },
    "zhayujie/chatgpt-on-wechat": {
        "category": "llm_app",
        "min_vram_gb": 0,
        "rec_vram_gb": 0,
        "gpu_required": False,
        "gpu_backends": ["cpu"],
        "disk_gb": 1,
        "ram_gb": 4,
    },
    "continuedev/continue": {
        "category": "ai_dev_tool",
        "min_vram_gb": 0,
        "rec_vram_gb": 0,
        "gpu_required": False,
        "gpu_backends": ["cpu"],
        "disk_gb": 1,
        "ram_gb": 4,
    },
    "invoke-ai/invokeai": {
        "category": "image_gen",
        "min_vram_gb": 4,
        "rec_vram_gb": 8,
        "gpu_required": True,
        "gpu_backends": ["cuda", "mps"],
        "disk_gb": 15,
        "ram_gb": 16,
    },
    "bmaltais/kohya_ss": {
        "category": "image_training",
        "min_vram_gb": 8,
        "rec_vram_gb": 12,
        "gpu_required": True,
        "gpu_backends": ["cuda"],
        "disk_gb": 20,
        "ram_gb": 16,
    },
    "huggingface/transformers": {
        "category": "ml_framework",
        "min_vram_gb": 0,
        "rec_vram_gb": 8,
        "gpu_required": False,
        "gpu_backends": ["cuda", "mps", "rocm", "cpu"],
        "disk_gb": 5,
        "ram_gb": 8,
    },
    "ultralytics/ultralytics": {
        "category": "cv_inference",
        "min_vram_gb": 2,
        "rec_vram_gb": 8,
        "gpu_required": False,
        "gpu_backends": ["cuda", "mps", "cpu"],
        "disk_gb": 3,
        "ram_gb": 8,
    },
    "facebookresearch/detectron2": {
        "category": "cv_training",
        "min_vram_gb": 4,
        "rec_vram_gb": 8,
        "gpu_required": True,
        "gpu_backends": ["cuda"],
        "disk_gb": 10,
        "ram_gb": 16,
    },
    "gradio-app/gradio": {
        "category": "ml_framework",
        "min_vram_gb": 0,
        "rec_vram_gb": 0,
        "gpu_required": False,
        "gpu_backends": ["cpu"],
        "disk_gb": 1,
        "ram_gb": 4,
    },
    "streamlit/streamlit": {
        "category": "ml_framework",
        "min_vram_gb": 0,
        "rec_vram_gb": 0,
        "gpu_required": False,
        "gpu_backends": ["cpu"],
        "disk_gb": 1,
        "ram_gb": 4,
    },
}

# 项目类别描述
_AI_CATEGORIES = {
    "llm_inference":  "LLM 推理引擎",
    "llm_training":   "LLM 训练/微调",
    "llm_ui":         "LLM 交互界面",
    "llm_app":        "LLM 应用",
    "image_gen":      "AI 图像生成",
    "image_training": "AI 图像训练",
    "cv_inference":   "计算机视觉推理",
    "cv_training":    "计算机视觉训练",
    "ml_framework":   "ML 框架/库",
    "ai_agent":       "AI Agent 框架",
    "ai_dev_tool":    "AI 开发工具",
}


def get_hardware_req(project_key: str) -> dict | None:
    """获取项目的硬件需求，未收录则返回 None"""
    return _AI_HARDWARE_REQS.get(project_key.lower())


def check_hardware_compatibility(project_key: str, gpu_info: dict, env: dict) -> dict:
    """
    检查用户硬件是否满足项目需求。

    Returns:
        {
            "compatible": bool,
            "warnings": [str],
            "recommendations": [str],
            "category": str,
        }
    """
    req = get_hardware_req(project_key)
    if not req:
        return {"compatible": True, "warnings": [], "recommendations": [], "category": "unknown"}

    warnings = []
    recommendations = []
    compatible = True

    vram = gpu_info.get("vram_gb") or 0
    gpu_type = gpu_info.get("type", "cpu_only")
    ram = env.get("hardware", {}).get("ram_gb") or 0
    disk = env.get("disk", {}).get("free_gb") or 0

    # GPU 检查
    if req["gpu_required"] and gpu_type == "cpu_only":
        compatible = False
        warnings.append(f"此项目需要 GPU，但未检测到独立显卡")
        recommendations.append("安装 NVIDIA GPU + CUDA 或使用 Apple Silicon Mac")

    # GPU 后端兼容性
    backend_map = {"nvidia": "cuda", "apple_mps": "mps", "amd_rocm": "rocm", "cpu_only": "cpu"}
    user_backend = backend_map.get(gpu_type, "cpu")
    if user_backend not in req["gpu_backends"] and user_backend != "cpu":
        warnings.append(f"你的 GPU 后端 ({user_backend}) 可能不完全支持此项目")

    # VRAM 检查
    if vram and vram < req["min_vram_gb"] and req["min_vram_gb"] > 0:
        compatible = False
        warnings.append(f"GPU 显存 {vram}GB 低于最低要求 {req['min_vram_gb']}GB")
    elif vram and vram < req["rec_vram_gb"]:
        warnings.append(f"GPU 显存 {vram}GB 低于推荐值 {req['rec_vram_gb']}GB，可能影响性能")

    # RAM 检查
    if ram and ram < req["ram_gb"]:
        warnings.append(f"系统内存 {ram}GB 低于推荐值 {req['ram_gb']}GB")

    # 磁盘检查
    if disk and disk < req["disk_gb"]:
        warnings.append(f"磁盘剩余 {disk}GB 可能不足（推荐 {req['disk_gb']}GB+）")

    category = _AI_CATEGORIES.get(req.get("category", ""), req.get("category", "unknown"))

    return {
        "compatible": compatible,
        "warnings": warnings,
        "recommendations": recommendations,
        "category": category,
    }


# ─────────────────────────────────────────────
#  已知热门项目数据库
#
#  为什么需要这个：
#  - 这些项目的安装步骤有特殊性，正则提取 README 拿不对
#  - 部分项目需要 GPU 选择，必须结合 env 动态生成
#  - 给用户直接正确的命令，避免踩坑
# ─────────────────────────────────────────────

# 格式说明：
#   "steps"        → 所有平台通用步骤
#   "by_os"        → 平台差异步骤（macos / linux / windows）
#   "by_platform"  → 特殊分发策略（docker_preferred 等）
#   "steps_docker" → Docker 分发路径
#   "steps_pip"    → pip 分发路径
#   支持模板占位符：{python} {pip} {venv_activate} {torch_install}

_KNOWN_PROJECTS: dict[str, dict] = {

    # ── 本地 LLM 推理 ──────────────────────────────
    "ollama/ollama": {
        "desc": "最简单的本地 LLM 运行工具",
        "by_os": {
            "macos": [
                {"cmd": "brew install ollama",                          "desc": "安装 Ollama"},
                {"cmd": "brew services start ollama",                   "desc": "设置后台自动启动"},
                {"cmd": "ollama pull qwen2.5:1.5b",                     "desc": "下载推荐小模型（~1GB，普通电脑可跑）"},
            ],
            "linux": [
                {"cmd": "curl -fsSL https://ollama.com/install.sh | sh", "desc": "一键安装 Ollama", "warn": True},
                {"cmd": "sudo systemctl enable --now ollama",            "desc": "设置为系统服务"},
                {"cmd": "ollama pull qwen2.5:1.5b",                      "desc": "下载推荐小模型（~1GB）"},
            ],
            "windows": [
                {"cmd": "winget install Ollama.Ollama",                  "desc": "安装 Ollama（winget）"},
                {"cmd": "ollama pull qwen2.5:1.5b",                      "desc": "下载推荐小模型（~1GB）"},
            ],
        },
        "launch": "ollama serve",
        "notes": (
            "Ollama API：http://localhost:11434\n"
            "推荐模型（按配置从低到高）：\n"
            "  qwen2.5:1.5b  (~1GB  显存/内存，普通笔记本可跑 ← 推荐新手)\n"
            "  qwen2.5:3b    (~2GB  显存/内存，流畅度更好)\n"
            "  qwen2.5:7b    (~4GB  显存/内存，质量最佳)\n"
            "中文支持：qwen2.5 系列最佳"
        ),
    },

    "ggerganov/llama.cpp": {
        "desc": "高性能本地 LLM 推理引擎（GGUF 格式）",
        "steps": [
            {"cmd": "git clone --depth 1 https://github.com/ggerganov/llama.cpp.git", "desc": "克隆代码"},
            {"cmd": "cd llama.cpp",                                                    "desc": "进入目录"},
        ],
        "by_os": {
            "macos": [
                {"cmd": "cmake -B build -DGGML_METAL=ON && cmake --build build --config Release -j$(sysctl -n hw.ncpu)",
                 "desc": "编译（Apple Metal 加速）"},
            ],
            "linux": [
                {"cmd": "cmake -B build && cmake --build build --config Release -j$(nproc)",
                 "desc": "编译（CPU）"},
            ],
            "windows": [
                {"cmd": "cmake -B build && cmake --build build --config Release",
                 "desc": "编译"},
            ],
        },
        "launch": "./build/bin/llama-cli -m model.gguf -p '你好' -n 128",
        "notes": "下载 GGUF 模型后放入项目目录即可运行",
    },

    # ── Stable Diffusion 图像生成 ──────────────────
    "comfyanonymous/comfyui": {
        "desc": "最强大的 Stable Diffusion 节点式工作流 UI",
        "steps": [
            {"cmd": "git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git", "desc": "克隆代码"},
            {"cmd": "cd ComfyUI",                          "desc": "进入目录"},
            {"cmd": "{python} -m venv venv",               "desc": "创建虚拟环境（隔离依赖）"},
            {"cmd": "{venv_activate}",                     "desc": "激活虚拟环境"},
            {"cmd": "{torch_install}",                     "desc": "安装 PyTorch（已自动适配你的 GPU）"},
            {"cmd": "{pip} install -r requirements.txt",   "desc": "安装其余依赖"},
        ],
        "launch": "{python} main.py --listen",
        "notes": "浏览器打开 http://127.0.0.1:8188\n模型（.safetensors）放入 models/checkpoints/ 目录",
    },

    "automatic1111/stable-diffusion-webui": {
        "desc": "最流行的 Stable Diffusion Web UI（A1111）",
        "steps": [
            {"cmd": "git clone https://github.com/AUTOMATIC1111/stable-diffusion-webui.git", "desc": "克隆代码"},
            {"cmd": "cd stable-diffusion-webui",     "desc": "进入目录"},
        ],
        "by_os": {
            "macos":   [{"cmd": "bash webui.sh",          "desc": "首次运行（自动配置环境，约30分钟）"}],
            "linux":   [{"cmd": "bash webui.sh",          "desc": "首次运行（自动配置环境，约30分钟）"}],
            "windows": [{"cmd": ".\\webui-user.bat",      "desc": "首次运行（自动配置环境，约30分钟）"}],
        },
        "launch": "bash webui.sh",
        "notes": "浏览器打开 http://127.0.0.1:7860\n首次运行会自动下载 Python 环境，需要稳定网络",
    },

    "lllyasviel/stable-diffusion-webui-forge": {
        "desc": "A1111 高性能优化版（Forge），速度提升 30%+",
        "steps": [
            {"cmd": "git clone https://github.com/lllyasviel/stable-diffusion-webui-forge.git", "desc": "克隆代码"},
            {"cmd": "cd stable-diffusion-webui-forge", "desc": "进入目录"},
        ],
        "by_os": {
            "macos":   [{"cmd": "bash webui.sh",     "desc": "首次运行（自动配置）"}],
            "linux":   [{"cmd": "bash webui.sh",     "desc": "首次运行（自动配置）"}],
            "windows": [{"cmd": ".\\webui-user.bat", "desc": "首次运行（自动配置）"}],
        },
        "launch": "bash webui.sh",
        "notes": "浏览器打开 http://127.0.0.1:7860",
    },

    # ── 文字/聊天 UI ───────────────────────────────
    "open-webui/open-webui": {
        "desc": "功能最完备的本地 ChatGPT 风格 Web UI，深度整合 Ollama",
        "by_platform": "docker_preferred",
        "steps_docker": [
            {"cmd": (
                "docker run -d -p 3000:8080 "
                "--add-host=host.docker.internal:host-gateway "
                "-v open-webui:/app/backend/data "
                "--name open-webui --restart always "
                "ghcr.io/open-webui/open-webui:main"
            ), "desc": "Docker 一键启动（推荐）"},
        ],
        "steps_pip": [
            {"cmd": "{pip} install open-webui",  "desc": "pip 安装"},
            {"cmd": "open-webui serve",           "desc": "启动服务"},
        ],
        "launch": "open-webui serve",
        "notes": "浏览器打开 http://localhost:3000\n需先启动 Ollama（ollama serve）",
    },

    "oobabooga/text-generation-webui": {
        "desc": "最全面的本地 LLM Web 界面，支持 GGUF/GPTQ/AWQ/EXL2 多格式",
        "steps": [
            {"cmd": "git clone https://github.com/oobabooga/text-generation-webui.git", "desc": "克隆代码"},
            {"cmd": "cd text-generation-webui", "desc": "进入目录"},
        ],
        "by_os": {
            "macos":   [{"cmd": "bash start_macos.sh",    "desc": "一键安装并启动（macOS）"}],
            "linux":   [{"cmd": "bash start_linux.sh",    "desc": "一键安装并启动（Linux）"}],
            "windows": [{"cmd": ".\\start_windows.bat",   "desc": "一键安装并启动（Windows）"}],
        },
        "launch": "bash start_linux.sh",
        "notes": "浏览器打开 http://127.0.0.1:7860\nmodels/ 目录放 GGUF 文件即可直接加载",
    },

    "lobehub/lobe-chat": {
        "desc": "现代化 AI 对话界面，支持多 LLM 和插件",
        "steps": [
            {"cmd": "git clone --depth 1 https://github.com/lobehub/lobe-chat.git", "desc": "克隆代码"},
            {"cmd": "cd lobe-chat",           "desc": "进入目录"},
            {"cmd": "{node_install}",          "desc": "安装依赖"},
            {"cmd": "cp .env.example .env",   "desc": "创建配置文件"},
        ],
        "launch": "{node_dev}",
        "notes": "编辑 .env 文件填写 API Key\n浏览器打开 http://localhost:3010",
    },

    "mckaywrigley/chatbot-ui": {
        "desc": "开源 ChatGPT UI 克隆（Next.js）",
        "steps": [
            {"cmd": "git clone --depth 1 https://github.com/mckaywrigley/chatbot-ui.git", "desc": "克隆代码"},
            {"cmd": "cd chatbot-ui",                    "desc": "进入目录"},
            {"cmd": "{node_install}",                    "desc": "安装依赖"},
            {"cmd": "cp .env.local.example .env.local", "desc": "创建配置文件"},
        ],
        "launch": "{node_dev}",
        "notes": "编辑 .env.local 设置 OPENAI_API_KEY\n浏览器打开 http://localhost:3000",
    },

    # ── 训练 / 微调 ────────────────────────────────
    "hiyouga/llama-factory": {
        "desc": "最受欢迎的 LLM 微调框架，支持 LoRA/QLoRA/全量微调",
        "steps": [
            {"cmd": "git clone --depth 1 https://github.com/hiyouga/LLaMA-Factory.git", "desc": "克隆代码"},
            {"cmd": "cd LLaMA-Factory",            "desc": "进入目录"},
            {"cmd": "{python} -m venv venv",        "desc": "创建虚拟环境"},
            {"cmd": "{venv_activate}",              "desc": "激活虚拟环境"},
            {"cmd": "{torch_install}",              "desc": "安装 PyTorch（已自动适配 GPU）"},
            {"cmd": "{pip} install -e '.[torch,metrics]'", "desc": "安装 LLaMA-Factory"},
        ],
        "launch": "llamafactory-cli webui",
        "notes": "浏览器打开 http://localhost:7860",
    },

    "bmaltais/kohya_ss": {
        "desc": "LoRA / DreamBooth 训练工具（Stable Diffusion）",
        "steps": [
            {"cmd": "git clone --depth 1 https://github.com/bmaltais/kohya_ss.git", "desc": "克隆代码"},
            {"cmd": "cd kohya_ss",    "desc": "进入目录"},
        ],
        "by_os": {
            "macos":   [{"cmd": "bash setup.sh",    "desc": "一键安装（macOS）"}],
            "linux":   [{"cmd": "bash setup.sh",    "desc": "一键安装（Linux）"}],
            "windows": [{"cmd": ".\\setup.bat",     "desc": "一键安装（Windows）"}],
        },
        "launch": "bash gui.sh",
        "notes": "浏览器打开 http://127.0.0.1:7860",
    },

    "invoke-ai/invokeai": {
        "desc": "专业级 AI 绘图工具，界面精美",
        "steps": [
            {"cmd": "{python} -m venv invokeai_env", "desc": "创建虚拟环境"},
            {"cmd": "{venv_activate}",               "desc": "激活虚拟环境（invokeai_env）"},
            {"cmd": "{torch_install}",               "desc": "安装 PyTorch（GPU 适配）"},
            {"cmd": "{pip} install InvokeAI",        "desc": "安装 InvokeAI"},
            {"cmd": "invokeai-configure",            "desc": "交互式初始化配置"},
        ],
        "launch": "invokeai --web",
        "notes": "浏览器打开 http://localhost:9090",
    },

    # ── 中文场景 ───────────────────────────────────
    "zhayujie/chatgpt-on-wechat": {
        "desc": "将 ChatGPT 接入微信/企业微信/飞书/钉钉",
        "steps": [
            {"cmd": "git clone --depth 1 https://github.com/zhayujie/chatgpt-on-wechat.git", "desc": "克隆代码"},
            {"cmd": "cd chatgpt-on-wechat",                         "desc": "进入目录"},
            {"cmd": "{python} -m venv venv",                         "desc": "创建虚拟环境"},
            {"cmd": "{venv_activate}",                               "desc": "激活虚拟环境"},
            {"cmd": "{pip} install -r requirements.txt",             "desc": "安装依赖"},
            {"cmd": "cp config-template.json config.json",          "desc": "创建配置文件"},
        ],
        "launch": "{python} app.py",
        "notes": "编辑 config.json：填写 model（gpt-3.5-turbo）和 open_ai_api_key",
    },

    # ── 本地 API 服务 ──────────────────────────────
    "mudler/localai": {
        "desc": "本地 OpenAI 兼容 API，可替换所有 ChatGPT 调用",
        "by_platform": "docker_preferred",
        "steps_docker": [
            {"cmd": (
                "docker run -p 8080:8080 "
                "-v $PWD/models:/build/models:cached "
                "localai/localai:latest-aio-cpu"
            ), "desc": "Docker 一键启动（CPU 模式）"},
        ],
        "steps_pip": [
            {"cmd": "# LocalAI 暂不支持 pip 安装，请使用 Docker 或查看官方文档", "desc": "提示"},
        ],
        "launch": None,
        "notes": "API 地址：http://localhost:8080\n完全兼容 OpenAI API，只需修改 base_url 即可",
    },

    # ── 开发工具 ───────────────────────────────────
    "continuedev/continue": {
        "desc": "VS Code / JetBrains AI 编程助手插件",
        "by_os": {
            "macos": [
                {"cmd": "code --install-extension continue.continue", "desc": "VS Code 安装 Continue 插件"},
            ],
            "linux": [
                {"cmd": "code --install-extension continue.continue", "desc": "VS Code 安装 Continue 插件"},
            ],
            "windows": [
                {"cmd": "code --install-extension continue.continue", "desc": "VS Code 安装 Continue 插件"},
            ],
        },
        "launch": None,
        "notes": "重启 VS Code 后，侧边栏出现 Continue 图标\n连接本地 Ollama：base_url = http://localhost:11434",
    },

    "huggingface/transformers": {
        "desc": "Hugging Face Transformers 深度学习库",
        "steps": [
            {"cmd": "{python} -m venv venv",              "desc": "创建虚拟环境"},
            {"cmd": "{venv_activate}",                    "desc": "激活虚拟环境"},
            {"cmd": "{torch_install}",                    "desc": "安装 PyTorch（GPU 适配）"},
            {"cmd": "{pip} install transformers[torch]",  "desc": "安装 Transformers"},
            {"cmd": "{pip} install accelerate datasets",  "desc": "安装常用配套库"},
        ],
        "launch": "{python} -c \"from transformers import pipeline; print(pipeline('text-generation')('Hello'))\"",
        "notes": "Transformers 文档：https://huggingface.co/docs/transformers",
    },

    # ── 目标检测 / 计算机视觉 ──────────────────────
    "ultralytics/ultralytics": {
        "desc": "YOLOv8/YOLOv11 目标检测，pip 一键安装",
        "steps": [
            {"cmd": "{python} -m venv venv",         "desc": "创建虚拟环境"},
            {"cmd": "{venv_activate}",               "desc": "激活虚拟环境"},
            {"cmd": "{torch_install}",               "desc": "安装 PyTorch（GPU 适配）"},
            {"cmd": "{pip} install ultralytics",     "desc": "安装 Ultralytics YOLO"},
        ],
        "launch": "yolo predict model=yolo11n.pt source=0",
        "notes": "快速测试：yolo detect predict model=yolo11n.pt source='https://ultralytics.com/images/bus.jpg'",
    },

    "facebookresearch/detectron2": {
        "desc": "Facebook Detectron2 目标检测/分割框架",
        "steps": [
            {"cmd": "git clone --depth 1 https://github.com/facebookresearch/detectron2.git", "desc": "克隆代码"},
            {"cmd": "cd detectron2",                 "desc": "进入目录"},
            {"cmd": "{python} -m venv venv",         "desc": "创建虚拟环境"},
            {"cmd": "{venv_activate}",               "desc": "激活虚拟环境"},
            {"cmd": "{torch_install}",               "desc": "安装 PyTorch（GPU 适配）"},
            {"cmd": "{pip} install -e .",            "desc": "安装 Detectron2（开发模式）"},
        ],
        "launch": "{python} demo/demo.py --help",
        "notes": "需要 OpenCV：pip install opencv-python",
    },

    # ── LLM 推理服务 ───────────────────────────────
    "vllm-project/vllm": {
        "desc": "高吞吐量 LLM 推理引擎（生产级 GPU 服务）",
        "steps": [
            {"cmd": "{python} -m venv venv",         "desc": "创建虚拟环境"},
            {"cmd": "{venv_activate}",               "desc": "激活虚拟环境"},
            {"cmd": "{pip} install vllm",            "desc": "安装 vLLM（需要 NVIDIA GPU）"},
        ],
        "launch": "python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-7B-Instruct",
        "notes": "⚠️ 需要 NVIDIA GPU，不支持 Apple Silicon\nAPI 兼容 OpenAI 格式：http://localhost:8000",
    },

    "nomic-ai/gpt4all": {
        "desc": "GPT4All 本地 LLM 桌面客户端（图形界面）",
        "by_os": {
            "macos": [
                {"cmd": "brew install --cask gpt4all", "desc": "安装 GPT4All 桌面版（macOS）"},
            ],
            "linux": [
                {"cmd": "# Linux 请从官网下载 AppImage：https://gpt4all.io/", "desc": "下载 Linux 版本"},
                {"cmd": "chmod +x gpt4all-*.AppImage && ./gpt4all-*.AppImage", "desc": "直接运行 AppImage"},
            ],
            "windows": [
                {"cmd": "winget install nomic-ai.gpt4all", "desc": "安装 GPT4All（winget）"},
            ],
        },
        "launch": "gpt4all",
        "notes": "图形界面本地 LLM，支持 llama/mistral/qwen 等，无需命令行",
    },

    "lm-sys/fastchat": {
        "desc": "FastChat - LLM 对话服务平台（支持 Vicuna/LLaMA 等）",
        "steps": [
            {"cmd": "{python} -m venv venv",          "desc": "创建虚拟环境"},
            {"cmd": "{venv_activate}",                "desc": "激活虚拟环境"},
            {"cmd": "{pip} install fschat",           "desc": "安装 FastChat"},
        ],
        "launch": "python -m fastchat.serve.cli --model-path lmsys/vicuna-7b-v1.5",
        "notes": "Web UI 启动：python -m fastchat.serve.gradio_web_server\nAPI 服务：python -m fastchat.serve.openai_api_server --host 0.0.0.0",
    },

    # ── Gradio / Streamlit 开发框架 ────────────────
    "gradio-app/gradio": {
        "desc": "快速构建 AI Demo Web UI（pip 一键安装）",
        "steps": [
            {"cmd": "{python} -m venv venv",          "desc": "创建虚拟环境"},
            {"cmd": "{venv_activate}",                "desc": "激活虚拟环境"},
            {"cmd": "{pip} install gradio",           "desc": "安装 Gradio"},
        ],
        "launch": "{python} -c \"import gradio as gr; gr.Interface(lambda x: x, 'text', 'text').launch()\"",
        "notes": "浏览器自动打开 http://127.0.0.1:7860",
    },

    "streamlit/streamlit": {
        "desc": "数据应用快速构建框架（pip 一键安装）",
        "steps": [
            {"cmd": "{python} -m venv venv",          "desc": "创建虚拟环境"},
            {"cmd": "{venv_activate}",                "desc": "激活虚拟环境"},
            {"cmd": "{pip} install streamlit",        "desc": "安装 Streamlit"},
        ],
        "launch": "streamlit hello",
        "notes": "启动自己的 app：streamlit run app.py",
    },

    # ── 工作流自动化 ───────────────────────────────
    "n8n-io/n8n": {
        "desc": "n8n 工作流自动化（npm 或 Docker）",
        "by_platform": "docker_preferred",
        "steps_docker": [
            {"cmd": (
                "docker run -it --rm "
                "-p 5678:5678 "
                "-v n8n_data:/home/node/.n8n "
                "docker.n8n.io/n8nio/n8n"
            ), "desc": "Docker 一键启动 n8n"},
        ],
        "steps_pip": [
            {"cmd": "npm install -g n8n",  "desc": "npm 全局安装 n8n"},
            {"cmd": "n8n start",           "desc": "启动 n8n"},
        ],
        "launch": "n8n start",
        "notes": "浏览器打开 http://localhost:5678\n⚠️ 首次启动需要创建账号",
    },

    # ── 搜索引擎 ───────────────────────────────────
    "searxng/searxng": {
        "desc": "SearXNG 隐私自托管搜索引擎（Docker 推荐）",
        "by_platform": "docker_preferred",
        "steps_docker": [
            {"cmd": "git clone --depth 1 https://github.com/searxng/searxng-docker.git && cd searxng-docker",
             "desc": "克隆配置文件"},
            {"cmd": "docker compose up -d",  "desc": "Docker 启动 SearXNG"},
        ],
        "steps_pip": [
            {"cmd": "git clone --depth 1 https://github.com/searxng/searxng.git && cd searxng", "desc": "克隆代码"},
            {"cmd": "{python} -m venv venv && {venv_activate}",  "desc": "创建虚拟环境"},
            {"cmd": "{pip} install -e '.[client]'",              "desc": "安装依赖"},
        ],
        "launch": None,
        "notes": "浏览器打开 http://localhost:8080",
    },

    # ── 命令行工具（Rust）─────────────────────────
    "burntsushi/ripgrep": {
        "desc": "ripgrep — 极速文本搜索工具（rust 实现，速度比 grep 快10x）",
        "by_os": {
            "macos": [
                {"cmd": "brew install ripgrep",  "desc": "安装 ripgrep（推荐方式）"},
            ],
            "linux": [
                {"cmd": "cargo install ripgrep", "desc": "Cargo 安装（需要 Rust 工具链）"},
            ],
            "windows": [
                {"cmd": "winget install BurntSushi.ripgrep.MSVC", "desc": "winget 安装"},
            ],
        },
        "launch": "rg --version",
        "notes": "使用：rg '搜索词' 目录/\nmacOS 推荐 brew，无需编译，30秒安装完",
    },

    "BurntSushi/ripgrep": {  # 大写别名
        "desc": "ripgrep — 极速文本搜索（Rust 编写）",
        "by_os": {
            "macos": [{"cmd": "brew install ripgrep", "desc": "安装 ripgrep（Homebrew）"}],
            "linux": [{"cmd": "cargo install ripgrep", "desc": "Cargo 安装"}],
            "windows": [{"cmd": "winget install BurntSushi.ripgrep.MSVC", "desc": "winget 安装"}],
        },
        "launch": "rg --version",
        "notes": "使用：rg '关键词' 目录/\n安装 Rust：curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh",
    },

    "sharkdp/bat": {
        "desc": "bat — 带语法高亮的 cat 替代品（Rust 编写）",
        "by_os": {
            "macos": [{"cmd": "brew install bat", "desc": "安装 bat（Homebrew）"}],
            "linux": [{"cmd": "sudo apt-get install -y bat || cargo install bat", "desc": "安装 bat"}],
            "windows": [{"cmd": "winget install sharkdp.bat", "desc": "winget 安装"}],
        },
        "launch": "bat --version",
        "notes": "使用：bat 文件名\nUbuntu/Debian 上命令可能叫 batcat",
    },

    "sharkdp/fd": {
        "desc": "fd — 更快更友好的 find 替代品（Rust 编写）",
        "by_os": {
            "macos": [{"cmd": "brew install fd", "desc": "安装 fd（Homebrew）"}],
            "linux": [{"cmd": "sudo apt-get install -y fd-find || cargo install fd-find", "desc": "安装 fd"}],
            "windows": [{"cmd": "winget install sharkdp.fd", "desc": "winget 安装"}],
        },
        "launch": "fd --version",
        "notes": "使用：fd '模式' 目录/\nUbuntu 上命令可能叫 fdfind",
    },

    "sharkdp/hyperfine": {
        "desc": "hyperfine — 命令行基准测试工具（Rust 编写）",
        "by_os": {
            "macos": [{"cmd": "brew install hyperfine", "desc": "安装 hyperfine（Homebrew）"}],
            "linux": [{"cmd": "cargo install hyperfine", "desc": "Cargo 安装"}],
            "windows": [{"cmd": "winget install sharkdp.hyperfine", "desc": "winget 安装"}],
        },
        "launch": "hyperfine --version",
        "notes": "使用：hyperfine 'sleep 0.5' '命令2'",
    },

    "junegunn/fzf": {
        "desc": "fzf — 通用命令行模糊查找器（Go 编写）",
        "by_os": {
            "macos": [{"cmd": "brew install fzf", "desc": "安装 fzf（Homebrew）"}],
            "linux": [{"cmd": "sudo apt-get install -y fzf || go install github.com/junegunn/fzf@latest", "desc": "安装 fzf"}],
            "windows": [{"cmd": "winget install junegunn.fzf", "desc": "winget 安装"}],
        },
        "launch": "fzf --version",
        "notes": "使用：fzf 启动交互搜索\nCtrl+R 搜索历史（需 shell 集成）",
    },

    "jqlang/jq": {
        "desc": "jq — 轻量级命令行 JSON 处理器（C 编写）",
        "by_os": {
            "macos": [{"cmd": "brew install jq", "desc": "安装 jq（Homebrew）"}],
            "linux": [{"cmd": "sudo apt-get install -y jq", "desc": "安装 jq（apt）"}],
            "windows": [{"cmd": "winget install jqlang.jq", "desc": "winget 安装"}],
        },
        "launch": "jq --version",
        "notes": "使用：echo '{\"a\":1}' | jq '.a'",
    },

    "stedolan/jq": {  # 旧 owner 别名
        "desc": "jq — 轻量级命令行 JSON 处理器",
        "by_os": {
            "macos": [{"cmd": "brew install jq", "desc": "安装 jq（Homebrew）"}],
            "linux": [{"cmd": "sudo apt-get install -y jq", "desc": "安装 jq（apt）"}],
            "windows": [{"cmd": "winget install jqlang.jq", "desc": "winget 安装"}],
        },
        "launch": "jq --version",
        "notes": "使用：echo '{\"a\":1}' | jq '.a'",
    },

    "jekyll/jekyll": {
        "desc": "Jekyll — 静态网站生成器（Ruby 编写，GitHub Pages 官方引擎）",
        "by_os": {
            "macos": [
                {"cmd": "gem install jekyll bundler", "desc": "安装 Jekyll + Bundler"},
            ],
            "linux": [
                {"cmd": "sudo apt-get install -y ruby-full build-essential zlib1g-dev", "desc": "安装 Ruby 及编译依赖"},
                {"cmd": "gem install jekyll bundler", "desc": "安装 Jekyll + Bundler"},
            ],
            "windows": [
                {"cmd": "winget install RubyInstallerTeam.Ruby.3.2", "desc": "安装 Ruby"},
                {"cmd": "gem install jekyll bundler", "desc": "安装 Jekyll + Bundler"},
            ],
        },
        "launch": "jekyll --version",
        "notes": "创建新站点：jekyll new my-site && cd my-site && bundle exec jekyll serve",
    },

    "dandavison/delta": {
        "desc": "delta — 美化 git diff 输出",
        "by_os": {
            "macos": [{"cmd": "brew install git-delta", "desc": "安装 delta（Homebrew）"}],
            "linux": [{"cmd": "cargo install git-delta", "desc": "Cargo 安装"}],
            "windows": [{"cmd": "winget install dandavison.delta", "desc": "winget 安装"}],
        },
        "launch": "delta --version",
        "notes": "配置 git 使用 delta：git config --global core.pager delta",
    },

    "ajeetdsouza/zoxide": {
        "desc": "zoxide — 更智能的 cd 命令替代品",
        "by_os": {
            "macos": [{"cmd": "brew install zoxide", "desc": "安装 zoxide（Homebrew）"}],
            "linux": [{"cmd": "cargo install zoxide --locked", "desc": "Cargo 安装"}],
            "windows": [{"cmd": "winget install ajeetdsouza.zoxide", "desc": "winget 安装"}],
        },
        "launch": "zoxide --version",
        "notes": "需要在 shell 配置中添加 eval \"$(zoxide init zsh)\"",
    },

    "starship/starship": {
        "desc": "starship — 极速跨 shell 提示符",
        "by_os": {
            "macos": [{"cmd": "brew install starship", "desc": "安装 starship（Homebrew）"}],
            "linux": [{"cmd": "cargo install starship --locked", "desc": "Cargo 安装"}],
            "windows": [{"cmd": "winget install Starship.Starship", "desc": "winget 安装"}],
        },
        "launch": "starship --version",
        "notes": "需要在 shell 配置中添加 eval \"$(starship init zsh)\"",
    },

    "eza-community/eza": {
        "desc": "eza — 现代化的 ls 替代品（exa 的维护版）",
        "by_os": {
            "macos": [{"cmd": "brew install eza", "desc": "安装 eza（Homebrew）"}],
            "linux": [{"cmd": "cargo install eza", "desc": "Cargo 安装"}],
            "windows": [{"cmd": "winget install eza-community.eza", "desc": "winget 安装"}],
        },
        "launch": "eza --version",
        "notes": "使用：eza -la --icons",
    },

    "gohugoio/hugo": {
        "desc": "Hugo — 最快的静态网站生成器（Go 编写）",
        "by_os": {
            "macos": [{"cmd": "brew install hugo", "desc": "安装 Hugo（Homebrew）"}],
            "linux": [{"cmd": "sudo apt-get install -y hugo || go install github.com/gohugoio/hugo@latest", "desc": "安装 Hugo"}],
            "windows": [{"cmd": "winget install Hugo.Hugo.Extended", "desc": "winget 安装"}],
        },
        "launch": "hugo version",
        "notes": "创建新站点：hugo new site my-site && cd my-site && hugo server",
    },

    # ── 命令行工具（Go）───────────────────────────
    "cli/cli": {
        "desc": "GitHub 官方命令行工具 (gh)，管理 PR/Issue/Release",
        "by_os": {
            "macos": [
                {"cmd": "brew install gh",   "desc": "安装 GitHub CLI（Homebrew）"},
                {"cmd": "gh auth login",     "desc": "登录 GitHub 账号"},
            ],
            "linux": [
                {"cmd": (
                    "type -p curl >/dev/null || (sudo apt update && sudo apt install curl -y) && "
                    "curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg && "
                    "echo \"deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main\" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null && "
                    "sudo apt update && sudo apt install gh -y"
                ), "desc": "安装 GitHub CLI（Ubuntu/Debian）", "warn": True},
                {"cmd": "gh auth login", "desc": "登录 GitHub 账号"},
            ],
            "windows": [
                {"cmd": "winget install --id GitHub.cli", "desc": "安装 GitHub CLI（winget）"},
                {"cmd": "gh auth login",                  "desc": "登录 GitHub 账号"},
            ],
        },
        "launch": "gh status",
        "notes": "常用命令：gh repo clone / gh pr list / gh issue create",
    },

    # ── 容器管理 ───────────────────────────────────
    "portainer/portainer": {
        "desc": "Portainer — Docker/K8s 可视化管理 Web UI",
        "by_platform": "docker_preferred",
        "steps_docker": [
            {"cmd": "docker volume create portainer_data",  "desc": "创建数据卷"},
            {"cmd": (
                "docker run -d -p 8000:8000 -p 9443:9443 "
                "--name portainer --restart=always "
                "-v /var/run/docker.sock:/var/run/docker.sock "
                "-v portainer_data:/data "
                "portainer/portainer-ce:latest"
            ), "desc": "启动 Portainer"},
        ],
        "steps_pip": [
            {"cmd": "# Portainer 必须通过 Docker 安装，请先安装 Docker", "desc": "提示"},
        ],
        "launch": None,
        "notes": "浏览器打开 https://localhost:9443\n首次启动需要创建管理员账号",
    },

    # ── AI Agent 框架 ─────────────────────────────
    "microsoft/autogen": {
        "desc": "Microsoft AutoGen — 多 Agent 协作 AI 框架",
        "steps": [
            {"cmd": "{python} -m venv venv",                    "desc": "创建虚拟环境"},
            {"cmd": "{venv_activate}",                          "desc": "激活虚拟环境"},
            {"cmd": "{pip} install pyautogen",                  "desc": "安装 AutoGen"},
        ],
        "launch": "{python} -c \"import autogen; print('AutoGen', autogen.__version__, 'ready')\"",
        "notes": "需配置 OAI_CONFIG_LIST（OpenAI/Azure/Ollama API）\n示例：https://github.com/microsoft/autogen/tree/main/samples",
    },

    # ── 智能家居 ───────────────────────────────────
    "home-assistant/core": {
        "desc": "Home Assistant — 最强开源智能家居平台",
        "by_platform": "docker_preferred",
        "steps_docker": [
            {"cmd": (
                "docker run -d "
                "--name homeassistant "
                "--privileged "
                "--restart=unless-stopped "
                "-e TZ=Asia/Shanghai "
                "-v /PATH_TO_YOUR_CONFIG:/config "
                "--network=host "
                "ghcr.io/home-assistant/home-assistant:stable"
            ), "desc": "Docker 启动 Home Assistant（推荐）"},
        ],
        "steps_pip": [
            {"cmd": "{python} -m venv hass_env",                 "desc": "创建虚拟环境"},
            {"cmd": "source hass_env/bin/activate",              "desc": "激活虚拟环境"},
            {"cmd": "{pip} install homeassistant",               "desc": "安装 Home Assistant"},
            {"cmd": "hass --open-ui",                            "desc": "启动并打开界面"},
        ],
        "launch": "hass --open-ui",
        "notes": "浏览器打开 http://localhost:8123\n⚠️ 将 /PATH_TO_YOUR_CONFIG 改为实际路径",
    },

    # ── 图片管理 ───────────────────────────────────
    "immich-app/immich": {
        "desc": "Immich — 自托管 Google Photos 替代品（Docker）",
        "by_platform": "docker_preferred",
        "steps_docker": [
            {"cmd": "git clone --depth 1 https://github.com/immich-app/immich.git && cd immich/docker",
             "desc": "克隆配置文件"},
            {"cmd": "cp .env.example .env",          "desc": "创建环境变量文件"},
            {"cmd": "docker compose up -d",          "desc": "一键启动 Immich"},
        ],
        "steps_pip": [
            {"cmd": "# Immich 需要 Docker 和 Docker Compose，请先安装 Docker", "desc": "提示"},
        ],
        "launch": None,
        "notes": "浏览器打开 http://localhost:2283\n⚠️ 修改 .env 设置 UPLOAD_LOCATION（照片存储路径）",
    },
}
