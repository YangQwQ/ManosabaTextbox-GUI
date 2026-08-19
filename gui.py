# gui.py - 修改后的主程序入口
"""PyQt 版本主程序入口"""

import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QGraphicsScene, QGraphicsPixmapItem, QSizePolicy, QDialog
from PySide6.QtCore import Qt, QTimer, QPoint, QMetaObject, Slot, Q_ARG
from PySide6.QtGui import QImage, QPixmap, QIcon
import threading
import traceback

from ui.main_window import Ui_MainWindow
from core import ManosabaCore
from config import CONFIGS
from pyqt_tabs import CharacterTabWidget, BackgroundTabWidget
from image_processor import clear_cache
from path_utils import get_resource_path
from pyqt_setting import SettingWindow
from pyqt_hotkeys import HotkeyManager


class ManosabaMainWindow(QMainWindow):
    """魔裁文本框 PyQt 主窗口"""
      
    def __init__(self):
        super().__init__()
        
        # 设置UI
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        
        # 存储角色和背景标签页引用
        self.character_tabs = []
        self.background_tab = None
        
        # 初始化核心
        self.core = ManosabaCore()
        self.core.gui = self

        # 连接信号到槽
        self.core.status_updated.connect(self.update_status)
        self.core.gui_notification.connect(self._update_sentiment_ui)
        
        # 图片生成状态
        self.is_generating = False
        
        # 添加标志，跟踪样式窗口是否打开
        self.style_window_open = False
        
        # 预览图缩放相关
        self.zoom_level = 1.0
        self.last_mouse_pos = None
        self.is_dragging = False
        
        self._ignore_signals = False

        # 初始化界面
        self._setup_ui()
        
        # 连接信号槽
        self._connect_signals()

        # 初始化热键管理器
        self.hotkey_manager = HotkeyManager(self, self.core)

        # 初始预览
        QTimer.singleShot(100, self.update_preview)
        # 初始化情感分析器
        QTimer.singleShot(500, self.core.init_sentiment_analyzer)

    def _setup_ui(self):
        """设置UI界面"""
        # 设置窗口标题
        self.setWindowTitle("魔裁文本框生成器")
        
        # 初始化样式选择下拉框
        self._init_style_combo()
        
        # 初始化角色和背景标签页
        self._init_components_tabs()
        if self.background_tab is not None:
            self.background_tab.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        
        # 设置预览区域支持鼠标交互
        self._setup_preview_interaction()
        
        # 初始化设置
        self._init_settings()
        
        # 调整预览区域大小
        self._adjust_preview_size()
    
    def _init_components_tabs(self):
        """初始化组件标签页"""
        try:
            self._ignore_signals = True
            # 清除现有标签页（除了背景标签页）
            while self.ui.tabWidget_Layer.count() > 1:
                widget = self.ui.tabWidget_Layer.widget(1)
                if widget:
                    widget.deleteLater()
                self.ui.tabWidget_Layer.removeTab(1)

            # 清空标签页引用
            self.character_tabs.clear()
            
            # 获取预览样式的组件
            sorted_components = CONFIGS.get_sorted_preview_components()
            
            if not sorted_components:
                print("警告：没有找到预览样式的组件")
                return
            
            # 分离背景组件和角色组件
            background_components = [c for c in sorted_components if c.get("type") == "background"]
            character_components = [c for c in sorted_components if c.get("type") == "character"]
            
            # 设置背景标签页（第一个标签页）
            if background_components and self.ui.tabWidget_Layer.count() > 0:
                bg_component = background_components[0]
                bg_layer = bg_component.get("layer", 0)
                
                # 准备UI控件
                ui_controls = {
                    'checkBox_randomBg': self.ui.checkBox_randomBg,
                    'comboBox_bgSelect': self.ui.comboBox_bgSelect,
                    'lineEdit_bgColor': self.ui.lineEdit,
                    'widget_bgColorPreview': self.ui.widget_bgColorPreview
                }
                
                # 创建或更新背景标签页管理器
                if not hasattr(self, 'background_tab') or self.background_tab is None:
                    self.background_tab = BackgroundTabWidget(
                        self, 
                        component_config=bg_component,
                        layer_index=bg_layer,
                        ui_controls=ui_controls
                    )

                    # 连接信号到更新预览
                    self.background_tab.config_changed.connect(self._bg_chara_Cfg_changed)
                    
                    # 更新标签页标题
                    tab_name = bg_component.get("name", "背景")
                    self.ui.tabWidget_Layer.setTabText(0, tab_name)
                else:
                    # 更新现有标签页的配置
                    self.background_tab._component_config = bg_component
                    self.background_tab.layer_index = bg_layer
                    self.background_tab._init_from_config()

            # 为每个角色组件创建标签页
            for component in character_components:
                layer_index = component.get("layer", 0)
                
                # 创建角色标签页
                tab = CharacterTabWidget(self, component_config=component, layer_index=layer_index)
                
                # 连接信号到更新预览
                tab.config_changed.connect(self._bg_chara_Cfg_changed)
                
                # 使用组件名、角色名或默认名称
                if "name" in component:
                    tab_name = component["name"]
                elif "character_name" in component:
                    char_id = component["character_name"]
                    full_name = CONFIGS.get_character(char_id, full_name=True)
                    tab_name = f"{full_name}"
                else:
                    tab_name = f"角色 {layer_index}"
                
                self.ui.tabWidget_Layer.addTab(tab, tab_name)
                self.character_tabs.append(tab)
            
            # 如果样式窗口打开，更新样式窗口的组件编辑器
            if hasattr(self, 'style_window') and self.style_window:
                self.style_window.init_component_editors()
        finally:
            self._ignore_signals = False

    def _bg_chara_Cfg_changed(self):
        """背景或角色配置已更改"""
        if self._ignore_signals:
            return
        
        CONFIGS.update_bracket_color_from_character()
        clear_cache()
        self.update_preview()

    def _init_style_combo(self):
        """初始化样式选择下拉框"""
        # 阻塞信号避免初始化时触发事件
        self.ui.comboBox_StyleSelect.blockSignals(True)
        
        available_styles = list(CONFIGS.style_configs.keys())
        self.ui.comboBox_StyleSelect.clear()
        self.ui.comboBox_StyleSelect.addItems(available_styles)
        
        # 设置当前样式
        current_style = CONFIGS.current_style
        index = self.ui.comboBox_StyleSelect.findText(current_style)
        if index >= 0:
            self.ui.comboBox_StyleSelect.setCurrentIndex(index)
        
        # 恢复信号
        self.ui.comboBox_StyleSelect.blockSignals(False)
    
    def _setup_preview_interaction(self):
        """设置预览区域鼠标交互"""
        # 启用鼠标跟踪
        self.ui.PreviewImg.setMouseTracking(True)
        
        # 设置拖动模式为手形拖动
        self.ui.PreviewImg.setDragMode(self.ui.PreviewImg.DragMode.ScrollHandDrag)
        
        # 设置接受滚轮事件
        self.ui.PreviewImg.viewport().installEventFilter(self)
        
        # 创建场景
        scene = QGraphicsScene()
        self.ui.PreviewImg.setScene(scene)
        
        # 连接鼠标事件
        self.ui.PreviewImg.mousePressEvent = self._preview_mouse_press
        self.ui.PreviewImg.mouseMoveEvent = self._preview_mouse_move
        self.ui.PreviewImg.mouseReleaseEvent = self._preview_mouse_release
        self.ui.PreviewImg.wheelEvent = self._preview_wheel
    
    def _adjust_preview_size(self):
        """调整预览区域大小"""
        # 设置预览视图的策略，使其可以扩展
        self.ui.PreviewImg.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        # 设置滚动区域策略
        self.ui.scrollArea.setWidgetResizable(True)
        
        # 调整预览图大小以适应窗口
        self.ui.scrollAreaWidgetContents.setMinimumHeight(400)
    
    def _init_settings(self):
        """初始化设置"""
        # 自动粘贴和自动发送
        self.ui.checkBox_AutoPaste.setChecked(CONFIGS.AUTO_PASTE_IMAGE)
        self.ui.checkBox_AutoSend.setChecked(CONFIGS.AUTO_SEND_IMAGE)
        
        # 情感匹配
        self.ui.checkBox_EmoMatch.setChecked(False)
        self.ui.checkBox_EmoMatch.setEnabled(True)
        sentiment_settings = CONFIGS.gui_settings.get("sentiment_matching", {})
        if not sentiment_settings.get("display", False):
            self.ui.checkBox_EmoMatch.hide()
    
    @Slot(bool, bool, str)
    def _update_sentiment_ui(self, enabled, available, error_message):
        """更新情感匹配UI - 通过信号调用"""
        try:
            self.ui.checkBox_EmoMatch.setChecked(enabled)
            self.ui.checkBox_EmoMatch.setEnabled(available)
            
            # 显示错误信息或状态
            if error_message and error_message.strip():
                self.update_status(error_message)
            elif enabled and available:
                self.update_status("情感匹配功能已启用")
            CONFIGS.gui_settings["sentiment_matching"]["enabled"] = enabled
            CONFIGS.save_gui_settings()
                
        except Exception as e:
            print(f"更新情感匹配UI时出错: {e}")
    
    def on_sentiment_matching_changed(self):
        """情感匹配设置改变"""
        checked = self.ui.checkBox_EmoMatch.isChecked()
        self.core.toggle_sentiment_matching(checked)

    def _connect_signals(self):
        """连接信号槽"""
        # 样式选择
        self.ui.comboBox_StyleSelect.currentIndexChanged.connect(self.on_style_changed)
        
        # 设置相关
        self.ui.checkBox_AutoPaste.toggled.connect(self.on_auto_paste_changed)
        self.ui.checkBox_AutoSend.toggled.connect(self.on_auto_send_changed)
        self.ui.checkBox_EmoMatch.toggled.connect(self.on_sentiment_matching_changed)
        
        # 按钮
        self.ui.Button_RefreshPreview.clicked.connect(self.update_preview)
        
        # 菜单栏
        self.ui.menu_setting.clicked.connect(self._open_settings)
        self.ui.menu_style.clicked.connect(self._open_style)
        self.ui.menu_about.clicked.connect(self._open_about)
    
    def on_style_changed(self, index):
        """样式改变事件"""
        if index < 0:
            return
        
        style_name = self.ui.comboBox_StyleSelect.currentText()
        if style_name in CONFIGS.style_configs:
            # 应用新样式
            CONFIGS.apply_style(style_name)
            
            # 重新初始化组件标签页
            self._init_components_tabs()
            
            # 更新预览
            self.update_preview()
            self.update_status(f"已切换到样式: {style_name}")
    
    def on_auto_paste_changed(self, checked):
        """自动粘贴设置改变"""
        CONFIGS.AUTO_PASTE_IMAGE = checked
    
    def on_auto_send_changed(self, checked):
        """自动发送设置改变"""
        CONFIGS.AUTO_SEND_IMAGE = checked

    def update_preview(self):
        """更新预览"""
        try:
            preview_image, info = self.core.generate_preview()
            self._update_preview_ui(preview_image, info)
        except Exception as e:
            error_msg = f"预览生成失败: {str(e)}"
            print(traceback.format_exc())
            self.update_status(error_msg)

    def _update_preview_ui(self, preview_image, info):
        """更新预览UI"""
        try:
            # 转换PIL图像为QPixmap
            if preview_image.mode == "RGBA":
                # RGBA图像
                image = QImage(preview_image.tobytes(), preview_image.width, 
                            preview_image.height, QImage.Format.Format_RGBA8888)
            else:
                # RGB图像
                preview_image = preview_image.convert("RGB")
                image = QImage(preview_image.tobytes(), preview_image.width,
                            preview_image.height, QImage.Format.Format_RGB888)
            
            pixmap = QPixmap.fromImage(image)
            
            # 设置到QGraphicsView中
            scene = self.ui.PreviewImg.scene()
            if scene is None:
                scene = QGraphicsScene()
                self.ui.PreviewImg.setScene(scene)
            
            # 清除现有内容
            scene.clear()
            
            # 添加图片
            pixmap_item = QGraphicsPixmapItem(pixmap)
            scene.addItem(pixmap_item)
            scene.setSceneRect(pixmap.rect())
            
            # 调整视图以适应图片
            self.ui.PreviewImg.fitInView(pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
            
            # 更新预览信息
            info_parts = info.split("\n")
            if len(info_parts) >= 3:
                info_text = f"{info_parts[0]} | {info_parts[1]} | {info_parts[2]}"
                self.ui.label_PreviewInfo.setText(info_text)
            else:
                self.ui.label_PreviewInfo.setText(info)
            
        except Exception as e:
            self.update_status(f"预览更新失败: {str(e)}")
            print(traceback.format_exc())
    
    def generate_image(self):
        """生成图片"""
        if self.is_generating:
            return
        
        self.is_generating = True
        self.update_status("正在剪切文本...")
        
        def generate_in_thread():
            try:
                result = self.core.generate_image()
                QMetaObject.invokeMethod(self, "_on_generation_complete", 
                                        Qt.ConnectionType.QueuedConnection,
                                        Q_ARG(str, result))
            except Exception as e:
                error_msg = f"生成失败: {str(e)}"
                print(traceback.format_exc())
                QMetaObject.invokeMethod(self, "_on_generation_complete", 
                                        Qt.ConnectionType.QueuedConnection,
                                        Q_ARG(str, error_msg))
            finally:
                self.is_generating = False
        
        thread = threading.Thread(target=generate_in_thread, daemon=True)
        thread.start()
    
    @Slot(str)
    def _on_generation_complete(self, result):
        """生成完成后的回调函数"""
        self.update_status(result)
        # 生成成功才刷新预览（成功消息始终含"用时:"）；剪切失败/白名单外/合成失败时直接结束，避免误导
        if "用时:" in result:
            self.update_preview()
    
    @Slot(str)
    def update_status(self, message):
        """更新状态栏 - 通过信号调用"""
        self.ui.statusbar.showMessage(message, 15000)
    
    # 预览图鼠标交互功能
    def _preview_mouse_press(self, event):
        """预览图鼠标按下事件"""
        if event.button() == Qt.MouseButton.LeftButton:
            # 使用 position().toPoint() 替代 pos()
            self.last_mouse_pos = event.position().toPoint()
            self.is_dragging = True
            event.accept()
        else:
            # 调用父类方法处理其他鼠标按钮
            self.ui.PreviewImg.__class__.mousePressEvent(self.ui.PreviewImg, event)
    
    def _preview_mouse_move(self, event):
        """预览图鼠标移动事件"""
        if self.is_dragging and self.last_mouse_pos is not None:
            # 使用 position().toPoint() 替代 pos()
            current_pos = event.position().toPoint()
            delta = current_pos - self.last_mouse_pos
            self.last_mouse_pos = current_pos
            
            # 滚动视图
            h_scroll = self.ui.PreviewImg.horizontalScrollBar()
            v_scroll = self.ui.PreviewImg.verticalScrollBar()
            h_scroll.setValue(h_scroll.value() - delta.x())
            v_scroll.setValue(v_scroll.value() - delta.y())
            event.accept()
        else:
            # 调用父类方法
            self.ui.PreviewImg.__class__.mouseMoveEvent(self.ui.PreviewImg, event)
    
    def _preview_mouse_release(self, event):
        """预览图鼠标释放事件"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = False
            self.last_mouse_pos = None
            event.accept()
        else:
            # 调用父类方法
            self.ui.PreviewImg.__class__.mouseReleaseEvent(self.ui.PreviewImg, event)
    
    def _preview_wheel(self, event):
        """预览图滚轮事件 - 缩放"""
        zoom_factor = 1.15
        if event.angleDelta().y() > 0:
            # 放大
            self.zoom_level *= zoom_factor
            self.ui.PreviewImg.scale(zoom_factor, zoom_factor)
        else:
            # 缩小
            self.zoom_level /= zoom_factor
            self.ui.PreviewImg.scale(1/zoom_factor, 1/zoom_factor)
        event.accept()
    
    def _open_settings(self):
        """打开设置窗口"""
        if hasattr(self, 'hotkey_manager'):
            self.hotkey_manager.stop()

        settings_window = SettingWindow(self, self.core)
        settings_window.exec()
        
        self.core.init_sentiment_analyzer()

        # 重新加载热键配置
        self.hotkey_manager.setup_hotkeys()

    def _open_style(self):
        """打开样式窗口"""
        from pyqt_style import StyleWindow
        
        # 设置标志，表示样式窗口已打开
        self.style_window_open = True
        
        # 禁用主界面相关控件并阻塞信号
        self._disable_main_controls(True)
        
        # 创建非模态对话框
        if not hasattr(self, 'style_window') or not self.style_window:
            self.style_window = StyleWindow(self, self.core, self, CONFIGS.current_style)
            self.style_window.setModal(False)  # 设置为非模态
            self.style_window.setWindowModality(Qt.NonModal)  # 非模态模式
            
            # 连接样式窗口的信号
            self.style_window.style_changed.connect(self._on_style_changed_from_window)
            self.style_window.destroyed.connect(self._closed_style_window)
            self.style_window.finished.connect(self._closed_style_window)
        
        # 显示窗口
        self.style_window.show()
        self.style_window.raise_()
        self.style_window.activateWindow()
    
    def _closed_style_window(self):
        """强制恢复主界面控件（用于窗口被销毁时）"""
        if self.style_window_open:
            # 恢复主界面控件状态
            self._disable_main_controls(False)
            
            # 清理引用
            self.style_window = None
            self.style_window_open = False

    def _disable_main_controls(self, disabled):
        """禁用或启用主界面相关控件"""
        # 阻塞样式下拉框信号
        self.ui.comboBox_StyleSelect.blockSignals(disabled)
        
        # 禁用样式下拉框
        self.ui.comboBox_StyleSelect.setEnabled(not disabled)
        
        # 禁用标签页控件
        self.ui.tabWidget_Layer.setEnabled(not disabled)
        
        # 如果禁用，记录当前状态；如果启用，恢复更新
        if not disabled:
            self.ui.comboBox_StyleSelect.blockSignals(False)
    
    def _on_style_changed_from_window(self, style_name):
        """从样式窗口接收样式改变信号"""
        # 如果样式窗口打开，只更新下拉框显示，不触发事件
        if self.style_window_open:
            # 阻塞信号避免循环
            self.ui.comboBox_StyleSelect.blockSignals(True)
            
            index = self.ui.comboBox_StyleSelect.findText(style_name)
            if index >= 0:
                self.ui.comboBox_StyleSelect.setCurrentIndex(index)
            self._init_components_tabs()
            
            # 恢复信号
            self.ui.comboBox_StyleSelect.blockSignals(False)

    def _open_about(self):
        """打开关于窗口"""
        from pyqt_about import AboutWindow
        about_window = AboutWindow(self)
        about_window.exec()

    def highlight_preview_rect(self, x, y, width, height, area_type="text"):
        """在预览图上高亮显示指定区域"""
        try:
            from PySide6.QtGui import QPen, QColor
            
            scene = self.ui.PreviewImg.scene()
            if not scene:
                return
            
            # 清除之前的高亮矩形
            for item in scene.items():
                if hasattr(item, 'is_highlight_rect') and item.is_highlight_rect:
                    scene.removeItem(item)
            
            # 创建新的高亮矩形
            from PySide6.QtWidgets import QGraphicsRectItem
            rect_item = QGraphicsRectItem(x, y, width, height)
            
            # 根据区域类型设置颜色
            if area_type == "text":
                pen = QPen(QColor(0, 255, 0, 200))  # 绿色，半透明
            else:
                pen = QPen(QColor(255, 0, 0, 200))  # 红色，半透明
            
            pen.setWidth(3)
            pen.setStyle(Qt.DashLine)
            rect_item.setPen(pen)
            rect_item.is_highlight_rect = True
            rect_item.setZValue(1000)  # 确保在最上层
            
            scene.addItem(rect_item)
            
            # 3秒后自动清除
            from PySide6.QtCore import QTimer
            QTimer.singleShot(3000, lambda: self.clear_highlight_rect(rect_item))
            
        except Exception as e:
            print(f"高亮预览区域失败: {e}")

    def clear_highlight_rect(self, rect_item=None):
        """清除高亮矩形"""
        scene = self.ui.PreviewImg.scene()
        if not scene:
            return
        
        if rect_item:
            scene.removeItem(rect_item)
        else:
            # 清除所有高亮矩形
            for item in scene.items():
                if hasattr(item, 'is_highlight_rect') and item.is_highlight_rect:
                    scene.removeItem(item)
    
    def get_character_tab_widgets(self):
        """返回所有角色标签页的引用"""
        widgets = {}
        # 修复：通过 self.ui 访问 tabWidget_Layer
        for i in range(self.ui.tabWidget_Layer.count()):
            widget = self.ui.tabWidget_Layer.widget(i)
            if isinstance(widget, CharacterTabWidget):
                widgets[widget.layer_index] = widget
        return widgets

    def get_background_tab_widgets(self):
        """背景标签页的引用"""
        widgets = {}
        # 修复：直接返回存储的 background_tab 实例，而不是遍历标签页
        if hasattr(self, 'background_tab') and self.background_tab is not None:
            widgets[self.background_tab.layer_index] = self.background_tab
        return widgets
                    
def _ensure_admin():
    """Windows 下强制以管理员权限运行。

    本程序需向前台窗口注入 Ctrl+A/X/V 完成剪切，若目标程序（如 QQ/微信）以管理员
    权限运行，未提权的本进程会被 UIPI 拦截导致剪切静默失败。因此要求本程序必须以
    管理员身份运行；非管理员时通过 UAC 请求提权后自动重启并退出。
    """
    if not sys.platform.startswith("win"):
        return
    import ctypes
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return
    except Exception:
        return
    # 当前非管理员，请求提权并以管理员身份重新启动
    try:
        if getattr(sys, "frozen", False):
            command = " ".join(sys.argv[1:])          # exe 模式下 argv[0] 即自身
        else:
            command = " ".join(sys.argv)              # 脚本模式需带上脚本路径
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, command, None, 1)
    finally:
        sys.exit(1)


def main():
    """主函数"""
    _ensure_admin()
    app = QApplication(sys.argv)
    
    # 设置应用程序图标
    icon_path = get_resource_path("assets/icon.ico")
    icon = QIcon(icon_path)
    app.setWindowIcon(icon)
    
    # 创建主窗口
    main_window = ManosabaMainWindow()
    main_window.setWindowIcon(icon)

    # 显示窗口
    main_window.show()
    
    # 运行应用
    app.exec()
    main_window.hotkey_manager.stop()
    sys.exit()

if __name__ == "__main__":
    main()