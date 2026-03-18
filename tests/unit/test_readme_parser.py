"""
README 智能解析增强测试
======================

覆盖：
- 代码块正则修复（任意语言标签）
- section 感知解析
- 扩展命令模式（30+种）
- 类型感知兜底
- PlatformIO / Arduino 模板
- C/C++ 通用模板
- 策略链路由（planner.py 新分支）
"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from planner import SmartPlanner
from planner_templates import PlanTemplateMixin


def _env():
    return {
        "os": {"type": "macos", "arch": "arm64", "is_apple_silicon": True, "chip": "M3"},
        "gpu": {"type": "mps"},
        "package_managers": {"brew": {"available": True}},
        "runtimes": {},
    }


def _planner():
    return PlanTemplateMixin()


def _commands(plan):
    return [s["command"] for s in plan["steps"]]


# ═══════════════════════════════════════════════
#  1. 代码块正则修复
# ═══════════════════════════════════════════════


class TestCodeBlockRegex:
    """验证修复后的正则能匹配各种语言标签的代码块"""

    def setup_method(self):
        self.p = _planner()

    def test_bash_block(self):
        readme = "```bash\npip install flask\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("pip install" in s["command"] for s in result["steps"])

    def test_python_block(self):
        """```python 标签的代码块也应被解析"""
        readme = "```python\npip install torch\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("pip install" in s["command"] for s in result["steps"])

    def test_c_block(self):
        """```c 标签也应匹配"""
        readme = "```c\nmake install\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("make" in s["command"] for s in result["steps"])

    def test_cmake_block(self):
        """```cmake 标签"""
        readme = "```cmake\ncmake -DCMAKE_BUILD_TYPE=Release ..\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("cmake" in s["command"] for s in result["steps"])

    def test_no_language_tag_block(self):
        """无语言标签的代码块"""
        readme = "```\ngit clone https://github.com/x/y.git\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("git clone" in s["command"] for s in result["steps"])

    def test_console_block(self):
        readme = "```console\nbrew install something\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("brew install" in s["command"] for s in result["steps"])

    def test_dockerfile_block(self):
        """```dockerfile 标签的代码块也应被匹配"""
        readme = "```dockerfile\ndocker build -t myapp .\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("docker build" in s["command"] for s in result["steps"])

    def test_indented_code_block(self):
        """4空格缩进的代码块"""
        readme = "Install:\n\n    pip install mypackage\n    pip install -r requirements.txt\n\nDone."
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("pip install" in s["command"] for s in result["steps"])

    def test_dollar_command_lines(self):
        """$ 前缀的命令行"""
        readme = "Run:\n\n$ git clone https://github.com/x/y.git\n$ cd y\n$ make\n"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("git clone" in s["command"] for s in result["steps"])


# ═══════════════════════════════════════════════
#  2. Section 感知解析
# ═══════════════════════════════════════════════


class TestSectionAwareParsing:

    def setup_method(self):
        self.p = _planner()

    def test_installation_section_prioritized(self):
        readme = """# My Project

Some description.

## Features
```python
import mylib
result = mylib.process()
```

## Installation

```bash
pip install mylib
```

## License
MIT
"""
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("pip install" in s["command"] for s in result["steps"])

    def test_build_section(self):
        readme = """# Project

## Build

```bash
mkdir build && cd build
cmake ..
make -j4
```
"""
        result = self.p._plan_from_readme("o", "r", readme)
        cmds = _commands(result)
        assert any("cmake" in c for c in cmds)
        assert any("make" in c for c in cmds)

    def test_getting_started_section(self):
        readme = """# App

## Getting Started

```shell
git clone https://github.com/owner/app.git
cd app
npm install
```
"""
        result = self.p._plan_from_readme("o", "r", readme)
        assert len(result["steps"]) >= 2

    def test_quick_start_section(self):
        readme = """# Tool

## Quick Start

```
pip install tool
tool run
```
"""
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("pip install" in s["command"] for s in result["steps"])


# ═══════════════════════════════════════════════
#  3. 扩展命令模式
# ═══════════════════════════════════════════════


