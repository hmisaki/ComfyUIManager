# MCP 服务（让 agent 直接管实例）

`mcp_server.py` 把管理器暴露成一组 MCP 工具，于是 agent（Claude Desktop、DSH 或任何
MCP 客户端）可以自己完成：**看现状 → 挑模板 → 建实例 → 起 ComfyUI → 登记工作流 →
查模型/节点库**，不用你在界面上点。

两个设计选择：

* **零第三方依赖。** MCP 的 stdio 传输就是「一行一个 JSON-RPC 消息」，
  这里用标准库实现了 `initialize` / `tools/list` / `tools/call` / `ping`，
  外加 `resources/list` 与 `prompts/list` 的空应答。仓库仍然只要 `PySide6`。
* **逻辑只写一遍（尽量）。** 工具调用的是 `core/ops.py`，它是无界面的操作层，
  和 GUI 用的是同一份工作区数据与同一套规则（相对路径、UTF-8、模板=conda 环境名）。

## 跑起来

```bash
python mcp_server.py            # 正常用法：由 MCP 客户端 spawn，走 stdin/stdout
python mcp_server.py --selftest # 自检：跑一遍 initialize / tools/list / 只读工具
python mcp_server.py --list     # 只打印工具清单
```

服务**不监听任何端口**（stdio），日志一律走 stderr，stdout 只出 JSON-RPC。

## 客户端配置

### DSH

在 `~/.dsh/profiles/<profile>/cordis.patch.yml` 里加一条（与 `mcp-voicebox` 同级）：

```yaml
- insert:
    - id: mcp-comfyuimanager
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: comfyuimanager
        transport: stdio
        command: 'C:\Users\you\miniconda3\python.exe'
        args:
          - 'D:\AI\ComfyUIManager\mcp_server.py'
        env:
          PYTHONIOENCODING: utf-8
        failOnStartupError: false
```

工具进模型表后叫 `mcp__comfyuimanager__cman_*`。

### 通用 MCP 客户端（Claude Desktop 等）

见仓库里的 `mcp.example.json`，把 `command` 换成你的 python 绝对路径：

```json
{
  "mcpServers": {
    "comfyuimanager": {
      "command": "C:\\Users\\you\\miniconda3\\python.exe",
      "args": ["D:\\AI\\ComfyUIManager\\mcp_server.py"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

> Windows 上 `python` 常被 Microsoft Store 的占位程序占据（`WindowsApps\python.exe`），
> 那玩意报了 `Python was not found`；这里一定要写**真解释器**的绝对路径。

## 工具表

| 工具 | 作用 | 关键参数 | 副作用 |
|---|---|---|---|
| `cman_status` | 配置探测 + 实例/工作流/节点/模型计数 + 模板清单 | — | 只读 |
| `cman_get_config` | 读 `launcher_config.json` 与探测结果 | — | 只读 |
| `cman_set_config` | 改配置（白名单五个键） | `workspace_path` / `ai_models_root` / `conda_exe` / `conda_root` / `git_bash_path` | 写配置；`workspace_path` 会建目录骨架 |
| `cman_list_templates` | 列模板，并标出「是否存在同名 conda 环境」 | — | 只读 |
| `cman_list_instances` | 列实例：解析后的目录、入口、端口是否在听、环境是否存在 | `heal` | 可自愈 `path` 为相对路径 |
| `cman_create_instance` | 复制模板 → `conda create --clone` → 挂模型 junction → 登记 | `template`、`env_name`、`port`、`clone_env`、`timeout_s` | **分钟级**：建目录、建 conda 环境 |
| `cman_launch_instance` | 新开控制台跑 `python main.py --port N` | `env`、`port`、`open_browser`、`wait_open_s` | **起进程、开窗口** |
| `cman_delete_instance` | 删实例目录 + 记录（可选连环境） | `env`、`confirm=true`、`remove_env` | **不可撤销** |
| `cman_check_instance` | 探 `/system_stats`，回版本与显存 | `env` 或 `port` | 只读 |
| `cman_list_workflows` | 列已登记工作流（可过滤/搜索，标出文件是否还在） | `env`、`query` | 只读 |
| `cman_add_workflow` | 登记一个已存在的 `.json` | `path`、`conda_env`、`category`、`description` | 写 `saved_workflows.json` |
| `cman_discover_workflows` | 扫实例目录下的工作流文件，报告哪些还没登记 | `env`、`limit` | 只读 |
| `cman_list_models` | 查模型库（LoRA/Checkpoint…） | `query`、`model_type`、`limit`、`include_nsfw` | 只读 |
| `cman_list_nodes` | 查自定义节点知识库 | `query`、`category`、`limit` | 只读 |

## 超时

创建/删除是分钟级操作（`conda create --clone`、`conda remove --all`）。
**客户端一侧的超时建议 ≥ 30 分钟**，否则你会看到「工具超时」而实际操作还在跑。
`cman_create_instance` 的 `timeout_s`（默认 1800）是给 conda 那一步的上限，两者别搞混。

## 安全边界

* 这是**本地特权服务**：它能改配置、建/删目录、建/删 conda 环境。只在你信任的客户端里配它，
  别把它暴露到网络（它本来也不监听端口）。
* **删除必须显式 `confirm=true`**，没传就只回一句人话拒绝（冒烟测试里有一条专门验证这个护栏）。
* 工具集是**固定动作**，没有「执行任意命令」的入口；conda 调用走参数列表而不是拼 shell 字符串，
  环境名里的空格/特殊字符不会变成注入。
* 不读也不传模型文件；模型库工具只回元数据。

## 排障

| 症状 | 先看这里 |
|---|---|
| 客户端里看不到工具 | 服务日志走 **stderr**；手动跑 `--selftest` 确认能起；确认 `command` 是**真解释器绝对路径** |
| `tools/call` 超时 | 调大客户端超时（创建/删除是分钟级） |
| 输出里有 `\uXXXX` | 那是 JSON 转义，正常；真乱码只会出现在 Windows 控制台回显上 |
| 中文名变成问号 | Windows 控制台 codepage 问题，不是数据问题；服务内部一律 UTF-8 |
| 说「还没配置工作区」 | 先跑一次 GUI 走首次启动向导，或 `cman_set_config` 传 `workspace_path` |

## 和 GUI 的关系

GUI（`managerModules/*`）与 MCP 工具共用同一个工作区和同一批 JSON 文件，但**不要同时**
对同一个实例做写操作（一边在复制目录、另一边在删它）。只读查询随便并发。

`core/ops.py` 与 `managerModules/01~04` 目前有一小部分逻辑是重复的（GUI 模块还没改成
调用 `ops`）——这是有意的低风险取舍：先把无界面路径跑通，再逐步让 GUI 复用。
进度记在 `docs/ARCHITECTURE.md` 的「后续方向」。
