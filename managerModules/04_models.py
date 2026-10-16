import os
import json
import time
import hashlib
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QLineEdit,
    QTextEdit, QPushButton, QComboBox, QCheckBox, QSplitter, QButtonGroup, QFileDialog, QHeaderView,
    QTableWidgetItem, QDialog, QGridLayout, QFrame, QApplication
)
from PySide6.QtCore import Qt, QSize, QUrl, Signal
from PySide6.QtGui import QClipboard, QImage, QPixmap, QIcon
from qfluentwidgets import (
    PrimaryPushButton, PushButton, LineEdit, ComboBox, InfoBar, 
    InfoBarPosition, SubtitleLabel, CardWidget, TextEdit, 
    ImageLabel, DoubleSpinBox, SwitchButton, TableWidget, MessageBox
)

# --- 文件路径与数据读写 (保持不变) ---
def get_data_paths(context):
    data_dir = context["data_dir"]
    models_json = os.path.join(data_dir, "models.json") 
    preview_dir = os.path.join(data_dir, "previews")
    os.makedirs(preview_dir, exist_ok=True)
    return models_json, preview_dir

def load_models_data(file_path):
    if not os.path.exists(file_path): return {}
    try:
        with open(file_path, "r", encoding="utf-8") as f: return json.load(f)
    except: return {}

