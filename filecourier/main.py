import sys
import os
import configparser
import shutil
import ftplib
import time
import threading

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLineEdit, QGroupBox, QRadioButton, 
                             QCheckBox, QTextEdit, QFileDialog, QListWidget, 
                             QListWidgetItem, QLabel, QMessageBox, QSpinBox)
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtCore import Qt, QObject, QRunnable, QThreadPool, pyqtSignal

class TaskSignals(QObject):
    """
    定义所有后台任务可能发出的信号。
    由于 QRunnable 不能继承自 QObject，我们创建一个单独的 QObject 来处理信号。
    """
    log = pyqtSignal(str)
    status_update = pyqtSignal(str, str, str)  # ip, status_text, color
    task_finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

class AtomicCounter:
    """一个简单的线程安全计数器"""
    def __init__(self, initial=0):
        self.value = initial
        self._lock = threading.Lock()

    def increment(self):
        with self._lock:
            self.value += 1

    def decrement(self):
        with self._lock:
            self.value -= 1
        return self.value

    def get(self):
        with self._lock:
            return self.value

class TransferTask(QRunnable):
    """
    单个 IP 的下发任务，可在线程池中运行。
    """
    def __init__(self, ip, settings, config_dict, cancellation_flag, signals):
        super().__init__()
        self.ip = ip
        self.settings = settings
        self.config = config_dict
        self.cancellation_flag = cancellation_flag
        self.signals = signals
        self.timeout = int(self.config.get('common', {}).get('timeout', 30))
        self.chunk_size = 8192  # 8KB

    def run(self):
        """线程执行的入口点，处理单个 IP 的下发任务"""
        try:
            if self.cancellation_flag.is_set():
                self.signals.status_update.emit(self.ip, "已取消", "gray")
                return

            self.signals.status_update.emit(self.ip, "处理中...", "blue")
            self.signals.log.emit(f"--- 开始处理 {self.ip} ---")

            source_file_path = self.settings['file_path']
            file_name = os.path.basename(source_file_path)
            target_subdir = self.config.get('common', {}).get('target_subdir', 'FileCourier')

            primary_success = False
            final_message = ""

            if self.cancellation_flag.is_set():
                self.signals.status_update.emit(self.ip, "已取消", "gray")
                return

            if self.settings['use_smb']:
                self.signals.log.emit(f"[{self.ip}] 尝试使用 SMB 方式...")
                primary_success, final_message = self._transfer_smb(source_file_path, file_name, target_subdir)
            elif self.settings['use_ftp']:
                self.signals.log.emit(f"[{self.ip}] 尝试使用 FTP 方式...")
                primary_success, final_message = self._transfer_ftp(source_file_path, file_name, target_subdir)

            if self.cancellation_flag.is_set():
                self.signals.status_update.emit(self.ip, "已取消", "gray")
                return

            if primary_success:
                method = "SMB" if self.settings['use_smb'] else "FTP"
                self.signals.log.emit(f"[{self.ip}] {method} 传输成功: {final_message}")
                self.signals.status_update.emit(self.ip, f"成功 ({method})", "green")
            elif self.settings['fallback_to_ftp']:
                self.signals.log.emit(f"[{self.ip}] SMB 失败，尝试回退到 FTP...")
                fallback_success, final_message = self._transfer_ftp(source_file_path, file_name, target_subdir)
                if fallback_success:
                    self.signals.log.emit(f"[{self.ip}] FTP 回退传输成功: {final_message}")
                    self.signals.status_update.emit(self.ip, "成功 (FTP 回退)", "orange")
                else:
                    self.signals.log.emit(f"[{self.ip}] FTP 回退传输也失败: {final_message}")
                    self.signals.status_update.emit(self.ip, f"失败: {final_message}", "red")
            else:
                self.signals.log.emit(f"[{self.ip}] 传输失败: {final_message}")
                self.signals.status_update.emit(self.ip, f"失败: {final_message}", "red")

            self.signals.log.emit(f"--- {self.ip} 处理完毕 ---\n")

        except Exception as e:
            self.signals.log.emit(f"!!! [{self.ip}] 发生意外错误: {e}")
            self.signals.status_update.emit(self.ip, "异常失败", "red")
        finally:
            self.signals.task_finished.emit()

    def _transfer_ftp(self, source_path, file_name, subdir):
        if self.cancellation_flag.is_set(): return False, "任务已取消"
        
        ftp_config = self.config['ftp']
        username = ftp_config.get('username')
        password = ftp_config.get('password')
        port = int(ftp_config.get('port', 21))

        try:
            with ftplib.FTP() as ftp:
                self.signals.log.emit(f"[{self.ip}] FTP: 正在连接到 {self.ip}:{port} (超时: {self.timeout}s)...")
                ftp.connect(self.ip, port, timeout=self.timeout)
                ftp.login(username, password)
                self.signals.log.emit(f"[{self.ip}] FTP: 登录成功。")

                if self.cancellation_flag.is_set(): return False, "任务已取消"

                try:
                    ftp.cwd(subdir)
                except ftplib.error_perm:
                    self.signals.log.emit(f"[{self.ip}] FTP: 目录 '{subdir}' 不存在，尝试创建...")
                    ftp.mkd(subdir)
                    ftp.cwd(subdir)

                if self.cancellation_flag.is_set(): return False, "任务已取消"

                self.signals.log.emit(f"[{self.ip}] FTP: 开始上传文件 '{file_name}'...")
                with open(source_path, 'rb') as f:
                    with ftp.transfercmd(f'STOR {file_name}', None) as conn:
                        while True:
                            if self.cancellation_flag.is_set():
                                ftp.abort()
                                return False, "任务已取消"
                            chunk = f.read(self.chunk_size)
                            if not chunk:
                                break
                            conn.sendall(chunk)
                
                return True, f"文件上传至 {subdir}/{file_name}"
        except Exception as e:
            return False, str(e)

    def _transfer_smb(self, source_path, file_name, subdir):
        if self.cancellation_flag.is_set(): return False, "任务已取消"
        
        try:
            import smbclient
        except ImportError:
            return False, "smbprotocol 库未安装"

        smb_config = self.config['smb']
        username = smb_config.get('username') or None
        password = smb_config.get('password') or None
        
        drives_to_try = ['D$', 'C$']
        for drive in drives_to_try:
            if self.cancellation_flag.is_set(): return False, "任务已取消"
            
            try:
                unc_path_base = f"\\\\{self.ip}\\{drive}"
                self.signals.log.emit(f"[{self.ip}] SMB: 正在连接到 {unc_path_base} (超时: {self.timeout}s)...")
                
                # Use listdir to check connection with timeout
                smbclient.listdir(unc_path_base, username=username, password=password, timeout=self.timeout)
                self.signals.log.emit(f"[{self.ip}] SMB: 连接成功。")

                if self.cancellation_flag.is_set(): return False, "任务已取消"

                target_dir_path = f"{unc_path_base}\\{subdir}"
                target_file_path = f"{target_dir_path}\\{file_name}"

                try:
                    smbclient.mkdir(target_dir_path, username=username, password=password)
                except smbclient.SambaClientError as e:
                    if "STATUS_OBJECT_NAME_COLLISION" not in str(e):
                        raise

                if self.cancellation_flag.is_set(): return False, "任务已取消"

                self.signals.log.emit(f"[{self.ip}] SMB: 开始上传文件 '{file_name}'...")
                with open(source_path, 'rb') as f_src:
                    with smbclient.open_file(target_file_path, mode='wb', username=username, password=password, timeout=self.timeout) as f_dst:
                        while True:
                            if self.cancellation_flag.is_set():
                                return False, "任务已取消"
                            chunk = f_src.read(self.chunk_size)
                            if not chunk:
                                break
                            f_dst.write(chunk)

                return True, f"文件上传至 {drive}\\{subdir}\\{file_name}"

            except Exception as e:
                self.signals.log.emit(f"[{self.ip}] SMB: 尝试驱动器 {drive} 失败: {e}")
                continue
        
        return False, "所有驱动器 (D$, C$) 均尝试失败"


