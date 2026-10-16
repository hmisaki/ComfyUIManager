import os
import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QHeaderView, QTableWidgetItem, QDialog, QLineEdit, QFileDialog, QGridLayout, QComboBox
)
from PySide6.QtCore import Qt
from qfluentwidgets import (
    PrimaryPushButton, PushButton, LineEdit, ComboBox, InfoBar, 
    InfoBarPosition, SubtitleLabel, TextEdit, TableWidget, MessageBox
)

# ---------------------------------------------------------------------
# 助手函数 (Helper Functions)
# ---------------------------------------------------------------------
def get_node_json_path(context):
    """ 获取节点知识库 JSON 文件的路径

    修复注：工作区里没有 custom_nodes.json；真实的节点数据是 Data/nodes.json
            （键=GitHub 地址，字段 title/reference/description/category/author/...）。
    """
    return os.path.join(context["data_dir"], "nodes.json")

def normalize_node_record(key, rec):
    """ 把抓取来的节点记录映射成本模块 UI 使用的字段名（不改原字段） """
    rec = dict(rec)
    if not rec.get("name_cn"):
        rec["name_cn"] = rec.get("title") or rec.get("name") or key
    if not rec.get("function"):
        rec["function"] = rec.get("description", "")
    url = rec.get("reference") or rec.get("github_url") or (key if str(key).startswith("http") else "")
    if not rec.get("github_url"):
        rec["github_url"] = url or "N/A"
    if not rec.get("repo_name"):
        if "github.com/" in url:
            rec["repo_name"] = url.split("github.com/", 1)[1].strip("/")
        else:
            rec["repo_name"] = url or str(key)
    return rec

def load_nodes_data(file_path):
    """ 读取节点知识库数据（落盘格式与 UI 字段名不一致，读入时统一） """
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: normalize_node_record(k, v) for k, v in raw.items() if isinstance(v, dict)}