def save_models_data(data, file_path):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ---------------------------------------------------------------------
# 2. 【新增】编辑/添加模态对话框
# ---------------------------------------------------------------------
class EditModelDialog(QDialog):
    def __init__(self, parent, db, current_key=None):
        super().__init__(parent)
        self.db = db
        self.current_key = current_key
        self.is_new_import = current_key is None
        self.preview_dir = parent.preview_dir
        self.original_record = None   # 编辑模式：保存时以此为基底合并
        self.result_data = None       # 交给调用方保存的最终数据
        self.init_ui()
        self.load_data()

    def init_ui(self):
        self.setWindowTitle("⚙️ 编辑模型信息" if not self.is_new_import else "📥 导入新模型")
        self.setFixedSize(650, 600)
        self.setStyleSheet(self.parent().styleSheet())
        
        main_layout = QVBoxLayout(self)
        
        # 表格布局
        grid = QGridLayout()
        grid.setSpacing(15)

        # 1. 预览图区域 (左上角)
        self.preview_label = ImageLabel()
        self.preview_label.setFixedSize(180, 180)
        self.preview_label.setStyleSheet("border: 1px dashed #666; border-radius: 8px;")
        
        # 2. 文件名/架构 (右侧信息区)
        info_widget = QWidget()
        info_layout = QVBoxLayout(info_widget)
        self.lbl_filename = QLabel("文件名: N/A")
        self.lbl_arch = QLabel("架构: N/A")
        self.lbl_type = QLabel("类型: N/A")
        info_layout.addWidget(self.lbl_filename)
        info_layout.addWidget(self.lbl_arch)
        info_layout.addWidget(self.lbl_type)
        info_layout.addStretch()

        # 3. 基础信息控件
        self.btn_browse = PrimaryPushButton("选择模型文件") # 仅新增时显示
        self.btn_browse.clicked.connect(self.browse_model_file)
        self.path_input = LineEdit() 
        self.path_input.setReadOnly(True) # 路径只读
        
        self.cn_name_input = LineEdit()
        self.cn_name_input.setPlaceholderText("中文名称 (必填)")
        
        self.nsfw_check = QCheckBox("🔞 NSFW 内容")
        self.nsfw_check.setStyleSheet("color: #FF5733; font-weight: bold;")
        
        # --- LoRA 专属区 ---
        self.trigger_edit = LineEdit()
        self.trigger_edit.setPlaceholderText("触发词 (例如: sks_girl, latex)")
        self.strength_spin = DoubleSpinBox()
        self.strength_spin.setRange(-2.0, 2.0)
        self.strength_spin.setDecimals(2)
        
        # 布局
        grid.addWidget(self.preview_label, 0, 0, 2, 1) # 跨越两行
        grid.addWidget(info_widget, 0, 1, 2, 1) # 跨越两行
        
        grid.addWidget(QLabel("中文名"), 2, 0)
        grid.addWidget(self.cn_name_input, 2, 1)
        grid.addWidget(self.nsfw_check, 2, 2)
        
        # 修复注：toggle_lora_fields() 要隐藏这个标签，原来用的是匿名 QLabel，
        #         导致 self.lbl_trigger 不存在（AttributeError 打不开对话框）。
        self.lbl_trigger = QLabel("触发词")
        grid.addWidget(self.lbl_trigger, 3, 0)
        grid.addWidget(self.trigger_edit, 3, 1)
        grid.addWidget(QLabel("强度"), 3, 2)
        grid.addWidget(self.strength_spin, 3, 3)

        grid.addWidget(QLabel("备注/参数"), 4, 0)
        self.notes_edit = TextEdit()
        grid.addWidget(self.notes_edit, 4, 1, 1, 3)

        if self.is_new_import:
             grid.addWidget(QLabel("文件路径"), 5, 0)
             grid.addWidget(self.path_input, 5, 1)
             grid.addWidget(self.btn_browse, 5, 2, 1, 2)
             # 新增：导入时总得能选类型/基础模型（原对话框没有入口，
             #       新增记录的类型永远只能是默认值）
             self.type_combo = ComboBox()
             self.type_combo.addItems(["LORA", "Checkpoint"])
             self.type_combo.currentTextChanged.connect(
                 lambda t: self.toggle_lora_fields(t == "LORA"))
             self.base_input = LineEdit()
             self.base_input.setPlaceholderText("如 SD 1.5 / SDXL / Flux")
             grid.addWidget(QLabel("类型"), 6, 0)
             grid.addWidget(self.type_combo, 6, 1)
             grid.addWidget(QLabel("基础模型"), 6, 2)
             grid.addWidget(self.base_input, 6, 3)

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
        """ 加载选中行的数据到 UI """
        if self.is_new_import:
            # 导入模式，只显示按钮
            self.lbl_filename.setText("状态: 待选择文件")
            self.toggle_lora_fields(self.type_combo.currentText() == "LORA")
            return

        # 编辑模式
        self.original_record = dict(self.db.get(self.current_key, {}))
        model_info = self.original_record
        self.lbl_filename.setText(f"文件名: {model_info.get('name_eng')}")
        # 修复注：数据里的字段是 base_model（原来读 architecture，永远显示 N/A）
        self.lbl_arch.setText(
            f"架构: {model_info.get('base_model', model_info.get('architecture', 'N/A'))}")
        self.lbl_type.setText(f"类型: {model_info.get('type', 'N/A')}")
        
        # 填充数据
        self.cn_name_input.setText(model_info.get("name_cn", ""))
        # 修复注：数据里的字段是 nsfw（旧代码读的是 is_nsfw，勾选状态永远错）
        self.nsfw_check.setChecked(bool(model_info.get("nsfw", model_info.get("is_nsfw", False))))
        self.notes_edit.setText(model_info.get("description", ""))
        
        # LoRA/Checkpoint 切换
        is_lora = model_info.get("type") == "LORA"
        self.toggle_lora_fields(is_lora)
        
        if is_lora:
            lora_info = model_info.get("lora_info", {})
            words = model_info.get("trainedWords") or lora_info.get("trigger_words") or []
            if isinstance(words, list):
                words = ", ".join(str(w).strip() for w in words if str(w).strip())
            self.trigger_edit.setText(str(words))
            weight = model_info.get("preferred_weight")
            if weight in (None, ""):
                weight = lora_info.get("reco_strength", 1.0)
            try:
                self.strength_spin.setValue(float(weight))
            except (TypeError, ValueError):
                self.strength_spin.setValue(1.0)
        
        # 载入预览图（数据字段名是 preview_image，旧代码读 preview_path）
        self.load_preview_image(model_info.get("preview_image") or model_info.get("preview_path"))

    def load_preview_image(self, path):
        """ 加载预览图 """
        full_path = os.path.join(self.parent().preview_dir, path) if path else ""
        if path and os.path.exists(full_path):
            pixmap = QPixmap(full_path)
            self.preview_label.setPixmap(pixmap.scaled(
                self.preview_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            ))
        else:
            self.preview_label.setText("无预览图")

    def toggle_lora_fields(self, is_lora):
        """ 切换 LoRA 字段可见性 """
        self.lbl_trigger.setVisible(is_lora)
        self.trigger_edit.setVisible(is_lora)
        self.strength_spin.setVisible(is_lora)

    def browse_model_file(self):
        """ 导入新文件时，浏览硬盘 """
        # 这里应该定位到 GlobalModels 文件夹，而不是 workspace_dir
        # 这里简化为让用户选择任意路径
        path, _ = QFileDialog.getOpenFileName(self, "选择模型文件", "", "Models (*.safetensors *.ckpt *.pth)")
        if path:
            self.path_input.setText(path)

    @staticmethod
    def _sha256_of_file(path, chunk_size=8 * 1024 * 1024):
        """ 大文件分块求哈希（与已有记录的 key/hash 规则一致） """
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                h.update(chunk)
        return h.hexdigest().upper()

    def validate_and_accept(self):
        """ 校验输入、组装 result_data 后关闭对话框 """
        if not self.cn_name_input.text().strip():
             MessageBox.warning(self.window(), "错误", "模型中文名不能为空。")
             return

        record = dict(self.original_record or {})
        record["name_cn"] = self.cn_name_input.text().strip()
        record["nsfw"] = self.nsfw_check.isChecked()
        record["description"] = self.notes_edit.toPlainText().strip()

        if self.is_new_import:
            path = self.path_input.text().strip()
            if not os.path.exists(path):
                 MessageBox.warning(self.window(), "错误", "新增模式下，模型文件路径无效。")
                 return
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                key = self._sha256_of_file(path)
            except OSError as e:
                 QApplication.restoreOverrideCursor()
                 MessageBox.warning(self.window(), "错误", f"读取模型文件失败: {e}")
                 return
            QApplication.restoreOverrideCursor()
            if key in self.db:
                 MessageBox.warning(self.window(), "提示", "这个文件已经导入过了（哈希重复）。")
                 return
            record["type"] = self.type_combo.currentText()
            record["base_model"] = self.base_input.text().strip()
            record["name_eng"] = os.path.basename(path)
            record["path"] = path
            record["hash"] = key
            record["trainedWords"] = [
                w.strip() for w in self.trigger_edit.text().split(",") if w.strip()]
            record["preferred_weight"] = float(self.strength_spin.value())
            record["updated_at"] = datetime.now().isoformat()
        else:
            key = self.current_key
            record.setdefault("hash", key)
            if record.get("type") == "LORA":
                record["trainedWords"] = [
                    w.strip() for w in self.trigger_edit.text().split(",") if w.strip()]
                record["preferred_weight"] = float(self.strength_spin.value())
            record["updated_at"] = datetime.now().isoformat()

        self.result_data = {"key": key, "is_new": self.is_new_import, "record": record}
        self.accept()


