"""
PyGen 信号收集器 - 运行时信号收集

在沙箱环境执行生成的爬虫脚本时，收集各种运行时信号用于故障诊断。

收集的信号：
1. HTTP 响应状态码
2. 控制台错误/异常
3. 反爬检测关键词
4. 页面截图（可选）
5. 输出数据统计

使用方式：
    from signals_collector import SignalsCollector, ExecutionSignals
    
    collector = SignalsCollector()
    signals = collector.execute_and_collect(script_path, timeout=60)
"""

import subprocess
import sys
import json
import re
import time
import tempfile
import os
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
from enum import Enum


class ExecutionStatus(Enum):
    """执行状态"""
    SUCCESS = "success"           # 成功执行并产出数据
    PARTIAL = "partial"           # 部分成功（有数据但有警告）
    FAILED = "failed"             # 执行失败
    TIMEOUT = "timeout"           # 超时
    BLOCKED = "blocked"           # 被反爬拦截
    NO_DATA = "no_data"           # 执行成功但无数据


@dataclass
class HttpSignal:
    """HTTP 请求信号"""
    url: str
    method: str = "GET"
    status_code: int = 0
    response_size: int = 0
    duration_ms: float = 0
    error: Optional[str] = None


@dataclass
class ExecutionSignals:
    """执行信号汇总"""
    status: ExecutionStatus = ExecutionStatus.FAILED
    exit_code: int = -1
    duration_seconds: float = 0
    
    # 输出数据
    output_file: Optional[str] = None
    output_record_count: int = 0
    date_fill_rate: float = 0.0
    
    # HTTP 信号
    http_signals: List[HttpSignal] = field(default_factory=list)
    
    # 错误信号
    console_errors: List[str] = field(default_factory=list)
    exceptions: List[str] = field(default_factory=list)
    
    # 反爬信号
    challenge_detected: bool = False
    challenge_keywords: List[str] = field(default_factory=list)
    
    # 截图（Base64）
    screenshot_b64: Optional[str] = None
    
    # 原始输出
    stdout: str = ""
    stderr: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "status": self.status.value,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "output_file": self.output_file,
            "output_record_count": self.output_record_count,
            "date_fill_rate": self.date_fill_rate,
            "http_signals": [
                {
                    "url": s.url[:100],
                    "status_code": s.status_code,
                    "error": s.error
                } for s in self.http_signals
            ],
            "console_errors": self.console_errors[:10],
            "exceptions": self.exceptions[:5],
            "challenge_detected": self.challenge_detected,
            "challenge_keywords": self.challenge_keywords,
            "has_screenshot": self.screenshot_b64 is not None,
        }


# 反爬关键词
CHALLENGE_KEYWORDS = [
    "captcha", "验证码", "人机验证",
    "challenge", "cf-browser-verification",
    "blocked", "forbidden", "access denied",
    "请求过于频繁", "访问受限", "请稍后再试",
    "rate limit", "too many requests",
    "waf", "firewall", "security check",
]


