import os
import json
from datetime import datetime
import subprocess

# 引入 PySide6 和 Fluent Widgets
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QHeaderView, QTableWidgetItem, QDialog, QLineEdit, QFileDialog
)
from PySide6.QtCore import Qt

from qfluentwidgets import (
    PrimaryPushButton, PushButton, LineEdit, ComboBox, TableWidget, 
    InfoBar, InfoBarPosition, SubtitleLabel, CardWidget, MessageBox
)

# ---------------------------------------------------------------------
# 助手函数 (Helper Functions) - 封装 JSON 读写
# ---------------------------------------------------------------------
def get_data_paths(context):
    """ 获取所有 JSON 文件的路径 """
    data_dir = context["data_dir"]
    # 存储环境实例的 JSON (用于获取环境列表)
    env_json = os.path.join(data_dir, "workflows.json") 
    # 存储工作流文件信息的 JSON (本模块的主数据源)
    wf_json = os.path.join(data_dir, "saved_workflows.json")
    return env_json, wf_json

def load_json_data(file_path, key="workflows"):
    """ 读取 JSON 数据

    修复注：saved_workflows.json 的顶层键是 workflows（另有 categories），
            原代码按 saved_workflows 读，工作流页永远是空列表。
    """
    if not os.path.exists(file_path):
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f).get(key, [])
    except:
        return []

def save_json_data(file_path, data, key="workflows"):
    """ 写入 JSON 数据（合并写：保留同文件里的其它键，例如 categories） """
    existing = {}
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                existing = loaded
        except Exception:
            existing = {}
    existing[key] = data
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)

def get_available_envs(env_json_path):
    """ 从环境 JSON 中提取出可用的 Conda 环境名称 """
    env_data = load_json_data(env_json_path, key="workflows")
    # 提取 "conda_env" 字段
    return [wf["conda_env"] for wf in env_data]

# ---------------------------------------------------------------------
# 模态对话框类 (Modal Dialog) - 用于添加/编辑记录
# ---------------------------------------------------------------------
class AddWorkflowDialog(QDialog):
    def __init__(self, parent, env_json_path):
        super().__init__(parent)
        self.setWindowTitle("➕ 添加新的工作流记录")
        self.setFixedSize(500, 350)
        self.env_json_path = env_json_path
        self.setStyleSheet("""
            QDialog { 
                background-color: #2b2b2b; /* 设置深色背景 */
                color: #d4d4d4; /* 确保默认文本颜色是白色 */
            } 
            QLineEdit { 
                background-color: #3e3e3e; /* 输入框背景比窗口深色浅一点 */
                border: 1px solid #555555; 
                color: white; /* 确保输入文字是白色 */
            } 
            QComboBox { 
                background-color: #3e3e3e; 
                color: white; 
            }
        """)
        #self.setStyleSheet(parent.styleSheet())
        self.init_ui()
        self.result_data = None

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        
        # 1. 文件路径输入
        path_row = QHBoxLayout()
        self.path_input = LineEdit()
        self.path_input.setPlaceholderText("工作流文件 (.json/.png) 路径 (必填)")
        
        btn_browse = PrimaryPushButton("浏览文件")
        btn_browse.setFixedWidth(100)
        btn_browse.clicked.connect(self.browse_file)
        
        path_row.addWidget(self.path_input)
        path_row.addWidget(btn_browse)
        layout.addLayout(path_row)
        
        # 2. 功能描述
        self.description_input = LineEdit()
        self.description_input.setPlaceholderText("功能描述 (如: Pony V6 极限姿势流) (必填)")
        layout.addWidget(self.description_input)
        
        # 3. 适用环境选择
        self.env_combo = ComboBox()
        self.env_combo.setPlaceholderText("选择所在环境 (必填)")
        self.reload_envs()
        layout.addWidget(self.env_combo)
        
        # 4. 底部按钮
        btn_layout = QHBoxLayout()
        btn_ok = PrimaryPushButton("确认添加")
        btn_cancel = PushButton("取消")
        
        btn_ok.clicked.connect(self.validate_and_accept)
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def reload_envs(self):
        """ 加载环境下拉框数据 """
        self.env_combo.clear()
        envs = get_available_envs(self.env_json_path)
        if not envs:
            self.env_combo.addItem("无可用环境")
            self.env_combo.setEnabled(False)
        else:
            self.env_combo.addItems(envs)
            self.env_combo.setEnabled(True)

    def browse_file(self):
        """ 浏览本地文件 """
        path, _ = QFileDialog.getOpenFileName(self, "选择工作流文件", "", "Workflows (*.json *.png)")
        if path:
            self.path_input.setText(path)

    def validate_and_accept(self):
        """ 验证数据并返回 """
        path = self.path_input.text().strip()
        desc = self.description_input.text().strip()
        env = self.env_combo.currentText()
        
        # 最终验证
        if not os.path.exists(path) or not path.lower().endswith(('.json', '.png')):
            MessageBox.warning(self.window(), "错误", "文件路径无效或非工作流文件 (.json/.png)")
            return
        if not desc:
            MessageBox.warning(self.window(), "错误", "功能描述不能为空")
            return
        if "无可用环境" in env:
            MessageBox.warning(self.window(), "错误", "请先创建环境实例")
            return
        
        self.result_data = {
            "path": path,
            "description": desc,
            "conda_env": env
        }
        self.accept()


