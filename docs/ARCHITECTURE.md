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
5. **跨平台**：`mklink`、Mica、Git Bash 都是 Windows 特性；要在 Linux/macOS 跑，需要抽象出「链接模型目录」这一层。
