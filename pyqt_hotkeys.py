# pyqt_hotkeys.py
# 全局热键实现：基于 Windows RegisterHotKey + QAbstractNativeEventFilter 捕获 WM_HOTKEY。
#
# 为什么不用 pynput 的 GlobalHotKeys：
#   pynput 在 Windows 上使用的是 WH_KEYBOARD_LL 低级键盘钩子，该钩子受 UIPI
#   （用户界面特权隔离）过滤——未提权进程收不到“发给更高完整性窗口”的按键事件，
#   导致焦点在管理员权限的 QQ/微信等程序上时快捷键静默失效。
#   RegisterHotKey 是系统级注册，不受 UIPI 过滤，普通权限也能收到全局热键。
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal, QAbstractNativeEventFilter, QCoreApplication

from config import CONFIGS

# --- Windows 常量 ---
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

# use_last_error=True 使 ctypes.get_last_error() 能取到真实的 Windows 错误码
# （否则 RegisterHotKey 失败时取到的永远是 0，无法区分"热键冲突"和其它失败）
_user32 = ctypes.WinDLL('user32', use_last_error=True)
_user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
_user32.UnregisterHotKey.restype = wintypes.BOOL


# 特殊键 → VK 码（对应 pyqt_setting.py 中 HotkeyConfigThread 生成的按键格式）
_SPECIAL_KEYS = {
    '<enter>': 0x0D, '<return>': 0x0D,
    '<esc>': 0x1B, '<escape>': 0x1B,
    '<tab>': 0x09,
    '<space>': 0x20,
    '<backspace>': 0x08,
    '<delete>': 0x2E, '<del>': 0x2E,
    '<insert>': 0x2D,
    '<pageup>': 0x21, '<pagedown>': 0x22,
    '<home>': 0x24, '<end>': 0x23,
    '<left>': 0x25, '<right>': 0x27,
    '<up>': 0x26, '<down>': 0x28,
    '<capslock>': 0x14,
    '<numlock>': 0x90,
    '<scrolllock>': 0x91,
    '<printscreen>': 0x2C,
    '<menu>': 0x5D,
}
for _i in range(1, 25):
    _SPECIAL_KEYS[f'<f{_i}>'] = 0x6F + _i

_MODIFIERS = {
    '<ctrl>': MOD_CONTROL,
    '<alt>': MOD_ALT,
    '<shift>': MOD_SHIFT,
    '<win>': MOD_WIN,
    '<cmd>': MOD_WIN,
}


def parse_hotkey(hotkey_str):
    """解析 '<ctrl>+<shift>+e' 形式的快捷键字符串。

    返回 (modifiers, vk)；无法解析时抛出 ValueError。
    """
    parts = hotkey_str.split('+')
    if not parts:
        raise ValueError(f"空快捷键: {hotkey_str!r}")

    modifiers = 0
    vk = None
    for part in parts:
        part = part.strip()
        if not part:
            continue
        lower = part.lower()
        if lower in _MODIFIERS:
            modifiers |= _MODIFIERS[lower]
        elif lower in _SPECIAL_KEYS:
            if vk is not None:
                raise ValueError(f"存在多个主键: {hotkey_str}")
            vk = _SPECIAL_KEYS[lower]
        elif len(part) == 1:
            if vk is not None:
                raise ValueError(f"存在多个主键: {hotkey_str}")
            c = part
            if ('a' <= c <= 'z') or ('A' <= c <= 'Z'):
                vk = ord(c.upper())
            elif '0' <= c <= '9':
                vk = ord(c)
            else:
                vk = _user32.VkKeyScanW(ord(c)) & 0xFF
                if vk == 0xFF:
                    raise ValueError(f"无法识别的按键: {part!r}")
        else:
            raise ValueError(f"无法识别的按键: {part!r}")

    if vk is None:
        raise ValueError(f"缺少主键: {hotkey_str}")

    return modifiers, vk