class SignalsCollector:
    """
    信号收集器 - 执行脚本并收集运行时信号
    """
    
    def __init__(
        self,
        output_dir: Optional[str] = None,
        capture_screenshot: bool = False
    ):
        """
        初始化信号收集器
        
        Args:
            output_dir: 输出目录（用于查找生成的 JSON 文件）
            capture_screenshot: 是否捕获截图
        """
        self.output_dir = output_dir or str(Path(__file__).parent / "output")
        self.capture_screenshot = capture_screenshot
        
    def execute_and_collect(
        self,
        script_path: str,
        timeout: int = 120,
        env: Optional[Dict[str, str]] = None
    ) -> ExecutionSignals:
        """
        执行脚本并收集信号
        
        Args:
            script_path: 脚本路径
            timeout: 超时时间（秒）
            env: 环境变量
            
        Returns:
            执行信号
        """
        signals = ExecutionSignals()
        start_time = time.time()
        
        # 记录执行前的输出文件
        files_before = set(self._list_output_files())
        
        try:
            # 执行脚本
            result = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, **(env or {})},
                cwd=str(Path(script_path).parent)
            )
            
            signals.exit_code = result.returncode
            signals.stdout = result.stdout
            signals.stderr = result.stderr
            signals.duration_seconds = time.time() - start_time
            
            # 分析输出
            self._analyze_output(signals)
            
            # 查找新生成的输出文件
            files_after = set(self._list_output_files())
            new_files = files_after - files_before
            
            if new_files:
                # 取最新的文件
                newest = max(new_files, key=lambda f: os.path.getmtime(f))
                signals.output_file = newest
                self._analyze_output_file(signals, newest)
            
            # 确定最终状态
            signals.status = self._determine_status(signals)
            
        except subprocess.TimeoutExpired:
            signals.status = ExecutionStatus.TIMEOUT
            signals.duration_seconds = timeout
            signals.exceptions.append(f"执行超时（{timeout}秒）")
            
        except Exception as e:
            signals.status = ExecutionStatus.FAILED
            signals.exceptions.append(f"执行异常: {str(e)}")
            signals.duration_seconds = time.time() - start_time
        
        return signals
    
    def _list_output_files(self) -> List[str]:
        """列出输出目录中的 JSON 文件"""
        if not os.path.exists(self.output_dir):
            return []
        return [
            os.path.join(self.output_dir, f)
            for f in os.listdir(self.output_dir)
            if f.endswith('.json')
        ]
    
    def _analyze_output(self, signals: ExecutionSignals) -> None:
        """分析标准输出/错误"""
        combined = signals.stdout + signals.stderr
        combined_lower = combined.lower()
        
        # 检测反爬关键词
        for keyword in CHALLENGE_KEYWORDS:
            if keyword.lower() in combined_lower:
                signals.challenge_detected = True
                signals.challenge_keywords.append(keyword)
        
        # 提取错误信息
        error_patterns = [
            r'Error:.*',
            r'Exception:.*',
            r'Traceback.*',
            r'❌.*',
            r'失败.*',
            r'错误.*',
        ]
        
        for pattern in error_patterns:
            matches = re.findall(pattern, combined, re.IGNORECASE)
            signals.console_errors.extend(matches[:5])
        
        # 提取 HTTP 状态码
        status_matches = re.findall(r'status[:\s]+(\d{3})', combined, re.IGNORECASE)
        for status in status_matches:
            code = int(status)
            if code >= 400:
                signals.http_signals.append(HttpSignal(
                    url="(from log)",
                    status_code=code
                ))
        
        # 提取异常信息
        exception_patterns = [
            r'(\w+Error: .+)',
            r'(\w+Exception: .+)',
        ]
        for pattern in exception_patterns:
            matches = re.findall(pattern, combined)
            signals.exceptions.extend(matches[:3])
    
    def _analyze_output_file(self, signals: ExecutionSignals, file_path: str) -> None:
        """分析输出文件"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            reports = data.get('reports', [])
            signals.output_record_count = len(reports)
            
            if reports:
                date_count = sum(1 for r in reports if r.get('date'))
                signals.date_fill_rate = date_count / len(reports)
            
        except Exception as e:
            signals.exceptions.append(f"解析输出文件失败: {str(e)}")
    
    def _determine_status(self, signals: ExecutionSignals) -> ExecutionStatus:
        """确定最终执行状态"""
        # 被反爬拦截
        if signals.challenge_detected:
            return ExecutionStatus.BLOCKED
        
        # 有严重异常
        if signals.exceptions and signals.exit_code != 0:
            return ExecutionStatus.FAILED
        
        # 无输出数据
        if signals.output_record_count == 0:
            return ExecutionStatus.NO_DATA
        
        # 部分成功（有数据但有警告）
        if signals.console_errors or signals.date_fill_rate < 0.3:
            return ExecutionStatus.PARTIAL
        
        return ExecutionStatus.SUCCESS


class PlaywrightSignalsCollector(SignalsCollector):
    """
    增强版信号收集器 - 使用 Playwright 收集更多信号
    
    额外收集：
    - 网络请求日志
    - 页面截图
    - Console 日志
    """
    
    def __init__(self, output_dir: Optional[str] = None):
        super().__init__(output_dir, capture_screenshot=True)
        self._playwright = None
        self._browser = None
    
    def collect_page_signals(
        self,
        url: str,
        wait_seconds: float = 3.0
    ) -> Tuple[List[HttpSignal], bool, Optional[str]]:
        """
        收集页面访问信号
        
        Args:
            url: 目标 URL
            wait_seconds: 等待时间
            
        Returns:
            (HTTP信号列表, 是否检测到反爬, 截图Base64)
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return [], False, None
        
        http_signals = []
        challenge_detected = False
        screenshot_b64 = None
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()
                
                # 收集网络请求
                def handle_response(response):
                    http_signals.append(HttpSignal(
                        url=response.url[:200],
                        method=response.request.method,
                        status_code=response.status
                    ))
                
                page.on("response", handle_response)
                
                # 收集 console 日志
                console_logs = []
                page.on("console", lambda msg: console_logs.append(msg.text))
                
                # 访问页面
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(int(wait_seconds * 1000))
                
                # 检测反爬
                content = page.content().lower()
                for keyword in CHALLENGE_KEYWORDS:
                    if keyword.lower() in content:
                        challenge_detected = True
                        break
                
                # 截图
                if self.capture_screenshot:
                    screenshot_bytes = page.screenshot(type="png")
                    import base64
                    screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
                
                browser.close()
                
        except Exception as e:
            http_signals.append(HttpSignal(
                url=url,
                error=str(e)
            ))
        
        return http_signals, challenge_detected, screenshot_b64


# ============================================================================
# 便捷函数
# ============================================================================

def execute_script(script_path: str, timeout: int = 120) -> ExecutionSignals:
    """
    执行脚本并收集信号的便捷函数
    
    Args:
        script_path: 脚本路径
        timeout: 超时时间
        
    Returns:
        执行信号
    """
    collector = SignalsCollector()
    return collector.execute_and_collect(script_path, timeout)


def check_page_accessibility(url: str) -> Dict[str, Any]:
    """
    检查页面可访问性
    
    Args:
        url: 目标 URL
        
    Returns:
        检查结果
    """
    import requests
    
    result = {
        "url": url,
        "accessible": False,
        "status_code": 0,
        "challenge_detected": False,
        "error": None
    }
    
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
            timeout=15
        )
        result["status_code"] = resp.status_code
        result["accessible"] = resp.status_code == 200
        
        # 检测反爬
        content_lower = resp.text.lower()
        for keyword in CHALLENGE_KEYWORDS:
            if keyword.lower() in content_lower:
                result["challenge_detected"] = True
                break
                
    except Exception as e:
        result["error"] = str(e)
    
    return result


if __name__ == "__main__":
    # 测试
    print("信号收集器测试")
    print("=" * 60)
    
    # 测试页面可访问性检查
    test_url = "https://www.baidu.com"
    result = check_page_accessibility(test_url)
    print(f"页面检查结果: {json.dumps(result, ensure_ascii=False, indent=2)}")