# 原来的 Worker 类将被移除，因为它的功能被 TransferTask 和 QThreadPool 取代





class FileCourierApp(QWidget):
    def __init__(self):
        super().__init__()
        
        # 1. 初始化核心组件
        self.config = configparser.ConfigParser()
        self.thread_pool = QThreadPool()
        self.cancellation_flag = None
        self.active_tasks_counter = None

        # 2. 创建 UI 界面
        self.init_ui()

        # 3. 创建并连接信号总线 (在UI创建之后)
        self.task_signals = TaskSignals(self)
        self.task_signals.log.connect(self.log)
        self.task_signals.status_update.connect(self.update_ip_status)
        self.task_signals.task_finished.connect(self.on_task_finished)

        # 4. 加载外部数据
        self.load_config()
        self.load_ips()

        self.log("程序启动，等待操作。")
        self.log(f"系统建议的最大线程数: {self.thread_pool.maxThreadCount()}")

    def init_ui(self):
        self.setWindowTitle('文件下发工具 v2.0 (并发版)')
        self.setGeometry(100, 100, 800, 600)

        # 主布局
        left_panel = QVBoxLayout()
        right_panel = QVBoxLayout()
        
        # --- 左侧面板 ---
        
        # 1. 文件选择
        self.file_group = QGroupBox("1. 选择文件")
        file_layout = QHBoxLayout()
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setPlaceholderText("请选择要下发的文件...")
        self.file_path_edit.setReadOnly(True)
        self.browse_button = QPushButton("浏览...")
        file_layout.addWidget(self.file_path_edit)
        file_layout.addWidget(self.browse_button)
        self.file_group.setLayout(file_layout)
        
        # 2. 下发方式
        self.method_group = QGroupBox("2. 下发方式")
        method_layout = QVBoxLayout()
        self.smb_radio = QRadioButton("Windows 共享 (SMB)")
        self.ftp_radio = QRadioButton("FTP")
        self.smb_radio.setChecked(True)
        self.fallback_checkbox = QCheckBox("SMB 失败后自动尝试 FTP")
        self.fallback_checkbox.setChecked(True)
        method_layout.addWidget(self.smb_radio)
        method_layout.addWidget(self.ftp_radio)
        method_layout.addWidget(self.fallback_checkbox)
        self.method_group.setLayout(method_layout)

        # 3. 并发控制
        self.concurrency_group = QGroupBox("3. 并发控制")
        concurrency_layout = QHBoxLayout()
        concurrency_layout.addWidget(QLabel("同时下发数:"))
        self.concurrency_spinbox = QSpinBox()
        self.concurrency_spinbox.setMinimum(1)
        self.concurrency_spinbox.setValue(5)
        concurrency_layout.addWidget(self.concurrency_spinbox)
        self.concurrency_group.setLayout(concurrency_layout)

        # 4. 操作按钮
        action_layout = QHBoxLayout()
        self.start_button = QPushButton("开始下发")
        self.start_button.setStyleSheet("background-color: #28a745; color: white; font-size: 14px; padding: 8px;")
        self.stop_button = QPushButton("停止下发")
        self.stop_button.setStyleSheet("background-color: #dc3545; color: white; font-size: 14px; padding: 8px;")
        self.stop_button.setEnabled(False)
        action_layout.addWidget(self.start_button)
        action_layout.addWidget(self.stop_button)

        left_panel.addWidget(self.file_group)
        left_panel.addWidget(self.method_group)
        left_panel.addWidget(self.concurrency_group)
        left_panel.addLayout(action_layout)
        left_panel.addStretch(1)

        # --- 右侧面板 ---
        self.ip_group = QGroupBox("4. 选择目标 IP")
        ip_layout = QVBoxLayout()
        self.ip_list_widget = QListWidget()
        
        # IP 列表上方的全选/全不选
        ip_actions_layout = QHBoxLayout()
        self.select_all_button = QPushButton("全选")
        self.deselect_all_button = QPushButton("全不选")
        ip_actions_layout.addWidget(self.select_all_button)
        ip_actions_layout.addWidget(self.deselect_all_button)
        ip_actions_layout.addStretch(1)
        
        ip_layout.addLayout(ip_actions_layout)
        ip_layout.addWidget(self.ip_list_widget)
        self.ip_group.setLayout(ip_layout)
        
        right_panel.addWidget(self.ip_group)

        # --- 底部日志面板 ---
        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout()
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        log_layout.addWidget(self.log_edit)
        
        # 将左右面板和日志面板组合
        top_splitter = QHBoxLayout()
        top_splitter.addLayout(left_panel, 1)
        top_splitter.addLayout(right_panel, 2)

        main_splitter = QVBoxLayout()
        main_splitter.addLayout(top_splitter, 2)
        main_splitter.addWidget(log_group, 1)

        self.setLayout(main_splitter)

        # --- 信号连接 ---
        self.browse_button.clicked.connect(self.browse_file)
        self.start_button.clicked.connect(self.execute_transfers)
        self.stop_button.clicked.connect(self.stop_transfer)
        self.select_all_button.clicked.connect(self.select_all_ips)
        self.deselect_all_button.clicked.connect(self.deselect_all_ips)
        self.smb_radio.toggled.connect(self.update_fallback_checkbox_state)
        
        self.update_fallback_checkbox_state() # 初始化复选框状态

    def closeEvent(self, event):
        """关闭窗口时确保所有线程都被正确处理"""
        self.stop_transfer()
        self.thread_pool.waitForDone()
        event.accept()

    def load_config(self):
        try:
            self.config.read('config.ini')
            self.log("配置文件 config.ini 加载成功。")
        except Exception as e:
            self.log(f"错误: 加载 config.ini 失败: {e}")

    def load_ips(self):
        try:
            with open('ip_list.txt', 'r') as f:
                ips = [line.strip() for line in f if line.strip()]
                for ip in ips:
                    item = QListWidgetItem()
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked)
                    
                    # 我们需要一个容器 widget 来承载布局
                    container = QWidget()
                    ip_layout = QHBoxLayout(container)
                    ip_layout.setContentsMargins(5, 2, 5, 2) # 调整边距让它看起来更好

                    # IP 地址本身
                    ip_label = QLabel(ip)
                    ip_layout.addWidget(ip_label)
                    ip_layout.addStretch()

                    # 状态标签
                    status_label = QLabel("等待中")
                    status_label.setStyleSheet("color: gray;")
                    status_label.setObjectName("status_label") # 设置对象名以便后续查找
                    ip_layout.addWidget(status_label)
                    
                    # 关键步骤：将 item 的大小调整为容器的大小
                    item.setSizeHint(container.sizeHint())
                    
                    self.ip_list_widget.addItem(item)
                    # 将自定义的 widget 设置给 item
                    self.ip_list_widget.setItemWidget(item, container)
                    
        except FileNotFoundError:
            self.log("错误: ip_list.txt 未找到！")

    def log(self, message):
        """在UI日志窗口和本地文件记录日志"""
        log_entry = f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {message}"
        self.log_edit.append(log_entry)
        
        # 写入本地日志文件
        try:
            with open(self.log_file_path, 'a', encoding='utf-8') as f:
                f.write(log_entry + '\n')
        except Exception as e:
            # 如果日志文件写入失败，在UI中打印一个紧急错误
            self.log_edit.append(f"CRITICAL: Failed to write to log file: {e}")

    def browse_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择要下发的文件")
        if file_path:
            self.file_path_edit.setText(file_path)
            self.log(f"已选择文件: {file_path}")

    def select_all_ips(self):
        for i in range(self.ip_list_widget.count()):
            item = self.ip_list_widget.item(i)
            item.setCheckState(Qt.CheckState.Checked)

    def deselect_all_ips(self):
        for i in range(self.ip_list_widget.count()):
            item = self.ip_list_widget.item(i)
            item.setCheckState(Qt.CheckState.Unchecked)

    def update_fallback_checkbox_state(self):
        is_smb_selected = self.smb_radio.isChecked()
        self.fallback_checkbox.setEnabled(is_smb_selected)
        if not is_smb_selected:
            self.fallback_checkbox.setChecked(False) # 如果不是 SMB，取消勾选并禁用

    def execute_transfers(self):
        """开始执行文件下发任务"""
        # 1. 收集设置
        selected_file = self.file_path_edit.text()
        if not selected_file or not os.path.exists(selected_file):
            QMessageBox.warning(self, "错误", "请先选择一个有效的文件！")
            return

        selected_ips = []
        for i in range(self.ip_list_widget.count()):
            item = self.ip_list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                container = self.ip_list_widget.itemWidget(item)
                ip_label = container.findChild(QLabel)
                selected_ips.append(ip_label.text())

        if not selected_ips:
            QMessageBox.warning(self, "错误", "请至少选择一个目标 IP！")
            return

        # 2. 更新UI和内部状态
        self.set_ui_running_state(True)
        self.cancellation_flag = threading.Event()
        self.active_tasks_counter = AtomicCounter(len(selected_ips))
        
        self.thread_pool.setMaxThreadCount(self.concurrency_spinbox.value())
        self.log(f"线程池最大线程数设置为: {self.thread_pool.maxThreadCount()}")
        self.log(f"开始向 {len(selected_ips)} 个目标分发任务...")

        # 3. 准备任务参数
        settings = {
            'file_path': selected_file,
            'use_smb': self.smb_radio.isChecked(),
            'use_ftp': self.ftp_radio.isChecked(),
            'fallback_to_ftp': self.fallback_checkbox.isChecked() and self.smb_radio.isChecked()
        }
        
        config_dict = {
            'common': dict(self.config.items('common')),
            'ftp': dict(self.config.items('ftp')),
            'smb': dict(self.config.items('smb'))
        }

        # 4. 创建并分发任务
        for ip in selected_ips:
            task = TransferTask(ip, settings, config_dict, self.cancellation_flag, self.task_signals)
            self.thread_pool.start(task)

    def stop_transfer(self):
        if self.cancellation_flag:
            self.log("!!! 用户请求停止所有任务，请等待当前操作完成...")
            self.cancellation_flag.set()
            self.stop_button.setEnabled(False) # 防止重复点击

    def on_task_finished(self):
        remaining = self.active_tasks_counter.decrement()
        if remaining == 0:
            self.log("所有任务均已完成或被取消。")
            self.set_ui_running_state(False)

    def update_ip_status(self, ip, status_text, color):
        for i in range(self.ip_list_widget.count()):
            item = self.ip_list_widget.item(i)
            widget = self.ip_list_widget.itemWidget(item)
            ip_label = widget.findChild(QLabel)
            if ip_label and ip_label.text() == ip:
                status_label = widget.findChild(QLabel, "status_label")
                if status_label:
                    status_label.setText(status_text)
                    status_label.setStyleSheet(f"color: {color};")
                break
    
    def reset_ip_statuses(self, ips_to_reset):
        for ip in ips_to_reset:
            self.update_ip_status(ip, "等待中", "gray")

    def set_ui_running_state(self, is_running):
        self.start_button.setEnabled(not is_running)
        self.stop_button.setEnabled(is_running)
        self.ip_list_widget.setEnabled(not is_running)
        self.file_path_edit.parent().setEnabled(not is_running)
        self.method_group.setEnabled(not is_running)
        self.concurrency_spinbox.parent().setEnabled(not is_running)
        
        if is_running:
            self.start_button.setText("正在下发...")
        else:
            self.start_button.setText("开始下发")




if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = FileCourierApp()
    ex.show()
    sys.exit(app.exec())