"""
Chrome 启动器模块 - PyGen独立版

自动检测和启动 Chrome 浏览器（CDP 模式）
"""
import os
import sys
import time
import json
import socket
import subprocess
import platform
import requests
from typing import Optional, Tuple
from pathlib import Path


class ChromeLauncher:
    """Chrome 浏览器启动器（CDP 模式）"""
    
    def __init__(
        self,
        debug_port: int = 9222,
        user_data_dir: Optional[str] = None,
        headless: bool = False,
        auto_select_port: bool = True
    ):
        """
        初始化 Chrome 启动器
        
        Args:
            debug_port: CDP 调试端口
            user_data_dir: Chrome Profile 目录（None = 自动创建）
            headless: 是否无头模式
            auto_select_port: 端口被占用时自动选择可用端口
        """
        self.debug_port = debug_port
        self.user_data_dir = user_data_dir or self._get_default_profile_dir()
        self.headless = headless
        self.auto_select_port = auto_select_port
        self.chrome_process: Optional[subprocess.Popen] = None
        self.actual_port: Optional[int] = None
        
    def _get_default_profile_dir(self) -> str:
        """获取默认 Profile 目录"""
        profile_dir = Path(__file__).parent / "chrome-profile"
        profile_dir.mkdir(exist_ok=True)
        return str(profile_dir)
    
    def _find_chrome_executable(self) -> Optional[str]:
        """跨平台查找 Chrome 可执行文件"""
        system = platform.system()
        
        if system == "Windows":
            possible_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            ]
        elif system == "Darwin":  # macOS
            possible_paths = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ]
        else:  # Linux
            possible_paths = [
                "/usr/bin/google-chrome",
                "/usr/bin/chromium-browser",
                "/usr/bin/chromium",
            ]
        
        for path in possible_paths:
            if os.path.exists(path):
                return path
        
        return None
    
    def _is_port_in_use(self, port: int) -> bool:
        """检查端口是否被占用"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return False
            except OSError:
                return True
    
    def _find_available_port(self, start_port: int = 9222, max_attempts: int = 10) -> int:
        """查找可用端口"""
        for i in range(max_attempts):
            port = start_port + i
            if not self._is_port_in_use(port):
                return port
        
        raise RuntimeError(f"无法在 {start_port}-{start_port + max_attempts} 范围内找到可用端口")
    
    def _check_cdp_ready(self, port: int, timeout: int = 15) -> bool:
        """检查 CDP 是否就绪"""
        url = f"http://127.0.0.1:{port}/json/version"
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                response = requests.get(url, timeout=2)
                if response.status_code == 200:
                    data = response.json()
                    print(f"✓ CDP 就绪: {data.get('Browser', 'Unknown')}")
                    return True
            except Exception:
                pass
            
            time.sleep(0.5)
        
        return False
    
    def _check_existing_instance(self, port: int) -> bool:
        """检查是否已有 Chrome 实例在运行"""
        if not self._is_port_in_use(port):
            return False
        
        try:
            response = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
            if response.status_code == 200:
                print(f"✓ 检测到现有 Chrome 实例（端口 {port}）")
                return True
        except Exception:
            pass
        
        return False
    
    def launch(self) -> Tuple[bool, int]:
        """启动 Chrome 浏览器"""
        # 1. 检查现有实例
        if self._check_existing_instance(self.debug_port):
            print(f"→ 复用现有 Chrome 实例（端口 {self.debug_port}）")
            self.actual_port = self.debug_port
            return True, self.debug_port
        
        # 2. 端口检查
        if self._is_port_in_use(self.debug_port):
            if self.auto_select_port:
                self.debug_port = self._find_available_port(self.debug_port)
                print(f"→ 端口被占用，自动选择端口 {self.debug_port}")
            else:
                raise RuntimeError(f"端口 {self.debug_port} 已被占用")
        
        # 3. 查找 Chrome 可执行文件
        chrome_path = self._find_chrome_executable()
        if not chrome_path:
            raise FileNotFoundError(
                "未找到 Chrome 浏览器。请确保已安装 Google Chrome。\n"
                "下载地址：https://www.google.com/chrome/"
            )
        
        print(f"→ 找到 Chrome: {chrome_path}")
        
        # 4. 构建启动参数
        args = [
            chrome_path,
            f"--remote-debugging-port={self.debug_port}",
            f"--user-data-dir={self.user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ]
        
        if self.headless:
            args.extend(["--headless", "--disable-gpu"])
        
        # 5. 启动 Chrome
        print(f"→ 正在启动 Chrome (端口 {self.debug_port})...")
        try:
            if platform.system() == "Windows":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                self.chrome_process = subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    startupinfo=startupinfo
                )
            else:
                self.chrome_process = subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
        except Exception as e:
            raise RuntimeError(f"启动 Chrome 失败: {e}")
        
        # 6. 等待 CDP 就绪
        print(f"→ 等待 CDP 就绪...")
        if not self._check_cdp_ready(self.debug_port):
            self.terminate()
            raise RuntimeError(f"CDP 未在超时时间内就绪（端口 {self.debug_port}）")
        
        self.actual_port = self.debug_port
        print(f"✓ Chrome 启动成功（端口 {self.debug_port}）")
        return True, self.debug_port
    
    def terminate(self):
        """终止 Chrome 进程"""
        if self.chrome_process:
            try:
                self.chrome_process.terminate()
                self.chrome_process.wait(timeout=5)
                print("✓ Chrome 进程已终止")
            except Exception as e:
                print(f"⚠️  终止 Chrome 进程时出错: {e}")
                try:
                    self.chrome_process.kill()
                except:
                    pass
    
    def get_ws_endpoint(self) -> str:
        """获取 WebSocket 端点"""
        if not self.actual_port:
            raise RuntimeError("Chrome 未启动或端口未知")
        
        try:
            response = requests.get(f"http://127.0.0.1:{self.actual_port}/json/version")
            data = response.json()
            ws_url = data.get("webSocketDebuggerUrl")
            if ws_url:
                return ws_url
        except Exception as e:
            print(f"⚠️  获取 WebSocket 端点失败: {e}")
        
        return f"ws://127.0.0.1:{self.actual_port}/devtools/browser"
    
    def __enter__(self):
        """上下文管理器支持"""
        self.launch()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器清理"""
        self.terminate()

