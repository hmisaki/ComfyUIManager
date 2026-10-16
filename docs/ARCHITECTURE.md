# 架构说明

## 一句话

`main.py` 负责启动引导与主窗口，把「工作区路径 / 数据目录 / 模型源 / 信号总线」打包成一个 `context` 字典；`managerModules/` 下的每个文件都是一个独立页面，通过 `register(context)` 把自己挂到左侧导航上。

## 启动流程

```
双击 startManager.bat
        │
        ▼
main.py
 ├─ 强制 stdout/stderr UTF-8（否则重定向到管道时 emoji 会写崩）
 ├─ get_or_create_workspace()
 │    ├─ 读 launcher_config.json（core.config.load_config）
 │    ├─ 有 workspace_path 且目录存在 → 直接用
 │    └─ 否则弹窗让用户选目录 → core.config.ensure_workspace() 建骨架 → 写回配置
 ├─ QApplication + MainWindow(workspace_path)
 │    ├─ ctx 组装：data_dir / config_file / Ai_models_root / SignalBus
 │    ├─ init_window()：标题、尺寸、Mica、深色主题
 │    ├─ load_modules()：扫描 managerModules/*.py → register(context) → addSubInterface
 │    ├─ SettingsInterface：背景图、切换工作区
 │    └─ load_config()：恢复上次的背景图
 └─ app.exec()
```

## 上下文契约（`context` 字典）

模块拿到的只有这一个字典，键名是模块与主程序之间的约定：

| 键 | 类型 | 说明 |
|---|---|---|
| `bus` | `SignalBus` | 跨模块信号总线 |
| `workspace_dir` | `str` | 工作区根目录（绝对路径） |
| `data_dir` | `str` | `<工作区>/Data`，所有登记类 JSON 都放这里 |
| `Ai_models_root` | `str \| None` | 共享模型库；先读工作区 `Data/config.json`，再读 `launcher_config.json` |
| `app_dir` | `str` | 程序目录（`core/config.py` 所在目录的上一级） |

## 模块注册协议

每个 `managerModules/*.py` 必须提供：

```python
def register(context):
    return {
        "name": "启动管理",      # 左侧导航显示名
        "icon": "🚀",            # 目前是占位，main.py 用文件名关键字映射 Fluent 图标
        "ui": EnvironmentManagerUI(context),   # QWidget
    }
```

约束：

- 文件名以数字前缀排序（`01_` `02_` …），加载顺序即导航顺序；
- 以 `_` 开头的文件会被跳过（临时禁用某个模块）；
- 模块内部**不要**直接读写 `launcher_config.json`，统一走 `core.config`；
- 拿到 `context` 后应立刻把需要的值存成实例属性（`self.cfg = cfg_util.load_config()`），不要在业务函数里反复读盘。

## 数据流

```
                 launcher_config.json          <工作区>/Data/*.json
                 （机器级：路径）              （用户级：登记数据）
                        │                              │
                        └──────────► core.config ◄──────┘
                                        │
              ┌─────────────────────────┼──────────────────────────┐
              ▼                         ▼                          ▼
      01_ComfyUIEnvs            02/03/04_* 模块               main.py 设置页
      （实例创建/启动/删除）      （登记与浏览）              （背景图/换工作区）
              │
              ├─ conda create --clone <模板>   ← conda 可执行由 core.config 探测
              ├─ mklink /J models <共享模型库>
              └─ start cmd /k "<激活> && cd /d <实例> && python main.py --port N"

                    core/ops.py  ← 同一批动作的「无界面版」，语义与上面一致
                        │            （模板校验 / 建·启·删实例 / 工作流 / 知识库 / 探测）
                        ▼
                   mcp_server.py  ← stdio MCP，14 个 cman_* 工具
                        │
                        ▼
                agent（Claude Desktop / DSH / 任意 MCP 客户端）
```

## `core/config.py` 的定位

它是唯一允许出现「系统路径」的文件，负责把「本机差异」挡在业务代码之外：

