# -*- coding: utf-8 -*-
"""
胧嫣桌宠（PyQt6）
============================
基于小蕾米桌宠精简而来，只保留核心桌宠交互功能。

运行方式：
    pip install PyQt6
    python pet.py

交互：
    左键拖拽  = 移动位置（松手吸附回屏幕内）
    左键单击  = 随机表情互动 + 气泡台词（2 秒后恢复）
    左键双击  = 切换自选动作
    鼠标滚轮  = 缩放（0.5x ~ 2x）
    托盘图标  = 单击隐藏/显示，右键打开功能菜单

功能开关（托盘菜单）：
    打字检测  = 检测到键盘输入时切换到记笔记动画
    全屏隐藏  = 前台应用全屏时自动隐藏桌宠
    随机待机  = 空闲时每 20 秒随机切换待机表情
    按时问候  = 早/午/晚固定时段各问候一次

模块结构：
    constants.py  常量配置（GIF 分类 + 气泡台词）
    pet.py        桌宠主窗口（本文件）
"""
import sys
import os
import random
import json
import time
import traceback
if sys.platform.startswith('win'):
    import winreg
    import ctypes
    import ctypes.wintypes

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.wintypes.DWORD),
                    ('rcMonitor', ctypes.wintypes.RECT),
                    ('rcWork', ctypes.wintypes.RECT),
                    ('dwFlags', ctypes.wintypes.DWORD)]

    _user32 = ctypes.windll.user32
    _user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
    _user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND,
                                      ctypes.POINTER(ctypes.wintypes.RECT)]
    _user32.MonitorFromWindow.argtypes = [ctypes.wintypes.HWND,
                                          ctypes.wintypes.DWORD]
    _user32.MonitorFromWindow.restype = ctypes.wintypes.HMONITOR
    _user32.GetMonitorInfoW.argtypes = [ctypes.wintypes.HMONITOR,
                                        ctypes.POINTER(_MONITORINFO)]
    _user32.SetWindowPos.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HWND,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.wintypes.UINT]
from PyQt6.QtWidgets import QApplication, QLabel, QMenu, QMessageBox, QSystemTrayIcon
from PyQt6.QtCore import Qt, QTimer, QPoint, QLockFile, QDir, QSize
from PyQt6.QtGui import QPixmap, QMovie, QPainter, QIcon, QAction, QActionGroup, QColor

from constants import GIF_CATEGORIES, GIF_NAMES, BUBBLE_LINES


class BubbleLabel(QLabel):
    """气泡标签：圆角半透明黑底 + 白字（QPainter 自绘，不依赖 QSS）"""

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        font = self.font()
        font.setPixelSize(14)
        self.setFont(font)
        self._padding_x = 12
        self._padding_y = 8
        self._radius = 10

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 圆角半透明黑底
        painter.setBrush(QColor(0, 0, 0, 100))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), self._radius, self._radius)
        # 白字居中
        text_rect = self.rect().adjusted(self._padding_x, self._padding_y,
                                         -self._padding_x, -self._padding_y)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.text())
        painter.end()

    def sizeHint(self):
        fm = self.fontMetrics()
        size = fm.size(0, self.text())
        return QSize(size.width() + self._padding_x * 2,
                     size.height() + self._padding_y * 2)


