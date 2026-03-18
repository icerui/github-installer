"""
installer_registry.py - 安装器服务注册表
==========================================

灵感来源：PersonalBrain 的 Service Registry 模式

将 pip/npm/cargo/go/docker/conda/brew/apt 等包管理器抽象为
可注册的 Installer Service，新包管理器只需实现接口即可插入。

架构：
  BaseInstaller (抽象基类)
    ├── PipInstaller
    ├── NpmInstaller
    ├── CargoInstaller
    ├── GoInstaller
    ├── DockerInstaller
    ├── CondaInstaller
    ├── BrewInstaller
    └── AptInstaller

零外部依赖，纯 Python 标准库。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class InstallerInfo:
    """安装器元信息"""
    name: str
    display_name: str
    ecosystems: list[str]          # python, node, rust, go, system, container
    install_command: str            # 主安装命令模板
    version_command: str            # 版本检测命令
    available: bool = False
    version: str = ""
    priority: int = 50             # 优先级 (0=最高, 100=最低)
    platforms: list[str] = field(default_factory=lambda: ["darwin", "linux", "win32"])


class BaseInstaller:
    """安装器基类"""

    info: InstallerInfo

    def __init__(self):
        self.info = self._get_info()
        self._detect()

    def _get_info(self) -> InstallerInfo:
        """子类必须实现：返回安装器 meta"""
        raise NotImplementedError

    def _detect(self):
        """检测安装器是否可用"""
        try:
            cmd = self.info.version_command.split()
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                self.info.available = True
                ver = result.stdout.strip().split("\n")[0]
                # 提取版本号
                for part in ver.split():
                    if any(c.isdigit() for c in part):
                        self.info.version = part.strip("(),v")
                        break
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            self.info.available = False

    def can_handle(self, project_types: list[str], dep_files: dict) -> bool:
        """判断是否能处理该项目"""
        raise NotImplementedError

    def generate_install_steps(self, project_info: dict) -> list[dict]:
        """生成安装步骤"""
        raise NotImplementedError

    def to_dict(self) -> dict:
        return {
            "name": self.info.name,
            "display_name": self.info.display_name,
            "ecosystems": self.info.ecosystems,
            "available": self.info.available,
            "version": self.info.version,
            "priority": self.info.priority,
        }


# ─────────────────────────────────────────────
#  内置安装器实现
# ─────────────────────────────────────────────

class PipInstaller(BaseInstaller):
    def _get_info(self) -> InstallerInfo:
        return InstallerInfo(
            name="pip", display_name="pip (Python)",
            ecosystems=["python"],
            install_command="pip install -r requirements.txt",
            version_command="pip --version",
            priority=30,
        )

    def can_handle(self, project_types: list[str], dep_files: dict) -> bool:
        if not self.info.available:
            return False
        py_files = {"requirements.txt", "setup.py", "pyproject.toml",
                    "setup.cfg", "Pipfile"}
        return bool(set(dep_files.keys()) & py_files) or "python" in project_types

    def generate_install_steps(self, project_info: dict) -> list[dict]:
        steps = []
        dep_files = project_info.get("dependency_files", {})
        if "requirements.txt" in dep_files:
            steps.append({
                "command": "pip install -r requirements.txt",
                "description": "安装 Python 依赖",
            })
        elif "pyproject.toml" in dep_files:
            steps.append({
                "command": "pip install -e .",
                "description": "安装 Python 项目（editable）",
            })
        elif "setup.py" in dep_files:
            steps.append({
                "command": "pip install -e .",
                "description": "安装 Python 项目",
            })
        return steps


class NpmInstaller(BaseInstaller):
    def _get_info(self) -> InstallerInfo:
        return InstallerInfo(
            name="npm", display_name="npm (Node.js)",
            ecosystems=["node", "javascript", "typescript"],
            install_command="npm install",
            version_command="npm --version",
            priority=30,
        )

    def can_handle(self, project_types: list[str], dep_files: dict) -> bool:
        if not self.info.available:
            return False
        return "package.json" in dep_files or "node" in project_types

    def generate_install_steps(self, project_info: dict) -> list[dict]:
        return [{"command": "npm install", "description": "安装 Node.js 依赖"}]


class CargoInstaller(BaseInstaller):
    def _get_info(self) -> InstallerInfo:
        return InstallerInfo(
            name="cargo", display_name="Cargo (Rust)",
            ecosystems=["rust"],
            install_command="cargo build --release",
            version_command="cargo --version",
            priority=40,
        )

    def can_handle(self, project_types: list[str], dep_files: dict) -> bool:
        if not self.info.available:
            return False
        return "Cargo.toml" in dep_files or "rust" in project_types

    def generate_install_steps(self, project_info: dict) -> list[dict]:
        return [{"command": "cargo build --release", "description": "编译 Rust 项目"}]


class GoInstaller(BaseInstaller):
    def _get_info(self) -> InstallerInfo:
        return InstallerInfo(
            name="go", display_name="Go",
            ecosystems=["go"],
            install_command="go build ./...",
            version_command="go version",
            priority=40,
        )

    def can_handle(self, project_types: list[str], dep_files: dict) -> bool:
        if not self.info.available:
            return False
        return "go.mod" in dep_files or "go" in project_types

    def generate_install_steps(self, project_info: dict) -> list[dict]:
        return [
            {"command": "go mod download", "description": "下载 Go 依赖"},
            {"command": "go build ./...", "description": "编译 Go 项目"},
        ]


class DockerInstaller(BaseInstaller):
    def _get_info(self) -> InstallerInfo:
        return InstallerInfo(
            name="docker", display_name="Docker",
            ecosystems=["container"],
            install_command="docker compose up -d",
            version_command="docker --version",
            priority=60,
        )

    def can_handle(self, project_types: list[str], dep_files: dict) -> bool:
        if not self.info.available:
            return False
        docker_files = {"Dockerfile", "docker-compose.yml",
                        "docker-compose.yaml", "compose.yml", "compose.yaml"}
        return bool(set(dep_files.keys()) & docker_files) or "docker" in project_types

    def generate_install_steps(self, project_info: dict) -> list[dict]:
        dep_files = project_info.get("dependency_files", {})
        compose_files = {"docker-compose.yml", "docker-compose.yaml",
                        "compose.yml", "compose.yaml"}
        if set(dep_files.keys()) & compose_files:
            return [{"command": "docker compose up -d",
                     "description": "启动 Docker 容器"}]
        return [{"command": "docker build -t app .",
                 "description": "构建 Docker 镜像"}]


class CondaInstaller(BaseInstaller):
    def _get_info(self) -> InstallerInfo:
        return InstallerInfo(
            name="conda", display_name="Conda",
            ecosystems=["python", "data-science"],
            install_command="conda env create -f environment.yml",
            version_command="conda --version",
            priority=35,
        )

    def can_handle(self, project_types: list[str], dep_files: dict) -> bool:
        if not self.info.available:
            return False
        conda_files = {"environment.yml", "environment.yaml", "conda.yml"}
        return bool(set(dep_files.keys()) & conda_files)

    def generate_install_steps(self, project_info: dict) -> list[dict]:
        return [{"command": "conda env create -f environment.yml",
                 "description": "创建 Conda 环境"}]


class BrewInstaller(BaseInstaller):
    def _get_info(self) -> InstallerInfo:
        return InstallerInfo(
            name="brew", display_name="Homebrew",
            ecosystems=["system"],
            install_command="brew install",
            version_command="brew --version",
            priority=50,
            platforms=["darwin"],
        )

    def can_handle(self, project_types: list[str], dep_files: dict) -> bool:
        if not self.info.available:
            return False
        return "Brewfile" in dep_files

    def generate_install_steps(self, project_info: dict) -> list[dict]:
        return [{"command": "brew bundle", "description": "安装 Homebrew 依赖"}]


class AptInstaller(BaseInstaller):
    def _get_info(self) -> InstallerInfo:
        return InstallerInfo(
            name="apt", display_name="APT (Debian/Ubuntu)",
            ecosystems=["system"],
            install_command="sudo apt-get install -y",
            version_command="apt --version",
            priority=50,
            platforms=["linux"],
        )

    def can_handle(self, project_types: list[str], dep_files: dict) -> bool:
        return self.info.available

    def generate_install_steps(self, project_info: dict) -> list[dict]:
        return []  # APT steps are generated dynamically based on missing deps


# ─────────────────────────────────────────────
#  服务注册表
# ─────────────────────────────────────────────

class InstallerRegistry:
    """安装器注册表（单例模式）"""

    _instance: Optional["InstallerRegistry"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._installers = {}
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._register_builtins()
            self._initialized = True

    def _register_builtins(self):
        """注册所有内置安装器"""
        builtin_classes = [
            PipInstaller, NpmInstaller, CargoInstaller, GoInstaller,
            DockerInstaller, CondaInstaller, BrewInstaller, AptInstaller,
        ]
        for cls in builtin_classes:
            try:
                installer = cls()
                self._installers[installer.info.name] = installer
            except Exception:
                pass

    def register(self, installer: BaseInstaller):
        """注册自定义安装器"""
        self._installers[installer.info.name] = installer

    def get(self, name: str) -> Optional[BaseInstaller]:
        """获取指定安装器"""
        return self._installers.get(name)

    def list_available(self) -> list[BaseInstaller]:
        """列出所有可用的安装器"""
        return sorted(
            [i for i in self._installers.values() if i.info.available],
            key=lambda i: i.info.priority,
        )

    def list_all(self) -> list[BaseInstaller]:
        """列出所有安装器（含不可用的）"""
        return sorted(self._installers.values(), key=lambda i: i.info.priority)

    def find_matching(self, project_types: list[str],
                      dep_files: dict) -> list[BaseInstaller]:
        """查找能处理当前项目的安装器"""
        matching = []
        for installer in self.list_available():
            if installer.can_handle(project_types, dep_files):
                matching.append(installer)
        return matching

    def format_registry(self) -> str:
        """格式化注册表状态"""
        lines = ["📦 安装器注册表：", ""]
        for installer in self.list_all():
            status = "✅" if installer.info.available else "❌"
            ver = f" v{installer.info.version}" if installer.info.version else ""
            eco = ", ".join(installer.info.ecosystems)
            lines.append(f"  {status} {installer.info.display_name}{ver}")
            lines.append(f"     生态：{eco}  优先级：{installer.info.priority}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "installers": [i.to_dict() for i in self.list_all()],
            "available_count": len(self.list_available()),
            "total_count": len(self._installers),
        }