class _HotkeyNativeFilter(QAbstractNativeEventFilter):
    """捕获 WM_HOTKEY 消息，转发给 HotkeyListener。"""

    def __init__(self, listener):
        super().__init__()
        self._listener = listener

    def nativeEventFilter(self, eventType, message):
        # PySide6 中 eventType 可能是 str 或 bytes
        if eventType in ("windows_generic_MSG", b"windows_generic_MSG"):
            try:
                msg = wintypes.MSG.from_address(int(message))
            except Exception:
                return (False, 0)
            if msg.message == WM_HOTKEY:
                hotkey_id = int(msg.wParam)
                action = self._listener._action_by_id.get(hotkey_id)
                if action is not None:
                    self._listener.hotkey_triggered.emit(action)
                    return (True, 0)
        return (False, 0)


class HotkeyListener(QObject):
    """全局热键监听（RegisterHotKey 实现）。"""

    hotkey_triggered = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._action_by_id = {}
        self._registered = []   # [(hotkey_id, hwnd)]
        self._filter = _HotkeyNativeFilter(self)
        app = QCoreApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._filter)

    def set_hotkeys(self, hotkey_configs, quick_chars, hwnd=None):
        """解析并注册全局热键。

        hotkey_configs: {action: '快捷键字符串'}
        quick_chars:    保留参数，与旧接口一致（暂未使用）
        hwnd:           接收 WM_HOTKEY 的窗口句柄（主窗口 winId）
        """
        self._unregister_all()
        self._action_by_id.clear()

        if not hwnd:
            return

        for hotkey_id, (action, hotkey_str) in enumerate(hotkey_configs.items(), start=1):
            try:
                modifiers, vk = parse_hotkey(hotkey_str)
            except ValueError as e:
                print(f"热键解析失败 [{action} = {hotkey_str}]: {e}")
                continue

            if not _user32.RegisterHotKey(hwnd, hotkey_id, modifiers | MOD_NOREPEAT, vk):
                err = ctypes.get_last_error()
                if err == ERROR_HOTKEY_ALREADY_REGISTERED:
                    print(f"热键冲突: {hotkey_str} 已被其他程序占用，已跳过")
                else:
                    print(f"注册热键失败 [{hotkey_str}]: {ctypes.WinError(err)}")
                continue

            self._action_by_id[hotkey_id] = action
            self._registered.append((hotkey_id, hwnd))
            print(f"已注册热键: {hotkey_str}")

    def stop_listening(self):
        """注销所有热键（过滤器保持安装，便于后续重新注册）。"""
        self._unregister_all()

    def _unregister_all(self):
        for hotkey_id, hwnd in self._registered:
            _user32.UnregisterHotKey(hwnd, hotkey_id)
        self._registered.clear()