| 能力 | 函数 | 探测顺序 |
|---|---|---|
| 程序级配置 | `load_config / save_config / update_config` | 文件 → 默认值补齐 |
| 工作区骨架 | `ensure_workspace / data_dir` | — |
| 相对路径 | `to_relative(workspace, path)` | 路径在工作区内则转相对 |
| Git Bash | `resolve_git_bash` | 配置 → `GIT_BASH_PATH` → PATH → 常见安装位置 |
| Conda | `find_conda / conda_root` | 配置 → `CONDA_EXE` → PATH → 常见安装位置 |
| 环境枚举 | `list_conda_envs` | `conda env list --json`（根目录映射成 `base`） |
| 激活命令 | `cmd_activate_prefix / bash_activate_prefix` | 优先 `condabin\conda.bat` / `etc\profile.d\conda.sh` |

自检（不需要 Qt）：

```bat
python -m core.config
```

## `core/ops.py` 与 `mcp_server.py`（0.2.0 新增）

`core/ops.py` 是**无界面操作层**：不导入 Qt，只依赖标准库与 `core.config`，对外返回
JSON 可序列化的 dict/list。所有"动作"都在这里定义一次，`mcp_server.py` 只是把它翻译成
MCP 工具（`cman_*`，14 个）。

要点：

- **模板校验**：`list_templates` 会把「是否存在同名 conda 环境」标出来（`conda create --clone`
  认的是环境名，这是最容易踩的坑）；
- **实例创建**：复制模板 → `conda create --clone` → `mklink /J models <共享模型库>` → 登记；
  任何一步失败都会 `remove_tree` 清掉复制了一半的目录，避免下次被「目录已存在」挡住；
- **路径落盘**：登记与自愈都写**相对工作区**的路径（`to_relative`），工作区搬家不会失效；
- **删除护栏**：`delete_instance(confirm=False)` 直接拒绝——它是唯一不可撤销的动作；
- **探测**：`check_instance` 拉 `/system_stats`，回 ComfyUI 版本与显存。

`mcp_server.py` 自己实现了 stdio JSON-RPC（`initialize` / `tools/list` / `tools/call` /
`ping`，外加两个空应答），因此不需要 `mcp` 这个第三方包。日志走 stderr，stdout 只出协议消息。
冒烟测试 `tools/mcp_smoke_test.py` 会真的 spawn 一次服务，跑完一轮对话并校验删除护栏与中文往返。

## `core/signals.py`

信号总线用于解耦，现状：

| 信号 | 参数 | 状态 |
|---|---|---|
| `request_switch_tab` | `(int)` | 预留 |
| `request_change_background` | `(str)` | 预留（当前直接调用主窗口方法） |
| `request_status_message` | `(str, int)` | 预留 |
| `request_launch_workflow` | `(str, str)` | **半成品**：02 会 emit，main.py 未 connect |
| `model_database_updated` | `()` | 预留 |
| `error_occurred` | `(str, str)` | 预留 |

## 已知的半成品与后续方向

1. **工作流启动接线**：`request_launch_workflow(env_name, wf_path)` → 复用 `01` 的启动路径（`resolve_instance_dir` + `build_launch_command`），把工作流路径作为参数传给 ComfyUI。
2. **创建/删除实例改异步**：`conda create --clone` 与 `conda remove --all` 都是分钟级操作，应放进 `QThread` 并回报进度。
3. **配置页**：把 `conda_exe / git_bash_path / ai_models_root` 做成界面可编辑，而不是让用户改 JSON。
4. **模板与环境的一致性校验**：创建实例前先校验「模板目录名 是否是已存在的 conda 环境」，否则提前给出人话报错。
   （`core/ops.py` 里已经这么做并返回 `env_exists`；GUI 侧还没跟上。）
5. **跨平台**：`mklink`、Mica、Git Bash 都是 Windows 特性；要在 Linux/macOS 跑，需要抽象出「链接模型目录」这一层。
6. **GUI 复用 `core/ops.py`**：`01~04` 模块目前各自有一份与 `ops` 重叠的逻辑（实例增删启停、登记读写）。
   无界面路径已经跑通并有冒烟测试兜底，下一步应让 GUI 调用 `ops` 的同一批函数，消掉这份重复；
   顺带把 `create/delete` 的 GUI 侧也做成后台线程 + 进度回报（第 2 条）。