class TestExpandedPatterns:

    def setup_method(self):
        self.p = _planner()

    def test_make(self):
        readme = "```bash\nmake\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("make" in s["command"] for s in result["steps"])

    def test_make_install(self):
        readme = "```\nmake install\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("make install" in s["command"] for s in result["steps"])

    def test_cmake(self):
        readme = "```\ncmake -DCMAKE_BUILD_TYPE=Release ..\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("cmake" in s["command"] for s in result["steps"])

    def test_configure(self):
        readme = "```\n./configure --prefix=/usr/local\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("./configure" in s["command"] for s in result["steps"])

    def test_pip_install_editable(self):
        readme = "```\npip install -e .\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("pip install -e" in s["command"] for s in result["steps"])

    def test_python_setup_py(self):
        readme = "```\npython setup.py install\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("setup.py" in s["command"] for s in result["steps"])

    def test_apt_install(self):
        readme = "```\nsudo apt-get install libssl-dev\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("apt" in s["command"] for s in result["steps"])

    def test_platformio_run(self):
        readme = "```\npio run\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("pio" in s["command"] for s in result["steps"])

    def test_arduino_cli(self):
        readme = "```\narduino-cli compile --fqbn arduino:avr:uno sketch\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("arduino-cli" in s["command"] for s in result["steps"])

    def test_gradle(self):
        readme = "```\n./gradlew build\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("gradlew" in s["command"] for s in result["steps"])

    def test_mvn(self):
        readme = "```\nmvn clean install\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("mvn" in s["command"] for s in result["steps"])

    def test_cargo_build(self):
        readme = "```\ncargo build --release\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("cargo build" in s["command"] for s in result["steps"])

    def test_go_build(self):
        readme = "```\ngo build ./...\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("go build" in s["command"] for s in result["steps"])

    def test_wget(self):
        readme = "```\nwget https://example.com/file.tar.gz\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("wget" in s["command"] for s in result["steps"])

    def test_npm_ci(self):
        readme = "```\nnpm ci\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("npm ci" in s["command"] for s in result["steps"])

    def test_npx(self):
        readme = "```\nnpx create-react-app myapp\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("npx" in s["command"] for s in result["steps"])

    def test_bundle_install(self):
        readme = "```\nbundle install\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("bundle install" in s["command"] for s in result["steps"])

    def test_composer_install(self):
        readme = "```\ncomposer install\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("composer install" in s["command"] for s in result["steps"])

    def test_conda_activate(self):
        readme = "```\nconda activate myenv\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("conda activate" in s["command"] for s in result["steps"])

    def test_chmod_plus_x(self):
        readme = "```\nchmod +x run.sh\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("chmod" in s["command"] for s in result["steps"])

    def test_cd_command(self):
        readme = "```\ncd myproject\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("cd " in s["command"] for s in result["steps"])

    def test_execute_script(self):
        readme = "```\n./install.sh\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("./install" in s["command"] for s in result["steps"])

    def test_mkdir_build(self):
        readme = "```\nmkdir -p build\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("mkdir" in s["command"] for s in result["steps"])

    def test_pacman(self):
        readme = "```\nsudo pacman -S base-devel\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("pacman" in s["command"] for s in result["steps"])

    def test_yum(self):
        readme = "```\nsudo yum install gcc\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("yum" in s["command"] for s in result["steps"])

    def test_docker_compose_build(self):
        readme = "```\ndocker-compose build\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert any("docker-compose" in s["command"] for s in result["steps"])


# ═══════════════════════════════════════════════
#  4. 类型感知兜底
# ═══════════════════════════════════════════════


class TestTypeAwareFallback:
    """当 README 提取不到命令时，根据 project_types 生成通用步骤"""

    def setup_method(self):
        self.p = _planner()

    def test_c_cpp_fallback(self):
        result = self.p._plan_from_readme("o", "r", "No code here", project_types=["c", "cpp"])
        assert len(result["steps"]) >= 3
        cmds = _commands(result)
        assert any("git clone" in c for c in cmds)
        assert any("cmake" in c or "make" in c for c in cmds)

    def test_python_fallback(self):
        result = self.p._plan_from_readme("o", "r", "No code", project_types=["python"])
        assert len(result["steps"]) >= 3
        cmds = _commands(result)
        assert any("pip install" in c for c in cmds)

    def test_node_fallback(self):
        result = self.p._plan_from_readme("o", "r", "No code", project_types=["node"])
        cmds = _commands(result)
        assert any("npm install" in c for c in cmds)

    def test_rust_fallback(self):
        result = self.p._plan_from_readme("o", "r", "No code", project_types=["rust"])
        cmds = _commands(result)
        assert any("cargo build" in c for c in cmds)

    def test_go_fallback(self):
        result = self.p._plan_from_readme("o", "r", "No code", project_types=["go"])
        cmds = _commands(result)
        assert any("go build" in c for c in cmds)

    def test_no_types_no_steps(self):
        result = self.p._plan_from_readme("o", "r", "Nothing useful")
        assert result["steps"] == []
        assert result["confidence"] == "low"
        assert "未能从 README" in result["notes"]

    def test_empty_readme_with_types(self):
        result = self.p._plan_from_readme("o", "r", "", project_types=["c"])
        assert len(result["steps"]) >= 2  # git clone + cd + build