def save_nodes_data(data, file_path):
    """ 写入节点知识库数据 """
    if not isinstance(data, dict): data = {}
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------
# 模态对话框类 (Modal Dialog) - 用于添加/编辑节点信息
# ---------------------------------------------------------------------
class EditNodeDialog(QDialog):
    CATEGORIES = ["图像", "视频", "效率/工具", "LLM/RAG"]

    def __init__(self, parent, db, node_data=None, current_key=None):
        super().__init__(parent)
        self.db = db
        self.current_key = current_key
        self.is_new_import = current_key is None
        self.node_data_source = node_data
        self.original_record = node_data or {}   # 编辑时保留原字段（author/files/…）
        self.init_ui()
        self.load_data()

    def init_ui(self):
        self.setWindowTitle("⚙️ 编辑节点信息" if not self.is_new_import else "➕ 添加新的节点记录")
        self.setFixedSize(600, 450)
        self.setStyleSheet(self.parent().styleSheet())
        
        main_layout = QVBoxLayout(self)
        grid = QGridLayout()
        grid.setSpacing(15)

        # 1. 基础信息
        self.repo_input = LineEdit()
        self.repo_input.setPlaceholderText("GitHub Repo 名 (例如: ltdrdata/ComfyUI-Impact-Pack)")
        self.cn_name_input = LineEdit()
        self.cn_name_input.setPlaceholderText("中文名称 (例如: 冲击包)")
        self.category_combo = ComboBox()
        self.category_combo.addItems(self.CATEGORIES)
        
        grid.addWidget(QLabel("Repo 名:"), 0, 0)
        grid.addWidget(self.repo_input, 0, 1)
        grid.addWidget(QLabel("中文名:"), 1, 0)
        grid.addWidget(self.cn_name_input, 1, 1)
        grid.addWidget(QLabel("分类:"), 2, 0)
        grid.addWidget(self.category_combo, 2, 1)
        
        # 2. 功能描述与备注
        self.function_edit = TextEdit()
        self.function_edit.setPlaceholderText("功能/作用 (例如: 人脸识别、高清重绘)...")
        
        grid.addWidget(QLabel("功能描述:"), 3, 0)
        grid.addWidget(self.function_edit, 3, 1, 1, 3) # 跨越 3 列
        
        # 3. 如果是新增模式，需要提供路径验证
        if self.is_new_import:
             self.path_input = LineEdit()
             self.path_input.setPlaceholderText("ComfyUI/custom_nodes/ 里的本地路径名")
             grid.addWidget(QLabel("本地目录名:"), 4, 0)
             grid.addWidget(self.path_input, 4, 1, 1, 3)

        main_layout.addLayout(grid)

        # 底部按钮
        btn_ok = PrimaryPushButton("确认保存")
        btn_cancel = PushButton("取消")
        btn_ok.clicked.connect(self.validate_and_accept)
        btn_cancel.clicked.connect(self.reject)
        
        action_row = QHBoxLayout()
        action_row.addStretch()
        action_row.addWidget(btn_cancel)
        action_row.addWidget(btn_ok)
        main_layout.addLayout(action_row)

    def load_data(self):
        """ 加载选中记录的数据到 UI """
        if self.is_new_import:
            return

        node_info = self.node_data_source
        self.repo_input.setText(node_info.get("repo_name", ""))
        self.cn_name_input.setText(node_info.get("name_cn", ""))
        self.function_edit.setText(node_info.get("function", ""))
        
        # 加载分类
        category = node_info.get("category", self.CATEGORIES[0])
        self.category_combo.setCurrentText(category)

    def validate_and_accept(self):
        """ 验证数据并返回 """
        repo_name = self.repo_input.text().strip()
        cn_name = self.cn_name_input.text().strip()

        if not repo_name or not cn_name:
             MessageBox.warning(self.window(), "错误", "Repo 名和中文名不能为空。")
             return

        # 组装返回数据
        # 修复注：编辑已有记录必须沿用原 key（现有数据的键是完整 GitHub 地址；
        #         若用 repo_name 当键，会新增一条重复记录、老记录永远不更新）。
        key = repo_name if self.is_new_import else self.current_key
        record = dict(self.original_record or {})
        record.update({
            "name_cn": cn_name,
            "repo_name": repo_name,
            "category": self.category_combo.currentText(),
            "function": self.function_edit.toPlainText().strip(),
            "github_url": f"https://github.com/{repo_name}",
        })
        self.result_data = {"key": key, "is_new": self.is_new_import, "record": record}
        
        self.accept()


