import os
import ftplib
import threading
import shutil

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

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