class DesktopPet(QLabel):
    """桌宠主窗口：一个置顶透明、播放 GIF 的 QLabel"""

    def __init__(self):
        super().__init__()
        if getattr(sys, 'frozen', False):
            self._resource_path = sys._MEIPASS
            self._data_dir = os.path.dirname(sys.executable)
        else:
            self._resource_path = os.path.dirname(os.path.abspath(__file__))
            self._data_dir = self._resource_path

        icon_path = os.path.join(self._resource_path, 'favicon.ico')
        if os.path.exists(icon_path):
            QApplication.instance().setWindowIcon(QIcon(icon_path))

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # 构建文件名 -> 完整路径的映射
        self.gif_paths = {name: os.path.join(self._resource_path, 'gif', name)
                          for name in GIF_NAMES}
        self.config_file = os.path.join(self._data_dir, '坐标配置.json')
        self.offsets = self.load_offsets()
        self.settings_file = os.path.join(self._data_dir, '设置.json')
        self._opacity = 1.0
        self._scale_stepped = True
        self._snap_to_edge = True
        self._random_idle_enabled = True
        self._greeting_enabled = True
        self.load_settings()
        self.scale_factor = 0.5
        self.original_size = None
        self.drag_position = QPoint()
        self.press_position = QPoint()
        self.is_dragging = False
        self.pre_drag_gif_name = None
        self.drag_threshold = 5
        self._position_locked = False
        self.click_recovery_timer = QTimer(self)
        self.click_recovery_timer.setSingleShot(True)
        self.click_recovery_timer.timeout.connect(self.recover_from_click)
        self._double_click_on = False
        self._suppress_next_release = False
        self._autostart = self._check_autostart()
        self._drag_gifs = GIF_CATEGORIES['drag']

        # 打字检测状态
        self._typing_active = False
        self._last_key_time = 0.0
        self._keyboard_listener = None
        self._global_typing_timer = QTimer(self)
        self._global_typing_timer.timeout.connect(self._poll_keyboard)
        self._start_keyboard_listener()

        # 全屏隐藏状态
        self._hidden_by_fullscreen = False

        # 气泡 / 随机待机 / 按时问候
        self.bubble = None
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self._hide_bubble)
        self._last_greet_key = ''
        self._idle_switch_timer = QTimer(self)
        self._idle_switch_timer.timeout.connect(self._maybe_switch_idle)
        self._greet_timer = QTimer(self)
        self._greet_timer.timeout.connect(self._check_greeting)

        # 启动：随机选一个待机表情
        self.current_gif_name = random.choice(GIF_CATEGORIES['idle'])
        self.movie = QMovie(self.gif_paths[self.current_gif_name])
        self.movie.frameChanged.connect(self.update_frame)
        self.setMovie(self.movie)
        self.movie.start()
        self.adjustSize()
        if hasattr(self, '_saved_pos') and hasattr(self, '_saved_scale'):
            self.scale_factor = self._saved_scale
            self.move(self._saved_pos)
            self.update_frame(self.movie.currentFrameNumber())
        else:
            self.move_to_corner()

        # 轮询定时器：全屏隐藏 / 保持置顶
        self._fullscreen_poll_timer = QTimer(self)
        self._fullscreen_poll_timer.timeout.connect(self._check_fullscreen)
        self._fullscreen_poll_timer.start(500)

        # 系统托盘
        self._tray_hidden = False
        self._setup_tray()

        # 启动随机待机 / 按时问候定时器
        self._idle_switch_timer.start(20000)
        self._greet_timer.start(60000)

    def load_offsets(self):
        """加载坐标配置（每个 GIF 的绘制偏移）"""
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {name: {'x': 0, 'y': 0} for name in GIF_NAMES}

    def load_settings(self):
        """加载上次保存的位置和大小"""
        if os.path.exists(self.settings_file):
            with open(self.settings_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._saved_pos = QPoint(data.get('x', 0), data.get('y', 0))
            self._saved_scale = data.get('scale', 1)
            saved_dbl = data.get('double_click', '比心.gif')
            # 兼容旧版索引格式
            if isinstance(saved_dbl, int):
                saved_dbl = GIF_NAMES[saved_dbl] if saved_dbl < len(GIF_NAMES) else '比心.gif'
            self._saved_double_click = saved_dbl
            self._typing_enabled = data.get('typing', False)
            self._hide_on_fullscreen = data.get('hide_on_fullscreen', True)
            self._opacity = data.get('opacity', 1.0)
            self._scale_stepped = data.get('scale_stepped', True)
            self._snap_to_edge = data.get('snap_to_edge', True)
            self._random_idle_enabled = data.get('random_idle', True)
            self._greeting_enabled = data.get('greeting', True)

    def save_settings(self):
        """保存当前位置和大小"""
        pos = self.pos()
        data = {'x': pos.x(), 'y': pos.y(), 'scale': self.scale_factor,
                'double_click': self.double_click_name,
                'typing': self._typing_enabled,
                'hide_on_fullscreen': self._hide_on_fullscreen,
                'opacity': self._opacity,
                'scale_stepped': self._scale_stepped,
                'snap_to_edge': self._snap_to_edge,
                'random_idle': self._random_idle_enabled,
                'greeting': self._greeting_enabled}
        with open(self.settings_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _check_autostart(self):
        """检查是否已设置开机自启"""
        if not sys.platform.startswith('win'):
            return False
        try:
            with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\CurrentVersion\Run',
                    0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, '胧嫣桌宠')
                exe = value.strip().strip('"')
                return os.path.exists(exe)
        except OSError:
            return False

    def _set_autostart(self, enable):
        """设置或取消开机自启（注册表 Run 键，带 --delay 延迟启动）"""
        if not sys.platform.startswith('win'):
            return False
        try:
            with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\CurrentVersion\Run',
                    0, winreg.KEY_SET_VALUE) as key:
                if enable:
                    if getattr(sys, 'frozen', False):
                        cmd = '"%s" --delay 10' % os.path.abspath(sys.argv[0])
                    else:
                        cmd = '"%s" "%s" --delay 10' % (
                            sys.executable, os.path.abspath(__file__))
                    winreg.SetValueEx(key, '胧嫣桌宠', 0,
                                      winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, '胧嫣桌宠')
                    except OSError:
                        pass
            self._autostart = enable
            return True
        except OSError:
            return False

    def toggle_autostart(self):
        """切换开机自启状态"""
        if not self._set_autostart(not self._autostart):
            QMessageBox.warning(self, '开机自启',
                                '设置开机自启失败（仅支持 Windows）')
        else:
            box = QMessageBox(self)
            box.setWindowTitle('开机自启')
            box.setText('已%s开机自启' % ('开启' if self._autostart else '关闭'))
            box.exec()

    def reset_position_and_size(self):
        """重置位置和大小到默认"""
        self.scale_factor = 1
        self.move_to_corner()
        self.update_frame(self.movie.currentFrameNumber())
        self.save_settings()

    def toggle_position_lock(self):
        """切换锁定位置（锁定后禁止拖拽移动）"""
        self._position_locked = not self._position_locked
        self._lock_action.setChecked(self._position_locked)

    def toggle_snap_to_edge(self):
        """切换边界吸附开关"""
        self._snap_to_edge = not self._snap_to_edge
        self._snap_action.setChecked(self._snap_to_edge)
        self.save_settings()

    def toggle_scale_stepped(self):
        """切换滚轮缩放模式（步进 / 平滑）"""
        self._scale_stepped = not self._scale_stepped
        self._scale_stepped_action.setChecked(self._scale_stepped)
        self.save_settings()

    def set_opacity(self, value):
        """设置整体透明度（0~1），重绘当前帧立即生效"""
        self._opacity = value
        self.update_frame(self.movie.currentFrameNumber())
        self.save_settings()

    def toggle_random_idle(self):
        """切换随机待机开关"""
        self._random_idle_enabled = not self._random_idle_enabled
        self._random_idle_action.setChecked(self._random_idle_enabled)
        self.save_settings()

    def toggle_greeting(self):
        """切换按时问候开关"""
        self._greeting_enabled = not self._greeting_enabled
        self._greeting_action.setChecked(self._greeting_enabled)
        self.save_settings()

    def _is_idle(self):
        """是否空闲待机态：无双击动作、无打字、无单击反应、当前是待机表情"""
        return (not self._double_click_on
                and not self._typing_active
                and not self.click_recovery_timer.isActive()
                and self.current_gif_name in GIF_CATEGORIES['idle'])

    def _maybe_switch_idle(self):
        """随机待机：空闲时随机换一个待机表情（排除当前）"""
        if not self._random_idle_enabled or not self._is_idle():
            return
        idle_gifs = [g for g in GIF_CATEGORIES['idle'] if g != self.current_gif_name]
        if not idle_gifs:
            idle_gifs = GIF_CATEGORIES['idle']
        self.switch_to_gif(random.choice(idle_gifs))

    def _check_greeting(self):
        """按时问候：固定时段（早 6-12 / 午 12-18 / 晚 18-24）各问候一次"""
        if not self._greeting_enabled or not self._is_idle():
            return
        now = time.localtime()
        hour = now.tm_hour
        if 6 <= hour < 12:
            period = 'morning'
        elif 12 <= hour < 18:
            period = 'afternoon'
        elif 18 <= hour < 24:
            period = 'evening'
        else:
            return  # 0~6 点不问候
        key = '%04d-%02d-%02d-%s' % (now.tm_year, now.tm_mon, now.tm_mday, period)
        if key == self._last_greet_key:
            return
        self._last_greet_key = key
        self.switch_to_gif('哈喽.gif')
        self._show_bubble(random.choice(BUBBLE_LINES['greet_' + period]))
        self.click_recovery_timer.start(4000)

    # ---- 气泡 ----

    def _show_bubble(self, text):
        """在桌宠头顶弹出文字气泡"""
        if self.bubble is None:
            self.bubble = BubbleLabel()
            self.bubble.setWindowFlags(Qt.WindowType.FramelessWindowHint
                                       | Qt.WindowType.Tool
                                       | Qt.WindowType.WindowStaysOnTopHint)
        self.bubble.setText(text)
        self.bubble.adjustSize()
        self.bubble.show()
        self.bubble.raise_()
        self._position_bubble()
        self._bubble_timer.start(3000)

    def _hide_bubble(self):
        """隐藏气泡"""
        if self.bubble is not None:
            self.bubble.hide()

    def _position_bubble(self):
        """气泡定位到可见内容（GIF）头顶居中，并裁剪到屏幕内"""
        if self.bubble is None or not self.bubble.isVisible():
            return
        geo = self.frameGeometry()
        left, top, right, bottom = self._visible_margins()
        bw = self.bubble.width()
        bh = self.bubble.height()
        # 可见内容（缩放后的 GIF）在屏幕上的实际位置
        content_left = geo.left() + left
        content_top = geo.top() + top
        content_width = geo.width() - left - right
        x = content_left + content_width // 2 - bw // 2
        y = content_top - bh - 6
        screen = QApplication.primaryScreen().availableGeometry()
        x = max(screen.left(), min(x, screen.right() - bw))
        y = max(screen.top(), min(y, screen.bottom() - bh))
        self.bubble.move(x, y)

    @property
    def double_click_name(self):
        """当前双击动作的 GIF 文件名"""
        return getattr(self, '_double_click_name', '比心.gif')

    @double_click_name.setter
    def double_click_name(self, name):
        self._double_click_name = name

    @property
    def _typing_enabled(self):
        return getattr(self, '_typing_enabled_value', False)

    @_typing_enabled.setter
    def _typing_enabled(self, value):
        self._typing_enabled_value = value

    @property
    def _hide_on_fullscreen(self):
        return getattr(self, '_hide_on_fullscreen_value', True)

    @_hide_on_fullscreen.setter
    def _hide_on_fullscreen(self, value):
        self._hide_on_fullscreen_value = value

    def set_double_click_action(self, name):
        """设置双击动作（按文件名）"""
        self.double_click_name = name
        self.save_settings()

    def toggle_typing(self):
        """切换打字检测开关"""
        self._typing_enabled = not self._typing_enabled
        self._typing_action.setChecked(self._typing_enabled)
        if self._typing_enabled:
            self._global_typing_timer.start(300)
        else:
            self._global_typing_timer.stop()
            if self._typing_active:
                self._typing_active = False
                self.switch_to_gif(random.choice(GIF_CATEGORIES['idle']))
        self.save_settings()

    def toggle_hide_on_fullscreen(self):
        """切换全屏隐藏开关"""
        self._hide_on_fullscreen = not self._hide_on_fullscreen
        self._fullscreen_action.setChecked(self._hide_on_fullscreen)
        if not self._hide_on_fullscreen and self._hidden_by_fullscreen:
            # 关闭开关时若正处于隐藏状态，立即恢复显示
            self._hidden_by_fullscreen = False
            self.show()
        self.save_settings()

    def random_from_category(self, category):
        """从分类中随机选一个 GIF 文件名"""
        gifs = GIF_CATEGORIES.get(category, GIF_CATEGORIES['idle'])
        return random.choice(gifs)

    def move_to_corner(self):
        """移动到屏幕右下角"""
        screen = QApplication.primaryScreen().geometry()
        x = screen.width() - self.width() - 100
        y = screen.height() - self.height() - 100
        self.move(x, y)

    def _visible_margins(self):
        """返回当前缩放下可见内容到窗口四边的距离（左, 上, 右, 下）"""
        frame = self.movie.currentPixmap()
        if frame.isNull():
            pad = self.PAD
            return pad, pad, pad, pad
        w = frame.width()
        h = frame.height()
        # 缩放后内容在窗口内居中，四周空隙随缩放变化；放大时内容超出被裁，空隙取 0
        hmargin = max(0, self.PAD + (w - int(w * self.scale_factor)) // 2)
        vmargin = max(0, self.PAD + (h - int(h * self.scale_factor)) // 2)
        offset = self.offsets.get(self.current_gif_name, {'x': 0, 'y': 0})
        return (hmargin + offset['x'], vmargin + offset['y'],
                hmargin - offset['x'], vmargin - offset['y'])

    def _clamp_to_screen(self):
        """拖拽结束后把桌宠吸附回屏幕可视区域"""
        if not self._snap_to_edge:
            return
        screen = QApplication.primaryScreen().availableGeometry()
        geo = self.frameGeometry()
        left, top, right, bottom = self._visible_margins()
        x = geo.x()
        y = geo.y()
        # 四边统一：可见内容贴边，透明边可超出（下边进入任务栏区域，透明穿透不遮挡）
        if x < screen.left() - left:
            x = screen.left() - left
        if y < screen.top() - top:
            y = screen.top() - top
        if x + geo.width() > screen.right() + right:
            x = screen.right() + right - geo.width()
        if y + geo.height() > screen.bottom() + bottom:
            y = screen.bottom() + bottom - geo.height()
        self.move(x, y)

    def update_frame(self, frame_number):
        """更新每一帧：应用坐标偏移并缩放"""
        current_frame = self.movie.currentPixmap()
        if current_frame.isNull():
            return
        if self.original_size is None:
            self.original_size = (current_frame.width(), current_frame.height())
        offset = self.offsets.get(self.current_gif_name, {'x': 0, 'y': 0})
        pad = self.PAD
        canvas_w = current_frame.width() + pad * 2
        canvas_h = current_frame.height() + pad * 2
        canvas = QPixmap(canvas_w, canvas_h)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        if self.scale_factor != 1.0:
            scaled_w = int(current_frame.width() * self.scale_factor)
            scaled_h = int(current_frame.height() * self.scale_factor)
            draw_frame = current_frame.scaled(scaled_w, scaled_h,
                                              Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation)
            # 居中绘制缩放后的图片
            dx = pad + (current_frame.width() - scaled_w) // 2 + offset['x']
            dy = pad + (current_frame.height() - scaled_h) // 2 + offset['y']
            painter.drawPixmap(dx, dy, draw_frame)
        else:
            painter.drawPixmap(pad + offset['x'], pad + offset['y'], current_frame)
        # 应用整体透明度（DestinationIn 只改 alpha，透明背景保持透明）
        if self._opacity < 1.0:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.fillRect(canvas.rect(), QColor(0, 0, 0, int(self._opacity * 255)))
        painter.end()
        self.setPixmap(canvas)
        self.adjustSize()

    PAD = 50
    SCALE_STEPS = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]

    def wheelEvent(self, event):
        """鼠标滚轮：缩放桌宠（步进模式按固定档位，平滑模式按比例）"""
        delta = event.angleDelta().y()
        if self._scale_stepped:
            scale = self.scale_factor
            if delta > 0:
                self.scale_factor = next(
                    (s for s in self.SCALE_STEPS if s > scale + 1e-6), scale)
            else:
                self.scale_factor = next(
                    (s for s in reversed(self.SCALE_STEPS) if s < scale - 1e-6), scale)
        else:
            if delta > 0:
                self.scale_factor *= 1.1
            else:
                self.scale_factor /= 1.1
            self.scale_factor = max(0.5, min(2, self.scale_factor))
        self.update_frame(self.movie.currentFrameNumber())
        self._position_bubble()
        event.accept()

    def recover_from_click(self):
        """单击互动后恢复到随机待机表情"""
        self.switch_to_gif(random.choice(GIF_CATEGORIES['idle']))

    def _start_keyboard_listener(self):
        """启动全局键盘监听线程（pynput）"""
        try:
            from pynput import keyboard
        except ImportError:
            return

        def _on_press(key):
            self._last_key_time = time.time()

        self._keyboard_listener = keyboard.Listener(on_press=_on_press)
        self._keyboard_listener.daemon = True
        self._keyboard_listener.start()
        self._global_typing_timer.start(300)

    def _stop_keyboard_listener(self):
        """停止键盘监听"""
        self._global_typing_timer.stop()
        if self._keyboard_listener is not None:
            try:
                self._keyboard_listener.stop()
            except Exception:
                pass
            self._keyboard_listener = None

    def _poll_keyboard(self):
        """轮询全局按键：最近 1 秒有按键则播放打字动画，停止 2 秒后恢复"""
        if not self._typing_enabled:
            return
        elapsed = time.time() - self._last_key_time
        if elapsed < 1.0:
            if not self._typing_active:
                self._typing_active = True
                self.click_recovery_timer.stop()
                self.switch_to_gif(random.choice(GIF_CATEGORIES['typing']))
        elif self._typing_active and elapsed >= 2.0:
            self._recover_from_typing()

    def _recover_from_typing(self):
        """打字停止后恢复到随机待机表情"""
        if self._typing_active:
            self._typing_active = False
            idle_gifs = [g for g in GIF_CATEGORIES['idle']
                         if g != self.current_gif_name]
            if not idle_gifs:
                idle_gifs = GIF_CATEGORIES['idle']
            self.switch_to_gif(random.choice(idle_gifs))

    def _assert_topmost(self):
        """重新置顶，防止被全屏/其它置顶窗口盖住"""
        if not sys.platform.startswith('win') or not self.isVisible():
            return
        try:
            # HWND_TOPMOST(-1)
            # SWP_NOSIZE(0x1) | SWP_NOMOVE(0x2) | SWP_NOACTIVATE(0x10) | SWP_SHOWWINDOW(0x40)
            _user32.SetWindowPos(int(self.winId()), -1, 0, 0, 0, 0,
                                 0x0001 | 0x0002 | 0x0010 | 0x0040)
        except Exception:
            pass

    def _check_fullscreen(self):
        """检测前台应用是否全屏：开关开则隐藏，否则保持置顶"""
        if not sys.platform.startswith('win'):
            return
        # 菜单/对话框打开时暂停，避免持续置顶把自家弹窗盖住
        if QApplication.activeModalWidget() or QApplication.activePopupWidget():
            return
        try:
            hwnd = _user32.GetForegroundWindow()

            # 无前台窗口或前台是自身：保证可见并置顶即可
            if not hwnd or int(hwnd) == int(self.winId()):
                if self._hidden_by_fullscreen:
                    self._hidden_by_fullscreen = False
                    if not self._tray_hidden:
                        self.show()
                elif not self._tray_hidden:
                    self._assert_topmost()
                return

            rect = ctypes.wintypes.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(rect))

            # 取前台窗口所在显示器，用物理像素比较
            # （GetWindowRect 是物理像素，而 QScreen.geometry 是逻辑像素，
            #   在高 DPI 缩放下两者不一致，会导致全屏误判）
            monitor = _user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
            if not monitor:
                return
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            _user32.GetMonitorInfoW(monitor, ctypes.byref(info))
            mon = info.rcMonitor

            is_fullscreen = (
                abs(rect.left - mon.left) <= 2 and
                abs(rect.top - mon.top) <= 2 and
                abs(rect.right - mon.right) <= 2 and
                abs(rect.bottom - mon.bottom) <= 2
            )

            if is_fullscreen and self._hide_on_fullscreen:
                if not self._hidden_by_fullscreen:
                    self._hidden_by_fullscreen = True
                    self.hide()
                    self._hide_bubble()
            else:
                if self._hidden_by_fullscreen:
                    self._hidden_by_fullscreen = False
                    if not self._tray_hidden:
                        self.show()
                if not self._tray_hidden:
                    # 无论是否全屏都保持置顶，避免被全屏/其它置顶窗口盖住
                    self._assert_topmost()
        except Exception:
            pass

    def _reset_typing_state(self):
        """重置打字状态（在鼠标交互时调用）"""
        if self._typing_active:
            self._typing_active = False

    def mousePressEvent(self, event):
        """鼠标按下事件"""
        self._reset_typing_state()
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_position = event.globalPosition().toPoint()
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.is_dragging = False
            self.pre_drag_gif_name = self.current_gif_name
            event.accept()

    def mouseMoveEvent(self, event):
        """鼠标移动事件（拖拽）"""
        if event.buttons() & Qt.MouseButton.LeftButton:
            if self._position_locked:
                return
            current_pos = event.globalPosition().toPoint()
            distance = (current_pos - self.press_position).manhattanLength()
            if distance > self.drag_threshold:
                if not self.is_dragging:
                    self.is_dragging = True
                    self.switch_to_gif(random.choice(self._drag_gifs))
                self.move(current_pos - self.drag_position)
                self._position_bubble()
                event.accept()

    def mouseReleaseEvent(self, event):
        """鼠标释放事件"""
        if event.button() == Qt.MouseButton.LeftButton:
            current_pos = event.globalPosition().toPoint()
            distance = (current_pos - self.press_position).manhattanLength()
            if getattr(self, '_suppress_next_release', False):
                self._suppress_next_release = False
                event.accept()
                return
            if self.is_dragging:
                self.is_dragging = False
                self.switch_to_gif(self.pre_drag_gif_name)
                self._clamp_to_screen()
            elif distance <= self.drag_threshold:
                reaction_gif = self.random_from_category('reactions')
                self.switch_to_gif(reaction_gif)
                self._show_bubble(random.choice(BUBBLE_LINES['click']))
            self.click_recovery_timer.start(2000)
            event.accept()

    def mouseDoubleClickEvent(self, event):
        """鼠标双击：在默认待机与选定的双击动作之间切换"""
        self._reset_typing_state()
        if event.button() == Qt.MouseButton.LeftButton:
            self.click_recovery_timer.stop()
            self._suppress_next_release = True
            if self._double_click_on:
                self._double_click_on = False
                self.switch_to_gif(random.choice(GIF_CATEGORIES['idle']))
            else:
                self._double_click_on = True
                self.switch_to_gif(self.double_click_name)
            event.accept()

    def _build_menu(self):
        """构建完整功能菜单（供托盘右键使用）"""
        menu = QMenu()

        self._tray_toggle_action = QAction('隐藏桌宠', self)
        self._tray_toggle_action.triggered.connect(self.toggle_visible_from_tray)
        menu.addAction(self._tray_toggle_action)
        menu.addSeparator()

        reset_action = QAction('重置位置和大小', self)
        reset_action.triggered.connect(self.reset_position_and_size)
        menu.addAction(reset_action)

        self._lock_action = QAction('锁定位置', self)
        self._lock_action.setCheckable(True)
        self._lock_action.setChecked(self._position_locked)
        self._lock_action.triggered.connect(self.toggle_position_lock)
        menu.addAction(self._lock_action)

        self._snap_action = QAction('边界吸附', self)
        self._snap_action.setCheckable(True)
        self._snap_action.setChecked(self._snap_to_edge)
        self._snap_action.triggered.connect(self.toggle_snap_to_edge)
        menu.addAction(self._snap_action)

        self._scale_stepped_action = QAction('缩放步进', self)
        self._scale_stepped_action.setCheckable(True)
        self._scale_stepped_action.setChecked(self._scale_stepped)
        self._scale_stepped_action.triggered.connect(self.toggle_scale_stepped)
        menu.addAction(self._scale_stepped_action)

        # 透明度子菜单
        opacity_menu = QMenu('透明度', menu)
        opacity_group = QActionGroup(self)
        opacity_group.setExclusive(True)
        for value, label in [(1.0, '100%'), (0.8, '80%'), (0.6, '60%'), (0.4, '40%')]:
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(abs(self._opacity - value) < 0.01)
            act.triggered.connect(lambda checked, v=value: self.set_opacity(v))
            opacity_group.addAction(act)
            opacity_menu.addAction(act)
        menu.addMenu(opacity_menu)
        menu.addSeparator()

        autostart_action = QAction('开机自启', self)
        autostart_action.setCheckable(True)
        autostart_action.setChecked(self._autostart)
        autostart_action.triggered.connect(self.toggle_autostart)
        if not sys.platform.startswith('win'):
            autostart_action.setEnabled(False)
        menu.addAction(autostart_action)
        menu.addSeparator()

        self._random_idle_action = QAction('随机待机', self)
        self._random_idle_action.setCheckable(True)
        self._random_idle_action.setChecked(self._random_idle_enabled)
        self._random_idle_action.triggered.connect(self.toggle_random_idle)
        menu.addAction(self._random_idle_action)

        self._greeting_action = QAction('按时问候', self)
        self._greeting_action.setCheckable(True)
        self._greeting_action.setChecked(self._greeting_enabled)
        self._greeting_action.triggered.connect(self.toggle_greeting)
        menu.addAction(self._greeting_action)

        self._typing_action = QAction('打字检测', self)
        self._typing_action.setCheckable(True)
        self._typing_action.setChecked(self._typing_enabled)
        self._typing_action.triggered.connect(self.toggle_typing)
        menu.addAction(self._typing_action)

        self._fullscreen_action = QAction('全屏隐藏', self)
        self._fullscreen_action.setCheckable(True)
        self._fullscreen_action.setChecked(self._hide_on_fullscreen)
        self._fullscreen_action.triggered.connect(self.toggle_hide_on_fullscreen)
        menu.addAction(self._fullscreen_action)
        menu.addSeparator()

        # 双击动作子菜单：按分类组织
        dbl_menu = QMenu('双击动作', menu)
        dbl_group = QActionGroup(self)
        dbl_group.setExclusive(True)
        for category, gifs in GIF_CATEGORIES.items():
            cat_names = {'idle': '待机', 'reactions': '反应', 'drag': '拖拽',
                         'actions': '动作', 'typing': '打字'}
            sub_menu = dbl_menu.addMenu(cat_names.get(category, category))
            for name in gifs:
                act = QAction(name.replace('.gif', ''), self)
                act.setCheckable(True)
                act.setChecked(name == self.double_click_name)
                act.triggered.connect(lambda checked, n=name: self.set_double_click_action(n))
                dbl_group.addAction(act)
                sub_menu.addAction(act)
        menu.addMenu(dbl_menu)
        menu.addSeparator()

        quit_action = QAction('退出', self)
        quit_action.triggered.connect(self.do_quit)
        menu.addAction(quit_action)

        return menu

    def _setup_tray(self):
        """创建系统托盘图标和菜单"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            self._tray_toggle_action = None
            self._typing_action = None
            self._fullscreen_action = None
            self._lock_action = None
            self._snap_action = None
            self._scale_stepped_action = None
            self._random_idle_action = None
            self._greeting_action = None
            return
        icon_path = os.path.join(self._resource_path, 'favicon.ico')
        self.tray = QSystemTrayIcon(QIcon(icon_path), self)
        self.tray.setToolTip('胧嫣桌宠')
        self.tray.setContextMenu(self._build_menu())
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        """单击托盘图标切换显示/隐藏"""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visible_from_tray()

    def toggle_visible_from_tray(self):
        """托盘切换桌宠显示/隐藏"""
        if self._tray_hidden:
            self._tray_hidden = False
            self._hidden_by_fullscreen = False
            self.show()
            self._assert_topmost()
            self._tray_toggle_action.setText('隐藏桌宠')
        else:
            self._tray_hidden = True
            self.hide()
            self._hide_bubble()
            self._tray_toggle_action.setText('显示桌宠')

    def do_quit(self):
        """退出前保存设置并清理监听"""
        self._stop_keyboard_listener()
        self.save_settings()
        self._hide_bubble()
        if self.tray is not None:
            self.tray.hide()
        QApplication.quit()

    def switch_to_gif(self, name):
        """切换到指定名称的 GIF"""
        if name != self.current_gif_name and name in self.gif_paths:
            self.current_gif_name = name
            self.movie.stop()
            self.movie = QMovie(self.gif_paths[name])
            self.movie.frameChanged.connect(self.update_frame)
            self.setMovie(self.movie)
            self.movie.start()
            self.adjustSize()


if __name__ == '__main__':
    def _excepthook(tp, val, tb):
        if tp is KeyboardInterrupt:
            return
        try:
            log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '错误日志.txt')
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write('\n[%s]\n' % time.strftime('%Y-%m-%d %H:%M:%S'))
                traceback.print_exception(tp, val, tb, file=f)
        except Exception:
            pass
        traceback.print_exception(tp, val, tb)

    sys.excepthook = _excepthook
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 防止重复启动：锁文件在系统临时目录，已有实例在运行则提示并退出
    lock = QLockFile(os.path.join(QDir.tempPath(), 'longyan_pet.lock'))
    if not lock.tryLock(100):
        QMessageBox.information(None, '胧嫣桌宠', '桌宠已经在运行中啦～')
        sys.exit(0)

    pet = DesktopPet()

    # 延迟启动：开机自启注册带 --delay N，延迟 N 秒再显示
    delay = 0
    if '--delay' in sys.argv:
        try:
            delay = int(sys.argv[sys.argv.index('--delay') + 1])
        except (ValueError, IndexError):
            delay = 0
    if delay > 0:
        QTimer.singleShot(delay * 1000, pet.show)
    else:
        pet.show()
    sys.exit(app.exec())
