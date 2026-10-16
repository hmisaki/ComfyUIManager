"""
路径与运行环境解析 (core/config.py)

集中处理三件事，避免把本机绝对路径写死在业务代码里：

1. 程序级配置 ``launcher_config.json`` 的读写（工作区 / Conda / Git Bash / 模型源）
2. Git Bash 可执行文件的定位（配置 → 环境变量 → PATH → 常见安装位置）
3. Conda 的定位、环境枚举，以及 cmd / Git Bash 两种 shell 的激活命令拼装

设计约束：本模块**不导入 Qt**，因此可以单独 ``import`` 做冒烟测试。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(APP_DIR, "launcher_config.json")
EXAMPLE_CONFIG_FILE = os.path.join(APP_DIR, "launcher_config.example.json")

#: 程序级配置的默认值。所有字段都可以留空 —— 留空表示"自动探测"。
DEFAULT_CONFIG: Dict[str, Any] = {
    "workspace_path": "",    # 工作区根目录；首次启动时由 GUI 引导选择
    "ai_models_root": "",    # 新建实例的模型源（工作区 GlobalModels 为空时使用）
    "conda_exe": "",         # conda 可执行文件；留空则自动探测
    "conda_root": "",        # conda 安装根目录；留空则由 conda_exe 推导
    "git_bash_path": "",     # git-bash.exe；留空则自动探测
}

WORKSPACE_SUBDIRS = ("ComfyUIApps", "GlobalModels", "Data", os.path.join("Data", "previews"))


# ---------------------------------------------------------------------------
# 通用 JSON 读写
# ---------------------------------------------------------------------------

def read_json(path: str, default: Any = None) -> Any:
    """读取 JSON；文件不存在或损坏时返回 ``default``（不抛异常）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path: str, data: Any) -> None:
    """写 JSON：显式 UTF-8 + ``ensure_ascii=False``。

    修复注：旧代码用 ``json.dump`` 时没有指定编码，Windows 上会落到本地代码页，
    背景图路径或备注里带中文时可能写坏。
    """
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 程序级配置
# ---------------------------------------------------------------------------

def config_exists() -> bool:
    return os.path.isfile(CONFIG_FILE)


def load_config() -> Dict[str, Any]:
    """读取程序级配置，并用默认值补齐缺失字段。"""
    cfg = dict(DEFAULT_CONFIG)
    loaded = read_json(CONFIG_FILE, default=None)
    if isinstance(loaded, dict):
        cfg.update({k: v for k, v in loaded.items() if v is not None})
    return cfg


def save_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg or {})
    write_json(CONFIG_FILE, merged)
    return merged


def update_config(**kwargs: Any) -> Dict[str, Any]:
    cfg = load_config()
    cfg.update(kwargs)
    return save_config(cfg)


# ---------------------------------------------------------------------------
# 工作区
# ---------------------------------------------------------------------------

def ensure_workspace(path: str) -> str:
    """确保工作区骨架存在：``ComfyUIApps / GlobalModels / Data / Data/previews``。"""
    for sub in WORKSPACE_SUBDIRS:
        os.makedirs(os.path.join(path, sub), exist_ok=True)
    return path


def data_dir(workspace: str) -> str:
    return os.path.join(workspace, "Data")


def to_relative(workspace: str, path: str) -> str:
    """把工作区内的绝对路径转成相对路径，供落盘使用。

    落盘坚持相对路径之后，整个工作区被搬走 / 改名都不会再让记录变成死路径
    （旧版本记录的是绝对路径）。
    """
    if not path or not workspace:
        return path or ""
    try:
        resolved = os.path.abspath(path)
        base = os.path.abspath(workspace)
        if os.path.normcase(resolved).startswith(os.path.normcase(base + os.sep)):
            return os.path.relpath(resolved, base)
    except Exception:
        pass
    return path


# ---------------------------------------------------------------------------
# Git Bash
# ---------------------------------------------------------------------------