class HotkeyManager(QObject):
    """热键管理器"""

    def __init__(self, gui, core):
        super().__init__()
        self.gui = gui
        self.core = core
        self.listener = HotkeyListener()
        self.listener.hotkey_triggered.connect(self._handle_hotkey)
        self._active = True

        # 初始化热键
        self.setup_hotkeys()

    def setup_hotkeys(self):
        """设置热键"""
        # 重新加载配置
        hotkey_configs = CONFIGS.keymap
        quick_chars = CONFIGS.gui_settings.get("quick_characters", {})

        # 主窗口句柄，用于接收 WM_HOTKEY
        hwnd = None
        if self.gui is not None:
            try:
                hwnd = int(self.gui.winId())
            except Exception:
                hwnd = None

        # 注册热键
        self.listener.set_hotkeys(hotkey_configs, quick_chars, hwnd)

        print(f"热键监听器已启动，平台: {CONFIGS.platform}")

    def _handle_hotkey(self, action):
        """处理热键触发"""
        if not self._active and action != "toggle_listener":
            return

        # 执行相应的动作
        if action == "start_generate":
            self.gui.generate_image()
        elif action == "next_character":
            self._switch_character(1)
        elif action == "prev_character":
            self._switch_character(-1)
        elif action == "next_emotion":
            self._switch_emotion(1)
        elif action == "prev_emotion":
            self._switch_emotion(-1)
        elif action == "next_background":
            self._switch_background(1)
        elif action == "prev_background":
            self._switch_background(-1)
        elif action.startswith("character_") and action in CONFIGS.gui_settings.get("quick_characters", {}):
            char_id = CONFIGS.gui_settings["quick_characters"][action]
            self._switch_to_character_by_id(char_id)
        elif action == "toggle_listener":
            self._toggle_hotkey_listener()

        print(f"触发热键: {action}")

    def _toggle_hotkey_listener(self):
        """切换热键监听状态"""
        self._active = not self._active
        status = "启用" if self._active else "禁用"
        self.gui.update_status(f"热键监听已{status}")
        print(f"热键监听状态已切换为: {status}")

    def _find_first_switchable_character_tab(self):
        """找到第一个可切换的角色标签页（非固定角色）"""
        for tab in self.gui.character_tabs:
            if not tab.is_fixed_character():
                return tab

        # 如果没有找到非固定角色标签页，返回第一个角色标签页
        if self.gui.character_tabs:
            return self.gui.character_tabs[0]
        return None

    def _switch_character(self, direction):
        """切换角色 - 修改对应标签页的下拉框"""
        tab = self._find_first_switchable_character_tab()
        if not tab:
            return

        # 计算新角色索引
        current_index =tab.ui.combo_character_select.currentIndex()
        max_index = tab.ui.combo_character_select.count()
        new_index = (current_index + direction) % max_index

        tab.ui.combo_character_select.setCurrentIndex(new_index)

        # 状态更新由UI变化自动触发
        new_char_id = CONFIGS.character_list[new_index]
        self.gui.update_status(f"已切换到角色: {CONFIGS.get_character(new_char_id, full_name=True)}")

    def _switch_to_character_by_id(self, char_id):
        """通过角色ID切换到指定角色"""
        if not char_id or char_id not in CONFIGS.character_list:
            return

        tab = self._find_first_switchable_character_tab()
        if tab:
            display_text = f"{CONFIGS.get_character(char_id, full_name=True)} ({char_id})"
            index = tab.ui.combo_character_select.findText(display_text)
            tab.ui.combo_character_select.setCurrentIndex(index)
            self.gui.update_status(f"已切换到角色: {CONFIGS.get_character(char_id, full_name=True)}")

    def _switch_emotion(self, direction):
        """切换表情 - 修改对应标签页的下拉框"""
        tab = self._find_first_switchable_character_tab()
        if not tab:
            return

        # 计算新表情索引
        current_emotion_index = tab.ui.combo_emotion_select.currentIndex()
        max_index = tab.ui.combo_emotion_select.count()
        new_index = (current_emotion_index + direction) % max_index

        tab.ui.checkbox_random_emotion.setChecked(False)

        tab.ui.combo_emotion_select.setCurrentIndex(new_index)
        self.gui.update_status(f"表情已切换到: 表情 {new_index+1}")

    def _switch_background(self, direction):
        """切换背景 - 直接修改下拉框索引"""
        if not self.gui.background_tab:
            return

        # 确保使用固定背景（取消随机）
        if self.gui.background_tab.checkBox_randomBg.isChecked():
            self.gui.background_tab.checkBox_randomBg.setChecked(False)

        # 检查下拉框是否启用且有内容（排除"无"选项）
        if not self.gui.background_tab.comboBox_bgSelect.isEnabled() or self.gui.background_tab.comboBox_bgSelect.count() <= 1:
            return

        # 计算新索引，跳过第一个"无"选项
        current_index = self.gui.background_tab.comboBox_bgSelect.currentIndex()
        max_index = self.gui.background_tab.comboBox_bgSelect.count()
        new_index = (current_index + direction) % max_index
        new_index = new_index if new_index != 0 else 1

        self.gui.background_tab.comboBox_bgSelect.setCurrentIndex(new_index)

        # 获取背景文件名
        bg_text = self.gui.background_tab.comboBox_bgSelect.currentText()
        self.gui.update_status(f"背景已切换到: {bg_text}")

    def stop(self):
        """停止热键监听"""
        self.listener.stop_listening()
        print("热键监听器已停止")
