# 变更记录

本文件记录 ComfyUIManager 的重要变更。开源整理之前的历史修复按「当时踩到的坑」保留，便于回溯。

## [0.2.0] - 2026-10-16 —— MCP 服务

### 新增

- **`mcp_server.py`：stdio MCP 服务（零第三方依赖）**，把管理器暴露成 14 个 `cman_*` 工具：
  `status` / `get_config` / `set_config` / `list_templates` / `list_instances` /
  `create_instance` / `launch_instance` / `delete_instance` / `check_instance` /
  `list_workflows` / `add_workflow` / `discover_workflows` / `list_models` / `list_nodes`。
  - 协议面：`initialize`（含 `instructions`）/ `tools/list` / `tools/call` / `ping`，
    以及 `resources/list`、`prompts/list` 的空应答；日志走 stderr，stdout 只出 JSON-RPC。
  - `--selftest` 与 `--list` 两个自检入口。
- **`core/ops.py`：无界面操作层**。GUI 与 MCP 共用同一套语义：模板校验（模板名 = conda 环境名）、
  实例创建（复制模板 → `conda create --clone` → 模型 junction → 相对路径登记）、启动/删除、
  工作流登记与扫描、模型/节点知识库过滤、`/system_stats` 探测。失败时清理复制了一半的目录。
- **删除类操作有 `confirm` 硬门槛**：`cman_delete_instance` 不传 `confirm=true` 直接拒绝（只回一句人话）。
- `docs/MCP.md`（工具表、客户端配置、超时与安全边界）、`mcp.example.json`、
  `tools/mcp_smoke_test.py`（真的 spawn 一次服务走 stdio 对话，含删除护栏与中文往返校验）。
- `core/config.py` 增 `clear_cache()`：常驻进程里新建/删除 conda 环境后清探测缓存。

### 变更

- `core/signals.py` 的 `request_launch_workflow` 修正为双参数（原声明与 `02_workflows.py` 的 emit 不一致）。
  GUI 侧的接线仍未实现，但 MCP 路径已经能真正启动实例。
- `README.md` 增「MCP 服务」一节与目录结构更新。

## [0.1.0] - 2026-10-16 —— 首次开源

### 新增

- `core/config.py`：路径与运行环境解析模块（纯 stdlib，不依赖 Qt）
  - conda 探测（配置 → `CONDA_EXE` → PATH → 常见安装位置）与环境枚举（`conda env list --json`）
  - Git Bash 探测（配置 → `GIT_BASH_PATH` → PATH → 常见安装位置）
  - 统一的 JSON 读写（显式 UTF-8 + `ensure_ascii=False`）
  - 工作区骨架创建与「绝对路径 → 相对工作区」转换
  - `cmd` / Git Bash 两种 shell 的 conda 激活命令拼装
  - 自检入口：`python -m core.config`
- `requirements.txt`、`.gitignore`、`LICENSE`（MIT）、`launcher_config.example.json`
- `README.md`、`docs/ARCHITECTURE.md`、`docs/CONFIGURATION.md`、`CHANGELOG.md`

### 变更（可移植性）

- **移除写死的 Git Bash 路径**（原 `managerModules/01_ComfyUIEnvs.py` 顶部常量 `C:\Program Files\Git\git-bash.exe`），改为运行时探测；找不到时给出可操作的提示。
- **conda 调用不再依赖 PATH**：创建实例改用 `conda_exec(["create", "-y", "-n", ENV, "--clone", TPL])`，启动命令优先 `call <conda_root>\condabin\conda.bat activate <ENV>`，Git Bash 里显式 `source etc/profile.d/conda.sh`。
- **conda 调用改为参数列表形式**（`shell=False`），环境名含空格或特殊字符时不再拼坏命令。
- **实例路径改为相对工作区落盘**（`ComfyUIApps/<ID>`）；旧数据里的绝对路径仍兼容，探测成功后会自愈改写成相对路径。
- 注释与文档里去掉了本机私有路径，改为通用表述。

### 修复

- `main.py`：`save_config` / `load_config` 读写 JSON 未指定编码（中文 Windows 上会落到本地代码页）→ 统一走 `core.config`。
- `main.py`：若干裸 `except:` 改为显式处理（不再吞掉全部异常与 KeyboardInterrupt）。
- `core/signals.py`：`request_launch_workflow` 声明为单参数，而 `02_workflows.py` 用双参数 emit，一旦接线就会抛 `Signal has 1 argument(s) but 2 provided` → 改为 `Signal(str, str)`。
- `01_ComfyUIEnvs.py`：`load/save_workflows_data` 改为合并写（保留文件里的其它键）、失败不再静默返回空列表。
- `01_ComfyUIEnvs.py`：创建实例失败时清理复制了一半的实例目录，避免重试时被「目标文件夹已存在」挡住。

### 已知限制（开源时未解决）

- 工作流「启动」按钮的信号没有接线（见 `docs/ARCHITECTURE.md`）。
- 创建/删除实例是同步阻塞调用（`conda create --clone` 可达数分钟），界面在期间无响应。
- 仅 Windows、且仅在作者本机验证过。
- 路径含空格时 conda 激活命令退化（见 `docs/CONFIGURATION.md`）。

---

## 历史修复（开源整理前，按日期）

### 2026-10-15

- `find_comfyui_entry()`：兼容两种实例布局（`<实例>\ComfyUI\main.py` 与 `<实例>\main.py`）。
- `resolve_instance_dir()`：实例路径失效时的探测与自愈（工作区被搬走后启动只报「系统找不到指定的路径」）。
- 启动命令：路径加引号，直接给 `python` 完整脚本路径，不再依赖 `cd` 后的相对名。
- 删除实例：同时处理路径过期的情况。

### 2026-10-14

- `remove_tree()`：`shutil.rmtree` 遇到只读文件（git pack 文件）会 `[WinError 5] 拒绝访问` 导致实例删不掉 → 清只读属性后重试，并检查残留。
- 启动命令：补 `--port`，此前所有实例都抢 8188，只能同时跑一个。
- 删除实例：检查 `conda remove` 返回码，此前失败也显示「删除成功」。
- 模型链接：检查 `mklink` 返回码，此前链接失败会静默通过。
- 移除 AI_Kernel / `core/ai_service` 相关代码。