# ═══════════════════════════════════════════════
#  5. PlatformIO 模板
# ═══════════════════════════════════════════════


class TestPlatformIOTemplate:

    def test_plan_platformio(self):
        p = _planner()
        result = p._plan_platformio("owner", "repo", _env())
        assert result["strategy"] == "type_template_platformio"
        cmds = _commands(result)
        assert any("git clone" in c for c in cmds)
        assert any("pio run" in c for c in cmds)
        assert any("platformio" in c for c in cmds)

    def test_platformio_routing(self):
        """planner.py 应将 platformio 类型路由到 _plan_platformio"""
        planner = SmartPlanner()
        result = planner.generate_plan(
            owner="test", repo="pio-project",
            env=_env(),
            project_types=["platformio", "c"],
            dependency_files={"platformio.ini": ""},
            readme="",
        )
        assert result["strategy"] == "type_template_platformio"

    def test_arduino_routing(self):
        """arduino 类型也路由到 _plan_platformio"""
        planner = SmartPlanner()
        result = planner.generate_plan(
            owner="test", repo="arduino-proj",
            env=_env(),
            project_types=["arduino", "cpp"],
            dependency_files={},
            readme="",
        )
        assert result["strategy"] == "type_template_platformio"


# ═══════════════════════════════════════════════
#  6. C/C++ 通用模板
# ═══════════════════════════════════════════════


class TestCCppTemplate:

    def test_plan_c_cpp(self):
        p = _planner()
        result = p._plan_c_cpp("owner", "repo", _env())
        assert result["strategy"] == "type_template_c_cpp"
        cmds = _commands(result)
        assert any("git clone" in c for c in cmds)
        assert any("cmake" in c or "make" in c for c in cmds)

    def test_c_routing(self):
        """纯 C 项目（无构建文件）应路由到 _plan_c_cpp"""
        planner = SmartPlanner()
        result = planner.generate_plan(
            owner="test", repo="pure-c",
            env=_env(),
            project_types=["c"],
            dependency_files={},
            readme="",
        )
        assert result["strategy"] == "type_template_c_cpp"

    def test_cpp_routing(self):
        """纯 C++ 项目（无构建文件）应路由到 _plan_c_cpp"""
        planner = SmartPlanner()
        result = planner.generate_plan(
            owner="test", repo="pure-cpp",
            env=_env(),
            project_types=["cpp"],
            dependency_files={},
            readme="A C++ library.",
        )
        assert result["strategy"] == "type_template_c_cpp"

    def test_c_with_makefile_uses_make_template(self):
        """C 项目有 Makefile 应走 make 模板，不是 c_cpp"""
        planner = SmartPlanner()
        result = planner.generate_plan(
            owner="test", repo="c-with-make",
            env=_env(),
            project_types=["c", "make"],
            dependency_files={"Makefile": ""},
            readme="",
        )
        assert result["strategy"] == "type_template_make"

    def test_c_with_cmake_uses_cmake_template(self):
        """C 项目有 CMakeLists.txt 应走 cmake 模板"""
        planner = SmartPlanner()
        result = planner.generate_plan(
            owner="test", repo="c-with-cmake",
            env=_env(),
            project_types=["c", "cmake"],
            dependency_files={"CMakeLists.txt": ""},
            readme="",
        )
        assert result["strategy"] == "type_template_cmake"


# ═══════════════════════════════════════════════
#  7. fetcher.py PlatformIO 检测
# ═══════════════════════════════════════════════


