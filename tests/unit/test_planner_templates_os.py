"""planner_templates.py 全语言模板 × OS 分支覆盖

覆盖目标：所有 _plan_* 方法的 linux/windows/macos 分支、
         _plan_from_known (docker_preferred + no-steps-docker)、
         _plan_python_ml 多种 dep_keys、_plan_from_readme
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

from planner_templates import PlanTemplateMixin


class _Planner(PlanTemplateMixin):
    """最小化 Planner 用于测试模板方法"""
    pass


def _env(os_name="macos", docker=False, gpu="cpu_only"):
    """构造最小 env dict（匹配 detector.py 输出格式）"""
    return {
        "os": {
            "type": os_name,
            "is_apple_silicon": os_name == "macos",
            "chip": "M3" if os_name == "macos" else "",
        },
        "arch": "arm64" if os_name == "macos" else "x86_64",
        "gpu": {"type": gpu, "name": "test-gpu", "vram_gb": 8},
        "runtimes": {"docker": "/usr/bin/docker"} if docker else {},
        "package_managers": {"brew": "/opt/homebrew/bin/brew"} if os_name == "macos" else {"apt": "/usr/bin/apt"},
    }


# ─── _plan_from_known ───


class TestPlanFromKnown:
    def setup_method(self):
        self.p = _Planner()

    def test_known_project_basic(self):
        from planner_known_projects import _KNOWN_PROJECTS
        # Pick a project that has 'steps' defined
        proj_with_steps = [k for k, v in _KNOWN_PROJECTS.items() if v.get("steps")]
        if proj_with_steps:
            result = self.p._plan_from_known(proj_with_steps[0], _env())
            assert len(result["steps"]) > 0

    def test_known_project_docker_preferred_with_docker(self):
        # Find a docker_preferred project
        from planner_known_projects import _KNOWN_PROJECTS
        docker_prefs = [k for k, v in _KNOWN_PROJECTS.items()
                        if v.get("by_platform") == "docker_preferred"]
        if docker_prefs:
            result = self.p._plan_from_known(docker_prefs[0], _env(docker=True))
            assert len(result["steps"]) > 0

    def test_known_project_docker_preferred_no_docker(self):
        from planner_known_projects import _KNOWN_PROJECTS
        docker_prefs = [k for k, v in _KNOWN_PROJECTS.items()
                        if v.get("by_platform") == "docker_preferred"]
        if docker_prefs:
            result = self.p._plan_from_known(docker_prefs[0], _env(docker=False))
            assert len(result["steps"]) > 0

    def test_known_with_by_os_linux(self):
        from planner_known_projects import _KNOWN_PROJECTS
        by_os_projects = [k for k, v in _KNOWN_PROJECTS.items()
                          if v.get("by_os")]
        if by_os_projects:
            result = self.p._plan_from_known(by_os_projects[0], _env("linux"))
            assert len(result["steps"]) > 0

    def test_known_steps_docker_no_regular_steps(self):
        from planner_known_projects import _KNOWN_PROJECTS
        docker_only = [k for k, v in _KNOWN_PROJECTS.items()
                       if not v.get("steps") and v.get("steps_docker")
                       and v.get("by_platform") != "docker_preferred"]
        if docker_only:
            result = self.p._plan_from_known(docker_only[0], _env(docker=True))
            assert len(result["steps"]) > 0
            result2 = self.p._plan_from_known(docker_only[0], _env(docker=False))
            assert len(result2["steps"]) > 0


# ─── _plan_python_ml ───


class TestPlanPythonMl:
    def setup_method(self):
        self.p = _Planner()

    def test_with_requirements(self):
        result = self.p._plan_python_ml("o", "r", _env(), ["requirements.txt"], [])
        assert any("requirements" in s.get("command", "") for s in result["steps"])

    def test_with_pyproject(self):
        result = self.p._plan_python_ml("o", "r", _env(), ["pyproject.toml"], [])
        assert any("install -e" in s.get("command", "") for s in result["steps"])

    def test_with_setup_py(self):
        result = self.p._plan_python_ml("o", "r", _env(), ["setup.py"], [])
        assert any("install -e" in s.get("command", "") for s in result["steps"])


# ─── OS 分支参数化 ───

OS_VARIANTS = ["macos", "linux", "windows"]


class TestPlanDocker:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_docker_os(self, os_name):
        p = _Planner()
        result = p._plan_docker("owner", "repo", _env(os_name))
        assert result["strategy"] == "type_template_docker"
        assert len(result["steps"]) >= 2


class TestPlanRust:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_rust_os(self, os_name):
        p = _Planner()
        result = p._plan_rust("owner", "repo", _env(os_name))
        assert result["strategy"] == "type_template_rust"


class TestPlanGo:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_go_os(self, os_name):
        p = _Planner()
        result = p._plan_go("owner", "repo", _env(os_name))
        assert result["strategy"] == "type_template_go"


class TestPlanCmake:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_cmake_os(self, os_name):
        p = _Planner()
        result = p._plan_cmake("owner", "repo", _env(os_name))
        assert result["strategy"] == "type_template_cmake"


class TestPlanJava:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_java_os(self, os_name):
        p = _Planner()
        result = p._plan_java("owner", "repo", _env(os_name), frozenset(), "")
        assert result["strategy"] == "type_template_java"

    def test_java_with_pom(self):
        p = _Planner()
        result = p._plan_java("o", "r", _env(), frozenset({"pom.xml"}), "")
        assert "Maven" in str(result["steps"])

    def test_java_with_gradle(self):
        p = _Planner()
        result = p._plan_java("o", "r", _env(), frozenset({"build.gradle"}), "")
        assert "Gradle" in str(result["steps"])


class TestPlanMake:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_make_os(self, os_name):
        p = _Planner()
        result = p._plan_make("owner", "repo", _env(os_name), frozenset())
        assert result["strategy"] == "type_template_make"


class TestPlanRuby:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_ruby_os(self, os_name):
        p = _Planner()
        result = p._plan_ruby("owner", "repo", _env(os_name))
        assert result["strategy"] == "type_template_ruby"


class TestPlanPhp:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_php_os(self, os_name):
        p = _Planner()
        result = p._plan_php("owner", "repo", _env(os_name))
        assert result["strategy"] == "type_template_php"


class TestPlanDotnet:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_dotnet_os(self, os_name):
        p = _Planner()
        result = p._plan_dotnet("owner", "repo", _env(os_name))
        assert result["strategy"] == "type_template_dotnet"


class TestPlanSwift:
    def test_swift(self):
        p = _Planner()
        result = p._plan_swift("owner", "repo", _env())
        assert result["strategy"] == "type_template_swift"


class TestPlanKotlin:
    def test_kotlin(self):
        p = _Planner()
        result = p._plan_kotlin("owner", "repo", _env())
        assert result["strategy"] == "type_template_kotlin"


class TestPlanScala:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_scala_os(self, os_name):
        p = _Planner()
        result = p._plan_scala("owner", "repo", _env(os_name))
        assert result["strategy"] == "type_template_scala"


class TestPlanDart:
    @pytest.mark.parametrize("os_name", ["macos", "linux"])
    def test_dart_os(self, os_name):
        p = _Planner()
        result = p._plan_dart("owner", "repo", _env(os_name))
        assert result["strategy"] == "type_template_dart"


class TestPlanElixir:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_elixir_os(self, os_name):
        p = _Planner()
        result = p._plan_elixir("owner", "repo", _env(os_name))
        assert result["strategy"] == "type_template_elixir"


class TestPlanLua:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_lua_os(self, os_name):
        p = _Planner()
        result = p._plan_lua("owner", "repo", _env(os_name))
        assert result["strategy"] == "type_template_lua"


class TestPlanPerl:
    def test_perl(self):
        p = _Planner()
        result = p._plan_perl("owner", "repo", _env())
        assert result["strategy"] == "type_template_perl"


class TestPlanR:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_r_os(self, os_name):
        p = _Planner()
        result = p._plan_r("owner", "repo", _env(os_name))
        assert result["strategy"] == "type_template_r"


class TestPlanJulia:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_julia_os(self, os_name):
        p = _Planner()
        result = p._plan_julia("owner", "repo", _env(os_name))
        assert result["strategy"] == "type_template_julia"


class TestPlanZig:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_zig_os(self, os_name):
        p = _Planner()
        result = p._plan_zig("owner", "repo", _env(os_name), {})
        assert result["strategy"] == "type_template_zig"

    def test_zig_with_min_version(self):
        p = _Planner()
        dep_files = {"build.zig.zon": '.minimum_zig_version = "0.13.0"'}
        result = p._plan_zig("owner", "repo", _env(), dep_files)
        assert "0.13.0" in result["notes"]

    def test_zig_legacy_build_api(self):
        p = _Planner()
        dep_files = {"build.zig": 'root_source_file = .{ .path = "src/main.zig" }'}
        result = p._plan_zig("owner", "repo", _env(), dep_files)
        assert result["strategy"] == "type_template_zig"


class TestPlanClojure:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_clojure_os(self, os_name):
        p = _Planner()
        result = p._plan_clojure("owner", "repo", _env(os_name))
        assert result["strategy"] == "type_template_clojure"


class TestPlanMeson:
    @pytest.mark.parametrize("os_name", OS_VARIANTS)
    def test_meson_os(self, os_name):
        p = _Planner()
        result = p._plan_meson("owner", "repo", _env(os_name))
        assert result["strategy"] == "type_template_meson"


class TestPlanShell:
    def test_shell(self):
        p = _Planner()
        result = p._plan_shell("owner", "repo", _env())
        assert result["strategy"] == "type_template_shell"


# ─── _plan_from_readme ───


class TestPlanFromReadme:
    def setup_method(self):
        self.p = _Planner()

    def test_extract_commands(self):
        readme = """# My Project