def _git_bash_candidates() -> List[str]:
    names = ["git-bash.exe", "git-bash"]
    found = [shutil.which(n) for n in names]
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramW6432", r"C:\Program Files"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
        os.environ.get("SystemDrive", "C:") + "\\",
    ]
    for root in roots:
        if not root:
            continue
        found.append(os.path.join(root, "Git", "git-bash.exe"))
    return [p for p in found if p]


def resolve_git_bash(cfg: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """定位 git-bash.exe：配置 → 环境变量 ``GIT_BASH_PATH`` → PATH → 常见安装位置。"""
    cfg = cfg or load_config()
    for cand in (cfg.get("git_bash_path"), os.environ.get("GIT_BASH_PATH")):
        if cand and os.path.isfile(cand):
            return cand
    for cand in _git_bash_candidates():
        if cand and os.path.isfile(cand):
            return cand
    return None


# ---------------------------------------------------------------------------
# Conda
# ---------------------------------------------------------------------------

_CACHE: Dict[str, Any] = {}


def _conda_candidates() -> List[str]:
    exe = shutil.which("conda") or shutil.which("conda.exe") or shutil.which("conda.bat")
    cands = [exe] if exe else []
    home = os.path.expanduser("~")
    program_data = os.environ.get("ProgramData", r"C:\ProgramData")
    roots = [
        os.path.join(home, "miniconda3"),
        os.path.join(home, "Miniconda3"),
        os.path.join(home, "anaconda3"),
        os.path.join(home, "Anaconda3"),
        os.path.join(program_data, "miniconda3"),
        os.path.join(program_data, "anaconda3"),
        os.path.join(program_data, "Miniconda3"),
        os.path.join(program_data, "Anaconda3"),
        r"C:\miniconda3",
        r"C:\anaconda3",
        r"D:\miniconda3",
        r"D:\anaconda3",
    ]
    for root in roots:
        cands.append(os.path.join(root, "Scripts", "conda.exe"))
        cands.append(os.path.join(root, "condabin", "conda.bat"))
    return [c for c in cands if c]


def find_conda(cfg: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """定位 conda：配置 → 环境变量 ``CONDA_EXE`` → PATH → 常见安装位置。

    返回可执行的 conda（``conda.exe`` 或 ``conda.bat``）；找不到返回 ``None``。
    """
    cfg = cfg or load_config()
    for cand in (cfg.get("conda_exe"), os.environ.get("CONDA_EXE")):
        if cand and os.path.isfile(cand):
            return cand
    for cand in _conda_candidates():
        if cand and os.path.isfile(cand):
            return cand
    return None


def conda_root(cfg: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """conda 安装根目录（用于拼激活命令）。"""
    cfg = cfg or load_config()
    configured = cfg.get("conda_root")
    if configured and os.path.isdir(configured):
        return configured
    exe = find_conda(cfg)
    if not exe:
        return None
    # <root>\Scripts\conda.exe 或 <root>\condabin\conda.bat
    parent = os.path.dirname(os.path.abspath(exe))
    root = os.path.dirname(parent)
    if os.path.isdir(os.path.join(root, "condabin")) or os.path.isdir(os.path.join(root, "Scripts")):
        return root
    return root


def conda_exec(cfg: Optional[Dict[str, Any]], args: List[str],
               timeout: int = 900) -> subprocess.CompletedProcess:
    """执行一条 conda 命令（参数列表形式，不经 shell）。

    修复注：旧代码把环境名直接拼进 ``shell=True`` 的字符串，环境名里出现空格
    或特殊字符就会出错；改成参数列表后由 ``subprocess`` 负责转义。
    """
    exe = find_conda(cfg)
    if not exe:
        return subprocess.CompletedProcess(args=["conda"] + args, returncode=127,
                                           stdout=b"", stderr=b"conda not found")
    if exe.lower().endswith(".bat"):
        cmd = ["cmd", "/c", exe] + args
    else:
        cmd = [exe] + args
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def list_conda_envs(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """返回 ``{环境名: 前缀目录}``；探测失败返回空字典。"""
    cfg = cfg or load_config()
    key = "envs:" + str(find_conda(cfg))
    if key in _CACHE:
        return _CACHE[key]
    envs: Dict[str, str] = {}
    try:
        proc = conda_exec(cfg, ["env", "list", "--json"], timeout=60)
        payload = json.loads((proc.stdout or b"{}").decode("utf-8", "replace") or "{}")
        root = conda_root(cfg)
        for path in payload.get("envs", []) or []:
            if not path:
                continue
            name = "base" if root and os.path.normcase(path) == os.path.normcase(root) \
                else os.path.basename(path.rstrip("\\/"))
            envs[name] = path
    except Exception:
        envs = {}
    _CACHE[key] = envs
    return envs


def env_prefix(cfg: Optional[Dict[str, Any]], env_name: str) -> Optional[str]:
    if not env_name:
        return None
    return list_conda_envs(cfg).get(env_name)


def env_python(cfg: Optional[Dict[str, Any]], env_name: str) -> Optional[str]:
    """环境里的 python 解释器绝对路径（找不到返回 ``None``）。"""
    prefix = env_prefix(cfg, env_name)
    if not prefix:
        return None
    for rel in (("python.exe",), ("bin", "python")):
        cand = os.path.join(prefix, *rel)
        if os.path.isfile(cand):
            return cand
    return None


def env_exists(cfg: Optional[Dict[str, Any]], env_name: str) -> bool:
    return bool(env_prefix(cfg, env_name))


def cmd_activate_prefix(cfg: Optional[Dict[str, Any]], env_name: str) -> str:
    """cmd 里激活环境的命令片段（以 ``&&`` 结尾，可直接拼后续命令）。"""
    root = conda_root(cfg)
    if root:
        bat = os.path.join(root, "condabin", "conda.bat")
        if os.path.isfile(bat):
            return f'call "{bat}" activate "{env_name}" && '
    return f'conda activate "{env_name}" && '


def bash_activate_prefix(cfg: Optional[Dict[str, Any]], env_name: str) -> str:
    """Git Bash 里激活环境的命令片段。

    直接 ``conda activate`` 要求 shell 已经初始化过 conda，在非登录 shell 里
    经常无声失败；这里显式 source ``conda.sh``，失败则退化为把环境目录塞进 PATH。
    """
    root = conda_root(cfg)
    if root:
        hook = os.path.join(root, "etc", "profile.d", "conda.sh")
        if os.path.isfile(hook):
            return f'source "{hook.replace(os.sep, "/")}" && conda activate "{env_name}" && '
    prefix = env_prefix(cfg, env_name)
    if prefix:
        posix = prefix.replace(os.sep, "/")
        return f'export PATH="{posix}:{posix}/Scripts:$PATH" && '
    return f'conda activate "{env_name}" && '


# ---------------------------------------------------------------------------
# 自检（可以脱离 GUI 运行：``python -m core.config``）
# ---------------------------------------------------------------------------

def describe(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or load_config()
    return {
        "app_dir": APP_DIR,
        "config_file": CONFIG_FILE,
        "config_exists": config_exists(),
        "workspace_path": cfg.get("workspace_path") or "",
        "workspace_ok": bool(cfg.get("workspace_path")) and os.path.isdir(cfg.get("workspace_path")),
        "conda_exe": find_conda(cfg) or "",
        "conda_root": conda_root(cfg) or "",
        "conda_envs": list_conda_envs(cfg),
        "git_bash_path": resolve_git_bash(cfg) or "",
        "ai_models_root": cfg.get("ai_models_root") or "",
        "python": sys.executable,
    }


if __name__ == "__main__":
    info = describe()
    for k, v in info.items():
        print(f"{k}: {v}")
