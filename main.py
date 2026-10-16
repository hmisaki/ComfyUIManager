import sys
import os
import importlib.util

from PySide6.QtCore import Qt, QUrl, QSize
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget, QVBoxLayout, QLabel, QPushButton, QFrame
from PySide6.QtGui import QIcon, QDesktopServices

# 引入 Fluent Widgets 核心组件
from qfluentwidgets import (
    FluentWindow, NavigationItemPosition, FluentIcon as FIF,
    SubtitleLabel, PrimaryPushButton, MessageBox, setTheme, Theme,
    PushButton, CardWidget, ImageLabel
)

# 引入本地模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core import config as cfg_util
from core.config import APP_DIR, CONFIG_FILE
from core.signals import SignalBus

# ---------------------------------------------------------
# 1. 启动引导逻辑 (保持不变，适配 PySide6)
# ---------------------------------------------------------
def get_or_create_workspace():
    """返回工作区路径；首次运行时引导用户选择，取消则退出。"""
    workspace_path = cfg_util.load_config().get("workspace_path") or ""

    if workspace_path and os.path.isdir(workspace_path):
        return workspace_path

    # 第一次运行
    app_temp = QApplication.instance() or QApplication(sys.argv)
    
    # 使用 Fluent 风格的弹窗 (需手动创建简易窗口来承载，这里用原生MessageBox简单处理)
    reply = QMessageBox.question(
        None, 
        "初始化 ComfyUI Manager", 
        "欢迎！\n请选择一个文件夹作为您的【工作区 (Workspace)】。\n所有数据将存储在那里。",
        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
    )

    if reply == QMessageBox.StandardButton.Ok:
        path = QFileDialog.getExistingDirectory(None, "选择工作区文件夹")
        if path:
            cfg_util.ensure_workspace(path)
            cfg_util.update_config(workspace_path=path)
            return path
    
    sys.exit(0)