# ---------------------------------------------------------------------
# 核心 UI 类 (主界面模块)
# ---------------------------------------------------------------------
class CustomNodeKnowledgeUI(QWidget):
    def __init__(self, context):
        super().__init__()
        self.context = context
        self.nodes_json_path = get_node_json_path(context)
        
        self.all_nodes = load_nodes_data(self.nodes_json_path)
        self.init_ui()
        self.refresh_node_list()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        layout.addWidget(SubtitleLabel("🛠️ 节点库与依赖管理", self))

        # --- 顶部：工具栏与筛选 ---
        tool_row = QHBoxLayout()
        self.search_input = LineEdit()
        self.search_input.setPlaceholderText("🔍 搜索功能/名称/Github...")
        self.search_input.setFixedWidth(300)
        self.search_input.textChanged.connect(self.filter_nodes)
        
        self.btn_refresh = PushButton("🔄 刷新")
        self.btn_refresh.clicked.connect(self.refresh_node_list)
        
        self.btn_add = PrimaryPushButton("➕ 添加新节点记录")
        self.btn_add.clicked.connect(self.on_open_add_dialog)
        
        tool_row.addWidget(self.search_input)
        tool_row.addWidget(self.btn_refresh)
        tool_row.addStretch()
        tool_row.addWidget(self.btn_add)
        layout.addLayout(tool_row)

        # --- 表格区 (展示所有关键信息) ---
        self.node_table = TableWidget(self)
        # 【修改】列数从 5 减少到 4，删除“状态”列
        self.node_table.setColumnCount(4) 
        self.node_table.setHorizontalHeaderLabels(["名称/功能", "分类", "GitHub 地址", "操作"])
        self.node_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch) # 名字/功能自适应
        self.node_table.setColumnWidth(1, 100) # 分类
        self.node_table.setColumnWidth(3, 80) # 操作
        self.node_table.verticalHeader().hide()
        self.node_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        
        self.node_table.cellDoubleClicked.connect(self.on_edit_dialog_by_table_click)
        layout.addWidget(self.node_table)

    # --- 数据与 UI 填充 ---
    
    def refresh_node_list(self):
        self.all_nodes = load_nodes_data(self.nodes_json_path)
        self.filter_nodes()

    def filter_nodes(self):
        keyword = self.search_input.text().strip().lower()
        
        filtered_list = []
        for key, node in self.all_nodes.items():
            
            # 关键词筛选
            if keyword and keyword not in node.get("name_cn", "").lower() and keyword not in node.get("function", "").lower() and keyword not in node.get("repo_name", "").lower():
                continue
            
            filtered_list.append((key, node))
            
        self.fill_table_data(filtered_list)

    def fill_table_data(self, filtered_list):
        """ 填充表格数据 """
        self.node_table.setRowCount(len(filtered_list))
        
        for i, (key, node) in enumerate(filtered_list):
            
            # 0. 名称/功能 (显示名字和描述)
            name_func = f"【{node.get('name_cn', key)}】\n{node.get('function', '')}"
            name_item = QTableWidgetItem(name_func)
            name_item.setData(Qt.ItemDataRole.UserRole, key) # 关键：存 Key
            self.node_table.setItem(i, 0, name_item)
            
            # 1. 分类
            self.node_table.setItem(i, 1, QTableWidgetItem(node.get("category", 'N/A')))
            
            # 2. GitHub 地址
            self.node_table.setItem(i, 2, QTableWidgetItem(node.get("github_url", 'N/A')))
            
            # 3. 操作 (编辑按钮)
            edit_btn = PrimaryPushButton("编辑")
            edit_btn.setFixedSize(60, 25)
            edit_btn.clicked.connect(lambda checked, k=key: self.on_open_edit_dialog(k))
            
            # 按钮容器
            btn_container = QWidget()
            btn_layout = QHBoxLayout(btn_container)
            btn_layout.setContentsMargins(0, 0, 0, 0)
            btn_layout.addWidget(edit_btn)
            btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.node_table.setCellWidget(i, 3, btn_container) # 【修改】列索引为 3
            
        self.node_table.resizeRowsToContents()

    # --- 对话框与保存逻辑 ---
    
    def on_open_add_dialog(self):
        self.on_open_edit_dialog(current_key=None)

    def on_edit_dialog_by_table_click(self, row, column):
        """ 双击表格行触发编辑对话框 """
        # 名字/功能在第 0 列 (索引 0)
        item = self.node_table.item(row, 0)
        if not item: return
        key = item.data(Qt.ItemDataRole.UserRole)
        self.on_open_edit_dialog(key)

    def on_open_edit_dialog(self, current_key):
        """ 核心：弹出编辑对话框 """
        
        node_data = self.all_nodes.get(current_key) if current_key else None
        
        dialog = EditNodeDialog(self.window(), self.all_nodes, node_data, current_key)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.save_node_record(dialog.result_data)

    def save_node_record(self, data):
        """ 保存数据到数据库 """
        key = data["key"]
        
        # 写入或更新数据
        self.all_nodes[key] = data["record"]
        
        # 写入硬盘
        save_nodes_data(self.all_nodes, self.nodes_json_path)
        self.show_message(f"节点信息已更新: {data['record']['name_cn']}")
        self.refresh_node_list()

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
        "name": "节点知识库",
        "icon": "🛠️", 
        "ui": CustomNodeKnowledgeUI(context)
    }