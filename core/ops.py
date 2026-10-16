"""ComfyUIManager 的无界面操作层（headless ops）。

GUI（``managerModules/*``）与 MCP 服务（``mcp_server.py``）都以这一层为语义基准，
这样"用鼠标点"和"让 agent 调"做的是同一件事。

设计约束：

* 只依赖标准库与 :mod:`core.config`，**不导入 Qt**（MCP 服务要在没有 GUI、
  甚至没有屏幕的会话里跑）；
* 所有落盘 JSON 都是 UTF-8、无 BOM；
* 实例记录里的 ``path`` 一律相对工作区保存，历史遗留的绝对路径会被兼容，
  并在读取时自愈回相对路径；
* 所有对外函数返回 **JSON 可序列化** 的 dict/list，方便直接塞进 MCP 响应。

关于副作用：``create_instance`` / ``delete_instance`` 会真的复制目录、克隆 conda
环境、删除目录与环境（分钟级操作）；``launch_instance`` 会新开一个控制台窗口。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core import config as cfg_util

APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8188
DEFAULT_CREATE_TIMEOUT = 1800   # conda create --clone 是分钟级操作
DEFAULT_LAUNCH_TIMEOUT = 60     # 等端口起来


class OpsError(RuntimeError):
    """操作层的人话错误（MCP 会把它作为 isError 返回，而不是抛栈）。"""


# ---------------------------------------------------------------------------
# 基础：工作区、数据文件
# ---------------------------------------------------------------------------

def load_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return cfg if cfg is not None else cfg_util.load_config()


def workspace(cfg: Optional[Dict[str, Any]] = None) -> Path:
    """工作区目录；没配置或不存在时给出可操作的报错。"""
    cfgd = load_cfg(cfg)
    raw = str(cfgd.get("workspace_path") or "").strip()
    if not raw:
        raise OpsError(
            "还没配置工作区。先运行 startManager.bat 走一遍首次启动向导，"
            "或用 cman_set_config 传 workspace_path。"
        )
    path = Path(raw)
    if not path.is_dir():
        raise OpsError(f"工作区不存在或不是目录：{path}（可用 cman_set_config 修正 workspace_path）")
    return path


def data_dir(cfg: Optional[Dict[str, Any]] = None) -> Path:
    return workspace(cfg) / "Data"


def _json_file(cfg: Optional[Dict[str, Any]], name: str) -> Path:
    return data_dir(cfg) / name


def _read(path: Path, default: Any) -> Any:
    data = cfg_util.read_json(str(path))
    return data if isinstance(data, type(default)) else default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg_util.write_json(str(path), data)


def _merge_write(path: Path, key: str, value: Any) -> None:
    """合并写：只替换 ``key``，保留文件里其它键（旧版 GUI 直接整文件覆盖）。"""
    data = _read(path, {}) or {}
    data[key] = value
    _write(path, data)


def _now() -> str:
    return datetime.now().isoformat()


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def find_comfyui_entry(instance_dir: Path) -> Optional[Path]:
    """定位实例里的 ComfyUI 入口 ``main.py``（兼容两种目录布局）。"""
    if not instance_dir or not instance_dir.is_dir():
        return None
    for candidate in (instance_dir / "ComfyUI" / "main.py", instance_dir / "main.py"):
        if candidate.is_file():
            return candidate
    return None


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def http_json(port: int, path: str = "/system_stats", timeout: float = 3.0) -> Dict[str, Any]:
    """向本机某端口发一次 GET，返回 ``{"ok": bool, ...}``。"""
    url = f"http://127.0.0.1:{int(port)}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read(65536)
        try:
            return {"ok": True, "url": url, "json": json.loads(raw.decode("utf-8", "replace"))}
        except Exception:
            return {"ok": True, "url": url, "text": raw.decode("utf-8", "replace")[:2000]}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "url": url, "error": f"HTTP {exc.code}"}
    except Exception as exc:  # 连接被拒、超时……
        return {"ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}


def remove_tree(path: Path) -> None:
    """删除目录树，遇到只读文件先改权限（Windows 上 ``rmtree`` 常见失败点）。"""
    if not path.exists():
        return

    def _onexc(func, target, exc_info):  # type: ignore[no-untyped-def]
        try:
            os.chmod(target, 0o700)
            func(target)
        except Exception:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_onexc)          # type: ignore[call-arg]
    else:
        shutil.rmtree(path, onerror=lambda f, t, e: _onexc(f, t, e))  # type: ignore[arg-type]


def _tail(raw: Any, limit: int = 300) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    return str(raw).strip()[-limit:]


# ---------------------------------------------------------------------------
# 模板与环境
# ---------------------------------------------------------------------------

def template_roots(cfg: Optional[Dict[str, Any]] = None) -> List[Path]:
    """模板目录候选：优先程序自带的 ``Data/ComfyUITemplates``，其次工作区内。"""
    roots = [APP_DIR / "Data" / "ComfyUITemplates", workspace(cfg) / "ComfyUITemplates"]
    return [r for r in roots if r.is_dir()]


def list_templates(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """列出可用模板。

    注意一个硬约定：``conda create --clone <模板名>`` 认的是**环境名**，
    所以模板目录名必须等于一个已存在的 conda 环境名，否则创建实例时会失败。
    这里直接把 ``env_exists`` 标出来。
    """
    cfgd = load_cfg(cfg)
    envs = cfg_util.list_conda_envs(cfgd)
    roots = template_roots(cfgd)
    items: List[Dict[str, Any]] = []
    for root in roots:
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            items.append({
                "name": child.name,
                "root": str(root),
                "path": str(child),
                "env_exists": child.name in envs,
                "has_comfyui_entry": find_comfyui_entry(child) is not None,
            })
    return {
        "template_roots": [str(r) for r in roots],
        "templates": items,
        "hint": "模板目录名必须等于已存在的 conda 环境名（conda create --clone 认环境名）",
    }


def models_source(cfg: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    """共享模型库：工作区的 ``GlobalModels`` 优先，否则配置里的 ``ai_models_root``。"""
    cfgd = load_cfg(cfg)
    local = workspace(cfgd) / "GlobalModels"
    if local.is_dir():
        return local
    configured = str(cfgd.get("ai_models_root") or "").strip()
    if configured and Path(configured).is_dir():
        return Path(configured)
    return None


# ---------------------------------------------------------------------------
# 实例（ComfyUIApps/<env>）
# ---------------------------------------------------------------------------

def _instances() -> List[Dict[str, Any]]:
    def _default() -> Dict[str, Any]:
        return {"workflows": []}

    data = _read(_json_file(None, "workflows.json"), _default()) or _default()
    records = data.get("workflows") or []
    return [r for r in records if isinstance(r, dict)]


def _save_instances(records: List[Dict[str, Any]]) -> None:
    _merge_write(_json_file(None, "workflows.json"), "workflows", records)


def _key(rec: Dict[str, Any]) -> str:
    return str(rec.get("eng_name") or rec.get("conda_env") or "").strip()


def find_instance(cfg: Optional[Dict[str, Any]], env: str) -> Dict[str, Any]:
    """按环境名/实例名查找记录（大小写不敏感）。"""
    wanted = str(env or "").strip().lower()
    if not wanted:
        raise OpsError("必须给出实例名（eng_name / conda_env）")
    for rec in _instances():
        candidates = {str(rec.get("eng_name") or "").lower(),
                      str(rec.get("conda_env") or "").lower()}
        if wanted in candidates:
            return rec
    names = sorted({_key(r) for r in _instances() if _key(r)})
    raise OpsError(f"找不到实例「{env}」。现有实例：{names or '（还没有）'}")


def resolve_instance_dir(cfg: Optional[Dict[str, Any]], rec: Dict[str, Any]) -> Optional[Path]:
    """解析实例的真实目录：记录的 path（相对/绝对）→ ``ComfyUIApps/<eng_name>``。"""
    cfgd = load_cfg(cfg)
    candidates: List[Path] = []
    raw = str(rec.get("path") or "").strip()
    if raw:
        p = Path(raw)
        candidates.append(p if p.is_absolute() else workspace(cfgd) / raw)
    eng = _key(rec)
    if eng:
        candidates.append(workspace(cfgd) / "ComfyUIApps" / eng)
    for cand in candidates:
        if cand.is_dir():
            return cand
    return None


def list_instances(cfg: Optional[Dict[str, Any]] = None, heal: bool = True) -> List[Dict[str, Any]]:
    """实例清单：补齐解析后的目录、入口、端口是否在监听，并顺手自愈路径。"""
    cfgd = load_cfg(cfg)
    ws = workspace(cfgd)
    records = _instances()
    out: List[Dict[str, Any]] = []
    changed = False
    for rec in records:
        directory = resolve_instance_dir(cfgd, rec)
        entry = find_comfyui_entry(directory) if directory else None
        port = int(rec.get("port") or DEFAULT_PORT)
        item = dict(rec)
        item["dir"] = str(directory) if directory else None
        item["exists"] = directory is not None
        item["comfyui_entry"] = str(entry) if entry else None
        item["port"] = port
        item["running"] = port_open(port) if directory else False
        item["conda_env_exists"] = cfg_util.env_exists(cfgd, str(rec.get("conda_env") or ""))
        if heal and directory is not None:
            fixed = cfg_util.to_relative(str(ws), str(directory))
            if fixed != str(rec.get("path") or ""):
                rec["path"] = fixed
                changed = True
        out.append(item)
    if changed:
        _save_instances(records)
    return out


def pick_port(cfg: Optional[Dict[str, Any]] = None, start: int = DEFAULT_PORT) -> int:
    """挑一个没被实例占用、且当前没人监听的端口。"""
    used = {int(r.get("port") or 0) for r in _instances()}
    port = int(start)
    while port in used or port_open(port):
        port += 1
    return port


def create_instance(cfg: Optional[Dict[str, Any]] = None, template: str = "",
                    env_name: str = "", cn_name: str = "", port: Optional[int] = None,
                    clone_env: bool = True, timeout: int = DEFAULT_CREATE_TIMEOUT) -> Dict[str, Any]:
    """从模板创建实例：复制模板 → 克隆 conda 环境 → 挂共享模型目录 → 登记。

    与 GUI「启动管理 → 创建」的步骤逐条对应；``env_name`` 缺省时等于模板名
    （模板名即 conda 环境名，见 :func:`list_templates`）。
    """
    cfgd = load_cfg(cfg)
    ws = workspace(cfgd)
    template = str(template or "").strip()
    if not template:
        raise OpsError("必须给出 template（可用 cman_list_templates 看有哪些）")
    roots = template_roots(cfgd)
    tpl_path = next((r / template for r in roots if (r / template).is_dir()), None)
    if tpl_path is None:
        raise OpsError(f"模板「{template}」不存在。模板目录：{[str(r) for r in roots] or '（一个都没找到）'}")

    name = str(env_name or template).strip()
    target = ws / "ComfyUIApps" / name
    if target.exists():
        raise OpsError(f"实例目录已存在：{target}（换个 env_name，或先在 GUI 里删掉它）")

    steps: List[str] = []
    target.parent.mkdir(parents=True, exist_ok=True)

    # 1) 复制模板
    try:
        shutil.copytree(tpl_path, target,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    except Exception as exc:
        raise OpsError(f"复制模板失败：{type(exc).__name__}: {exc}") from exc
    steps.append(f"已复制模板 {tpl_path} → {target}")

    # 2) 克隆 conda 环境（模板名即环境名）
    cloned = False
    if clone_env:
        if cfg_util.env_exists(cfgd, template):
            if name == template:
                steps.append(f"conda 环境「{name}」已存在，直接复用（不克隆）")
            else:
                proc = cfg_util.conda_exec(cfgd, ["create", "-y", "-n", name, "--clone", template],
                                           timeout=timeout)
                if proc.returncode != 0:
                    remove_tree(target)
                    raise OpsError(
                        f"conda 克隆失败（exit={proc.returncode}）：{_tail(proc.stderr) or _tail(proc.stdout)}"
                    )
                cfg_util.clear_cache()
                cloned = True
                steps.append(f"已克隆 conda 环境 {template} → {name}")
        else:
            remove_tree(target)
            raise OpsError(
                f"conda 里没有名为「{template}」的环境，无法 --clone。"
                f"请先建环境（conda create -n {template} ...）或换一个模板。"
            )

    # 3) 挂共享模型目录（junction，失败不致命）
    src = models_source(cfgd)
    link = target / "models"
    junction_note = ""
    if src and not link.exists():
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(src)],
                              capture_output=True)
        if proc.returncode == 0:
            junction_note = f"已挂 models → {src}"
        else:
            junction_note = f"挂 models 失败（可手动处理）：{_tail(proc.stderr) or _tail(proc.stdout)}"
        steps.append(junction_note)

    # 4) 登记
    chosen_port = int(port) if port else pick_port(cfgd)
    record = {
        "eng_name": name,
        "cn_name": str(cn_name or name),
        "template": template,
        "path": cfg_util.to_relative(str(ws), str(target)),
        "conda_env": name,
        "created_at": _now(),
        "port": chosen_port,
    }
    records = _instances()
    records.append(record)
    _save_instances(records)
    steps.append(f"已登记实例（端口 {chosen_port}）")
    return {"instance": record, "steps": steps, "cloned_env": cloned}


def launch_instance(cfg: Optional[Dict[str, Any]] = None, env: str = "",
                    port: Optional[int] = None, open_browser: bool = True,
                    wait_open: int = DEFAULT_LAUNCH_TIMEOUT) -> Dict[str, Any]:
    """启动实例：新开一个控制台窗口跑 ``python main.py --port N``。

    ``wait_open`` 秒内轮询端口，返回 ``port_open`` 告诉你它是真起来了还是还在加载。
    """
    cfgd = load_cfg(cfg)
    rec = find_instance(cfgd, env)
    directory = resolve_instance_dir(cfgd, rec)
    if directory is None:
        raise OpsError(
            f"实例「{_key(rec)}」的目录找不到（记录里的 path={rec.get('path')!r}）。"
            "可以用 cman_list_instances 看解析结果。"
        )
    entry = find_comfyui_entry(directory)
    if entry is None:
        raise OpsError(f"实例目录里没有 ComfyUI 入口 main.py：{directory}")

    chosen_port = int(port or rec.get("port") or DEFAULT_PORT)
    conda_env = str(rec.get("conda_env") or _key(rec))
    work_dir = entry.parent
    inner = (cfg_util.cmd_activate_prefix(cfgd, conda_env)
             + f'cd /d "{work_dir}" && '
             + f'python "{entry.name}" --port {chosen_port}'
             + (" --auto-launch" if open_browser else ""))
    proc = subprocess.Popen(["cmd", "/c", "start", "", "cmd", "/k", inner],
                            cwd=str(work_dir),
                            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    deadline = time.time() + max(0, int(wait_open))
    opened = False
    while time.time() < deadline:
        if port_open(chosen_port):
            opened = True
            break
        time.sleep(1.0)
    return {
        "instance": _key(rec),
        "conda_env": conda_env,
        "work_dir": str(work_dir),
        "entry": str(entry),
        "port": chosen_port,
        "spawned_pid": proc.pid,
        "command": f'cmd /k "{inner}"',
        "port_open": opened,
        "note": ("端口已监听，ComfyUI 可以访问了" if opened
                 else f"{wait_open}s 内端口还没起来：模型加载/首次启动可能更慢，稍后再用 cman_check_instance"),
    }


def delete_instance(cfg: Optional[Dict[str, Any]] = None, env: str = "",
                    confirm: bool = False, remove_env: bool = True,
                    timeout: int = 900) -> Dict[str, Any]:
    """删除实例（目录 + 记录，可选连 conda 环境一起删）。

    ``confirm=True`` 是硬门槛：这是不可撤销的删除。
    """
    if not confirm:
        raise OpsError(
            "危险操作，已拦下：删除实例会删掉整个实例目录（含该环境的 "
            "custom_nodes、outputs、user 数据）。确认请显式传 confirm=true。"
        )
    cfgd = load_cfg(cfg)
    rec = find_instance(cfgd, env)
    directory = resolve_instance_dir(cfgd, rec)
    conda_env = str(rec.get("conda_env") or _key(rec))
    steps: List[str] = []

    if directory is not None:
        remove_tree(directory)
        steps.append(f"已删除实例目录 {directory}")
    else:
        steps.append(f"实例目录本来就不在（记录 path={rec.get('path')!r}），只清记录")

    failed_env_removal = ""
    if remove_env and conda_env:
        proc = cfg_util.conda_exec(cfgd, ["remove", "-y", "-n", conda_env, "--all"], timeout=timeout)
        if proc.returncode == 0:
            cfg_util.clear_cache()
            steps.append(f"已删除 conda 环境「{conda_env}」")
        else:
            failed_env_removal = _tail(proc.stderr) or _tail(proc.stdout)
            steps.append(f"conda 环境删除失败（exit={proc.returncode}），记录保留以免误删：{failed_env_removal}")

    if not failed_env_removal:
        records = [r for r in _instances() if r is not rec]
        _save_instances(records)
        steps.append("已移除实例记录")
    return {"instance": _key(rec), "steps": steps, "conda_env_removed": not failed_env_removal}


# ---------------------------------------------------------------------------
# 工作流登记
# ---------------------------------------------------------------------------

def list_workflows(cfg: Optional[Dict[str, Any]] = None, env: str = "",
                   query: str = "") -> List[Dict[str, Any]]:
    data = _read(_json_file(cfg, "saved_workflows.json"), {"workflows": []}) or {"workflows": []}
    items = [w for w in (data.get("workflows") or []) if isinstance(w, dict)]
    if env:
        items = [w for w in items if str(w.get("conda_env") or "").lower() == env.lower()]
    if query:
        needle = query.lower()
        items = [w for w in items
                 if needle in json.dumps(w, ensure_ascii=False).lower()]
    out = []
    for w in items:
        item = dict(w)
        item["exists"] = bool(w.get("path")) and Path(str(w["path"])).is_file()
        out.append(item)
    return out


def add_workflow(cfg: Optional[Dict[str, Any]] = None, path: str = "", conda_env: str = "",
                 category: str = "", description: str = "") -> Dict[str, Any]:
    """把一个已存在的 ``.json`` 工作流登记进管理器（不复制文件）。"""
    p = Path(str(path or "").strip())
    if not p.is_file():
        raise OpsError(f"工作流文件不存在：{p}")
    conda_env = str(conda_env or "").strip()
    if not conda_env:
        # 从路径里猜：<ws>/ComfyUIApps/<env>/ComfyUI/user/default/workflows/x.json
        parts = p.parts
        if "ComfyUIApps" in parts:
            idx = parts.index("ComfyUIApps")
            if len(parts) > idx + 1:
                conda_env = parts[idx + 1]
    record = {
        "path": str(p),
        "description": description or p.stem,
        "category": category or (f"[{conda_env}]" if conda_env else ""),
        "conda_env": conda_env,
        "created_at": _now(),
    }
    data = _read(_json_file(cfg, "saved_workflows.json"), {"workflows": []}) or {"workflows": []}
    items = [w for w in (data.get("workflows") or []) if isinstance(w, dict)]
    existing = next((w for w in items if str(w.get("path")) == record["path"]), None)
    if existing:
        existing.update({k: v for k, v in record.items() if k != "created_at"})
        record = existing
    else:
        items.append(record)
    _merge_write(_json_file(cfg, "saved_workflows.json"), "workflows", items)
    return record


def discover_workflows(cfg: Optional[Dict[str, Any]] = None, env: str = "",
                       limit: int = 200) -> Dict[str, Any]:
    """扫描实例目录下的 ``user/default/workflows/*.json``（只报告，不登记）。"""
    cfgd = load_cfg(cfg)
    targets = list_instances(cfgd, heal=False)
    if env:
        wanted = env.lower()
        targets = [t for t in targets if wanted in str(t.get("eng_name", "")).lower()
                   or wanted in str(t.get("conda_env", "")).lower()]
    found: List[Dict[str, Any]] = []
    for item in targets:
        directory = Path(str(item.get("dir") or ""))
        root = directory / "ComfyUI" / "user" / "default" / "workflows"
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.json")):
            found.append({"env": item.get("eng_name"), "path": str(path), "size": path.stat().st_size})
            if len(found) >= limit:
                break
        if len(found) >= limit:
            break
    registered = {str(w.get("path")) for w in list_workflows(cfgd)}
    for item in found:
        item["registered"] = item["path"] in registered
    return {"count": len(found), "workflows": found}


# ---------------------------------------------------------------------------
# 知识库（节点 / 模型）
# ---------------------------------------------------------------------------

def list_nodes(cfg: Optional[Dict[str, Any]] = None, query: str = "",
               category: str = "", limit: int = 50) -> Dict[str, Any]:
    data = _read(_json_file(cfg, "nodes.json"), {}) or {}
    items: List[Dict[str, Any]] = []
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        item = dict(value)
        item.setdefault("repo_name", key)
        item["key"] = key
        items.append(item)
    if category:
        items = [i for i in items if category.lower() in str(i.get("category") or "").lower()]
    if query:
        needle = query.lower()
        items = [i for i in items if needle in json.dumps(i, ensure_ascii=False).lower()]
    return {"total": len(items), "nodes": items[:limit]}


def list_models(cfg: Optional[Dict[str, Any]] = None, query: str = "",
                model_type: str = "", limit: int = 50,
                include_nsfw: bool = True) -> Dict[str, Any]:
    data = _read(_json_file(cfg, "models.json"), {}) or {}
    items: List[Dict[str, Any]] = []
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        item = dict(value)
        item.setdefault("hash", key)
        items.append(item)
    if model_type:
        items = [i for i in items
                 if model_type.lower() in str(i.get("type") or "").lower()
                 or model_type.lower() in str(i.get("base_model") or "").lower()]
    if not include_nsfw:
        items = [i for i in items if not i.get("nsfw")]
    if query:
        needle = query.lower()
        fields = ("name_cn", "name_eng", "base_model", "description", "trainedWords", "path", "type")
        items = [i for i in items
                 if needle in " ".join(str(i.get(f) or "") for f in fields).lower()]
    brief = [{k: i.get(k) for k in ("hash", "name_cn", "name_eng", "type", "base_model",
                                    "path", "nsfw")} for i in items[:limit]]
    return {"total": len(items), "models": brief}


# ---------------------------------------------------------------------------
# 配置与总览
# ---------------------------------------------------------------------------

CONFIG_KEYS = ("workspace_path", "ai_models_root", "conda_exe", "conda_root", "git_bash_path")


def get_config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfgd = load_cfg(cfg)
    info = cfg_util.describe(cfgd)
    info["config"] = {k: cfgd.get(k) or "" for k in CONFIG_KEYS}
    return info


def set_config(cfg: Optional[Dict[str, Any]] = None, **values: Any) -> Dict[str, Any]:
    """改 ``launcher_config.json``（只允许白名单键）。"""
    unknown = [k for k in values if k not in CONFIG_KEYS]
    if unknown:
        raise OpsError(f"不认识的配置键：{unknown}；允许：{list(CONFIG_KEYS)}")
    clean = {k: v for k, v in values.items() if v is not None}
    ws = str(clean.get("workspace_path") or "").strip()
    if ws:
        cfg_util.ensure_workspace(ws)
    saved = cfg_util.update_config(**clean)
    return {"saved": {k: saved.get(k) or "" for k in CONFIG_KEYS},
            "config_file": cfg_util.CONFIG_FILE}


def status(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """一句话看懂当前状态：配置探测结果 + 实例/工作流/知识库计数。"""
    cfgd = load_cfg(cfg)
    info: Dict[str, Any] = {"config": get_config(cfgd)}
    try:
        info["workspace"] = str(workspace(cfgd))
        info["instances"] = list_instances(cfgd, heal=False)
        info["counts"] = {
            "instances": len(info["instances"]),
            "workflows": len(list_workflows(cfgd)),
            "nodes": list_nodes(cfgd, limit=1)["total"],
            "models": list_models(cfgd, limit=1)["total"],
        }
        src = models_source(cfgd)
        info["models_source"] = str(src) if src else ""
        info["templates"] = [t["name"] for t in list_templates(cfgd)["templates"]]
    except OpsError as exc:
        info["workspace_error"] = str(exc)
    return info


def check_instance(cfg: Optional[Dict[str, Any]] = None, env: str = "",
                   port: Optional[int] = None) -> Dict[str, Any]:
    """探一次实例的 HTTP：默认 ``/system_stats``。"""
    cfgd = load_cfg(cfg)
    if not port:
        rec = find_instance(cfgd, env)
        port = int(rec.get("port") or DEFAULT_PORT)
    probe = http_json(int(port))
    result: Dict[str, Any] = {"port": int(port), "reachable": bool(probe.get("ok"))}
    stats = (probe.get("json") or {}).get("system") if isinstance(probe.get("json"), dict) else None
    if stats:
        result["system"] = {k: stats.get(k) for k in
                            ("os", "comfyui_version", "python_version", "pytorch_version",
                             "ram_free", "ram_total") if k in stats}
        devices = (probe.get("json") or {}).get("devices") or []
        if devices:
            dev = devices[0]
            result["device"] = {k: dev.get(k) for k in
                                ("name", "type", "vram_total", "vram_free") if k in dev}
    if not result["reachable"]:
        result["error"] = probe.get("error")
        result["hint"] = "端口没起来：可能还在加载，或根本没启动（cman_launch_instance）"
    return result
