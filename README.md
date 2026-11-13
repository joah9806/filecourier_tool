# FileCourier - 文件下发工具

一个基于 PyQt6 开发的图形化批量文件下发工具，支持通过 SMB 和 FTP 协议向多个目标主机并发传输文件。

## 功能特性

- **多协议支持**：支持 Windows 共享 (SMB) 和 FTP 两种文件传输方式
- **自动回退机制**：SMB 传输失败时可自动尝试 FTP 作为备用方案
- **并发控制**：可自定义同时下发任务数量，提高传输效率
- **批量操作**：支持从 IP 列表中选择多个目标主机进行批量传输
- **实时状态监控**：每个目标 IP 显示实时传输状态和结果
- **详细日志记录**：完整记录所有操作日志到文件和界面
- **任务取消**：支持随时停止正在进行的传输任务

## 系统要求

- Windows 操作系统
- Python 3.8+ (源码运行时)

## 安装与运行

### 方式一：直接运行可执行文件

1. 下载最新的 `FileCourier-vX.X.X-Windows.zip` 压缩包
2. 解压到任意目录
3. 运行 `FileCourier.exe` 即可启动程序

### 方式二：从源码运行

1. 克隆或下载项目源码
2. 创建虚拟环境并安装依赖：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install PyQt6 smbclient
```

3. 准备配置文件（见下文配置说明）
4. 运行程序：

```bash
python filecourier/main.py
```

## 配置说明

### 1. 创建 config.ini 配置文件

在程序目录下创建 `config.ini` 文件，内容如下：

```ini
[common]
timeout = 30
target_subdir = FileCourier
log_file = file_courier.log

[ftp]
username = ftp_user
password = ftp_password
port = 21

[smb]
username = smb_user
password = smb_password
```

**配置项说明**：
- `timeout`：连接超时时间（秒）
- `target_subdir`：目标主机上的存储目录
- `log_file`：日志文件路径
- FTP 和 SMB 部分分别对应各自协议的认证信息

### 2. 创建 ip_list.txt 目标列表文件

在程序目录下创建 `ip_list.txt` 文件，每行一个目标 IP 地址：

```
192.168.1.100
192.168.1.101
192.168.1.102
192.168.1.103
```

## 使用说明

1. **选择文件**：点击"浏览..."按钮选择要下发的文件
2. **选择下发方式**：
   - Windows 共享 (SMB)：使用 SMB 协议传输
   - FTP：使用 FTP 协议传输
   - 可勾选"SMB 失败后自动尝试 FTP"启用回退机制
3. **设置并发数**：调整同时下发的任务数量（建议 5-10）
4. **选择目标 IP**：在右侧列表中勾选需要下发的目标主机
5. **开始下发**：点击"开始下发"按钮启动传输任务
6. **监控进度**：在日志面板查看详细传输信息，IP 列表中显示每个目标的状态
7. **停止任务**：如需中断传输，点击"停止下发"按钮

## 状态说明

- **等待中**：任务尚未开始
- **处理中...**：正在连接或传输文件
- **成功 (SMB)**：SMB 传输成功
- **成功 (FTP 回退)**：SMB 失败后 FTP 回退成功
- **失败: [错误信息]**：传输失败，显示具体错误原因
- **已取消**：任务被用户取消

## 项目结构

```
filecourier_tool/
├── filecourier/
│   ├── main.py              # 程序主入口
│   ├── app_window.py        # 主界面实现
│   └── transfer_task.py     # 传输任务实现
├── main.spec                # PyInstaller 打包配置
├── config.ini               # 配置文件（需自行创建）
├── ip_list.txt              # 目标IP列表（需自行创建）
└── README.md                # 项目说明文档
```

## 技术栈

- **GUI 框架**：PyQt6
- **SMB 协议**：smbclient
- **FTP 协议**：ftplib (Python 标准库)
- **并发处理**：QThreadPool + QRunnable
- **打包工具**：PyInstaller

## 打包为可执行文件

如需自行打包为 EXE 文件：

```bash
pyinstaller main.spec
```

## 注意事项

1. 确保目标主机的 SMB 或 FTP 服务正常运行
2. 确保防火墙允许相应端口的连接
3. SMB 传输需要目标主机开放 445 端口
4. FTP 传输需要目标主机开放 21 端口
5. 并发数设置过高可能会影响传输稳定性

## 版本历史

- **v2.1**：重构版本，优化并发处理和错误处理
- **v1.0.0**：初始版本，基础文件下发功能

## 许可证

本项目为内部工具，仅供授权使用。
