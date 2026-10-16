import os
import stat
import sys
import shutil
import subprocess
from datetime import datetime

# 切换到 PySide6
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QHeaderView, QTableWidgetItem
)
from PySide6.QtCore import Qt

# 引入 Fluent Widgets 组件 (颜值核心)
from qfluentwidgets import (
    PrimaryPushButton, PushButton, LineEdit, ComboBox, TableWidget, 
    MessageBox, InfoBar, InfoBarPosition, SubtitleLabel, CardWidget
)

# 本地模块：路径 / conda / git-bash 的解析都在 core.config，本文件不再写死任何本机路径。
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import config as cfg_util


def remove_tree(path):
    """
    删除整个目录树（修复注 2026-10-14）。

    为什么不能用裸 shutil.rmtree：git 克隆下来的 custom_nodes 里，
    `.git/objects/pack/*.pack|.idx|.rev` 这些文件是**只读**的；Windows 上
    删除只读文件会直接抛 `[WinError 5] 拒绝访问`，于是整个实例都删不掉
    （实测：每个实例都有 3~775 个只读文件，必然踩到）。
    这里遇到任何删不掉的条目就清掉只读属性后重试。

    另：实例里的 models 是 junction/symlink 指向共享模型库（工作区 GlobalModels
    或 launcher_config.json 里配置的 ai_models_root），rmtree 只删除链接本身，
    不会递归进去删共享模型（2026-10-14 实测确认）。
    """
    def _retry(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception as e:      # 仍失败就跳过，让调用方看到残留
            print(f"⚠️ [ComfyUIEnvs] 无法删除 {p}: {e}")

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry)
    else:
        shutil.rmtree(path, onerror=lambda f, p, e: _retry(f, p, e))


def find_comfyui_entry(instance_dir):
    """
    在一个实例目录里找 ComfyUI 的入口（修复注 2026-10-15）。

    返回 (work_dir, run_path)；找不到返回 (None, None)。
    兼容两种模板布局：
      <实例>\\ComfyUI\\main.py   —— 源码版（当前 ComfyBase_py3xx 模板）
      <实例>\\main.py            —— portable 版 / 直接把 ComfyUI 平铺在实例根
    """
    if not instance_dir or not os.path.isdir(instance_dir):
        return None, None
    for sub in ("ComfyUI", ""):
        work_dir = os.path.join(instance_dir, sub) if sub else instance_dir
        run_path = os.path.join(work_dir, "main.py")
        if os.path.isfile(run_path):
            return work_dir, run_path
    return None, None