```bash
git clone https://github.com/owner/repo.git
cd repo
pip install -r requirements.txt
```
"""
        result = self.p._plan_from_readme("owner", "repo", readme)
        assert result["strategy"] == "readme_extract"
        assert len(result["steps"]) >= 2

    def test_no_commands(self):
        result = self.p._plan_from_readme("owner", "repo", "Just text, no code blocks")
        assert result["strategy"] == "readme_extract"
        assert result["confidence"] == "low"

    def test_dangerous_commands_filtered(self):
        readme = """
```bash
rm -rf /
pip install flask
```
"""
        result = self.p._plan_from_readme("owner", "repo", readme)
        cmds = [s["command"] for s in result["steps"]]
        assert not any("rm -rf /" in c for c in cmds)

    def test_curl_pipe_bash_warning(self):
        readme = """
```bash
curl -fsSL https://example.com/install.sh | bash
```
"""
        result = self.p._plan_from_readme("owner", "repo", readme)
        if result["steps"]:
            assert result["steps"][0].get("_warning")

    def test_multiple_package_managers(self):
        readme = """
```bash
npm install
cargo install mypackage
docker run myimage
brew install something
```
"""
        result = self.p._plan_from_readme("owner", "repo", readme)
        assert len(result["steps"]) >= 3


# ─── _plan_conda ───


class TestPlanConda:
    def test_conda(self):
        p = _Planner()
        result = p._plan_conda("owner", "repo", _env())
        assert result["strategy"] == "type_template_conda"


class TestPlanNode:
    def test_node(self):
        p = _Planner()
        result = p._plan_node("owner", "repo", _env())
        assert result["strategy"] == "type_template_node"


class TestPlanPythonRust:
    def test_plan_python_rust_package(self):
        p = _Planner()
        result = p._plan_python_rust_package("owner", "repo", _env())
        assert result["strategy"] == "type_template_python_rust"


class TestPlanPython:
    def test_with_requirements(self):
        p = _Planner()
        result = p._plan_python("owner", "repo", _env(), ["requirements.txt"])
        assert any("requirements" in s.get("command", "") for s in result["steps"])

    def test_with_pyproject(self):
        p = _Planner()
        result = p._plan_python("owner", "repo", _env(), ["pyproject.toml"])
        assert any("install" in s.get("command", "") for s in result["steps"])

    def test_with_setup_py(self):
        p = _Planner()
        result = p._plan_python("owner", "repo", _env(), ["setup.py"])
        assert any("install" in s.get("command", "") for s in result["steps"])

    def test_no_deps(self):
        p = _Planner()
        result = p._plan_python("owner", "repo", _env(), [])
        assert len(result["steps"]) > 0


class TestPlanHaskell:
    @pytest.mark.parametrize("os_name", ["macos", "linux"])
    def test_haskell_os(self, os_name):
        p = _Planner()
        result = p._plan_haskell("owner", "repo", _env(os_name), {})
        assert result["strategy"] == "type_template_haskell"

    def test_haskell_with_cabal(self):
        p = _Planner()
        dep_files = {"mycabal.cabal": "name: mycabal"}
        result = p._plan_haskell("owner", "repo", _env(), dep_files)
        assert "cabal" in str(result["steps"]).lower() or "cabal" in result.get("notes", "").lower()

    def test_haskell_stack_only(self):
        p = _Planner()
        dep_files = {"stack.yaml": "resolver: lts-21.0"}
        result = p._plan_haskell("owner", "repo", _env(), dep_files)
        assert result["strategy"] == "type_template_haskell"