class TestPlatformIODetection:

    def test_platformio_ini(self):
        from fetcher import detect_project_types
        types = detect_project_types(
            {"language": "C++"},
            "",
            {"platformio.ini": ""},
        )
        assert "platformio" in types

    def test_library_json(self):
        from fetcher import detect_project_types
        types = detect_project_types(
            {"language": "C"},
            "",
            {"library.json": ""},
        )
        assert "platformio" in types

    def test_library_properties(self):
        from fetcher import detect_project_types
        types = detect_project_types(
            {"language": "C++"},
            "",
            {"library.properties": ""},
        )
        assert "platformio" in types

    def test_ino_file(self):
        from fetcher import detect_project_types
        types = detect_project_types(
            {"language": "C++"},
            "",
            {"sketch.ino": ""},
        )
        assert "arduino" in types

    def test_no_platformio_without_signals(self):
        from fetcher import detect_project_types
        types = detect_project_types(
            {"language": "C++"},
            "",
            {"main.cpp": ""},
        )
        assert "platformio" not in types
        assert "arduino" not in types


# ═══════════════════════════════════════════════
#  8. 安全过滤 & 边界情况
# ═══════════════════════════════════════════════


class TestSafety:

    def setup_method(self):
        self.p = _planner()

    def test_dangerous_dd(self):
        readme = "```bash\ndd if=/dev/zero of=/dev/sda\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        cmds = _commands(result)
        assert not any("dd if=" in c for c in cmds)

    def test_dangerous_mkfs(self):
        readme = "```bash\nmkfs.ext4 /dev/sda1\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        assert result["steps"] == []

    def test_empty_readme(self):
        result = self.p._plan_from_readme("o", "r", "")
        assert result["strategy"] == "readme_extract"
        assert result["confidence"] == "low"

    def test_very_short_commands_filtered(self):
        """过短的命令应被过滤"""
        readme = "```\nls\n```"
        result = self.p._plan_from_readme("o", "r", readme)
        # ls 只有2个字符，不匹配任何模式，应该没有提取
        assert result["steps"] == []

    def test_confidence_medium_with_many_steps(self):
        readme = """```bash
git clone https://github.com/x/y.git
cd y
pip install -r requirements.txt
pip install -e .
python setup.py develop
```"""
        result = self.p._plan_from_readme("o", "r", readme)
        assert result["confidence"] == "medium"

    def test_dedup(self):
        """重复命令只保留一次"""
        readme = """```bash
pip install flask
```

```bash
pip install flask
```"""
        result = self.p._plan_from_readme("o", "r", readme)
        cmds = _commands(result)
        flask_cmds = [c for c in cmds if "flask" in c]
        assert len(flask_cmds) == 1


# ═══════════════════════════════════════════════
#  9. 综合 README 场景
# ═══════════════════════════════════════════════


class TestRealWorldReadmeScenarios:

    def setup_method(self):
        self.p = _planner()

    def test_typical_python_project(self):
        readme = """# My ML Project

## Installation

```bash
git clone https://github.com/owner/ml-project.git
cd ml-project
pip install -r requirements.txt
```

## Usage

```python
from ml_project import Model
model = Model()
model.train()
```
"""
        result = self.p._plan_from_readme("o", "r", readme)
        cmds = _commands(result)
        assert any("git clone" in c for c in cmds)
        assert any("pip install -r" in c for c in cmds)
        assert len(result["steps"]) >= 2

    def test_typical_cmake_project(self):
        readme = """# My C++ Library

## Build

```
mkdir build
cd build
cmake -DCMAKE_BUILD_TYPE=Release ..
make -j$(nproc)
sudo make install
```
"""
        result = self.p._plan_from_readme("o", "r", readme)
        cmds = _commands(result)
        assert any("cmake" in c for c in cmds)
        assert any("make" in c for c in cmds)

    def test_multi_language_readme(self):
        """README 中同时有多种语言的安装说明"""
        readme = """# Cross-platform Tool

## Linux

```bash
sudo apt-get install build-essential
make install
```

## macOS

```bash
brew install mytool
```

## From source

```bash
git clone https://github.com/x/tool.git
cd tool
./configure
make
```
"""
        result = self.p._plan_from_readme("o", "r", readme)
        assert len(result["steps"]) >= 3

    def test_platformio_readme(self):
        """PlatformIO 项目的 README"""
        readme = """# IoT Sensor

## Build

```
pio run
pio run -t upload
```
"""
        result = self.p._plan_from_readme("o", "r", readme)
        cmds = _commands(result)
        assert any("pio" in c for c in cmds)
