"""
planner_templates.py - 各语言安装计划模板
==========================================

从 planner.py 拆分出来的所有语言模板方法（Mixin）。
SmartPlanner 继承 PlanTemplateMixin 来获得所有 _plan_* 方法。

支持的语言/生态：
  Python, Python ML, Python+Rust, Conda,
  Node.js, Docker, Rust, Go, CMake, Java, Make,
  Ruby, PHP, .NET, Swift, Kotlin, Scala, Dart,
  Elixir, Haskell, Lua, Perl, R, Julia, Zig,
  Clojure, Meson, Shell
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

_THIS_DIR = str(Path(__file__).parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from planner_known_projects import _KNOWN_PROJECTS
from planner_helpers import (
    _os_type, _is_apple_silicon, _gpu_type, _cuda_major,
    _has_pm, _has_runtime, _python_cmd, _pip_cmd,
    _venv_activate, _dep_names, _dep_content,
    _is_maturin_project, _preferred_java_version,
    _has_haskell_cabal_file, _stack_resolver, _stack_lts_major,
    _zig_minimum_version, _zig_fallback_version, _version_tuple,
    _zig_uses_legacy_build_api, _haskell_system_packages,
    _haskell_macos_env_prefix, _haskell_repo_template,
    _torch_install_cmd, _node_pm,
    _make_step, _get_gpu_name,
)


class PlanTemplateMixin:
    """所有语言安装模板方法的 Mixin 类"""

    # 源信息（由 SmartPlanner.generate_plan 在调用模板前设置）
    _source_path: str = ""    # 非空 = 本地路径，跳过 git clone
    _clone_url: str = ""      # 非空 = 自定义 clone URL（非 GitHub）

    def _get_clone_url(self, owner: str, repo: str) -> str:
        """获取 clone URL，优先使用自定义 URL，否则回退 GitHub"""
        return self._clone_url or f"https://github.com/{owner}/{repo}.git"

    def _clone_and_cd(self, owner: str, repo: str) -> list[dict]:
        """生成 clone + cd 步骤，本地路径时只生成 cd"""
        if self._source_path:
            return [_make_step(f"cd {self._source_path}", "进入项目目录")]
        url = self._get_clone_url(owner, repo)
        return [
            _make_step(f"git clone --depth 1 {url}", "克隆代码"),
            _make_step(f"cd {repo}", "进入目录"),
        ]


    # ─────────────────────────────────────────
    #  策略 1：已知项目
    # ─────────────────────────────────────────

    def _plan_from_known(self, key: str, env: dict) -> dict:
        p = _KNOWN_PROJECTS[key]
        os_type = _os_type(env)
        node_install, node_dev = _node_pm(env)
        steps = []

        # 通用步骤
        for s in p.get("steps", []):
            steps.append(self._resolve_step(s, env, node_install, node_dev))

        # by_os 步骤
        by_os = p.get("by_os", {})
        os_steps = by_os.get(os_type) or by_os.get("linux", [])
        for s in os_steps:
            steps.append(self._resolve_step(s, env, node_install, node_dev))

        # docker_preferred：有 Docker 走 Docker，没有走 pip
        if p.get("by_platform") == "docker_preferred":
            if _has_runtime(env, "docker"):
                src = p.get("steps_docker", [])
            else:
                src = p.get("steps_pip", [])
            steps = [self._resolve_step(s, env, node_install, node_dev) for s in src]

        # 纯 Docker 项目（没有步骤但有 steps_docker）
        if not steps and p.get("steps_docker"):
            if _has_runtime(env, "docker"):
                steps = [self._resolve_step(s, env, node_install, node_dev)
                         for s in p.get("steps_docker", [])]
            else:
                steps = [_make_step(
                    "# 此项目需要 Docker，请先安装 Docker Desktop",
                    "⚠️ Docker 未安装"
                )]

        launch = p.get("launch") or ""
        launch = self._fill(launch, env, node_install, node_dev)

        return {
            "project_name": key,
            "steps": steps,
            "launch_command": launch,
            "notes": p.get("notes", ""),
        }

    # ─────────────────────────────────────────
    #  策略 2：类型模板
    # ─────────────────────────────────────────

    def _plan_python_ml(self, owner, repo, env, dep_keys, types):
        """AI/ML Python 项目：必须 GPU 自适应"""
        pip = _pip_cmd(env)
        steps = self._clone_and_cd(owner, repo) + [
            _make_step(f"{_python_cmd(env)} -m venv venv",  "创建虚拟环境（隔离依赖，避免污染系统）"),
            _make_step(_venv_activate(env),            "激活虚拟环境"),
            _make_step(_torch_install_cmd(env),        f"安装 PyTorch（已自动适配：{_get_gpu_name(env)}）"),
        ]
        if "requirements.txt" in dep_keys:
            steps.append(_make_step(f"{pip} install -r requirements.txt", "安装项目依赖"))
        elif "pyproject.toml" in dep_keys or "setup.py" in dep_keys:
            steps.append(_make_step(f"{pip} install -e .", "安装项目（开发模式）"))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": f"{_python_cmd(env)} app.py",
            "notes": "GPU 类型已自动识别，如有问题请检查 CUDA/ROCm 驱动版本",
            "confidence": "medium",
            "strategy": "type_template_python_ml",
        }

    def _plan_python(self, owner, repo, env, dep_keys):
        """普通 Python 项目"""
        pip = _pip_cmd(env)
        py = _python_cmd(env)
        steps = self._clone_and_cd(owner, repo) + [
            _make_step(f"{py} -m venv venv",           "创建虚拟环境"),
            _make_step(_venv_activate(env),            "激活虚拟环境"),
        ]
        if "requirements.txt" in dep_keys:
            steps.append(_make_step(f"{pip} install -r requirements.txt", "安装依赖"))
        elif "pyproject.toml" in dep_keys:
            steps.append(_make_step(f"{pip} install -e '.[all]'", "安装项目（含可选依赖）"))
        elif "setup.py" in dep_keys:
            steps.append(_make_step(f"{pip} install -e .", "安装项目"))
        else:
            steps.append(_make_step(f"{pip} install -r requirements.txt", "安装依赖（尝试）"))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": f"{py} main.py",
            "notes": "如有具体启动命令，请查阅项目 README",
            "confidence": "medium",
            "strategy": "type_template_python",
        }

    def _plan_python_rust_package(self, owner, repo, env):
        """PyO3/maturin 混合项目，优先按 Python 包安装。"""
        pip = _pip_cmd(env)
        py = _python_cmd(env)
        steps = self._clone_and_cd(owner, repo) + [
            _make_step(f"{py} -m venv venv", "创建虚拟环境"),
            _make_step(_venv_activate(env), "激活虚拟环境"),
            _make_step(f"{pip} install -e .", "安装 Python 包（自动触发 Rust 扩展构建）"),
        ]
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": py,
            "notes": "该仓库是 Rust 驱动的 Python 包，优先使用 pip/pyproject 安装而不是 cargo install。",
            "confidence": "medium",
            "strategy": "type_template_python_rust",
        }

    def _plan_conda(self, owner, repo, env):
        """含 environment.yml 的 Conda 项目"""
        env_name = repo.lower().replace("-", "_")
        steps = self._clone_and_cd(owner, repo) + [
            _make_step(f"conda env create -f environment.yml -n {env_name}", "创建 Conda 环境"),
            _make_step(f"conda activate {env_name}",                          "激活 Conda 环境"),
        ]
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": f"{_python_cmd(env)} main.py",
            "notes": f"每次使用前需运行：conda activate {env_name}",
            "confidence": "medium",
            "strategy": "type_template_conda",
        }

    def _plan_node(self, owner, repo, env):
        """Node.js / TypeScript 项目"""
        install, start = _node_pm(env)
        steps = self._clone_and_cd(owner, repo) + [
            _make_step(f"test -f package.json && {install} || echo 'INFO: 未找到 package.json，跳过依赖安装'",
                        "安装依赖（如有 package.json）"),
        ]
        # 如果有 .env.example，提醒用户创建配置
        steps.append(_make_step("cp .env.example .env 2>/dev/null || echo '无.env模板，跳过'",
                                "创建配置文件（如存在模板）"))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": start,
            "notes": "如需配置 API Key，编辑 .env 文件",
            "confidence": "medium",
            "strategy": "type_template_node",
        }

    def _plan_docker(self, owner, repo, env):
        """Docker 项目"""
        steps = []

        # 如果没有安装 Docker，先安装
        if not _has_runtime(env, "docker"):
            os_t = _os_type(env)
            if os_t == "macos":
                steps.append(_make_step("brew install --cask docker", "安装 Docker Desktop（macOS）"))
            elif os_t == "linux":
                steps.append(_make_step("curl -fsSL https://get.docker.com | sh",
                                        "安装 Docker（Linux）", warn=True))
            elif os_t == "windows":
                steps.append(_make_step("winget install Docker.DockerDesktop",
                                        "安装 Docker Desktop（Windows）"))

        steps += self._clone_and_cd(owner, repo) + [
            _make_step("docker compose up -d",         "后台启动服务"),
        ]
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": "docker compose ps",
            "notes": "查看日志：docker compose logs -f\n停止服务：docker compose down",
            "confidence": "medium",
            "strategy": "type_template_docker",
        }

    def _plan_rust(self, owner, repo, env):
        """Rust / Cargo 项目"""
        steps = []
        os_t = _os_type(env)
        if not _has_runtime(env, "rustc"):
            # 不使用 curl|sh（会被安全过滤器拦截），改用包管理器
            if os_t == "macos":
                steps.append(_make_step("brew install rust", "安装 Rust 工具链（Homebrew）"))
            elif os_t == "linux":
                steps.append(_make_step(
                    "sudo apt-get install -y cargo rustc",
                    "安装 Rust 工具链（apt）"
                ))
            elif os_t == "windows":
                steps.append(_make_step("winget install Rustlang.Rust.MSVC", "安装 Rust 工具链"))
        steps.append(_make_step(
            f"cargo install --git {self._get_clone_url(owner, repo).removesuffix('.git')}",
            f"编译安装 {repo}（Cargo，约5-15分钟）"
        ))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": repo.lower(),
            "notes": "Cargo 编译时间较长，请耐心等待",
            "confidence": "medium",
            "strategy": "type_template_rust",
        }

    def _plan_go(self, owner, repo, env, dep_keys=frozenset()):
        """Go 项目"""
        steps = []
        if not _has_runtime(env, "go"):
            os_t = _os_type(env)
            if os_t == "macos":
                steps.append(_make_step("brew install go", "安装 Go"))
            elif os_t == "linux":
                steps.append(_make_step("sudo apt install -y golang-go", "安装 Go"))
            elif os_t == "windows":
                steps.append(_make_step("winget install GoLang.Go", "安装 Go"))
        steps.extend(self._clone_and_cd(owner, repo))
        if "go.mod" in dep_keys:
            # 标准单模块 Go 项目
            steps.append(_make_step("go build ./...", f"编译 {repo}（兼容 CLI 与库项目）"))
        else:
            # 无根 go.mod — 可能是 monorepo 或旧 GOPATH 项目
            steps.append(_make_step(
                "test -f go.mod && go build ./... || "
                "find . -name go.mod -maxdepth 3 -not -path '*/vendor/*' "
                "-exec dirname {} \\; | head -5 | "
                "while read d; do echo \"Building $d\"; (cd \"$d\" && go build ./...); done",
                f"编译 {repo}（自动查找 Go 模块）"
            ))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": "echo '构建完成，请参考 README 运行具体入口'",
            "notes": "Go 仓库可能是库项目而非 CLI，默认用 go build ./... 做通用验证。",
            "confidence": "medium",
            "strategy": "type_template_go",
        }

    def _plan_cmake(self, owner, repo, env):
        """CMake / C++ 项目"""
        os_t = _os_type(env)
        steps = self._clone_and_cd(owner, repo) + [
            _make_step("mkdir build && cd build", "创建构建目录"),
        ]
        if os_t == "macos":
            steps.append(_make_step(
                "cmake .. && make -j$(sysctl -n hw.ncpu)",
                "编译（利用所有 CPU 核心）"
            ))
        elif os_t == "windows":
            steps.append(_make_step(
                "cmake .. && cmake --build . --config Release",
                "编译（Windows）"
            ))
        else:
            steps.append(_make_step(
                "cmake .. && make -j$(nproc)",
                "编译（利用所有 CPU 核心）"
            ))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": f"./build/{repo}",
            "notes": "需要安装 cmake 和 C++ 编译器（Xcode CLT / build-essential）",
            "confidence": "medium",
            "strategy": "type_template_cmake",
        }

    def _plan_java(self, owner, repo, env, dep_keys, readme):
        """Java 项目（Maven / Gradle 自适应）"""
        os_t = _os_type(env)
        java_ver = _preferred_java_version(readme)
        steps = self._clone_and_cd(owner, repo)
        # 检测 JDK
        if not _has_runtime(env, "java"):
            if os_t == "macos":
                steps.append(_make_step(f"brew install openjdk@{java_ver}", f"安装 JDK {java_ver}"))
            elif os_t == "linux":
                steps.append(_make_step(f"sudo apt install -y openjdk-{java_ver}-jdk", f"安装 JDK {java_ver}"))
            elif os_t == "windows":
                steps.append(_make_step(f"winget install Microsoft.OpenJDK.{java_ver}", f"安装 JDK {java_ver}"))
        maven_build_cmd = (
            'first_pom=$(find . -name pom.xml -print -quit); '
            'if [ -n "$first_pom" ]; then '
            'mvn_dir=$(dirname "$first_pom"); '
            'if [ -f "$mvn_dir/mvnw" ]; then '
            '(cd "$mvn_dir" && chmod +x mvnw && ./mvnw clean package -DskipTests -U -am) || '
            'mvn -f "$first_pom" clean package -DskipTests -U -am; '
            'else '
            'mvn -f "$first_pom" clean package -DskipTests -U -am; '
            'fi; '
            'else echo "INFO: 未找到 pom.xml，跳过 Maven 编译"; fi'
        )
        gradle_build_cmd = (
            'first_gradle=$(find . \\( -name build.gradle -o -name build.gradle.kts \\) -print -quit); '
            'if [ -n "$first_gradle" ]; then '
            'gradle_dir=$(dirname "$first_gradle"); '
            'if [ -f "$gradle_dir/gradlew" ]; then '
            '(cd "$gradle_dir" && chmod +x gradlew && ./gradlew build -x test --no-daemon) || '
            'gradle -p "$gradle_dir" build -x test --no-daemon; '
            'else '
            'gradle -p "$gradle_dir" build -x test --no-daemon; '
            'fi; '
            'else echo "INFO: 未找到 build.gradle，跳过 Gradle 编译"; fi'
        )
        # Maven vs Gradle
        if "pom.xml" in dep_keys:
            steps.append(_make_step(maven_build_cmd, "Maven 编译打包（跳过测试）"))
            launch = f"java -jar target/{repo}-*.jar"
        elif "build.gradle" in dep_keys or "build.gradle.kts" in dep_keys or "settings.gradle" in dep_keys or "settings.gradle.kts" in dep_keys:
            steps.append(_make_step(gradle_build_cmd, "Gradle 编译打包（跳过测试）"))
            launch = f"./gradlew bootRun || java -jar build/libs/{repo}-*.jar"
        else:
            steps.append(_make_step(
                "find . -name pom.xml -print -quit | grep -q . && ("
                + maven_build_cmd + ") || "
                "find . \\( -name build.gradle -o -name build.gradle.kts \\) -print -quit | grep -q . && ("
                + gradle_build_cmd + ") || "
                "echo 'INFO: 未找到构建文件，跳过编译'",
                "检测并编译（自适应 Maven/Gradle）"))
            launch = f"java -jar target/{repo}-*.jar 2>/dev/null || java -jar build/libs/{repo}-*.jar 2>/dev/null || echo '请参考 README 运行'"
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": launch,
            "notes": f"Java 项目首次编译需要下载依赖，耗时较长。README 若声明 JDK 版本，将优先使用 JDK {java_ver}。",
            "confidence": "medium",
            "strategy": "type_template_java",
        }

    def _plan_make(self, owner, repo, env, dep_keys):
        """Makefile / autotools 项目"""
        os_t = _os_type(env)
        steps = list(self._clone_and_cd(owner, repo))
        # autotools：有 configure 文件
        if "configure" in dep_keys:
            steps.append(_make_step("./configure", "配置编译选项"))
        # 编译
        if os_t == "macos":
            steps.append(_make_step("make -j$(sysctl -n hw.ncpu)", "编译"))
        else:
            steps.append(_make_step("make -j$(nproc)", "编译"))
        steps.append(_make_step("sudo make install", "安装到系统路径", warn=True))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": repo.lower(),
            "notes": "需要 C/C++ 编译器（macOS: Xcode CLT, Linux: build-essential）",
            "confidence": "medium",
            "strategy": "type_template_make",
        }

    def _plan_ruby(self, owner, repo, env):
        """Ruby 项目（Bundler）"""
        os_t = _os_type(env)
        steps = list(self._clone_and_cd(owner, repo))
        if not _has_runtime(env, "ruby"):
            if os_t == "macos":
                steps.append(_make_step("brew install ruby", "安装 Ruby"))
            elif os_t == "linux":
                steps.append(_make_step("sudo apt install -y ruby-full", "安装 Ruby"))
        steps.append(_make_step("bundle install", "安装 Ruby 依赖"))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": f"bundle exec ruby {repo}.rb || bundle exec rails server",
            "notes": "Ruby on Rails 项目请先运行 rails db:migrate",
            "confidence": "medium",
            "strategy": "type_template_ruby",
        }

    def _plan_php(self, owner, repo, env):
        """PHP 项目（Composer）"""
        os_t = _os_type(env)
        steps = list(self._clone_and_cd(owner, repo))
        if os_t == "macos":
            steps.append(_make_step("brew install php composer", "安装 PHP + Composer（如未安装）"))
        elif os_t == "linux":
            steps.append(_make_step(
                "sudo apt install -y php php-cli php-mbstring php-xml composer",
                "安装 PHP + Composer"))
        steps.append(_make_step("composer install", "安装 PHP 依赖"))
        steps.append(_make_step("cp .env.example .env 2>/dev/null || true", "创建配置文件"))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": "php artisan serve || php -S localhost:8000",
            "notes": "Laravel 项目需要：php artisan key:generate",
            "confidence": "medium",
            "strategy": "type_template_php",
        }

    def _plan_dotnet(self, owner, repo, env):
        """.NET / C# 项目"""
        os_t = _os_type(env)
        steps = list(self._clone_and_cd(owner, repo))
        if os_t == "macos":
            steps.append(_make_step("brew install dotnet", "安装 .NET SDK"))
        elif os_t == "linux":
            steps.append(_make_step(
                "sudo apt install -y dotnet-sdk-8.0",
                "安装 .NET SDK 8.0"))
        steps.append(_make_step("dotnet restore", "恢复 NuGet 依赖"))
        steps.append(_make_step("dotnet build", "编译项目"))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": "dotnet run",
            "notes": "如有多个项目，用 dotnet run --project src/项目名",
            "confidence": "medium",
            "strategy": "type_template_dotnet",
        }

    def _plan_swift(self, owner, repo, env):
        """Swift 项目（Swift Package Manager）"""
        steps = self._clone_and_cd(owner, repo) + [
            _make_step("swift build", "编译项目"),
        ]
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": f".build/debug/{repo}",
            "notes": "需要 Xcode 或 Swift 工具链",
            "confidence": "medium",
            "strategy": "type_template_swift",
        }

    def _plan_kotlin(self, owner, repo, env):
        """Kotlin 项目（Gradle）"""
        steps = self._clone_and_cd(owner, repo) + [
            _make_step("./gradlew build || gradle build", "编译项目"),
        ]
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": "./gradlew run",
            "notes": "需要 JDK 11+。Android 项目请用 Android Studio 打开。",
            "confidence": "medium",
            "strategy": "type_template_kotlin",
        }

    def _plan_scala(self, owner, repo, env):
        """Scala 项目（SBT）"""
        os_t = _os_type(env)
        steps = list(self._clone_and_cd(owner, repo))
        if os_t == "macos":
            steps.append(_make_step("brew install sbt", "安装 SBT（如未安装）"))
        elif os_t == "linux":
            steps.append(_make_step(
                "echo 'deb https://repo.scala-sbt.org/scalasbt/debian all main' | sudo tee /etc/apt/sources.list.d/sbt.list && sudo apt update && sudo apt install -y sbt",
                "安装 SBT"))
        steps.append(_make_step("sbt compile", "编译项目"))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": "sbt run",
            "notes": "需要 JDK 8+。首次编译会下载依赖，耗时较长。",
            "confidence": "medium",
            "strategy": "type_template_scala",
        }

    def _plan_dart(self, owner, repo, env):
        """Dart / Flutter 项目"""
        os_t = _os_type(env)
        steps = list(self._clone_and_cd(owner, repo))
        if os_t == "macos":
            steps.append(_make_step("brew install flutter || brew install dart", "安装 Flutter / Dart"))
        steps.append(_make_step("dart pub get || flutter pub get", "安装依赖"))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": "dart run || flutter run",
            "notes": "Flutter 项目需要设备/模拟器。纯 Dart 项目可直接 dart run。",
            "confidence": "medium",
            "strategy": "type_template_dart",
        }

    def _plan_elixir(self, owner, repo, env):
        """Elixir 项目（Mix）"""
        os_t = _os_type(env)
        steps = list(self._clone_and_cd(owner, repo))
        if os_t == "macos":
            steps.append(_make_step("brew install elixir", "安装 Elixir"))
        elif os_t == "linux":
            steps.append(_make_step("sudo apt install -y elixir", "安装 Elixir"))
        steps.append(_make_step("mix deps.get", "安装依赖"))
        steps.append(_make_step("mix compile", "编译项目"))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": "mix run || iex -S mix",
            "notes": "Phoenix 框架项目请用 mix phx.server",
            "confidence": "medium",
            "strategy": "type_template_elixir",
        }

    def _plan_haskell(self, owner, repo, env, dependency_files):
        """Haskell 项目（Stack / Cabal）"""
        os_t = _os_type(env)
        ghcup_cmd = "ghcup" if os_t == "macos" else "$HOME/.ghcup/bin/ghcup"
        has_cabal = _has_haskell_cabal_file(dependency_files)
        resolver = _stack_resolver(dependency_files)
        old_stack_resolver = bool(resolver) and _stack_lts_major(resolver) and _stack_lts_major(resolver) < 20
        steps = list(self._clone_and_cd(owner, repo))
        if os_t == "macos":
            steps.append(_make_step(
                "brew install ghcup && ghcup install ghc recommended && ghcup install cabal recommended && ghcup install stack latest",
                "安装 GHCup + GHC + Cabal + Stack"))
        elif os_t == "linux":
            steps.append(_make_step(
                "curl --proto '=https' --tlsv1.2 -sSf https://get-ghcup.haskell.org | sh && $HOME/.ghcup/bin/ghcup install ghc recommended && $HOME/.ghcup/bin/ghcup install cabal recommended && $HOME/.ghcup/bin/ghcup install stack latest",
                "安装 GHCup（Haskell 工具链管理）", warn=True))
        system_packages = _haskell_system_packages(dependency_files, env)
        env_prefix = _haskell_macos_env_prefix(dependency_files, env)
        if system_packages:
            if os_t == "macos":
                steps.append(_make_step(f"brew install {' '.join(system_packages)}", "安装 Haskell 常见系统库依赖"))
            elif os_t == "linux":
                steps.append(_make_step(f"sudo apt-get install -y {' '.join(system_packages)}", "安装 Haskell 常见系统库依赖"))
        repo_template = _haskell_repo_template(owner, repo, ghcup_cmd, env_prefix)
        template_notes: list[str] = []
        if repo_template is not None:
            build_cmd, launch_cmd, template_notes = repo_template
        if has_cabal:
            if repo_template is None:
                build_cmd = (
                    f"{env_prefix} ({ghcup_cmd} run --ghc recommended --cabal recommended -- cabal update && "
                    f"{ghcup_cmd} run --ghc recommended --cabal recommended -- cabal build all)"
                    f" || {ghcup_cmd} run --stack latest -- stack build"
                )
                launch_cmd = (
                    f"{env_prefix} {ghcup_cmd} run --ghc recommended --cabal recommended -- cabal run"
                    f" || {ghcup_cmd} run --stack latest -- stack run"
                )
        else:
            if repo_template is None:
                build_cmd = f"{env_prefix} {ghcup_cmd} run --stack latest -- stack build"
                launch_cmd = f"{env_prefix} {ghcup_cmd} run --stack latest -- stack run"
        steps.append(_make_step(build_cmd, "编译项目"))
        notes = ["首次编译会下载 GHC 和所有依赖，耗时可能较长。"]
        if resolver:
            notes.append(f"检测到 stack resolver: {resolver}。")
        if has_cabal:
            notes.append("优先走 ghcup run + cabal，避免 cabal/ghc PATH 不一致导致的假失败。")
        if system_packages:
            notes.append(f"已为 Haskell 预检常见系统库依赖: {', '.join(system_packages)}。")
        if env_prefix:
            notes.append("macOS 上会自动导出 Homebrew 的 pkg-config、include、lib 路径，避免 PCRE/GTK 头文件找不到。")
        if old_stack_resolver and _is_apple_silicon(env):
            notes.append("旧版 Stack resolver 在 Apple Silicon 上成功率较低；若 stack 失败，优先接受 cabal 路径结果。")
        notes.extend(template_notes)
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": launch_cmd,
            "notes": " ".join(notes),
            "confidence": "medium",
            "strategy": "type_template_haskell",
        }

    def _plan_lua(self, owner, repo, env):
        """Lua 项目"""
        os_t = _os_type(env)
        steps = list(self._clone_and_cd(owner, repo))
        if os_t == "macos":
            steps.append(_make_step("brew install lua luarocks", "安装 Lua + LuaRocks"))
        elif os_t == "linux":
            steps.append(_make_step("sudo apt install -y lua5.4 luarocks", "安装 Lua"))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": f"lua init.lua || lua {repo}.lua",
            "notes": "Neovim 插件项目无需编译，把目录放入 Neovim 插件路径即可。",
            "confidence": "medium",
            "strategy": "type_template_lua",
        }

    def _plan_perl(self, owner, repo, env):
        """Perl 项目"""
        steps = self._clone_and_cd(owner, repo) + [
            _make_step("cpanm --installdeps . || perl Makefile.PL && make && make install", "安装依赖"),
        ]
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": f"perl {repo}.pl",
            "notes": "推荐使用 cpanminus (cpanm) 管理依赖。",
            "confidence": "medium",
            "strategy": "type_template_perl",
        }

    def _plan_r(self, owner, repo, env):
        """R 语言项目"""
        os_t = _os_type(env)
        steps = list(self._clone_and_cd(owner, repo))
        if os_t == "macos":
            steps.append(_make_step("brew install r", "安装 R"))
        elif os_t == "linux":
            steps.append(_make_step("sudo apt install -y r-base r-base-dev", "安装 R"))
        steps.append(_make_step("Rscript -e 'if(file.exists(\"DESCRIPTION\")) devtools::install_deps()'", "安装依赖"))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": "Rscript main.R || R CMD BATCH main.R",
            "notes": "R 包项目可用 R CMD INSTALL . 安装到本地 R 库。",
            "confidence": "medium",
            "strategy": "type_template_r",
        }

    def _plan_julia(self, owner, repo, env):
        """Julia 项目"""
        os_t = _os_type(env)
        steps = list(self._clone_and_cd(owner, repo))
        if os_t == "macos":
            steps.append(_make_step("brew install julia", "安装 Julia"))
        elif os_t == "linux":
            steps.append(_make_step(
                "curl -fsSL https://install.julialang.org | sh",
                "安装 Julia", warn=True))
        steps.append(_make_step(
            "julia --project=. -e 'using Pkg; Pkg.instantiate()'",
            "安装 Julia 依赖"))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": "julia --project=. src/main.jl",
            "notes": "Julia 首次运行会预编译依赖，耗时较长。",
            "confidence": "medium",
            "strategy": "type_template_julia",
        }

    def _plan_zig(self, owner, repo, env, dependency_files):
        """Zig 项目"""
        os_t = _os_type(env)
        min_version = _zig_minimum_version(dependency_files)
        legacy_build_api = _zig_uses_legacy_build_api(dependency_files)
        steps = list(self._clone_and_cd(owner, repo))
        if os_t == "macos":
            steps.append(_make_step("brew install zig", "安装 Zig"))
        elif os_t == "linux":
            steps.append(_make_step("snap install zig --classic", "安装 Zig"))
        build_cmd = "zig build"
        notes = ["Zig 也可用于编译 C/C++ 项目。"]
        if os_t == "macos":
            build_cmd = (
                'if [ -d /Applications/Xcode.app/Contents/Developer ]; then '
                'export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer; '
                'fi; export SDKROOT="${SDKROOT:-$(xcrun --sdk macosx --show-sdk-path 2>/dev/null)}"; zig build'
            )
            notes.append("macOS 上 Zig 依赖 Apple SDK；若报 DarwinSdkNotFound，应优先检查 Xcode/Command Line Tools，而不是重复重试。")
        if min_version:
            notes.append(f"项目声明最低 Zig 版本: {min_version}。")
        if legacy_build_api:
            notes.append("检测到旧版 Zig build API（如 root_source_file/source_file）；若当前 Zig 0.15+ 失败，应优先判定为版本兼容问题，而不是继续盲目重试。")
        steps.append(_make_step(build_cmd, "编译项目"))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": "./zig-out/bin/" + repo,
            "notes": " ".join(notes),
            "confidence": "medium",
            "strategy": "type_template_zig",
        }

    def _plan_clojure(self, owner, repo, env):
        """Clojure 项目（Leiningen）"""
        os_t = _os_type(env)
        steps = list(self._clone_and_cd(owner, repo))
        if os_t == "macos":
            steps.append(_make_step("brew install leiningen", "安装 Leiningen"))
        elif os_t == "linux":
            steps.append(_make_step(
                "curl -fsSL https://raw.githubusercontent.com/technomancy/leiningen/stable/bin/lein -o /usr/local/bin/lein && chmod +x /usr/local/bin/lein",
                "安装 Leiningen", warn=True))
        steps.append(_make_step("lein deps", "安装 Clojure 依赖"))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": "lein run || lein repl",
            "notes": "需要 JDK 8+。lein repl 启动交互式开发环境。",
            "confidence": "medium",
            "strategy": "type_template_clojure",
        }

    def _plan_meson(self, owner, repo, env):
        """Meson 构建系统项目"""
        os_t = _os_type(env)
        steps = list(self._clone_and_cd(owner, repo))
        if os_t == "macos":
            steps.append(_make_step("brew install meson ninja", "安装 Meson + Ninja"))
        elif os_t == "linux":
            steps.append(_make_step("sudo apt install -y meson ninja-build", "安装 Meson + Ninja"))
        steps.append(_make_step("meson setup builddir", "配置 Meson 构建目录"))
        steps.append(_make_step("ninja -C builddir", "编译"))
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": f"./builddir/{repo}",
            "notes": "需要 C/C++ 编译器。meson test -C builddir 运行测试。",
            "confidence": "medium",
            "strategy": "type_template_meson",
        }

    def _plan_shell(self, owner, repo, env):
        """Shell 脚本项目"""
        steps = self._clone_and_cd(owner, repo) + [
            _make_step("chmod +x *.sh install.sh 2>/dev/null || true", "添加执行权限"),
        ]
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": "bash install.sh || bash setup.sh || bash main.sh",
            "notes": "Shell 项目通常包含 install.sh 或 setup.sh 脚本。请先阅读后再执行。",
            "confidence": "medium",
            "strategy": "type_template_shell",
        }

    # ─────────────────────────────────────────
    #  PlatformIO / Arduino 模板
    # ─────────────────────────────────────────

    def _plan_platformio(self, owner, repo, env):
        """PlatformIO / Arduino 嵌入式项目"""
        steps = self._clone_and_cd(owner, repo) + [
            _make_step("pip install platformio || brew install platformio", "安装 PlatformIO"),
            _make_step("pio run", "编译项目"),
        ]
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": "pio run -t upload",
            "notes": "PlatformIO 嵌入式项目。pio run 编译，pio run -t upload 上传到板子。\n"
                     "如需特定开发板，请查阅 platformio.ini 中的 [env] 配置。",
            "confidence": "medium",
            "strategy": "type_template_platformio",
        }

    # ─────────────────────────────────────────
    #  C/C++ 通用模板（无 CMakeLists / Makefile）
    # ─────────────────────────────────────────

    def _plan_c_cpp(self, owner, repo, env):
        """纯 C/C++ 项目的通用保底模板"""
        steps = self._clone_and_cd(owner, repo) + [
            _make_step(
                "if [ -f CMakeLists.txt ]; then mkdir -p build && cd build && cmake .. && make; "
                "elif [ -f Makefile ]; then make; "
                "elif [ -f configure ]; then ./configure && make; "
                "elif [ -f meson.build ]; then meson setup build && ninja -C build; "
                "else echo '未找到构建文件，请查阅 README'; fi",
                "自动检测并构建"
            ),
        ]
        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": "",
            "notes": "C/C++ 通用模板。自动检测 CMake/Make/configure/meson 构建系统。\n"
                     "如需安装系统依赖，请查阅 README。",
            "confidence": "low",
            "strategy": "type_template_c_cpp",
        }

    # ─────────────────────────────────────────
    #  策略 3：README 提取
    # ─────────────────────────────────────────

    def _plan_from_readme(self, owner, repo, readme, project_types=None) -> dict:
        """从 README 代码块中提取安装命令（增强版：section 感知 + 宽泛代码块匹配）"""
        steps = []
        seen: set[str] = set()
        types = set(project_types or [])

        # ── 提取代码块（匹配任意语言标签） ──
        code_blocks = re.findall(
            r'```[^\n]*\n(.*?)```',
            readme, re.DOTALL | re.IGNORECASE
        )
        # 也提取缩进代码块（4空格或1tab开头的连续行）
        for m in re.finditer(r'(?:^|\n)((?:(?:    |\t)[^\n]+\n?)+)', readme):
            block = re.sub(r'^(?:    |\t)', '', m.group(1), flags=re.MULTILINE)
            code_blocks.append(block)
        # 提取 $ 命令行（README 中 `$ pip install foo` 样式）
        for m in re.finditer(r'^\s*\$\s+(.+)$', readme, re.MULTILINE):
            code_blocks.append(m.group(1))

        # ── section 感知：优先从安装相关章节提取 ──
        _INSTALL_HEADINGS = re.compile(
            r'^#{1,3}\s+(?:install|setup|getting\s+started|build|quick\s+start'
            r'|usage|compilation|安装|构建|快速开始)',
            re.MULTILINE | re.IGNORECASE
        )
        priority_blocks = []
        for m in _INSTALL_HEADINGS.finditer(readme):
            # 取该 heading 到下一个同级 heading 之间的内容
            start = m.end()
            next_heading = re.search(r'^#{1,3}\s+', readme[start:], re.MULTILINE)
            section = readme[start:start + next_heading.start()] if next_heading else readme[start:]
            section_code = re.findall(r'```[^\n]*\n(.*?)```', section, re.DOTALL)
            priority_blocks.extend(section_code)

        # 优先使用安装章节的代码块，然后是全部代码块
        ordered_blocks = priority_blocks + code_blocks

        # 按优先级的模式（扩展至 30+）
        _PATTERNS = [
            # 克隆 / 下载
            (r'git\s+clone\s+\S+',                       "克隆代码"),
            (r'curl[^\n]+\|\s*(?:bash|sh)',              "下载执行安装脚本"),
            (r'wget\s+\S+',                              "下载文件"),
            (r'curl\s+(?:-[fsSLOk]+\s+)*\S+',           "下载文件"),
            # Python
            (r'pip(?:3)?\s+install\s+-r\s+\S+',          "安装 Python 依赖"),
            (r'pip(?:3)?\s+install\s+-e\s+\S[^\n]*',     "开发模式安装"),
            (r'pip(?:3)?\s+install\s+\S[^\n]+',          "安装 Python 包"),
            (r'python(?:3)?\s+setup\.py\s+\S+',          "Python setup.py"),
            (r'conda\s+env\s+create[^\n]+',               "创建 Conda 环境"),
            (r'conda\s+install[^\n]+',                    "Conda 安装"),
            (r'conda\s+activate\s+\S+',                  "激活 Conda 环境"),
            # Node.js
            (r'npm\s+(?:install|i|ci)[^\n]*',            "安装 Node.js 包"),
            (r'pnpm\s+install[^\n]*',                    "安装 Node.js 包（pnpm）"),
            (r'yarn(?:\s+install)?[^\n]*',               "安装 Node.js 包（yarn）"),
            (r'npx\s+\S[^\n]*',                          "npx 执行"),
            # Docker
            (r'docker\s+(?:run|pull|compose|build)[^\n]+', "Docker 运行"),
            (r'docker-compose\s+(?:up|build)[^\n]*',     "Docker Compose"),
            # 系统包管理
            (r'brew\s+install[^\n]+',                    "Homebrew 安装"),
            (r'(?:sudo\s+)?apt(?:-get)?\s+install[^\n]+', "APT 安装"),
            (r'(?:sudo\s+)?yum\s+install[^\n]+',        "YUM 安装"),
            (r'(?:sudo\s+)?pacman\s+-S[^\n]+',          "Pacman 安装"),
            # C/C++ 构建
            (r'(?:sudo\s+)?make(?:\s+install)?',         "Make 构建"),
            (r'cmake\s+[^\n]+',                          "CMake 配置"),
            (r'\./configure[^\n]*',                      "configure 配置"),
            (r'(?:sudo\s+)?make\s+-j\s*\d*[^\n]*',      "并行 Make 构建"),
            (r'mkdir\s+(?:-p\s+)?build[^\n]*',           "创建构建目录"),
            # PlatformIO / Arduino
            (r'(?:platformio|pio)\s+(?:run|init|lib)[^\n]*', "PlatformIO 构建"),
            (r'arduino-cli\s+\S[^\n]*',                  "Arduino CLI"),
            # Rust / Go / Cargo
            (r'cargo\s+(?:install|build|run)[^\n]+',     "Cargo 操作"),
            (r'go\s+(?:install|build|get)[^\n]+',        "Go 操作"),
            # Java / Gradle / Maven
            (r'(?:\./)?gradlew?\s+\S[^\n]*',             "Gradle 构建"),
            (r'mvn\s+\S[^\n]*',                          "Maven 构建"),
            # Ruby / PHP / Other
            (r'bundle\s+install[^\n]*',                  "Ruby 依赖安装"),
            (r'gem\s+install[^\n]+',                     "Gem 安装"),
            (r'composer\s+install[^\n]*',                "Composer 安装"),
            # 通用
            (r'cd\s+\S+',                               "切换目录"),
            (r'chmod\s+\+x\s+\S+',                      "添加执行权限"),
            (r'\./(?:install|setup|build|run)\S*',       "执行脚本"),
        ]

        _BLOCK_DANGEROUS = [
            r'rm\s+-rf\s+/', r':\(\)\{', r'mkfs\.', r'dd\s+if=',
            r'format\s+[cCdDeE]:', r'shutdown', r'reboot',
        ]

        for block in ordered_blocks:
            for pattern, desc in _PATTERNS:
                for m in re.finditer(pattern, block, re.IGNORECASE):
                    cmd = m.group(0).strip()
                    if any(re.search(d, cmd, re.IGNORECASE) for d in _BLOCK_DANGEROUS):
                        continue
                    if cmd not in seen and len(cmd) > 3:
                        seen.add(cmd)
                        warn = "⚠️ 执行前请确认命令来源可信" if "| sh" in cmd or "| bash" in cmd else ""
                        steps.append({"command": cmd, "description": desc, "_warning": warn})

        # ── 类型感知兜底：提取失败时根据 project_types 生成通用步骤 ──
        if not steps and types:
            steps.extend(self._clone_and_cd(owner, repo))
            if types & {"c", "cpp", "cmake", "make"}:
                steps.append(_make_step("mkdir -p build && cd build && cmake .. || cd .. && make", "构建项目"))
            elif "python" in types:
                steps.append(_make_step("pip install -e . || pip install -r requirements.txt", "安装依赖"))
            elif "node" in types:
                steps.append(_make_step("npm install", "安装依赖"))
            elif "rust" in types:
                steps.append(_make_step("cargo build --release", "构建项目"))
            elif "go" in types:
                steps.append(_make_step("go build ./...", "构建项目"))

        confidence = "medium" if len(steps) >= 3 else ("low" if steps else "low")
        message = "" if steps else (
            "⚠️ 未能从 README 中自动提取到安装命令。\n"
            "建议：(1) 手动查阅项目 README\n"
            "      (2) 配置任意 LLM（哪怕免费的 Groq）以获得 AI 辅助分析"
        )

        return {
            "project_name": f"{owner}/{repo}",
            "steps": steps,
            "launch_command": "",
            "notes": message or "规则提取模式，建议对照 README 确认每一步",
            "confidence": confidence,
            "strategy": "readme_extract",
        }

    # ─────────────────────────────────────────
    #  辅助方法
    # ─────────────────────────────────────────

    def _resolve_step(self, step: dict, env: dict,
                      node_install: str, node_dev: str) -> dict:
        cmd = self._fill(step["cmd"], env, node_install, node_dev)
        warn = "⚠️ 执行前请确认命令来源可信" if step.get("warn") else ""
        return {"command": cmd, "description": step.get("desc", ""), "_warning": warn}

    def _fill(self, text: str, env: dict, node_install: str, node_dev: str) -> str:
        """替换模板占位符为平台正确的命令"""
        if not text:
            return text
        replacements = {
            "{python}":       _python_cmd(env),
            "{pip}":          _pip_cmd(env),
            "{venv_activate}": _venv_activate(env),
            "{torch_install}": _torch_install_cmd(env),
            "{node_install}":  node_install,
            "{node_dev}":      node_dev,
        }
        for k, v in replacements.items():
            text = text.replace(k, v)
        return text