# ---------------------------------------------------------------------
# 核心 UI 类 (主界面模块)
# ---------------------------------------------------------------------
class ModelKnowledgeUI(QWidget):
    def __init__(self, context):
        super().__init__()
        self.context = context
        self.bus = context["bus"]
        self.models_json_path, self.preview_dir = get_data_paths(context)
        
        self.all_models = load_models_data(self.models_json_path)
        self.init_ui()
        self.refresh_model_list()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        layout.addWidget(SubtitleLabel("📚 模型知识库与资产管理", self))

        # --- 顶部：工具栏与筛选 ---
        tool_row = QHBoxLayout()
        self.search_input = LineEdit()
        self.search_input.setPlaceholderText("🔍 搜索模型 (名称/功能/触发词)...")
        self.search_input.setFixedWidth(300)
        self.search_input.textChanged.connect(self.filter_models)
        
        self.btn_refresh = PushButton("🔄 刷新")
        self.btn_refresh.clicked.connect(self.refresh_model_list)
        
        self.cp_lora_switch = SwitchButton(text="CHECKPOINT")
        self.cp_lora_switch.setChecked(False) 
        self.cp_lora_switch.checkedText = "LORA"
        self.cp_lora_switch.unCheckedText = "CP"
        self.cp_lora_switch.checkedChanged.connect(self.toggle_cp_lora_filter)
        
        self.nsfw_switch = SwitchButton(text="显示 NSFW")
        self.nsfw_switch.setChecked(True)
        self.nsfw_switch.checkedChanged.connect(self.toggle_nsfw_filter)

        # 修复注：filter_models() 要读这两个属性，原代码只在开关回调里赋值，
        #         于是有数据时首次 refresh 就抛 AttributeError，整页加载失败。
        self.filter_mode = "ALL"
        self.nsfw_filter_on = self.nsfw_switch.isChecked()
        
        self.btn_add = PrimaryPushButton("➕ 导入新模型")
        self.btn_add.clicked.connect(self.on_open_add_dialog)
        
        tool_row.addWidget(self.search_input)
        tool_row.addWidget(self.btn_refresh)
        tool_row.addSpacing(20)
        tool_row.addWidget(self.cp_lora_switch)
        tool_row.addWidget(self.nsfw_switch)
        tool_row.addStretch()
        tool_row.addWidget(self.btn_add)
        layout.addLayout(tool_row)

        # --- 表格区 (展示所有关键信息) ---
        self.model_table = TableWidget(self)
        self.model_table.setColumnCount(6)
        self.model_table.setHorizontalHeaderLabels(["类型", "名称", "架构", "强度", "触发词", "操作"])
        self.model_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.model_table.setColumnWidth(0, 80) # 类型
        self.model_table.setColumnWidth(2, 80) # 架构
        self.model_table.setColumnWidth(3, 80) # 强度
        self.model_table.setColumnWidth(5, 100) # 操作
        self.model_table.verticalHeader().hide()
        self.model_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        
        # 绑定双击事件 (双击编辑)
        self.model_table.cellDoubleClicked.connect(self.on_edit_dialog_by_table_click)

        layout.addWidget(self.model_table)

    # --- 筛选与数据逻辑 ---
    
    def refresh_model_list(self):
        self.all_models = load_models_data(self.models_json_path) # 根目录是字典，key=""
        self.filter_models()

    def toggle_cp_lora_filter(self, checked):
        self.filter_mode = "LORA" if checked else "CHECKPOINT"
        self.filter_models()
        self.cp_lora_switch.setText("LORA" if checked else "CP")

    def toggle_nsfw_filter(self, checked):
        self.nsfw_filter_on = checked
        self.filter_models()

    def filter_models(self):
        keyword = self.search_input.text().strip().lower()
        
        filtered_list = []
        for key, wf in self.all_models.items():
            
            # 1. 类型筛选（数据里类型大小写不一：LORA / Checkpoint，统一大写比较）
            if (self.filter_mode != "ALL"
                    and str(wf.get("type", "")).upper() != self.filter_mode):
                continue
            
            # 2. NSFW 筛选（数据字段名是 nsfw，旧代码读 is_nsfw，两个都认）
            is_nsfw = bool(wf.get("nsfw", wf.get("is_nsfw", False)))
            if not self.nsfw_filter_on and is_nsfw: continue
            
            # 3. 关键词筛选
            name = wf.get("name_cn", key)
            if keyword and keyword not in name.lower() and keyword not in wf.get("description", "").lower():
                continue
            
            filtered_list.append((key, wf))
            
        self.model_table.setRowCount(len(filtered_list))
        
        for i, (key, wf) in enumerate(filtered_list):
            lora_info = wf.get("lora_info", {})
            
            # 0. 类型
            self.model_table.setItem(i, 0, QTableWidgetItem(wf.get("type", 'N/A')))
            # 1. 名字 (UserRole 存 Key)
            # 修复注：is_nsfw 必须在渲染循环里按当前记录重新取——原来从筛选循环里
            #         漏出来的那个值只等于"最后一条记录"，标记会整体错位。
            is_nsfw = bool(wf.get("nsfw", wf.get("is_nsfw", False)))
            name_display = f"🔞 {wf.get('name_cn', key)}" if is_nsfw else wf.get('name_cn', key)
            name_item = QTableWidgetItem(name_display)
            name_item.setData(Qt.ItemDataRole.UserRole, key) # 关键：存 Key
            self.model_table.setItem(i, 1, name_item)
            
            # 2. 架构（数据字段名是 base_model）
            self.model_table.setItem(i, 2, QTableWidgetItem(
                str(wf.get("base_model", wf.get("architecture", 'N/A')))))
            # 3. 强度（数据字段名是 preferred_weight，其次旧字段 lora_info.reco_strength）
            weight = wf.get("preferred_weight")
            if weight in (None, ""):
                weight = lora_info.get("reco_strength", "N/A")
            self.model_table.setItem(i, 3, QTableWidgetItem(f"{weight}"))
            # 4. 触发词（数据字段名是 trainedWords 列表）
            words = wf.get("trainedWords") or lora_info.get("trigger_words") or []
            if isinstance(words, list):
                words = ", ".join(str(w).strip() for w in words if str(w).strip())
            self.model_table.setItem(i, 4, QTableWidgetItem(words or wf.get('description', '')))
            # 5. 操作 (按钮 - 占位)
            edit_btn = PrimaryPushButton("编辑")
            self.model_table.setCellWidget(i, 5, edit_btn)
            edit_btn.clicked.connect(lambda checked, k=key: self.on_edit_button_clicked(k))
            
        self.model_table.resizeRowsToContents()

    def get_selected_record_by_key(self, key):
        """ 根据 Key 获取完整的记录 """
        return self.all_models.get(key)
        
    # --- 按钮/双击 操作 ---

    def on_edit_dialog_by_table_click(self, row, column):
        """ 双击表格行触发编辑对话框 """
        # 获取 Key
        item = self.model_table.item(row, 1)
        if not item: return
        key = item.data(Qt.ItemDataRole.UserRole)
        self.on_open_edit_dialog(key)

    def on_edit_button_clicked(self, key):
        """ 触发编辑对话框 (来自按钮点击) """
        self.on_open_edit_dialog(key)

    def on_open_add_dialog(self):
        """ 导入新模型 (打开空对话框) """
        self.on_open_edit_dialog(current_key=None)


    def on_open_edit_dialog(self, current_key):
        """ 核心：弹出编辑对话框 """
        
        # 1. 找到模型完整数据
        model_data = self.get_selected_record_by_key(current_key) if current_key else None
        
        # 2. 实例化并运行对话框
        # 修复注：① 原来传 self.window()（主窗口没有 preview_dir），EditModelDialog
        #         要用 parent.preview_dir 载入预览图，这里必须传本页；
        #         ② 原来的第 3 个实参 model_data 是多余的——签名只有
        #         (parent, db, current_key)，多传一个直接 TypeError。
        dialog = EditModelDialog(self, self.all_models, current_key)
        
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_data:
            # 3. 如果用户点击 OK，保存数据
            self.save_model_record(dialog.result_data)

    def save_model_record(self, data):
        """ 保存数据到数据库 """
        key = data["key"]
        
        if data["is_new"]:
            # 新增模式
            self.all_models[key] = data["record"]
            self.show_message(f"模型 {data['record']['name_cn']} 已成功导入！")
        else:
            # 编辑模式 (直接更新已存在的 Key)
            self.all_models[key] = data["record"]
            self.show_message(f"模型 {data['record']['name_cn']} 已更新。")
        
        # 写入硬盘（修复注：save_models_data 只有 2 个参数，原来的 key="" 会 TypeError）
        save_models_data(self.all_models, self.models_json_path)
        self.refresh_model_list()

    def get_blurred_icon(self):
        """ 占位函数：返回一个警告图标或模糊图标的路径 """
        return QPixmap(20, 20)

    def show_message(self, text, is_error=False):
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
        "name": "模型知识库",
        "icon": "📚", 
        "ui": ModelKnowledgeUI(context)
    }