import os
import sys
import time
import configparser
import threading

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLineEdit, QGroupBox, QRadioButton, 
                             QCheckBox, QTextEdit, QFileDialog, QListWidget, 
                             QListWidgetItem, QLabel, QMessageBox, QSpinBox)
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtCore import Qt, QThreadPool

from transfer_task import TaskSignals, TransferTask, AtomicCounter

class FileCourierApp(QWidget):
    def __init__(self):
        super().__init__()
        
        # 1. 初始化核心组件
        self.config = configparser.ConfigParser()
        self.thread_pool = QThreadPool()
        self.cancellation_flag = None
        self.active_tasks_counter = None
        self.log_file_path = self.config.get('common', 'log_file', fallback='file_courier.log')

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
        self.setWindowTitle('文件下发工具 v2.1 (重构版)')
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
                    
                    container = QWidget()
                    ip_layout = QHBoxLayout(container)
                    ip_layout.setContentsMargins(5, 2, 5, 2)

                    ip_label = QLabel(ip)
                    ip_layout.addWidget(ip_label)
                    ip_layout.addStretch()

                    status_label = QLabel("等待中")
                    status_label.setStyleSheet("color: gray;")
                    status_label.setObjectName("status_label")
                    ip_layout.addWidget(status_label)
                    
                    item.setSizeHint(container.sizeHint())
                    
                    self.ip_list_widget.addItem(item)
                    self.ip_list_widget.setItemWidget(item, container)
                    
        except FileNotFoundError:
            self.log("错误: ip_list.txt 未找到！")

    def log(self, message):
        log_entry = f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {message}"
        self.log_edit.append(log_entry)
        
        try:
            with open(self.log_file_path, 'a', encoding='utf-8') as f:
                f.write(log_entry + '\n')
        except Exception as e:
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
            self.fallback_checkbox.setChecked(False)

    def execute_transfers(self):
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

        self.set_ui_running_state(True)
        self.cancellation_flag = threading.Event()
        self.active_tasks_counter = AtomicCounter(len(selected_ips))
        
        self.thread_pool.setMaxThreadCount(self.concurrency_spinbox.value())
        self.log(f"线程池最大线程数设置为: {self.thread_pool.maxThreadCount()}")
        self.log(f"开始向 {len(selected_ips)} 个目标分发任务...")

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

        for ip in selected_ips:
            task = TransferTask(ip, settings, config_dict, self.cancellation_flag, self.task_signals)
            self.thread_pool.start(task)

    def stop_transfer(self):
        if self.cancellation_flag:
            self.log("!!! 用户请求停止所有任务，请等待当前操作完成...")
            self.cancellation_flag.set()
            self.stop_button.setEnabled(False)

    def set_ui_running_state(self, is_running):
        self.file_group.setEnabled(not is_running)
        self.method_group.setEnabled(not is_running)
        self.concurrency_group.setEnabled(not is_running)
        self.ip_group.setEnabled(not is_running)
        self.start_button.setEnabled(not is_running)
        self.stop_button.setEnabled(is_running)

    def update_ip_status(self, ip, status, color):
        for i in range(self.ip_list_widget.count()):
            item = self.ip_list_widget.item(i)
            container = self.ip_list_widget.itemWidget(item)
            ip_label = container.findChild(QLabel)
            if ip_label.text() == ip:
                status_label = container.findChild(QLabel, "status_label")
                status_label.setText(status)
                status_label.setStyleSheet(f"color: {color};")
                break

    def on_task_finished(self):
        remaining_tasks = self.active_tasks_counter.decrement()
        if remaining_tasks == 0:
            self.log("--- 所有任务处理完毕 ---")
            self.set_ui_running_state(False)