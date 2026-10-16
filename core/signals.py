from PySide6.QtCore import QObject, Signal

class SignalBus(QObject):
    """
    全局信号总线 (Global Signal Bus)
    用于解耦各个 UI 模块，实现跨模块通信。
    """
    
    # --- 基础交互信号 ---
    
    # 请求切换左侧导航栏 (参数: 索引 ID)
    # 用途: 在"模型页"点击某个模型，自动跳转到"启动页"并选中对应环境
    request_switch_tab = Signal(int)

    # 请求更换背景图片 (参数: 图片绝对路径)
    # 用途: 设置页面修改背景后，通知主窗口刷新
    request_change_background = Signal(str)

    # 请求显示状态栏消息 (参数: 消息内容, 持续时间ms)
    # 用途: 比如 "模型保存成功", "环境启动中..."
    request_status_message = Signal(str, int)

    # --- 业务逻辑信号 (为未来功能预留) ---

    # 请求启动某个工作流 (参数: 环境名称/ID, 工作流文件路径)
    # 注：02_workflows 已经在用双参数 emit；此处必须声明两个参数，否则 PySide6 会直接抛
    #     "Signal has 1 argument(s) but 2 provided"。目前 main.py 尚未 connect 该信号，
    #     属于半成品链路（见 README「已知限制」）。
    request_launch_workflow = Signal(str, str)

    # 模型数据库已更新 (无参数)
    # 用途: 当你添加/修改了模型信息后，通知列表页刷新
    model_database_updated = Signal()

    # 报错/日志信号 (参数: 错误级别, 错误信息)
    # 用途: 统一收集错误弹窗
    error_occurred = Signal(str, str)