# ---------------------------------------------------------------------
# 核心 UI 类 (主界面模块)
# ---------------------------------------------------------------------
class WorkflowManagementUI(QWidget):
    def __init__(self, context):
        super().__init__()
        self.context = context
        self.bus = context["bus"]
        self.env_json_path, self.wf_json_path = get_data_paths(context)
        
        self.all_workflows = load_json_data(self.wf_json_path, key="workflows")
        self.init_ui()
        self.refresh_table()

    def init_ui(self):
        # 主布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        layout.addWidget(SubtitleLabel("📄 工作流文件管理与归档", self))

        # --- 顶部：工具栏 ---
        tool_row = QHBoxLayout()
        self.search_input = LineEdit()
        self.search_input.setPlaceholderText("🔍 搜索工作流描述...")
        self.search_input.setFixedWidth(300)
        self.search_input.textChanged.connect(self.filter_table)
        
        self.btn_refresh = PushButton("🔄 刷新列表")
        self.btn_refresh.clicked.connect(self.refresh_table)
        
        self.btn_open_dialog = PrimaryPushButton("➕ 添加新的工作流")
        self.btn_open_dialog.clicked.connect(self.on_open_add_dialog)
        
        tool_row.addWidget(self.search_input)
        tool_row.addWidget(self.btn_refresh)
        tool_row.addStretch()
        tool_row.addWidget(self.btn_open_dialog)
        layout.addLayout(tool_row)

        # --- 表格区 ---
        self.table = TableWidget(self)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["文件名", "功能描述", "适用环境", "创建时间"])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers) # 禁止编辑
        layout.addWidget(self.table)

        # --- 底部：操作区 ---
        action_row = QHBoxLayout()
        self.btn_delete = PushButton("🗑️ 删除记录")
        self.btn_delete.clicked.connect(self.on_delete_clicked)
        self.btn_launch_wf = PrimaryPushButton("🚀 启动并加载")
        self.btn_launch_wf.clicked.connect(self.on_launch_wf_clicked)
        
        action_row.addWidget(self.btn_delete)
        action_row.addStretch()
        action_row.addWidget(self.btn_launch_wf)
        layout.addLayout(action_row)

        self.setLayout(layout)
        
    # --- UI 填充与过滤 ---
    
    def refresh_table(self):
        """ 重新加载数据并刷新表格 """
        self.all_workflows = load_json_data(self.wf_json_path, key="workflows")
        self.filter_table()

    def filter_table(self):
        """ 过滤表格 """
        keyword = self.search_input.text().strip().lower()
        filtered = [
            wf for wf in self.all_workflows 
            if keyword in wf["description"].lower() or keyword in os.path.basename(wf["path"]).lower()
        ]
        
        self.table.setRowCount(len(filtered))
        for i, wf in enumerate(filtered):
            self.table.setItem(i, 0, QTableWidgetItem(os.path.basename(wf["path"])))
            self.table.setItem(i, 1, QTableWidgetItem(wf["description"]))
            self.table.setItem(i, 2, QTableWidgetItem(wf["conda_env"]))
            self.table.setItem(i, 3, QTableWidgetItem(wf["created_at"][:10])) # 只显示日期

    def get_selected_record(self):
        """ 获取选中行的完整数据记录 """
        row = self.table.currentRow()
        if row < 0: return None
        selected_file = self.table.item(row, 0).text()
        
        for wf in self.all_workflows:
            if os.path.basename(wf["path"]) == selected_file:
                return wf
        return None

    # --- 业务操作 ---

    def on_open_add_dialog(self):
        """ 弹出添加工作流的对话框 """
        dialog = AddWorkflowDialog(self.window(), self.env_json_path)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.result_data
            self.add_workflow_record(data)

    def add_workflow_record(self, data):
        """ 将数据保存到 JSON 并刷新列表 """
        path = data["path"]
        
        # 检查是否已存在 (以路径为唯一ID)
        path_exists = next((wf for wf in self.all_workflows if wf["path"] == path), None)
        
        if path_exists:
            # 更新模式
            path_exists["description"] = data["description"]
            path_exists["conda_env"] = data["conda_env"]
            self.show_message(f"工作流记录已更新: {os.path.basename(path)}")
        else:
            # 新增模式
            new_record = {
                "id": str(len(self.all_workflows) + 1),
                "path": path,
                "description": data["description"],
                "conda_env": data["conda_env"],
                "created_at": datetime.now().isoformat()
            }
            self.all_workflows.append(new_record)
            self.show_message(f"工作流记录已添加: {os.path.basename(path)}")

        save_json_data(self.wf_json_path, self.all_workflows, key="workflows")
        self.refresh_table()

    def on_delete_clicked(self):
        """ 删除工作流记录 """
        wf = self.get_selected_record()
        if not wf:
            self.show_message("请选择要删除的记录", is_error=True)
            return
        
        reply = MessageBox.warning(self.window(), "确认", f"确定删除【{os.path.basename(wf['path'])}】的记录吗？\n(注意：这不会删除硬盘上的实际文件)", MessageBox.StandardButton.Yes | MessageBox.StandardButton.No)

        if reply == MessageBox.StandardButton.Yes:
            self.all_workflows = [r for r in self.all_workflows if r["path"] != wf["path"]]
            save_json_data(self.wf_json_path, self.all_workflows, key="workflows")
            self.show_message(f"记录 {os.path.basename(wf['path'])} 已删除")
            self.refresh_table()

    def on_launch_wf_clicked(self):
        """ 启动对应环境，并自动加载工作流 """
        wf_record = self.get_selected_record()
        if not wf_record:
            self.show_message("请选择要启动的工作流", is_error=True)
            return

        env_name = wf_record["conda_env"]
        wf_path = wf_record["path"]
        
        # 核心逻辑：利用 Signal Bus 通知主程序 Launcher 启动
        # Main.py 会监听这个信号，并调用 launch_workflow(env_name, wf_path)
        # 注意：这里假设你已经在 signals.py 中定义了 request_launch_workflow(str, str)
        try:
             self.bus.request_launch_workflow.emit(env_name, wf_path)
             self.show_message(f"请求启动环境 {env_name}，并加载工作流 {os.path.basename(wf_path)}...")
        except AttributeError:
             self.show_message("信号总线未连接启动逻辑！请检查 main.py", is_error=True)


    def show_message(self, text, is_error=False):
        """ 使用 Fluent 的 InfoBar 提示消息 """
        InfoBar.error(
            title='错误', content=text, position=InfoBarPosition.TOP_RIGHT, parent=self
        ) if is_error else InfoBar.success(
            title='成功', content=text, position=InfoBarPosition.TOP_RIGHT, parent=self
        )


# ---------------------------------------------------------------------
# 模块注册接口
# ---------------------------------------------------------------------
def register(context):
    return {
        "name": "工作流管理",
        "icon": "📄", # 文件图标
        "ui": WorkflowManagementUI(context)
    }