# ---------------------------------------------------------
# 2. 内置设置页面 (Settings Interface)
# ---------------------------------------------------------
class SettingsInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("settings_interface")
        self.main_window = parent
        
        # 布局
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(30, 30, 30, 30)
        self.vBoxLayout.setSpacing(20)

        # 标题
        self.lbl_title = SubtitleLabel("⚙️ 全局设置", self)
        self.vBoxLayout.addWidget(self.lbl_title)

        # 卡片容器
        self.card = CardWidget(self)
        self.card_layout = QVBoxLayout(self.card)
        
        # 背景设置行
        self.lbl_bg_info = QLabel("自定义背景图片", self.card)
        self.btn_set_bg = PrimaryPushButton("选择图片", self.card)
        self.btn_set_bg.clicked.connect(self.select_bg_image)
        
        self.btn_clear_bg = PushButton("恢复默认", self.card)
        self.btn_clear_bg.clicked.connect(self.clear_bg_image)

        self.card_layout.addWidget(self.lbl_bg_info)
        self.card_layout.addWidget(self.btn_set_bg)
        self.card_layout.addWidget(self.btn_clear_bg)
        
        self.vBoxLayout.addWidget(self.card)
        
        # 切换工作区
        self.btn_switch_ws = PushButton("📂 切换工作区 (重启)", self)
        self.btn_switch_ws.clicked.connect(self.switch_workspace)
        self.vBoxLayout.addWidget(self.btn_switch_ws)

        self.vBoxLayout.addStretch(1)

    def select_bg_image(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择背景", "", "Images (*.png *.jpg *.webp)")
        if file_path:
            self.main_window.update_background(file_path)

    def clear_bg_image(self):
        self.main_window.update_background("")

    def switch_workspace(self):
        w = MessageBox("确认", "切换工作区需要重启程序，是否继续？", self.window())
        if w.exec():
            if os.path.exists(CONFIG_FILE):
                os.remove(CONFIG_FILE)
            QApplication.quit()
            import subprocess
            subprocess.Popen([sys.executable] + sys.argv)

# ---------------------------------------------------------
# 3. 主窗口 (Fluent Window)
# ---------------------------------------------------------
class MainWindow(FluentWindow):
    def __init__(self, workspace_path):
        super().__init__()
        self.workspace_dir = workspace_path
        
        # 初始化上下文
        self.data_dir = os.path.join(self.workspace_dir, "Data")
        self.config_file = os.path.join(self.data_dir, "config.json")
        self.bus = SignalBus()

        # 模型根目录：优先读工作区 Data/config.json（GUI 保存的位置），
        # 其次 launcher_config.json。
        # 用途：新建实例时作为默认模型源（GlobalModels 为空时的回退，见 01_ComfyUIEnvs.py）。
        # 注：AI_Kernel 与 core/ai_service 已于 2026-10-14 整体移除，此处不再供 AI 工具解析模型路径。
        self.Ai_models_root = None
        for cfg_path in (self.config_file, CONFIG_FILE):
            payload = cfg_util.read_json(cfg_path, default={})
            if isinstance(payload, dict):
                # 兼容历史两种键名（GUI 设置页写的是 Ai_models_root）
                self.Ai_models_root = payload.get("Ai_models_root") or payload.get("ai_models_root")
            if self.Ai_models_root:
                break

        if not self.Ai_models_root:
            print("⚠️ [Main] 未配置 Ai_models_root：新建实例将没有默认模型源。")
        elif not os.path.isdir(self.Ai_models_root):
            print(f"⚠️ [Main] Ai_models_root 不存在: {self.Ai_models_root}")
        else:
            print(f"✅ [Main] Ai_models_root = {self.Ai_models_root}")

        self.context = {
            "bus": self.bus,
            "workspace_dir": self.workspace_dir,
            "data_dir": self.data_dir,
            "Ai_models_root":self.Ai_models_root,
            "app_dir": APP_DIR
        }

        self.init_window()

        # 2. 动态加载业务模块
        self.load_modules()

        # 1. 加载设置页 (固定在底部)
        self.settings_interface = SettingsInterface(self)
        self.addSubInterface(
            self.settings_interface, 
            FIF.SETTING, 
            "全局设置", 
            NavigationItemPosition.BOTTOM
        )

        # 3. 加载背景配置
        self.load_config()
        


    def init_window(self):
        self.setWindowTitle(f"ComfyUI Manager - {os.path.basename(self.workspace_dir)}")
        self.resize(1200, 800)
        # 开启亚克力效果 (Win11 Mica)
        self.windowEffect.setMicaEffect(self.winId())
        
        # 默认主题设为深色
        setTheme(Theme.DARK)

    def load_modules(self):
        """ 动态扫描 managerModules """
        modules_dir = os.path.join(APP_DIR, "managerModules")
        if not os.path.exists(modules_dir): os.makedirs(modules_dir)
        
        files = sorted([f for f in os.listdir(modules_dir) if f.endswith(".py") and not f.startswith("_")])
        
        # 图标映射表 (根据文件名猜图标)
        icon_map = {
            "run": FIF.PLAY,
            "workflow": FIF.IOT,
            "model": FIF.LIBRARY,
            "knowledge": FIF.BOOK_SHELF,
            "tool": FIF.DEVELOPER_TOOLS
        }

        for filename in files:
            module_name = filename[:-3]
            # 简单的图标匹配逻辑
            icon = FIF.APPLICATION
            for key, val in icon_map.items():
                if key in module_name.lower():
                    icon = val
                    break

            file_path = os.path.join(modules_dir, filename)
            try:
                spec = importlib.util.spec_from_file_location(module_name, file_path)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    if hasattr(module, "register"):
                        info = module.register(self.context)
                        widget = info.get("ui")
                        name = info.get("name", module_name)
                        
                        if widget:
                            # 给子模块设置个对象名，方便 CSS 定位
                            widget.setObjectName(module_name)
                            # 添加到 Fluent 导航栏
                            routeKey = widget.objectName()
                            self.addSubInterface(widget, icon, name)
                            print(f"✅ Loaded: {name}")
            except Exception as e:
                print(f"❌ Failed: {filename} - {e}")

    def update_background(self, image_path):
        """ 背景图逻辑：覆盖在 StackedWidget 上 """
        if image_path and os.path.exists(image_path):
            image_path = image_path.replace("\\", "/")
            # FluentWindow 的内容区叫 stackedWidget
            # 我们给它加个半透明遮罩，并把图放在最底层
            qss = f"""
                QStackedWidget {{
                    border-image: url("{image_path}") 0 0 0 0 stretch stretch;
                }}
                /* 让子页面背景透明，透出底图 */
                QWidget {{ 
                    background-color: transparent; 
                }}
                /* 给文字加点阴影防止看不清 */
                QLabel {{
                    color: white;
                    font-weight: bold;
                }}
            """
            self.stackedWidget.setStyleSheet(qss)
            
            # 保存配置
            self.save_config(image_path)
        else:
            self.stackedWidget.setStyleSheet("") # 恢复默认
            self.save_config("")

    def save_config(self, bg_path):
        """把背景图路径写进工作区 Data/config.json（显式 UTF-8）。"""
        cfg = cfg_util.read_json(self.config_file, default={})
        if not isinstance(cfg, dict):
            cfg = {}
        # 背景图若在工作区内，落盘时转成相对路径 —— 整个工作区搬走也能继续用。
        cfg["background_image"] = cfg_util.to_relative(self.workspace_dir, bg_path) if bg_path else ""
        cfg_util.write_json(self.config_file, cfg)

    def load_config(self):
        cfg = cfg_util.read_json(self.config_file, default={})
        if isinstance(cfg, dict):
            self.update_background(self._absolute(cfg.get("background_image", "")))

    def _absolute(self, path):
        """把落盘的相对路径还原成绝对路径。"""
        if not path:
            return ""
        if os.path.isabs(path):
            return path
        return os.path.join(self.workspace_dir, path)

if __name__ == "__main__":
    # 修复注：stdout 被重定向（管道 / IDE / 计划任务）时 Python 默认用 GBK 编码，
    #         打印 ✅/❌/emoji 会抛 UnicodeEncodeError 直接崩掉。这里强制 UTF-8。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    app = QApplication(sys.argv)
    
    # 初始化工作区
    ws_path = get_or_create_workspace()
    
    w = MainWindow(ws_path)
    w.show()
    sys.exit(app.exec())