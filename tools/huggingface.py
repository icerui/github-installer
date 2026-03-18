"""
huggingface.py - HuggingFace Hub 集成
======================================

支持 HuggingFace 模型和数据集的安装与管理。

功能：
  1. HuggingFace 模型/数据集元数据获取
  2. VRAM 智能评估（根据模型参数量 + 量化方式）
  3. 模型下载策略生成（全量 / GGUF / AWQ / GPTQ）
  4. Gated Model（受限模型）访问检测
  5. LFS 大文件智能处理

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

# ─────────────────────────────────────────────
#  HuggingFace 模型 VRAM 数据库
#  来源：官方 Model Card + 社区实测
# ─────────────────────────────────────────────

_MODEL_VRAM_DB: dict[str, dict] = {
    # Meta Llama 系列
    "meta-llama/Llama-2-7b-hf":    {"params_b": 7, "family": "llama2"},
    "meta-llama/Llama-2-13b-hf":   {"params_b": 13, "family": "llama2"},
    "meta-llama/Llama-2-70b-hf":   {"params_b": 70, "family": "llama2"},
    "meta-llama/Llama-3.1-8B":     {"params_b": 8, "family": "llama3"},
    "meta-llama/Llama-3.1-70B":    {"params_b": 70, "family": "llama3"},
    "meta-llama/Llama-3.1-405B":   {"params_b": 405, "family": "llama3"},
    "meta-llama/Llama-3.2-1B":     {"params_b": 1, "family": "llama3"},
    "meta-llama/Llama-3.2-3B":     {"params_b": 3, "family": "llama3"},
    "meta-llama/Llama-4-Scout-17B-16E": {"params_b": 109, "family": "llama4"},
    "meta-llama/Llama-4-Maverick-17B-128E": {"params_b": 400, "family": "llama4"},

    # Qwen（通义千问）系列
    "Qwen/Qwen2.5-0.5B":     {"params_b": 0.5, "family": "qwen2.5"},
    "Qwen/Qwen2.5-1.5B":     {"params_b": 1.5, "family": "qwen2.5"},
    "Qwen/Qwen2.5-3B":       {"params_b": 3, "family": "qwen2.5"},
    "Qwen/Qwen2.5-7B":       {"params_b": 7, "family": "qwen2.5"},
    "Qwen/Qwen2.5-14B":      {"params_b": 14, "family": "qwen2.5"},
    "Qwen/Qwen2.5-32B":      {"params_b": 32, "family": "qwen2.5"},
    "Qwen/Qwen2.5-72B":      {"params_b": 72, "family": "qwen2.5"},
    "Qwen/Qwen3-8B":         {"params_b": 8, "family": "qwen3"},
    "Qwen/Qwen3-32B":        {"params_b": 32, "family": "qwen3"},
    "Qwen/Qwen3-235B-A22B":  {"params_b": 235, "family": "qwen3_moe"},
    "Qwen/QwQ-32B":          {"params_b": 32, "family": "qwen3"},
    "Qwen/Qwen2.5-Coder-7B": {"params_b": 7, "family": "qwen2.5-coder"},
    "Qwen/Qwen2.5-Coder-32B": {"params_b": 32, "family": "qwen2.5-coder"},

    # DeepSeek 系列
    "deepseek-ai/DeepSeek-R1":        {"params_b": 671, "family": "deepseek_moe"},
    "deepseek-ai/DeepSeek-R1-0528":   {"params_b": 671, "family": "deepseek_moe"},
    "deepseek-ai/DeepSeek-V3":        {"params_b": 671, "family": "deepseek_moe"},
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B": {"params_b": 1.5, "family": "qwen2.5"},
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B":   {"params_b": 7, "family": "qwen2.5"},
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B":  {"params_b": 32, "family": "qwen2.5"},
    "deepseek-ai/DeepSeek-R1-Distill-Llama-8B":  {"params_b": 8, "family": "llama3"},
    "deepseek-ai/DeepSeek-R1-Distill-Llama-70B": {"params_b": 70, "family": "llama3"},
    "deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct": {"params_b": 16, "family": "deepseek"},

    # Mistral 系列
    "mistralai/Mistral-7B-v0.3":      {"params_b": 7, "family": "mistral"},
    "mistralai/Mixtral-8x7B-v0.1":    {"params_b": 47, "family": "mixtral"},
    "mistralai/Mistral-Small-24B":    {"params_b": 24, "family": "mistral"},
    "mistralai/Mistral-Large-2411":   {"params_b": 123, "family": "mistral"},
    "mistralai/Codestral-25.01":      {"params_b": 22, "family": "mistral"},

    # Google Gemma
    "google/gemma-2-2b":   {"params_b": 2, "family": "gemma2"},
    "google/gemma-2-9b":   {"params_b": 9, "family": "gemma2"},
    "google/gemma-2-27b":  {"params_b": 27, "family": "gemma2"},
    "google/gemma-3-1b-it": {"params_b": 1, "family": "gemma3"},
    "google/gemma-3-4b-it": {"params_b": 4, "family": "gemma3"},
    "google/gemma-3-12b-it": {"params_b": 12, "family": "gemma3"},
    "google/gemma-3-27b-it": {"params_b": 27, "family": "gemma3"},

    # Microsoft Phi
    "microsoft/phi-4":           {"params_b": 14, "family": "phi"},
    "microsoft/Phi-3.5-mini-instruct": {"params_b": 3.8, "family": "phi"},
    "microsoft/Phi-3-medium-128k-instruct": {"params_b": 14, "family": "phi"},

    # 百川 / GLM / 其他中国模型
    "baichuan-inc/Baichuan2-13B-Chat": {"params_b": 13, "family": "baichuan"},
    "THUDM/chatglm3-6b":     {"params_b": 6, "family": "glm"},
    "THUDM/glm-4-9b-chat":   {"params_b": 9, "family": "glm4"},
    "01-ai/Yi-1.5-34B-Chat":   {"params_b": 34, "family": "yi"},
    "internlm/internlm2_5-7b-chat": {"params_b": 7, "family": "internlm"},

    # Stability AI
    "stabilityai/stable-diffusion-3.5-large": {"params_b": 8.1, "family": "sd3"},
    "stabilityai/stable-diffusion-xl-base-1.0": {"params_b": 3.5, "family": "sdxl"},
    "black-forest-labs/FLUX.1-dev":  {"params_b": 12, "family": "flux"},
    "black-forest-labs/FLUX.1-schnell": {"params_b": 12, "family": "flux"},

    # Whisper (语音)
    "openai/whisper-large-v3":   {"params_b": 1.5, "family": "whisper"},
    "openai/whisper-large-v3-turbo": {"params_b": 0.8, "family": "whisper"},
}

# 受限模型列表（需要 HF Token + 协议同意）
_GATED_MODELS = {
    "meta-llama/Llama-2-7b-hf", "meta-llama/Llama-2-13b-hf",
    "meta-llama/Llama-2-70b-hf", "meta-llama/Llama-3.1-8B",
    "meta-llama/Llama-3.1-70B", "meta-llama/Llama-3.1-405B",
    "meta-llama/Llama-3.2-1B", "meta-llama/Llama-3.2-3B",
    "meta-llama/Llama-4-Scout-17B-16E", "meta-llama/Llama-4-Maverick-17B-128E",
    "google/gemma-2-2b", "google/gemma-2-9b", "google/gemma-2-27b",
    "google/gemma-3-1b-it", "google/gemma-3-4b-it",
    "google/gemma-3-12b-it", "google/gemma-3-27b-it",
    "mistralai/Mistral-Large-2411",
}


@dataclass
class HFModelInfo:
    """HuggingFace 模型元数据"""
    model_id: str
    params_b: float = 0.0
    family: str = ""
    pipeline_tag: str = ""      # text-generation, image-classification, ...
    library_name: str = ""      # transformers, diffusers, ...
    is_gated: bool = False
    license: str = ""
    downloads: int = 0
    likes: int = 0
    tags: list[str] = field(default_factory=list)
    siblings: list[str] = field(default_factory=list)  # 文件列表
    error: str = ""


@dataclass
class VRAMEstimate:
    """VRAM 估算结果"""
    model_id: str
    params_b: float
    available_vram_gb: float
    can_run: bool
    recommended_method: str      # "fp16" / "q8" / "q4_k" / "gguf" / "api_only"
    vram_needed_gb: float
    options: list[dict] = field(default_factory=list)
    advice: str = ""
    install_commands: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────
#  HuggingFace API 交互（零依赖）
# ─────────────────────────────────────────────

def _hf_token() -> str:
    """获取 HuggingFace token"""
    token = os.getenv("HF_TOKEN", "").strip()
    if not token:
        token = os.getenv("HUGGING_FACE_HUB_TOKEN", "").strip()
    if not token:
        token_path = os.path.expanduser("~/.cache/huggingface/token")
        if os.path.exists(token_path):
            with open(token_path) as f:
                token = f.read().strip()
    return token


def _hf_api_get(endpoint: str, timeout: int = 10) -> dict:
    """调用 HuggingFace API"""
    url = f"https://huggingface.co/api/{endpoint}"
    headers = {"User-Agent": "gitinstall/1.1"}
    token = _hf_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def is_huggingface_id(identifier: str) -> bool:
    """判断是否为 HuggingFace 模型 ID (owner/model 格式，非 GitHub URL)"""
    if "/" not in identifier:
        return False
    if any(identifier.startswith(p) for p in ("http://", "https://", "git@")):
        return False
    # HF model ID 通常是 owner/model-name 格式
    # 检查是否在已知模型库中，或以 huggingface.co 开头
    if identifier in _MODEL_VRAM_DB:
        return True
    if "huggingface.co" in identifier:
        return True
    return False


def parse_hf_url(url: str) -> str:
    """从 HuggingFace URL 提取模型 ID"""
    # https://huggingface.co/meta-llama/Llama-2-7b-hf → meta-llama/Llama-2-7b-hf
    match = re.match(r'https?://huggingface\.co/([^/]+/[^/?#]+)', url)
    if match:
        return match.group(1)
    return url


def fetch_model_info(model_id: str) -> HFModelInfo:
    """获取 HuggingFace 模型详细信息"""
    model_id = parse_hf_url(model_id)
    info = HFModelInfo(model_id=model_id)

    # 先查本地数据库
    if model_id in _MODEL_VRAM_DB:
        db = _MODEL_VRAM_DB[model_id]
        info.params_b = db["params_b"]
        info.family = db["family"]

    # 检查受限模型
    if model_id in _GATED_MODELS:
        info.is_gated = True

    # 尝试 API 获取更多信息
    try:
        data = _hf_api_get(f"models/{model_id}")
        info.pipeline_tag = data.get("pipeline_tag", "")
        info.library_name = data.get("library_name", "")
        info.license = data.get("cardData", {}).get("license", "")
        info.downloads = data.get("downloads", 0)
        info.likes = data.get("likes", 0)
        info.tags = data.get("tags", [])
        info.is_gated = data.get("gated", False) or info.is_gated
        # 文件列表
        siblings = data.get("siblings", [])
        info.siblings = [s.get("rfilename", "") for s in siblings]
        # 如果本地数据库没有参数量，尝试从 safetensors metadata 推断
        if not info.params_b:
            info.params_b = _infer_params_from_metadata(data)
    except Exception as e:
        info.error = str(e)

    return info


def _infer_params_from_metadata(data: dict) -> float:
    """从 HF API 响应推断模型参数量"""
    # safetensors.parameters 字段
    st = data.get("safetensors", {})
    if isinstance(st, dict):
        params = st.get("parameters", {})
        if isinstance(params, dict):
            total = sum(params.values())
            if total > 0:
                return round(total / 1e9, 1)
    # 从 model ID 或 tag 推断
    model_id = data.get("modelId", "")
    for tag in data.get("tags", []) + [model_id]:
        match = re.search(r'(\d+)[._-]?(\d*)\s*[bB]', str(tag))
        if match:
            b = float(match.group(1))
            if match.group(2):
                b = float(f"{match.group(1)}.{match.group(2)}")
            if 0.1 <= b <= 1000:
                return b
    return 0.0


# ─────────────────────────────────────────────
#  VRAM 智能评估
# ─────────────────────────────────────────────

def estimate_vram(
    model_id: str,
    available_vram_gb: float,
    use_case: str = "inference",  # "inference" | "finetune" | "lora"
) -> VRAMEstimate:
    """
    智能评估给定模型在目标硬件上的可用性。

    Returns:
        VRAMEstimate 包含是否能运行、推荐方案、安装命令等
    """
    # 优先从本地数据库获取参数量，避免网络请求
    db_info = _MODEL_VRAM_DB.get(model_id)
    if db_info:
        info = HFModelInfo(model_id=model_id, params_b=db_info["params_b"])
    elif model_id.startswith("http") or "/" in model_id:
        info = fetch_model_info(model_id)
    else:
        info = HFModelInfo(model_id=model_id)

    # 如果还没有参数量，尝试从 ID 推断
    params_b = info.params_b
    if not params_b:
        match = re.search(r'(\d+\.?\d*)\s*[bB]', model_id)
        if match:
            params_b = float(match.group(1))

    if not params_b:
        return VRAMEstimate(
            model_id=model_id, params_b=0, available_vram_gb=available_vram_gb,
            can_run=False, recommended_method="unknown", vram_needed_gb=0,
            advice=f"无法确定模型 {model_id} 的参数量，请手动指定",
        )

    # 量化方案及其 VRAM 占用公式
    quant_formulas = {
        "fp32":  lambda b: b * 4.0 + 2,           # 4 bytes/param + overhead
        "fp16":  lambda b: b * 2.0 + 1.5,         # 2 bytes/param
        "q8":    lambda b: b * 1.1 + 1.0,         # ~1.1 bytes/param
        "q6_k":  lambda b: b * 0.85 + 0.8,        # ~0.85 bytes/param
        "q5_k":  lambda b: b * 0.75 + 0.7,        # ~0.75 bytes/param
        "q4_k":  lambda b: b * 0.6 + 0.5,         # ~0.6 bytes/param
        "q4_0":  lambda b: b * 0.55 + 0.5,        # ~0.55 bytes/param
        "q3_k":  lambda b: b * 0.45 + 0.4,        # ~0.45 bytes/param
        "q2_k":  lambda b: b * 0.35 + 0.3,        # ~0.35 bytes/param
    }

    # LoRA 微调额外开销
    lora_overhead_gb = 0.0
    if use_case == "lora":
        lora_overhead_gb = params_b * 0.15 + 2.0  # ~15% 参数 + optimizer states
    elif use_case == "finetune":
        lora_overhead_gb = params_b * 3.0 + 4.0   # full finetune 需要 ~3x model size

    quality_labels = {
        "fp32": "完美（无损）", "fp16": "极好", "q8": "优秀",
        "q6_k": "很好", "q5_k": "良好", "q4_k": "良好（性价比最佳）",
        "q4_0": "可用", "q3_k": "一般", "q2_k": "较差",
    }

    options = []
    recommended = None
    for quant, formula in quant_formulas.items():
        vram = round(formula(params_b) + lora_overhead_gb, 1)
        fits = vram <= available_vram_gb
        options.append({
            "quant": quant, "vram_gb": vram, "fits": fits,
            "quality": quality_labels.get(quant, ""),
        })
        if fits and recommended is None:
            recommended = quant

    can_run = recommended is not None
    vram_needed = quant_formulas.get(recommended or "q2_k", lambda b: b * 0.35)(params_b)

    # 生成安装命令
    install_cmds = _generate_install_commands(model_id, info, recommended, use_case)

    # 生成建议
    if not can_run:
        min_q = options[-1]["vram_gb"] if options else 0
        advice = (
            f"❌ 当前 VRAM {available_vram_gb}GB 不足以{_use_case_label(use_case)} {params_b}B 模型\n"
            f"   最低需 {min_q}GB (Q2_K 量化)\n"
            f"   建议: 使用更小的模型，或通过 API 调用"
        )
    elif recommended in ("fp32", "fp16"):
        advice = f"✅ VRAM 充足！可以 {recommended.upper()} 全精度{_use_case_label(use_case)} {params_b}B 模型"
    elif recommended in ("q8", "q6_k"):
        advice = f"✅ 推荐 {recommended.upper()} 量化，质量损失极小"
    else:
        advice = f"⚠️ 推荐 {recommended.upper()} 量化（VRAM 紧张，会有一定质量损失）"

    if info.is_gated:
        advice += "\n⚠️ 这是受限模型，需要先在 HuggingFace 网站同意使用协议，并设置 HF_TOKEN"

    return VRAMEstimate(
        model_id=model_id, params_b=params_b,
        available_vram_gb=available_vram_gb,
        can_run=can_run, recommended_method=recommended or "api_only",
        vram_needed_gb=round(vram_needed, 1),
        options=options, advice=advice,
        install_commands=install_cmds,
    )


def _use_case_label(use_case: str) -> str:
    return {"inference": "推理运行", "finetune": "全量微调", "lora": "LoRA 微调"}.get(use_case, "运行")


def _generate_install_commands(
    model_id: str, info: HFModelInfo,
    recommended_quant: Optional[str], use_case: str,
) -> list[str]:
    """根据模型信息和推荐方案生成安装命令"""
    cmds = []

    if info.is_gated:
        cmds.append("# 1. 请先设置 HuggingFace Token")
        cmds.append("export HF_TOKEN=hf_your_token_here")
        cmds.append("# 或: huggingface-cli login")

    pipeline = info.pipeline_tag or "text-generation"

    if pipeline in ("text-generation", "text2text-generation"):
        # LLM 模型
        if recommended_quant and recommended_quant.startswith("q"):
            # 推荐使用 llama.cpp / Ollama 运行量化版本
            cmds.append("# 方式一: 使用 Ollama（推荐，最简单）")
            ollama_name = _model_to_ollama_name(model_id)
            if ollama_name:
                cmds.append(f"ollama pull {ollama_name}")
                cmds.append(f"ollama run {ollama_name}")
            cmds.append("")
            cmds.append("# 方式二: 使用 llama.cpp + GGUF 量化文件")
            cmds.append("pip install llama-cpp-python")
        else:
            # FP16/FP32 全精度
            cmds.append("pip install transformers torch accelerate")
            if use_case == "lora":
                cmds.append("pip install peft bitsandbytes")
            cmds.append(f"python -c \"from transformers import AutoModelForCausalLM, AutoTokenizer; "
                        f"m = AutoModelForCausalLM.from_pretrained('{model_id}', torch_dtype='auto', "
                        f"device_map='auto'); t = AutoTokenizer.from_pretrained('{model_id}')\"")

    elif pipeline in ("text-to-image", "image-to-image"):
        # Diffusion 模型
        cmds.append("pip install diffusers transformers torch accelerate")
        cmds.append(f"python -c \"from diffusers import DiffusionPipeline; "
                    f"pipe = DiffusionPipeline.from_pretrained('{model_id}', torch_dtype='auto')\"")

    elif pipeline in ("automatic-speech-recognition",):
        # ASR 模型 (Whisper 等)
        cmds.append("pip install transformers torch torchaudio")
        cmds.append(f"python -c \"from transformers import pipeline; "
                    f"pipe = pipeline('automatic-speech-recognition', model='{model_id}')\"")

    else:
        # 通用 transformers
        cmds.append("pip install transformers torch")
        cmds.append(f"python -c \"from transformers import AutoModel; "
                    f"m = AutoModel.from_pretrained('{model_id}')\"")

    return cmds


def _model_to_ollama_name(model_id: str) -> Optional[str]:
    """将 HuggingFace 模型 ID 映射到 Ollama 模型名"""
    mapping = {
        "meta-llama/Llama-3.1-8B": "llama3.1:8b",
        "meta-llama/Llama-3.1-70B": "llama3.1:70b",
        "meta-llama/Llama-3.2-1B": "llama3.2:1b",
        "meta-llama/Llama-3.2-3B": "llama3.2:3b",
        "Qwen/Qwen2.5-0.5B": "qwen2.5:0.5b",
        "Qwen/Qwen2.5-1.5B": "qwen2.5:1.5b",
        "Qwen/Qwen2.5-3B": "qwen2.5:3b",
        "Qwen/Qwen2.5-7B": "qwen2.5:7b",
        "Qwen/Qwen2.5-14B": "qwen2.5:14b",
        "Qwen/Qwen2.5-32B": "qwen2.5:32b",
        "Qwen/Qwen2.5-72B": "qwen2.5:72b",
        "Qwen/Qwen3-8B": "qwen3:8b",
        "Qwen/Qwen3-32B": "qwen3:32b",
        "Qwen/QwQ-32B": "qwq:32b",
        "Qwen/Qwen2.5-Coder-7B": "qwen2.5-coder:7b",
        "Qwen/Qwen2.5-Coder-32B": "qwen2.5-coder:32b",
        "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B": "deepseek-r1:1.5b",
        "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B": "deepseek-r1:7b",
        "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B": "deepseek-r1:32b",
        "deepseek-ai/DeepSeek-R1-Distill-Llama-8B": "deepseek-r1:8b",
        "deepseek-ai/DeepSeek-R1-Distill-Llama-70B": "deepseek-r1:70b",
        "mistralai/Mistral-7B-v0.3": "mistral:7b",
        "google/gemma-2-2b": "gemma2:2b",
        "google/gemma-2-9b": "gemma2:9b",
        "google/gemma-2-27b": "gemma2:27b",
        "google/gemma-3-1b-it": "gemma3:1b",
        "google/gemma-3-4b-it": "gemma3:4b",
        "google/gemma-3-12b-it": "gemma3:12b",
        "google/gemma-3-27b-it": "gemma3:27b",
        "microsoft/phi-4": "phi4:14b",
    }
    return mapping.get(model_id)


# ─────────────────────────────────────────────
#  模型推荐
# ─────────────────────────────────────────────

def recommend_models_for_hardware(
    vram_gb: float,
    use_case: str = "inference",
    language: str = "zh",  # "zh" | "en" | "code"
) -> list[dict]:
    """
    根据硬件 VRAM 推荐最适合的模型。

    Args:
        vram_gb: 可用 GPU 内存
        use_case: "inference" / "lora" / "finetune"
        language: "zh" (中文优先) / "en" (英文) / "code" (代码)

    Returns:
        推荐列表，按适合程度排序
    """
    recommendations = []

    # 按语言偏好排序的模型家族
    family_priority = {
        "zh": ["qwen3", "qwen2.5", "qwen2.5-coder", "deepseek", "glm4", "llama3", "gemma3", "phi"],
        "en": ["llama3", "gemma3", "mistral", "phi", "qwen2.5"],
        "code": ["qwen2.5-coder", "deepseek", "llama3", "phi"],
    }
    preferred = family_priority.get(language, family_priority["en"])

    for model_id, db_info in _MODEL_VRAM_DB.items():
        params_b = db_info["params_b"]
        family = db_info["family"]

        est = estimate_vram(model_id, vram_gb, use_case)
        if not est.can_run:
            continue

        # 评分: 参数量越大越好 + 语言匹配加分
        score = params_b * 10
        if family in preferred:
            score += (len(preferred) - preferred.index(family)) * 50

        recommendations.append({
            "model_id": model_id,
            "params_b": params_b,
            "family": family,
            "quant": est.recommended_method,
            "vram_needed": est.vram_needed_gb,
            "score": score,
            "advice": est.advice,
        })

    recommendations.sort(key=lambda x: x["score"], reverse=True)
    return recommendations[:10]


# ─────────────────────────────────────────────
#  格式化输出
# ─────────────────────────────────────────────

def format_vram_estimate(est: VRAMEstimate) -> str:
    """格式化 VRAM 估算结果"""
    lines = [
        f"🧠 模型 VRAM 评估: {est.model_id}",
        f"   参数量: {est.params_b}B",
        f"   可用 VRAM: {est.available_vram_gb}GB",
        "",
        est.advice,
        "",
    ]

    if est.options:
        lines.append("   量化方案对比:")
        for opt in est.options:
            mark = "✅" if opt["fits"] else "❌"
            lines.append(f"   {mark} {opt['quant']:6s} → {opt['vram_gb']:6.1f}GB  {opt['quality']}")

    if est.install_commands:
        lines.append("")
        lines.append("   安装命令:")
        for cmd in est.install_commands:
            lines.append(f"   {cmd}")

    return "\n".join(lines)


def format_model_recommendations(recs: list[dict], vram_gb: float) -> str:
    """格式化模型推荐列表"""
    lines = [
        f"🤖 基于 {vram_gb}GB VRAM 的模型推荐:",
        "",
    ]
    for i, rec in enumerate(recs, 1):
        lines.append(
            f"  {i}. {rec['model_id']} ({rec['params_b']}B)"
            f"  → {rec['quant'].upper()} ({rec['vram_needed']:.1f}GB)"
        )
    return "\n".join(lines)


# ─────────────────────────────────────────────
#  AI 模型部署自动化  (Market Opportunity #1)
# ─────────────────────────────────────────────

@dataclass
class DeploymentPlan:
    """模型部署计划"""
    model_id: str = ""
    serving_engine: str = ""     # vllm | tgi | ollama | llama_cpp | triton
    quantization: str = ""       # fp16 | int8 | int4 | awq | gptq | gguf
    gpu_type: str = ""
    gpu_count: int = 1
    port: int = 8000
    docker: bool = True
    steps: list[str] = field(default_factory=list)
    docker_compose: str = ""
    env_vars: dict[str, str] = field(default_factory=dict)
    estimated_vram_gb: float = 0.0
    estimated_tps: float = 0.0   # tokens per second


_SERVING_ENGINES = {
    "vllm": {
        "name": "vLLM",
        "docker_image": "vllm/vllm-openai:latest",
        "default_port": 8000,
        "api_compatible": "OpenAI",
        "min_vram_gb": 16,
        "supports": ["fp16", "awq", "gptq", "int8"],
    },
    "tgi": {
        "name": "Text Generation Inference (TGI)",
        "docker_image": "ghcr.io/huggingface/text-generation-inference:latest",
        "default_port": 8080,
        "api_compatible": "HuggingFace",
        "min_vram_gb": 16,
        "supports": ["fp16", "gptq", "awq", "int8", "int4"],
    },
    "ollama": {
        "name": "Ollama",
        "docker_image": "ollama/ollama:latest",
        "default_port": 11434,
        "api_compatible": "Ollama / OpenAI",
        "min_vram_gb": 4,
        "supports": ["gguf", "fp16"],
    },
    "llama_cpp": {
        "name": "llama.cpp Server",
        "docker_image": "",
        "default_port": 8080,
        "api_compatible": "OpenAI",
        "min_vram_gb": 2,
        "supports": ["gguf"],
    },
    "triton": {
        "name": "NVIDIA Triton Inference Server",
        "docker_image": "nvcr.io/nvidia/tritonserver:24.05-trtllm-python-py3",
        "default_port": 8001,
        "api_compatible": "gRPC / HTTP",
        "min_vram_gb": 24,
        "supports": ["fp16", "int8", "int4"],
    },
}


def select_serving_engine(
    model_id: str,
    vram_gb: float,
    use_case: str = "inference",
    prefer_docker: bool = True,
) -> str:
    """根据模型和硬件自动选择最佳推理引擎"""
    params_b = _lookup_params(model_id)

    # 小模型 → Ollama 最简单
    if params_b <= 13 and vram_gb >= 8:
        ollama_name = _model_to_ollama_name(model_id)
        if ollama_name:
            return "ollama"

    # 大显存 + 大模型 → vLLM（吞吐量最高）
    if vram_gb >= 24 and params_b >= 7:
        return "vllm"

    # 中等场景 → TGI
    if vram_gb >= 16:
        return "tgi"

    # 低显存 → llama.cpp
    return "llama_cpp"


def _lookup_params(model_id: str) -> float:
    """从数据库查找模型参数量"""
    mid = model_id.lower()
    for key, val in _MODEL_VRAM_DB.items():
        if key.lower() in mid:
            return val.get("params_b", 7.0)
    return 7.0  # 默认假设 7B


def generate_deployment_plan(
    model_id: str,
    vram_gb: float = 24.0,
    gpu_count: int = 1,
    engine: str = "",
    port: int = 0,
    quantization: str = "",
) -> DeploymentPlan:
    """
    生成完整的模型部署计划。

    包含部署步骤、Docker Compose 配置、环境变量等。
    """
    if not engine:
        engine = select_serving_engine(model_id, vram_gb)

    engine_info = _SERVING_ENGINES.get(engine, _SERVING_ENGINES["vllm"])
    if not port:
        port = engine_info["default_port"]

    params_b = _lookup_params(model_id)

    # 自动选择量化方案
    if not quantization:
        if engine == "ollama" or engine == "llama_cpp":
            quantization = "gguf"
        elif params_b * 2 > vram_gb * gpu_count * 0.85:
            quantization = "awq" if engine == "vllm" else "int8"
        else:
            quantization = "fp16"

    # 估算 VRAM
    bytes_per_param = {"fp16": 2, "int8": 1.1, "int4": 0.6, "awq": 0.6, "gptq": 0.6, "gguf": 0.6}
    est_vram = params_b * bytes_per_param.get(quantization, 2) * 1.15  # 15% overhead

    plan = DeploymentPlan(
        model_id=model_id,
        serving_engine=engine,
        quantization=quantization,
        gpu_type="",
        gpu_count=gpu_count,
        port=port,
        docker=bool(engine_info["docker_image"]),
        estimated_vram_gb=round(est_vram, 1),
    )

    # 生成部署步骤
    steps = []
    env_vars: dict[str, str] = {}

    if engine == "vllm":
        env_vars["HF_TOKEN"] = "${HF_TOKEN}"
        steps.append(f"# 拉取 vLLM Docker 镜像")
        steps.append(f"docker pull {engine_info['docker_image']}")
        steps.append(f"# 启动 vLLM 推理服务")
        run_cmd = (
            f"docker run --gpus all -p {port}:{port} "
            f"-e HF_TOKEN=$HF_TOKEN "
            f"-v ~/.cache/huggingface:/root/.cache/huggingface "
            f"{engine_info['docker_image']} "
            f"--model {model_id} "
            f"--port {port} "
            f"--tensor-parallel-size {gpu_count}"
        )
        if quantization in ("awq", "gptq"):
            run_cmd += f" --quantization {quantization}"
        steps.append(run_cmd)
        steps.append(f"# 测试 API")
        steps.append(
            f'curl http://localhost:{port}/v1/chat/completions '
            f'-H "Content-Type: application/json" '
            f'-d \'{{"model":"{model_id}","messages":[{{"role":"user","content":"Hello"}}]}}\''
        )

    elif engine == "tgi":
        env_vars["HF_TOKEN"] = "${HF_TOKEN}"
        steps.append(f"docker pull {engine_info['docker_image']}")
        run_cmd = (
            f"docker run --gpus all -p {port}:{port} "
            f"-e HF_TOKEN=$HF_TOKEN "
            f"-v ~/.cache/huggingface:/data "
            f"{engine_info['docker_image']} "
            f"--model-id {model_id} "
            f"--port {port} "
            f"--num-shard {gpu_count}"
        )
        if quantization in ("gptq", "awq"):
            run_cmd += f" --quantize {quantization}"
        steps.append(run_cmd)
        steps.append(
            f'curl http://localhost:{port}/generate '
            f'-H "Content-Type: application/json" '
            f'-d \'{{"inputs":"Hello","parameters":{{"max_new_tokens":50}}}}\''
        )

    elif engine == "ollama":
        ollama_name = _model_to_ollama_name(model_id) or model_id
        steps.append("# 安装 Ollama")
        steps.append("curl -fsSL https://ollama.com/install.sh | sh")
        steps.append(f"# 拉取模型")
        steps.append(f"ollama pull {ollama_name}")
        steps.append(f"# 启动对话")
        steps.append(f"ollama run {ollama_name}")

    elif engine == "llama_cpp":
        steps.append("# 安装 llama-cpp-python")
        steps.append("pip install llama-cpp-python[server]")
        steps.append(f"# 从 HuggingFace 中下载 GGUF 模型文件")
        steps.append(f"# 启动服务")
        steps.append(
            f"python -m llama_cpp.server --model ./model.gguf "
            f"--host 0.0.0.0 --port {port} --n_gpu_layers -1"
        )

    elif engine == "triton":
        steps.append(f"docker pull {engine_info['docker_image']}")
        steps.append("# 准备模型仓库 (需转为 TensorRT-LLM 格式)")
        steps.append(f"# 启动 Triton")
        steps.append(
            f"docker run --gpus all -p {port}:{port} -p 8002:8002 "
            f"-v ./model_repository:/models "
            f"{engine_info['docker_image']} tritonserver --model-repository=/models"
        )

    plan.steps = steps
    plan.env_vars = env_vars

    # 生成 docker-compose
    if plan.docker and engine_info["docker_image"]:
        plan.docker_compose = _generate_docker_compose(plan, engine_info)

    return plan


def _generate_docker_compose(plan: DeploymentPlan, engine_info: dict) -> str:
    """生成 docker-compose.yml"""
    lines = [
        "version: '3.8'",
        "services:",
        f"  {plan.serving_engine}:",
        f"    image: {engine_info['docker_image']}",
        "    ports:",
        f"      - '{plan.port}:{plan.port}'",
        "    environment:",
        "      - HF_TOKEN=${HF_TOKEN}",
        "    volumes:",
        "      - ~/.cache/huggingface:/root/.cache/huggingface",
        "    deploy:",
        "      resources:",
        "        reservations:",
        "          devices:",
        "            - driver: nvidia",
        f"              count: {plan.gpu_count}",
        "              capabilities: [gpu]",
    ]

    if plan.serving_engine == "vllm":
        cmd = f"--model {plan.model_id} --port {plan.port} --tensor-parallel-size {plan.gpu_count}"
        if plan.quantization in ("awq", "gptq"):
            cmd += f" --quantization {plan.quantization}"
        lines.append(f"    command: {cmd}")
    elif plan.serving_engine == "tgi":
        cmd = f"--model-id {plan.model_id} --port {plan.port} --num-shard {plan.gpu_count}"
        if plan.quantization in ("gptq", "awq"):
            cmd += f" --quantize {plan.quantization}"
        lines.append(f"    command: {cmd}")

    return "\n".join(lines)


def format_deployment_plan(plan: DeploymentPlan) -> str:
    """格式化部署计划为可读输出"""
    engine_info = _SERVING_ENGINES.get(plan.serving_engine, {})
    lines = [
        "🚀 AI 模型部署计划",
        f"   模型: {plan.model_id}",
        f"   引擎: {engine_info.get('name', plan.serving_engine)}",
        f"   量化: {plan.quantization.upper()}",
        f"   GPU: ×{plan.gpu_count} (预估 VRAM: {plan.estimated_vram_gb}GB)",
        f"   端口: {plan.port}",
        f"   API: {engine_info.get('api_compatible', 'REST')} 兼容",
        "",
        "📋 部署步骤:",
    ]
    for i, step in enumerate(plan.steps, 1):
        if step.startswith("#"):
            lines.append(f"   {step}")
        else:
            lines.append(f"   $ {step}")

    if plan.docker_compose:
        lines.extend(["", "📦 docker-compose.yml:", plan.docker_compose])

    return "\n".join(lines)


def generate_model_api_client(model_id: str, engine: str = "vllm", port: int = 0) -> str:
    """生成 Python API 客户端代码"""
    engine_info = _SERVING_ENGINES.get(engine, _SERVING_ENGINES["vllm"])
    if not port:
        port = engine_info["default_port"]

    if engine in ("vllm", "ollama"):
        return f'''import urllib.request, json

def chat(message: str, model: str = "{model_id}") -> str:
    """调用本地部署的 {engine_info.get("name", engine)} 模型"""
    data = json.dumps({{
        "model": model,
        "messages": [{{"role": "user", "content": message}}],
        "temperature": 0.7,
    }}).encode()
    req = urllib.request.Request(
        "http://localhost:{port}/v1/chat/completions",
        data=data,
        headers={{"Content-Type": "application/json"}},
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    return result["choices"][0]["message"]["content"]

# 使用示例
# print(chat("你好，请介绍一下你自己"))
'''
    elif engine == "tgi":
        return f'''import urllib.request, json

def generate(prompt: str) -> str:
    """调用本地部署的 TGI 模型"""
    data = json.dumps({{
        "inputs": prompt,
        "parameters": {{"max_new_tokens": 512, "temperature": 0.7}},
    }}).encode()
    req = urllib.request.Request(
        "http://localhost:{port}/generate",
        data=data,
        headers={{"Content-Type": "application/json"}},
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    return result["generated_text"]

# 使用示例
# print(generate("你好"))
'''
    return "# 暂不支持此引擎的客户端生成"
