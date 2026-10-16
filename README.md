# ComfyUIManager

一个 Windows 上的 **ComfyUI 多实例管理器**：用图形界面管理「一套代码 = 一个 conda 环境 = 一个端口」的多个 ComfyUI 实例，外加自定义节点与模型的知识库。

> 这是一个**个人自用工具**，功能可用但没有做过全面测试，也没有做跨机器验证。请把它当作一个能省的脚本集合，而不是一个成熟产品。

---

## 它解决什么问题

手工维护多个 ComfyUI 环境时，重复劳动集中在四件事上：

1. **每一次都要复制代码、`conda create --clone`、再把 `models` 目录接上**（Windows 上用 `mklink /J` 做 junction，省磁盘）。
2. **每个实例要一个独立端口**，否则只能同时跑一个。
3. **工作区被搬走/改名后，记录里的绝对路径全部失效**，启动时只看到 `系统找不到指定的路径。`
4. **自定义节点和模型散落在各处**，装了哪些、从哪来的、干嘛用的，全靠记忆。

ComfyUIManager 把这几件事收敛到界面上：选模板 → 填个英文 ID → 点「创建实例」，剩下的复制、克隆环境、链接模型、分配端口一次做完；每个实例在表格里一行，可启动、可开终端、可删除。

---

## 功能

### 1. 启动管理（`managerModules/01_ComfyUIEnvs.py`）

- **创建实例**：复制模板目录 → `conda create --name <ID> --clone <模板>` → 把实例里的 `models` 换成指向共享模型库的 junction → 记录到 `workflows.json`。
- **端口分配**：从 8188 起自动挑一个没被占用的端口，实例之间可并行运行。
- **启动**：`start cmd /k "<conda 激活> && cd /d <实例目录> && python main.py --port <端口> --auto-launch"`。
- **打开终端**：在实例目录里开一个已经激活该环境的 Git Bash。
- **删除实例**：删目录（自动清只读属性重试）+ `conda remove --all`；只有两边都成功才移除记录，失败会保留记录并提示原因。
- **路径自愈**：记录里的路径失效时，按「记录路径 → 工作区/ComfyUIApps/<ID> → 记录中的相对路径」依次探测，命中后把记录改回来。

### 2. 工作流管理（`02_workflows.py`）