class EnvironmentManagerUI(QWidget):
    def __init__(self, context):
        super().__init__()
        self.context = context
        self.bus = context["bus"]
        self.cfg = cfg_util.load_config()       # conda / git-bash 的探测结果都从这里来
        
        # --- 1. 从上下文获取动态路径 ---
        self.workspace_dir = context["workspace_dir"]
        self.apps_dir = os.path.join(self.workspace_dir, "ComfyUIApps")

        # 模板目录：优先使用随程序分发的 <app_dir>/Data/ComfyUITemplates（重构后的新位置），
        # 其次回退到工作区内的 ComfyUITemplates。
        # 修复注：旧代码只认工作区里那个空目录，导致"选择模板"下拉框永远只有一个"无模板"。
        legacy_templates = os.path.join(self.workspace_dir, "ComfyUITemplates")
        app_templates = os.path.join(context.get("app_dir") or "", "Data", "ComfyUITemplates")
        if self._has_templates(app_templates) or not self._has_templates(legacy_templates):
            self.templates_dir = app_templates
        else:
            self.templates_dir = legacy_templates

        # 模型源：工作区 GlobalModels 优先；为空时退回 context 里的 Ai_models_root
        # 修复注：GlobalModels 目前是空目录，链接过去等于给新实例配一个空模型库。
        self.models_src = os.path.join(self.workspace_dir, "GlobalModels")
        ai_models_root = context.get("Ai_models_root")
        if (not os.path.isdir(self.models_src) or not os.listdir(self.models_src)) \
                and ai_models_root and os.path.isdir(ai_models_root):
            self.models_src = ai_models_root

        # 数据文件存到 Data 目录
        self.json_file = os.path.join(context["data_dir"], "workflows.json")

        # 确保目录存在（templates_dir 可能是随程序分发的目录，不做创建）
        for p in [self.apps_dir, self.models_src]:
            os.makedirs(p, exist_ok=True)

        self.all_workflows = self.load_workflows_data()
        self.init_ui()
        self.refresh_table()

    @staticmethod
    def _has_templates(path):
        """ 目录存在且至少包含一个模板子目录 """
        if not path or not os.path.isdir(path):
            return False
        try:
            return any(os.path.isdir(os.path.join(path, n)) for n in os.listdir(path))
        except OSError:
            return False

    def init_ui(self):
        # 主布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        # 标题
        layout.addWidget(SubtitleLabel("🚀 环境与工作流实例管理", self))

        # --- 顶部：创建区 (CardWidget 增加层次感) ---
        create_card = CardWidget(self)
        card_layout = QVBoxLayout(create_card)
        
        input_row = QHBoxLayout()
        input_row.setSpacing(15)

        # 模板选择
        self.template_combo = ComboBox()
        self.template_combo.setPlaceholderText("选择模板")
        self.template_combo.setMinimumWidth(150)
        self.reload_templates()
        
        # 输入框
        self.eng_name_input = LineEdit()
        self.eng_name_input.setPlaceholderText("英文ID (如: Flux_Dev)")
        
        self.cn_name_input = LineEdit()
        self.cn_name_input.setPlaceholderText("中文备注 (如: Flux 开发环境)")
        
        # 创建按钮
        self.btn_create = PrimaryPushButton("创建实例")
        self.btn_create.clicked.connect(self.on_create_clicked)

        input_row.addWidget(QLabel("基础模板:"))
        input_row.addWidget(self.template_combo)
        input_row.addWidget(QLabel("ID:"))
        input_row.addWidget(self.eng_name_input)
        input_row.addWidget(QLabel("备注:"))
        input_row.addWidget(self.cn_name_input)
        input_row.addWidget(self.btn_create)
        
        card_layout.addLayout(input_row)
        layout.addWidget(create_card)

        # --- 中间：工具栏 ---
        tool_row = QHBoxLayout()
        self.search_input = LineEdit()
        self.search_input.setPlaceholderText("🔍 搜索工作流...")
        self.search_input.textChanged.connect(self.filter_table)
        self.search_input.setFixedWidth(300)
        
        self.btn_refresh = PushButton("🔄 刷新列表")
        self.btn_refresh.clicked.connect(lambda: (self.reload_templates(), self.refresh_table()))
        
        tool_row.addWidget(self.search_input)
        tool_row.addWidget(self.btn_refresh)
        tool_row.addStretch()
        layout.addLayout(tool_row)

        # --- 表格区 ---
        self.table = TableWidget(self)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["英文 ID", "中文备注", "源模板", "Conda 环境"])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setWordWrap(False)
        layout.addWidget(self.table)

        # --- 底部：操作区 ---
        action_row = QHBoxLayout()
        action_row.setSpacing(15)
        
        self.btn_launch = PrimaryPushButton("🚀 启动 ComfyUI")
        self.btn_launch.clicked.connect(self.on_launch_clicked)
        self.btn_launch.setMinimumWidth(150)
        
        self.btn_cmd = PushButton("💻 打开终端 (Git Bash)")
        self.btn_cmd.clicked.connect(self.on_cmd_clicked)
        
        self.btn_delete = PushButton("🗑️ 删除实例")
        self.btn_delete.clicked.connect(self.on_delete_clicked)
        # 设置删除按钮为红色样式 (可选)
        
        action_row.addStretch()
        action_row.addWidget(self.btn_delete)
        action_row.addWidget(self.btn_cmd)
        action_row.addWidget(self.btn_launch)
        layout.addLayout(action_row)

    # --- 数据逻辑 ---
    
    def reload_templates(self):
        self.template_combo.clear()
        if os.path.exists(self.templates_dir):
            tpls = sorted(os.listdir(self.templates_dir))
            self.template_combo.addItems(tpls)
        else:
            self.template_combo.addItem("无模板")

    def load_workflows_data(self):
        payload = cfg_util.read_json(self.json_file, default={})
        if isinstance(payload, dict):
            return payload.get("workflows", []) or []
        return []

    def save_workflows_data(self, data):
        # 合并写：保留文件里的其它键，只覆盖 workflows。
        payload = cfg_util.read_json(self.json_file, default={})
        if not isinstance(payload, dict):
            payload = {}
        payload["workflows"] = data
        cfg_util.write_json(self.json_file, payload)

    def refresh_table(self):
        self.all_workflows = self.load_workflows_data()
        self.filter_table()

    def filter_table(self):
        keyword = self.search_input.text().strip().lower()
        filtered = [
            w for w in self.all_workflows 
            if keyword in w["eng_name"].lower() or keyword in w["cn_name"]
        ]
        
        self.table.setRowCount(len(filtered))
        for i, wf in enumerate(filtered):
            self.table.setItem(i, 0, QTableWidgetItem(wf["eng_name"]))
            self.table.setItem(i, 1, QTableWidgetItem(wf["cn_name"]))
            self.table.setItem(i, 2, QTableWidgetItem(wf["template"]))
            self.table.setItem(i, 3, QTableWidgetItem(wf["conda_env"]))

    def get_selected_wf(self):
        row = self.table.currentRow()
        if row < 0: return None
        eng_name = self.table.item(row, 0).text()
        # 从原始数据里找完整信息
        for wf in self.all_workflows:
            if wf["eng_name"] == eng_name:
                return wf
        return None

    # --- 业务操作 ---

    def on_create_clicked(self):
        eng = self.eng_name_input.text().strip()
        cn = self.cn_name_input.text().strip()
        tpl = self.template_combo.text()
        
        if not eng or not tpl:
            self.show_message("请输入英文名并选择模板", is_error=True)
            return
            
        # 检查重复
        for w in self.all_workflows:
            if w["eng_name"] == eng:
                self.show_message("该 ID 已存在！", is_error=True)
                return

        target_path = os.path.join(self.apps_dir, eng)
        if os.path.exists(target_path):
             self.show_message("目标文件夹已存在！", is_error=True)
             return

        # 开始创建 (耗时操作，理想情况应该放线程，这里先同步跑)
        try:
            # 1. 复制文件
            tpl_path = os.path.join(self.templates_dir, tpl)
            shutil.copytree(tpl_path, target_path)
            
            # 2. 克隆环境
            env_name = f"{eng}" # 环境名直接用英文ID
            # 注意：模板名必须是一个**已存在的 conda 环境名**（--clone 靠它定位），
            # 所以 Data/ComfyUITemplates 下的模板目录名要和 conda 环境名保持一致。
            proc = cfg_util.conda_exec(self.cfg, ["create", "-y", "-n", env_name, "--clone", tpl])
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()[-300:]
                raise RuntimeError(f"conda create 失败: {tail or '返回码 ' + str(proc.returncode)}")
            
            # 3. 链接模型库 (Junction)
            model_link = os.path.join(target_path, "ComfyUI", "models")
            # 如果模板里自带了 models 文件夹，先改名或删除，否则无法创建链接
            if os.path.exists(model_link) and not os.path.islink(model_link):
                shutil.rmtree(model_link) # 危险操作：确保模板里的 models 是空的
            
            # 创建链接（cmd 的 mklink 失败时返回码非 0；旧代码没有 check，链接失败会静默通过）
            link_result = subprocess.run(
                f'mklink /J "{model_link}" "{self.models_src}"',
                shell=True, capture_output=True, text=True
            )
            if link_result.returncode != 0:
                print(f"⚠️ [ComfyUIEnvs] 模型链接创建失败: {link_result.stdout} {link_result.stderr}")

            # 4. 保存数据
            #    路径落盘用**相对工作区**的形式：整个工作区被搬走/改名后记录依然有效。
            new_record = {
                "eng_name": eng,
                "cn_name": cn,
                "template": tpl,
                "path": cfg_util.to_relative(self.workspace_dir, target_path),
                "conda_env": env_name,
                "port": self._next_port(),      # 每个实例一个端口，可同时运行
                "created_at": datetime.now().isoformat()
            }
            self.all_workflows.append(new_record)
            self.save_workflows_data(self.all_workflows)
            
            self.refresh_table()
            self.show_message(f"成功创建: {cn}")
            
        except Exception as e:
            # 失败时清掉复制了一半的实例目录，否则重试会被"目标文件夹已存在"挡住。
            if os.path.isdir(target_path):
                try:
                    remove_tree(target_path)
                except Exception as clean_err:
                    print(f"⚠️ [ComfyUIEnvs] 创建失败后的残留清理也未成功: {clean_err}")
            self.show_message(f"创建失败: {str(e)}", is_error=True)

    def _next_port(self, start=8188):
        """ 给实例分配一个未被占用的端口号（记录里已存的端口优先保留） """
        used = {w.get("port") for w in getattr(self, "all_workflows", []) or []
                if isinstance(w.get("port"), int)}
        port = start
        while port in used:
            port += 1
        return port

    def resolve_instance_dir(self, wf, heal=True):
        """
        返回实例在磁盘上的真实目录（修复注 2026-10-15）。

        背景：实例路径旧版本是**绝对路径**存在 workflows.json 里的。整个工作区被
        搬走或改名之后，记录里的 path 就成了死路径 —— 启动时 `cd /d` 只会甩一句
        「系统找不到指定的路径。」，而此时 `conda activate` 已经成功，看起来像
        环境坏了，其实只是路径过期。

        探测顺序：记录路径 → 工作区/ComfyUIApps/<eng_name> → 记录里的相对路径。
        命中非记录路径时顺手把记录改回来并落盘（自愈），下次直接命中；
        自愈写回的仍然是**相对工作区**的路径。
        """
        eng = wf.get("eng_name", "")
        recorded = (wf.get("path") or "").strip()
        candidates = []
        if recorded:
            candidates.append(recorded)
            if not os.path.isabs(recorded):
                candidates.append(os.path.join(self.workspace_dir, recorded))
        if eng:
            candidates.append(os.path.join(self.apps_dir, eng))

        for cand in candidates:
            if cand and os.path.isdir(cand):
                same = recorded and os.path.normcase(os.path.abspath(cand)) == \
                    os.path.normcase(os.path.abspath(recorded))
                if heal and not same:
                    wf["path"] = cfg_util.to_relative(self.workspace_dir, cand)
                    try:
                        self.save_workflows_data(self.all_workflows)
                    except Exception as e:
                        print(f"⚠️ [ComfyUIEnvs] 路径自愈后保存失败: {e}")
                    print(f"🔧 [ComfyUIEnvs] 实例 {eng} 路径已自愈: {recorded} → {wf['path']}")
                return cand
        return None

    def build_launch_command(self, wf, work_dir, port, run_path=None):
        """ 构造启动命令。
        修复注 2026-10-14：原来不带 --port，所有实例都抢 8188，只能同时跑一个。
        修复注 2026-10-15：路径加引号；直接给 python 完整脚本路径，不再依赖 cd 后的相对名。
        修复注（开源整理）：conda 激活改用 core.config 拼装 —— 先 call condabin\\conda.bat，
        不再依赖 conda 已经进 PATH。
        """
        script = run_path or os.path.join(work_dir, "main.py")
        activate = cfg_util.cmd_activate_prefix(self.cfg, wf["conda_env"])
        return (f'start cmd /k "{activate}cd /d "{work_dir}" '
                f'&& python "{script}" --port {port} --auto-launch"')

    def on_launch_clicked(self):
        wf = self.get_selected_wf()
        if not wf: return

        # 修复注 2026-10-15：先解析真实目录。path 过期时直接给出人能看懂的报错，
        # 而不是让 cmd 甩一句「系统找不到指定的路径。」
        instance_dir = self.resolve_instance_dir(wf)
        if not instance_dir:
            self.show_message(
                f"实例目录不存在，无法启动。\n记录路径：{wf.get('path')}\n"
                f"已尝试：{os.path.join(self.apps_dir, wf.get('eng_name', ''))}",
                is_error=True
            )
            return

        work_dir, run_path = find_comfyui_entry(instance_dir)
        if not run_path:
            self.show_message(f"在 {instance_dir} 下找不到 ComfyUI 入口 main.py，无法启动。", is_error=True)
            return

        port = wf.get("port") or self._next_port()
        if wf.get("port") != port:          # 首次启动时把端口固定下来
            wf["port"] = port
            self.save_workflows_data(self.all_workflows)

        cmd = self.build_launch_command(wf, work_dir, port, run_path)
        os.system(cmd)
        self.show_message(f"正在启动: {wf['cn_name']} (端口 {port}) ...")

    def on_cmd_clicked(self):
        wf = self.get_selected_wf()
        if not wf: return

        # Git Bash 不再写死：配置 → 环境变量 GIT_BASH_PATH → PATH → 常见安装位置。
        git_bash = cfg_util.resolve_git_bash(self.cfg)
        if not git_bash:
            self.show_message(
                "未找到 git-bash.exe。请安装 Git for Windows，"
                "或在 launcher_config.json 里填写 git_bash_path。",
                is_error=True
            )
            return

        instance_dir = self.resolve_instance_dir(wf)     # 修复注 2026-10-15：同样处理路径过期
        if not instance_dir:
            self.show_message(f"实例目录不存在：{wf.get('path')}", is_error=True)
            return

        # 显式 source conda.sh 再 activate：非登录 shell 里 conda 往往没被初始化。
        activate = cfg_util.bash_activate_prefix(self.cfg, wf["conda_env"])
        cmd = f'start "" "{git_bash}" --cd="{instance_dir}" -i -c "{activate}exec bash"'
        os.system(cmd)

    def on_delete_clicked(self):
        wf = self.get_selected_wf()
        if not wf: return
        
        w = MessageBox("危险操作", f"确定要删除【{wf['cn_name']}】吗？\n这也将删除其 Conda 环境和文件夹！", self.window())
        if w.exec():
            errors = []

            # 1. 删文件夹（修复注：原来用裸 rmtree，遇到只读文件就 [WinError 5] 拒绝访问，
            #    文件夹删一半、环境没删、记录还留着 → 现在会清只读属性重试并检查残留）
            #    修复注 2026-10-15：路径过期（工作区被搬走）时也要能删到真目录。
            instance_dir = self.resolve_instance_dir(wf)
            if instance_dir and os.path.exists(instance_dir):
                try:
                    remove_tree(instance_dir)
                except Exception as e:
                    errors.append(f"文件夹删除失败({e})")
                if os.path.exists(instance_dir):
                    errors.append(f"文件夹仍有残留: {instance_dir}")
            elif not instance_dir:
                print(f"ℹ️ [ComfyUIEnvs] 实例 {wf.get('eng_name')} 的目录已不在磁盘上，跳过文件夹删除")

            # 2. 删 conda 环境（修复注：原来不检查返回码，conda 失败也显示“删除成功”）
            try:
                r = cfg_util.conda_exec(self.cfg, ["remove", "-y", "-n", wf["conda_env"], "--all"])
                if r.returncode != 0:
                    tail = (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip()[-300:]
                    errors.append(f'Conda 环境 {wf["conda_env"]} 删除失败: {tail}')
            except Exception as e:
                errors.append(f"Conda 环境删除失败({e})")

            # 3. 只有在文件和环节都清掉后，才移除记录；失败则保留记录方便重试
            if errors:
                self.show_message("删除未完成：" + "；".join(errors), is_error=True)
            else:
                self.all_workflows = [x for x in self.all_workflows if x["eng_name"] != wf["eng_name"]]
                self.save_workflows_data(self.all_workflows)
                self.refresh_table()
                self.show_message("删除成功")

    def show_message(self, text, is_error=False):
        # 使用 Fluent 的 InfoBar 提示
        InfoBar.error(
            title='错误',
            content=text,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            parent=self
        ) if is_error else InfoBar.success(
            title='成功',
            content=text,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            parent=self
        )

# --- 注册接口 ---
def register(context):
    return {
        "name": "启动管理",
        "icon": "🚀", # 这里的图标在 main.py 里会被映射为 Fluent Icon
        "ui": EnvironmentManagerUI(context)
    }