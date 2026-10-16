# 配置说明

## 两个配置文件，两层职责

| 文件 | 位置 | 管什么 | 是否入库 |
|---|---|---|---|
| `launcher_config.json` | 程序目录 | **机器级**：工作区在哪、conda 在哪、Git Bash 在哪、模型库在哪 | 否（入库的是 `.example`） |
| `config.json` | `<工作区>/Data/` | **用户级**：背景图、`Ai_models_root` | 否（在工作区里） |
| `workflows.json` / `saved_workflows.json` / `nodes.json` / `models.json` | `<工作区>/Data/` | 各模块的登记数据 | 否 |

---

## `launcher_config.json`

首次运行时若不存在，程序会弹窗引导选择工作区，然后自动写出。所有字段都可以留空 —— **留空 = 自动探测**。

```json
{
  "workspace_path": "D:\\ComfyUIWorkSpace",
  "ai_models_root": "D:\\AI\\AIModels",
  "conda_exe": "",
  "conda_root": "",
  "git_bash_path": ""
}
```

| 键 | 说明 | 留空时 |
|---|---|---|
| `workspace_path` | 工作区根目录（绝对路径） | 启动时弹窗引导选择 |
| `ai_models_root` | 新建实例时把 `models` 链接过去的共享模型库 | 退化为工作区里的 `GlobalModels` |
| `conda_exe` | conda 可执行文件，`.exe` 或 `.bat` 都行 | 依次找 `CONDA_EXE` → PATH → `~\miniconda3` 等常见位置 |
| `conda_root` | conda 安装根目录（用于拼激活命令） | 由 `conda_exe` 反推 |
| `git_bash_path` | `git-bash.exe` 的完整路径 | 依次找 `GIT_BASH_PATH` → PATH → `Program Files\Git` 等 |

### 例：conda 装在非默认位置

```json
{
  "conda_exe": "E:\\miniconda3\\Scripts\\conda.exe",
  "conda_root": "E:\\miniconda3"
}
```

### 例：想强制指定 Git Bash

```json
{ "git_bash_path": "D:\\Tools\\Git\\git-bash.exe" }
```

### 环境变量兜底

不想改配置文件时，也可以用环境变量：

- `GIT_BASH_PATH` —— git-bash.exe 路径
- `CONDA_EXE` —— conda 可执行文件路径

配置文件里的值优先于环境变量。

---

## 探测顺序汇总

**conda**（`core.config.find_conda`）：

1. `launcher_config.json` 的 `conda_exe`
2. 环境变量 `CONDA_EXE`
3. `PATH` 上的 `conda` / `conda.exe` / `conda.bat`
4. 常见安装位置：`~\miniconda3`、`~\Miniconda3`、`~\anaconda3`、`%ProgramData%\miniconda3`、`C:\miniconda3`、`D:\anaconda3` …（每个都试 `Scripts\conda.exe` 与 `condabin\conda.bat`）

**Git Bash**（`resolve_git_bash`）：

1. `git_bash_path`
2. `GIT_BASH_PATH`
3. `PATH`
4. `%ProgramFiles%\Git\git-bash.exe`、`%ProgramFiles(x86)%\Git\...`、`%LOCALAPPDATA%\Programs\Git\...`

**共享模型库**：`<工作区>/GlobalModels`（非空） → 否则 `Ai_models_root`（先读工作区 `Data/config.json`，再读 `launcher_config.json`）。

---

## 命令拼装

### 启动实例（cmd）

```
start cmd /k "call "<conda_root>\condabin\conda.bat" activate "<环境名>" && cd /d "<实例目录>" && python "<实例目录>\main.py" --port <端口> --auto-launch"
```

- `--port` 由程序从 8188 起分配，写回实例记录；
- `--auto-launch` 让 ComfyUI 起来后自动打开浏览器；
- 优先用 `condabin\conda.bat`，因此**不要求 conda 已经在 PATH 里**。

> 已知退化：如果 `conda_root` 的完整路径里含空格（例如用户名带空格），命令会退回 `conda activate <环境名>`，此时要求 `conda` 已在 PATH 中。

### 打开终端（Git Bash）

```bash
start "" "<git-bash>\git-bash.exe" --cd="<实例目录>" -i -c "source "<conda_root>/etc/profile.d/conda.sh" && conda activate "<环境名>" && exec bash"
```

显式 `source conda.sh` 的原因：`conda activate` 要求 shell 初始化过 conda，非登录 shell 里经常无声失败；如果连 `conda.sh` 都找不到，会退化为把环境目录塞进 `PATH`。

### 删除实例

```
conda remove -y -n <环境名> --all
```

所有 conda 调用都走 `core.config.conda_exec()`（**参数列表形式，`shell=False`**），环境名里的空格或特殊字符不会再破坏命令。

---

## 路径落盘的规则

- **实例路径**：写进 `workflows.json` 时转成**相对工作区**的路径（`ComfyUIApps/<ID>`）。
  这样整个工作区被搬走或改名后记录依然有效。
- **兼容旧数据**：读到的绝对路径照旧能用；一旦探测到真实目录，会把记录改写成相对路径（自愈）。
- **背景图**：同样优先存相对路径，读回时再还原成绝对路径。
- **所有 JSON 写入**统一用 `core.config.write_json()`：显式 UTF-8 + `ensure_ascii=False`，
  避免中文路径/备注在中文 Windows 上被写坏。

---

## 自检

不需要界面，也不需要 Qt：

```bat
cd /d D:\ComfyUIManager
python -m core.config
```

输出会长这样（本机实测）：

```
app_dir: D:\ComfyUIManager
config_file: D:\ComfyUIManager\launcher_config.json
config_exists: True
workspace_path: D:/ComfyUIWorkSpace
workspace_ok: True
conda_exe: C:\Users\<user>\miniconda3\Scripts\conda.exe
conda_root: C:\Users\<user>\miniconda3
conda_envs: {'base': '...', 'ComfyBase_py312': '...', ...}
git_bash_path: C:\Program Files\Git\git-bash.exe
ai_models_root:
python: C:\Users\<user>\miniconda3\python.exe
```

排查「起不来」时先看这三行：`workspace_ok`、`conda_exe`、`git_bash_path`。
