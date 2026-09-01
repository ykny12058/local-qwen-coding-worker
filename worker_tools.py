from pathlib import Path
import subprocess
import sys


# ============================================================
# Default Workspace
# ============================================================

DEFAULT_WORKSPACE = (
    Path(__file__).parent
    / "workspace"
).resolve()

DEFAULT_WORKSPACE.mkdir(
    exist_ok=True
)


# ============================================================
# Workspace Tools
# ============================================================

class WorkspaceTools:
    """
    一个 WorkspaceTools 实例只允许操作一个项目目录。

    所有文件访问、搜索、写入和代码运行
    都被限制在该 workspace 内。
    """

    def __init__(
        self,
        workspace_path: str | Path,
    ):

        self.workspace = Path(
            workspace_path
        ).expanduser().resolve()

        if not self.workspace.exists():
            raise FileNotFoundError(
                f"Workspace 不存在："
                f"{self.workspace}"
            )

        if not self.workspace.is_dir():
            raise NotADirectoryError(
                f"Workspace 不是目录："
                f"{self.workspace}"
            )

    # ========================================================
    # Path Safety
    # ========================================================

    def safe_path(
        self,
        relative_path: str,
    ) -> Path:
        """
        将相对路径限制在当前 workspace 中。
        """

        path = (
            self.workspace
            / relative_path
        ).resolve()

        if (
            self.workspace not in path.parents
            and path != self.workspace
        ):
            raise PermissionError(
                "禁止访问当前 workspace "
                "以外的文件。"
            )

        return path

    # ========================================================
    # list_files
    # ========================================================

    def list_files(
        self,
    ) -> str:
        """
        递归列出项目普通文件。
        """

        ignored_dirs = {
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "node_modules",
            ".idea",
            ".vscode",
        }

        files = []

        for path in self.workspace.rglob("*"):

            if not path.is_file():
                continue

            relative = path.relative_to(
                self.workspace
            )

            if any(
                part in ignored_dirs
                for part in relative.parts
            ):
                continue

            files.append(
                str(relative)
            )

        if not files:
            return "(workspace 当前为空)"

        return "\n".join(
            sorted(files)
        )

    # ========================================================
    # read_file
    # ========================================================

    def read_file(
        self,
        relative_path: str,
    ) -> str:
        """
        读取 UTF-8 文本文件。
        """

        path = self.safe_path(
            relative_path
        )

        if not path.exists():
            return (
                f"文件不存在："
                f"{relative_path}"
            )

        if not path.is_file():
            return (
                f"不是文件："
                f"{relative_path}"
            )

        try:

            return path.read_text(
                encoding="utf-8"
            )

        except UnicodeDecodeError:

            return (
                "读取失败："
                f"{relative_path} "
                "不是 UTF-8 文本文件。"
            )

        except Exception as exc:

            return (
                "读取异常："
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    # ========================================================
    # search_code
    # ========================================================

    def search_code(
        self,
        query: str,
        file_pattern: str = "*",
        max_results: int = 50,
    ) -> str:
        """
        在当前项目中搜索字符串。
        """

        if not query:
            return (
                "搜索失败：query 不能为空。"
            )

        max_results = max(
            1,
            min(
                int(max_results),
                200,
            ),
        )

        ignored_dirs = {
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "node_modules",
            ".idea",
            ".vscode",
        }

        results = []

        for path in self.workspace.rglob(
            file_pattern
        ):

            if not path.is_file():
                continue

            relative = path.relative_to(
                self.workspace
            )

            if any(
                part in ignored_dirs
                for part in relative.parts
            ):
                continue

            try:

                text = path.read_text(
                    encoding="utf-8"
                )

            except (
                UnicodeDecodeError,
                PermissionError,
                OSError,
            ):
                continue

            for line_number, line in enumerate(
                text.splitlines(),
                start=1,
            ):

                if query in line:

                    results.append(
                        f"{relative}:"
                        f"{line_number}: "
                        f"{line.strip()}"
                    )

                    if (
                        len(results)
                        >= max_results
                    ):
                        return "\n".join(
                            results
                        )

        if not results:
            return (
                f"未找到：{query}"
            )

        return "\n".join(
            results
        )

    # ========================================================
    # write_file
    # ========================================================

    def write_file(
        self,
        relative_path: str,
        content: str,
    ) -> str:
        """
        创建或完整覆盖文本文件。
        """

        path = self.safe_path(
            relative_path
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        path.write_text(
            content,
            encoding="utf-8",
        )

        return (
            f"已写入：{relative_path}"
        )

    # ========================================================
    # replace_in_file
    # ========================================================

    def replace_in_file(
        self,
        relative_path: str,
        old_text: str,
        new_text: str,
        expected_count: int = 1,
    ) -> str:
        """
        精确字符串替换。
        """

        path = self.safe_path(
            relative_path
        )

        if not path.exists():
            return (
                f"文件不存在："
                f"{relative_path}"
            )

        if not path.is_file():
            return (
                f"不是文件："
                f"{relative_path}"
            )

        if expected_count < 1:
            return (
                "替换未执行："
                "expected_count 必须 >= 1。"
            )

        try:

            content = path.read_text(
                encoding="utf-8"
            )

        except UnicodeDecodeError:

            return (
                "替换未执行："
                f"{relative_path} "
                "不是 UTF-8 文本文件。"
            )

        actual_count = content.count(
            old_text
        )

        if (
            actual_count
            != expected_count
        ):
            return (
                "替换未执行："
                f"old_text 实际出现 "
                f"{actual_count} 次，"
                f"expected_count="
                f"{expected_count}。"
            )

        new_content = content.replace(
            old_text,
            new_text,
            expected_count,
        )

        path.write_text(
            new_content,
            encoding="utf-8",
        )

        return (
            f"已精确替换："
            f"{relative_path} "
            f"({actual_count} 处)"
        )

    # ========================================================
    # run_python
    # ========================================================

    def run_python(
        self,
        relative_path: str,
        timeout: int = 30,
    ) -> str:
        """
        使用当前 Python 环境运行项目中的 .py 文件。
        """

        path = self.safe_path(
            relative_path
        )

        if not path.exists():
            return (
                f"文件不存在："
                f"{relative_path}"
            )

        if not path.is_file():
            return (
                f"不是文件："
                f"{relative_path}"
            )

        if (
            path.suffix.lower()
            != ".py"
        ):
            return (
                "run_python 当前只允许"
                "运行 .py 文件。"
            )

        try:

            result = subprocess.run(
                [
                    sys.executable,
                    str(path),
                ],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )

            return self._format_process_result(
                result
            )

        except subprocess.TimeoutExpired:

            return (
                f"执行超时："
                f"超过 {timeout} 秒。"
            )

        except Exception as exc:

            return (
                "执行异常："
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    # ========================================================
    # run_tests
    # ========================================================

    def run_tests(
        self,
        target: str = "",
        timeout: int = 120,
    ) -> str:
        """
        使用 pytest 运行当前项目。
        """

        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
        ]

        if target:
            command.append(
                target
            )

        try:

            result = subprocess.run(
                command,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )

            return self._format_process_result(
                result
            )

        except subprocess.TimeoutExpired:

            return (
                f"测试超时："
                f"超过 {timeout} 秒。"
            )

        except Exception as exc:

            return (
                "测试执行异常："
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    # ========================================================
    # git_diff
    # ========================================================

    def git_diff(
        self,
    ) -> str:
        """
        返回当前项目未提交 Git diff。
        """

        git_dir = (
            self.workspace
            / ".git"
        )

        if not git_dir.exists():
            return (
                "Git diff 不可用："
                "workspace 不是 Git 仓库。"
            )

        try:

            result = subprocess.run(
                [
                    "git",
                    "diff",
                    "--",
                ],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )

            if result.returncode != 0:

                return (
                    "git diff 执行失败：\n"
                    + (
                        result.stderr
                        or result.stdout
                    )
                )

            if not result.stdout.strip():

                return (
                    "(当前没有未提交修改)"
                )

            return result.stdout

        except FileNotFoundError:

            return (
                "Git diff 不可用："
                "系统找不到 git 命令。"
            )

        except Exception as exc:

            return (
                "git diff 异常："
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    # ========================================================
    # git_status
    # ========================================================

    def git_status(
        self,
    ) -> str:
        """
        返回当前项目 Git status --short。
        """

        git_dir = (
            self.workspace
            / ".git"
        )

        if not git_dir.exists():
            return (
                "Git status 不可用："
                "workspace 不是 Git 仓库。"
            )

        try:

            result = subprocess.run(
                [
                    "git",
                    "status",
                    "--short",
                ],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )

            if result.returncode != 0:

                return (
                    "git status 执行失败：\n"
                    + (
                        result.stderr
                        or result.stdout
                    )
                )

            if not result.stdout.strip():

                return (
                    "(working tree clean)"
                )

            return result.stdout

        except FileNotFoundError:

            return (
                "Git status 不可用："
                "系统找不到 git 命令。"
            )

        except Exception as exc:

            return (
                "git status 异常："
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    # ========================================================
    # Process Formatter
    # ========================================================

    @staticmethod
    def _format_process_result(
        result,
    ) -> str:

        output = []

        if result.stdout:

            output.append(
                "STDOUT:\n"
                + result.stdout.rstrip()
            )

        if result.stderr:

            output.append(
                "STDERR:\n"
                + result.stderr.rstrip()
            )

        output.append(
            f"EXIT CODE: "
            f"{result.returncode}"
        )

        return "\n\n".join(
            output
        )


# ============================================================
# Backwards-Compatible Default Tools
# ============================================================

_default_tools = WorkspaceTools(
    DEFAULT_WORKSPACE
)


def safe_path(
    relative_path: str,
) -> Path:

    return _default_tools.safe_path(
        relative_path
    )


def list_files() -> str:

    return _default_tools.list_files()


def read_file(
    relative_path: str,
) -> str:

    return _default_tools.read_file(
        relative_path
    )


def search_code(
    query: str,
    file_pattern: str = "*",
    max_results: int = 50,
) -> str:

    return _default_tools.search_code(
        query,
        file_pattern,
        max_results,
    )


def write_file(
    relative_path: str,
    content: str,
) -> str:

    return _default_tools.write_file(
        relative_path,
        content,
    )


def replace_in_file(
    relative_path: str,
    old_text: str,
    new_text: str,
    expected_count: int = 1,
) -> str:

    return _default_tools.replace_in_file(
        relative_path,
        old_text,
        new_text,
        expected_count,
    )


def run_python(
    relative_path: str,
    timeout: int = 30,
) -> str:

    return _default_tools.run_python(
        relative_path,
        timeout,
    )


def run_tests(
    target: str = "",
    timeout: int = 120,
) -> str:

    return _default_tools.run_tests(
        target,
        timeout,
    )


def git_diff() -> str:

    return _default_tools.git_diff()


def git_status() -> str:

    return _default_tools.git_status()


# 保持旧代码仍能 import WORKSPACE
WORKSPACE = DEFAULT_WORKSPACE


# ============================================================
# Direct Test
# ============================================================

if __name__ == "__main__":

    print(
        "Default Workspace:"
    )

    print(
        DEFAULT_WORKSPACE
    )

    print(
        "\nFiles:"
    )

    print(
        _default_tools.list_files()
    )

    print(
        "\nGit Status:"
    )

    print(
        _default_tools.git_status()
    )