在 `saved_workflows.json` 里登记常用工作流（名称、路径、分类），支持增删改查与搜索。
（「一键启动工作流」的信号目前没有接线，见[已知限制](#已知限制)。）

### 3. 自定义节点知识库（`03_nodes_knowledge.py`）

以 Git 仓库地址为主键登记节点包：中文名、功能说明、分类、作者；兼容把 `title/description/reference` 归一化成中文名/功能/仓库名。

### 4. 模型知识库（`04_models.py`）

登记模型文件（Checkpoint / LoRA）、SHA256、预览图、备注；支持 CP/LoRA 过滤与「NSFW 模糊」显示。

### 5. 全局设置

自定义背景图、切换工作区（改完重启程序生效）。

### 6. MCP 服务（`mcp_server.py`）

把管理器暴露成 MCP 工具，让 agent 自己干这些事：看现状 → 挑模板 → 建实例 →
起 ComfyUI → 登记工作流 → 查模型/节点库。零第三方依赖（标准库实现的 stdio MCP），
不监听端口，删除类操作有 `confirm` 硬门槛。

```bash
python mcp_server.py --selftest   # 自检
python mcp_server.py --list       # 看工具清单
python mcp_server.py             # 由 MCP 客户端 spawn
```

14 个工具（`cman_` 前缀）：`status` / `get_config` / `set_config` / `list_templates` /
`list_instances` / `create_instance` / `launch_instance` / `delete_instance` /
`check_instance` / `list_workflows` / `add_workflow` / `discover_workflows` /
`list_models` / `list_nodes`。配置写法与工具细节见 [`docs/MCP.md`](docs/MCP.md)。

---

## 环境要求

| 项目 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Windows 10 / 11 | 依赖 `mklink /J`、Mica 效果、`start cmd /k`，未适配 Linux/macOS |
| Python | 3.10+（实测 3.13.9） | 界面用的 PySide6 |
| Conda | **必需**，Miniconda 或 Anaconda | 创建实例靠 `conda create --clone` |
| Git for Windows | 可选 | 只有「打开终端 (Git Bash)」按钮需要它 |

界面依赖：

```
PySide6==6.10.1
PySide6-Fluent-Widgets==1.9.2
```

---

## 安装

```bat
git clone https://github.com/hmisaki/ComfyUIManager.git
cd ComfyUIManager

rem 建议单独建一个环境（也可以直接用 base）
conda create -n comfyuimanager python=3.12 -y
conda activate comfyuimanager
pip install -r requirements.txt

double-click startManager.bat
rem 或者： set COMFYUI_MANAGER_ENV=comfyuimanager & startManager.bat
```

第一次启动会弹出选择文件夹的对话框：**这个文件夹是「工作区」**，之后所有实例、模型链接、登记数据都放在里面。选完会写入程序目录下的 `launcher_config.json`。

### 首次运行后建议补一条配置

打开 `launcher_config.json`，把 `ai_models_root` 指向你已有的模型库（例如 `D:\Models`）：

```json
{
  "workspace_path": "D:\\ComfyUIWorkSpace",
  "ai_models_root": "D:\\AI\\AIModels"
}
```

它的作用是：新建实例时把实例里的 `models` 链接到这份共享模型库。留空也能跑，但新实例会指向工作区里空的 `GlobalModels`。

---

## 准备一个模板（重要）

「创建实例」下拉框里的模板来自 `Data/ComfyUITemplates/` 下的子目录，而且有一个硬约束：

> **模板目录名必须等于一个已经存在的 conda 环境名。**

因为创建实例执行的是 `conda create -n <新ID> --clone <模板名>` —— `--clone` 认的是环境名，不是目录名。

所以准备模板的流程是：

```bat
rem 1) 先建一个可用的 ComfyUI 环境（装好 torch / ComfyUI 依赖）
conda create -n ComfyBase_py312 python=3.12 -y
conda activate ComfyBase_py312
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
git clone https://github.com/comfyanonymous/ComfyUI

rem 2) 把这份代码放进模板目录，目录名和环境名保持一致
rem    Data/ComfyUITemplates/ComfyBase_py312/ComfyUI/main.py
```

模板目录可以放在两处，程序优先用第一处：

1. `<程序目录>/Data/ComfyUITemplates/` —— 随程序分发（本仓库不含，需自建）
2. `<工作区>/ComfyUITemplates/` —— 工作区里的旧位置（兼容保留）

---

## 目录结构

```
ComfyUIManager/
├── main.py                        # 入口：工作区引导、主窗口、模块动态加载
├── startManager.bat               # 启动脚本
├── launcher_config.json           # 程序级配置（本机生成，不入库）
├── launcher_config.example.json   # 配置模板
├── mcp_server.py                  # MCP 服务（stdio，标准库实现）
├── mcp.example.json               # MCP 客户端配置示例
├── core/
│   ├── config.py                  # 路径 / conda / Git Bash 解析（纯 stdlib）
│   ├── ops.py                     # 无界面操作层：GUI 与 MCP 共用的语义
│   └── signals.py                 # 跨模块信号总线
├── docs/
│   ├── ARCHITECTURE.md            # 架构与数据流
│   ├── CONFIGURATION.md           # 配置项与命令原文
│   └── MCP.md                     # MCP 工具表与客户端配置
├── tools/mcp_smoke_test.py        # 冒烟测试：真的 spawn 一次 MCP 服务
├── managerModules/                # 业务模块，按文件名排序加载
│   ├── 01_ComfyUIEnvs.py          # 启动管理
│   ├── 02_workflows.py            # 工作流管理
│   ├── 03_nodes_knowledge.py      # 自定义节点知识库
│   └── 04_models.py               # 模型知识库
└── Data/                          # 不入库：模板与工作区（体积 10+ GB）
```

工作区（默认放在 `Data/ComfyUIWorkSpace`，可自选）内部：

```
<工作区>/
├── ComfyUIApps/<实例ID>/          # 每个实例一份 ComfyUI 代码
├── GlobalModels/                  # 共享模型库（实例的 models 指向它）
└── Data/
    ├── config.json                # 背景图 / Ai_models_root
    ├── workflows.json             # 实例记录
    ├── saved_workflows.json       # 工作流登记
    ├── nodes.json                 # 自定义节点登记
    ├── models.json                # 模型登记
    └── previews/                  # 模型预览图
```

模块化机制：`main.py` 扫描 `managerModules/*.py`（跳过 `_` 开头的），逐个 `import` 并调用 `module.register(context)`，用返回的 `{"name", "icon", "ui"}` 挂到左侧导航。要加新页面，丢一个文件进去即可，不需要改 `main.py`。

---

## 已知限制

诚实地列出来，避免踩坑：

- **工作流的「启动」按钮没有接线。** `02_workflows.py` 会 emit `request_launch_workflow`，但 `main.py` 没有 connect；`core/signals.py` 里的签名已按双参数修正，接线尚未实现。
- **创建实例是同步阻塞的。** `conda create --clone` 可能跑几分钟，期间界面无响应，没有进度条（后续应放到 `QThread`）。
- **没有自动更新、没有插件市场集成**；自定义节点只是「登记 + 浏览」，不会替你安装。
- **「NSFW 模糊」只是列表层的显示效果**，不是加密。
- **只在 Windows 上验证过**，且只在作者本机验证过；换机器时最可能出问题的是 conda 路径与模板环境。
- **路径里含空格**时，conda 激活命令退化为依赖 `conda` 已在 PATH 中（见 `docs/CONFIGURATION.md`）。
- 部分代码是陆续补全/修复的，**没有完整回归测试**。

---

## 隐私与数据

仓库里**只有管理器自身的源码**：

- 不含任何模型文件、不含 ComfyUI 本体、不含内嵌模板；
- 不含工作区数据（`Data/` 已在 `.gitignore`）；
- 不含 `launcher_config.json`（含本机绝对路径，已忽略；入库的是 `.example`）；
- 源码中不写死任何本机绝对路径 —— conda、Git Bash、工作区全部靠探测或配置解析。

---

## 文档

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) —— 模块划分、上下文契约、数据流
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) —— 每个配置项、探测顺序、常见改法
- [`CHANGELOG.md`](CHANGELOG.md) —— 变更记录

---

## 许可

[MIT](LICENSE)。可以随便改、随便用，不需要署名；出了问题不负责。
