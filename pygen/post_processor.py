"""
PyGen 后处理器模块 - 条件性代码注入

根据静态检查结果，智能决定注入哪些增强代码。

设计原则：
1. 先检查，后注入（根据问题决定注入什么）
2. 不冗余（避免与 LLM 修复冲突）
3. 不误伤（结合 page_structure 判断）

v2.0 更新：
- 后注入放到 LLM 修复之前
- 删除 _fix_hardcoded_date_extraction（与 LLM 智能修复冲突）
- 所有注入改为条件性的（基于检查结果）
"""

import re
from typing import Dict, Any, List, Optional

# 导入日期提取工具代码生成器
try:
    from date_extractor import get_injectable_code as get_date_extractor_code
except ImportError:
    def get_date_extractor_code():
        return "# 日期提取工具代码未找到\n"


# =============================================================================
# HTTP/SSL 韧性层（无条件注入，不影响业务逻辑）
# =============================================================================

HTTP_RESILIENCE_BLOCK = r'''
# === PyGen 注入：HTTP/SSL 韧性层（通用） ===
# 说明：
# 1) 优先保持 verify=True；如确需临时绕过（不推荐），可设置环境变量 PYGEN_INSECURE_SSL=1
# 2) 默认遇到 418/403/429 仅做一次"预热 cookie + 浏览器化 headers"的重试
# 3) 若仍被拦截且本机已安装 Playwright，会自动尝试一次 request-context 兜底
#    如需禁用：设置 PYGEN_DISABLE_PLAYWRIGHT_FALLBACK=1
import os as _pygen_os
import ssl as _pygen_ssl
import urllib.parse as _pygen_urlparse

try:
    # truststore 会让 Python/requests 使用系统证书库（Windows/macOS/Linux），更贴近浏览器行为
    import truststore as _pygen_truststore  # type: ignore
    _pygen_truststore.inject_into_ssl()
except Exception:
    _pygen_truststore = None  # noqa: F401

try:
    import requests as _pygen_requests
    from requests.structures import CaseInsensitiveDict as _pygen_CaseInsensitiveDict
except Exception:
    _pygen_requests = None  # noqa: F401

_PYGEN_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0 Safari/537.36"
)

def _pygen_origin(url: str) -> str:
    try:
        p = _pygen_urlparse.urlsplit(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""

def _pygen_merge_headers(h: dict | None) -> dict:
    base = {
        "User-Agent": _PYGEN_DEFAULT_UA,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }
    if h:
        for k, v in h.items():
            if v is not None:
                base[k] = v
    return base

def _pygen_make_response(status: int, url: str, headers: dict, body: bytes):
    r = _pygen_requests.Response()
    r.status_code = status
    r.url = url
    r.headers = _pygen_CaseInsensitiveDict(headers or {})
    r._content = body or b""
    return r

def _pygen_playwright_fetch(method: str, url: str, headers: dict, data=None, json=None, timeout: int = 30):
    """
    兜底：使用 Playwright 的 request context（更像浏览器/更容易过 WAF）。
    仅当已安装 playwright 且未设置 PYGEN_DISABLE_PLAYWRIGHT_FALLBACK=1 时启用。
    """
    if _pygen_os.getenv("PYGEN_DISABLE_PLAYWRIGHT_FALLBACK") == "1":
        return None
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return None

    with sync_playwright() as p:
        ctx = p.request.new_context(ignore_https_errors=_pygen_os.getenv("PYGEN_INSECURE_SSL") == "1",
                                    extra_http_headers=headers)
        try:
            resp = ctx.fetch(url, method=method.upper(), data=data, json=json, timeout=timeout * 1000)
            body = resp.body()
            return _pygen_make_response(resp.status, url, dict(resp.headers), body)
        finally:
            ctx.dispose()

def _pygen_install_requests_patch():
    if _pygen_requests is None:
        return
    _orig = _pygen_requests.sessions.Session.request

    def _patched(self, method, url, **kwargs):
        # 默认超时，避免脚本卡死
        kwargs.setdefault("timeout", 30)

        # 证书校验策略：默认开启；仅在用户明确设置时关闭
        if _pygen_os.getenv("PYGEN_INSECURE_SSL") == "1":
            kwargs["verify"] = False

        # 合并"更像浏览器"的 headers（很多站会对 UA/Accept 做拦截）
        headers = _pygen_merge_headers(kwargs.get("headers"))
        kwargs["headers"] = headers

        # 首次请求
        try:
            resp = _orig(self, method, url, **kwargs)
        except Exception as e:
            # 若是 SSL 校验错误：truststore 已注入则直接抛出；否则给出更友好的提示
            msg = str(e)
            if "CERTIFICATE_VERIFY_FAILED" in msg or "certificate verify failed" in msg:
                # 若 playwright 可用，尝试用 request-context 做一次兜底（有些环境下系统信任链更完整）
                try:
                    pw_resp = _pygen_playwright_fetch(method, url, headers=headers,
                                                      data=kwargs.get("data"),
                                                      json=kwargs.get("json"),
                                                      timeout=int(kwargs.get("timeout") or 30))
                    if pw_resp is not None:
                        return pw_resp
                except Exception:
                    pass
                raise
            raise

        # WAF/反爬：做一次轻量重试（预热 cookie + 再请求）
        if resp is not None and getattr(resp, "status_code", 0) in (418, 403, 429):
            try:
                origin = _pygen_origin(url)
                if origin:
                    # 预热：访问首页拿 cookie
                    _ = _orig(self, "GET", origin + "/", headers=headers, timeout=15, verify=kwargs.get("verify", True))
                resp2 = _orig(self, method, url, **kwargs)
                if resp2 is not None and getattr(resp2, "status_code", 0) not in (418, 403, 429):
                    return resp2
                # 若仍被拦截，且 playwright 可用，则再尝试一次（自动兜底）
                pw_resp = _pygen_playwright_fetch(method, url, headers=headers,
                                                  data=kwargs.get("data"),
                                                  json=kwargs.get("json"),
                                                  timeout=int(kwargs.get("timeout") or 30))
                if pw_resp is not None:
                    return pw_resp
            except Exception:
                # 兜底失败则返回原始响应，交由上层处理
                return resp

        return resp

    _pygen_requests.sessions.Session.request = _patched

_pygen_install_requests_patch()
# === PyGen 注入结束 ===
'''


def inject_http_resilience(script_code: str) -> str:
    """
    注入 HTTP/SSL 韧性层
    
    - 无条件注入（网络层增强，不影响业务逻辑）
    - 已有标记则跳过
    """
    if "# === PyGen 注入：HTTP/SSL 韧性层（通用） ===" in script_code:
        return script_code
    
    lines = script_code.splitlines()
    insert_at = 0
    for i, line in enumerate(lines[:80]):
        if line.startswith("import ") or line.startswith("from "):
            insert_at = i
            break
    else:
        insert_at = min(2, len(lines))
    
    lines.insert(insert_at, HTTP_RESILIENCE_BLOCK.strip("\n"))
    return "\n".join(lines) + ("\n" if not script_code.endswith("\n") else "")


def inject_date_extraction_tools(script_code: str) -> str:
    """
    注入日期提取工具函数
    
    - 只注入函数定义，不强制替换调用
    - 已有标记则跳过
    """
    if "# === PyGen 注入：日期提取韧性层（通用） ===" in script_code:
        return script_code
    
    date_utils_code = get_date_extractor_code()
    
    lines = script_code.splitlines()
    insert_at = 0
    for i, line in enumerate(lines[:80]):
        if line.startswith("import ") or line.startswith("from "):
            insert_at = i
            break
    else:
        insert_at = min(2, len(lines))
    
    lines.insert(insert_at, date_utils_code.strip("\n"))
    return "\n".join(lines) + ("\n" if not script_code.endswith("\n") else "")


def fix_brittle_table_selectors(script_code: str) -> str:
    """
    修复脆弱的表格选择器
    
    - 把 table.select('tbody tr') 改成更健壮的形式
    - 只在检测到该模式时才替换
    """
    script = script_code
    
    # 模式：rows = table.select('tbody tr')
    pattern = re.compile(r"(\s*)(rows\s*=\s*)(\w+)\.select\(['\"]tbody tr['\"]\)")
    
    def replacement(match):
        indent = match.group(1)
        var_assign = match.group(2)
        table_var = match.group(3)
        return (
            f"{indent}# PyGen: 健壮化表格行选择器（兼容无 tbody 的表格）\n"
            f"{indent}{var_assign}{table_var}.select('tbody tr') or {table_var}.select('tr')[1:]  # 跳过表头"
        )
    
    return pattern.sub(replacement, script)


# =============================================================================
# 条件性后处理器（根据检查结果决定注入）
# =============================================================================

class ConditionalPostProcessor:
    """
    条件性后处理器
    
    根据静态检查结果，智能决定注入哪些增强代码。
    在 LLM 修复之前执行，为后续修复提供工具函数。
    """
    
    def __init__(self, page_structure: Optional[Dict[str, Any]] = None):
        self.page_structure = page_structure
        self.injection_log: List[str] = []
    
    def process(self, script_code: str, issues: List) -> str:
        """
        根据问题列表，条件性地注入增强代码
        
        Args:
            script_code: 原始代码
            issues: 静态检查发现的问题列表（来自 StaticCodeValidator）
            
        Returns:
            处理后的代码
        """
        self.injection_log = []
        script = script_code
        
        # 提取问题代码列表
        issue_codes = [i.code for i in issues]
        
        # =========================================================
        # 1. HTTP 韧性层：无条件注入（网络层增强）
        # =========================================================
        if "# === PyGen 注入：HTTP/SSL 韧性层" not in script:
            script = inject_http_resilience(script)
            self.injection_log.append("注入 HTTP/SSL 韧性层（网络增强）")
        
        # =========================================================
        # 2. 日期提取工具：条件性注入
        # =========================================================
        # 条件：检测到日期相关问题（ERR_001, ERR_009）
        # 或者代码中有硬编码日期提取模式
        needs_date_tools = (
            "ERR_001" in issue_codes or  # 硬编码列索引
            "ERR_009" in issue_codes or  # 缺少日期提取
            self._has_hardcoded_date_pattern(script)
        )
        
        if needs_date_tools and "# === PyGen 注入：日期提取韧性层" not in script:
            script = inject_date_extraction_tools(script)
            self.injection_log.append("注入日期提取工具（检测到日期提取问题）")
        
        # =========================================================
        # 3. 脆弱选择器修复：条件性注入
        # =========================================================
        # 条件：检测到链式调用问题（ERR_002）
        # 或者代码中有 tbody tr 模式
        needs_selector_fix = (
            "ERR_002" in issue_codes or
            "tbody tr" in script
        )
        
        if needs_selector_fix:
            old_script = script
            script = fix_brittle_table_selectors(script)
            if script != old_script:
                self.injection_log.append("修复脆弱的表格选择器（tbody tr）")
        
        # =========================================================
        # 注意：删除了 _fix_hardcoded_date_extraction
        # 原因：与 LLM 智能修复冲突
        # LLM 修复阶段会结合 page_structure 判断列索引是否正确
        # 如果正确则不修改，如果错误则提示正确的列位置
        # =========================================================
        
        return script
    
    def _has_hardcoded_date_pattern(self, code: str) -> bool:
        """检测是否有硬编码日期提取模式"""
        if "_pygen_smart_find_date_in_row" in code:
            return False  # 已使用智能扫描
        
        patterns = [
            r"tds\[\d+\]\.query_selector\(['\"]span['\"]\)",
            r"tds\[\d+\]\.select_one\(['\"]span['\"]\)",
            r"tds\[\d+\]\.get_text\(",
            r"tds\[\d+\]\.inner_text\(",
        ]
        
        for pattern in patterns:
            if re.search(pattern, code):
                if "date" in code.lower():
                    return True
        
        return False
    
    def get_injection_log(self) -> List[str]:
        """获取注入日志"""
        return self.injection_log


# =============================================================================
# 便捷函数
# =============================================================================

def apply_conditional_post_processing(
    script_code: str,
    issues: List,
    page_structure: Optional[Dict[str, Any]] = None
) -> tuple:
    """
    应用条件性后处理
    
    Args:
        script_code: 原始代码
        issues: 静态检查发现的问题列表
        page_structure: 页面结构（可选）
        
    Returns:
        (处理后的代码, 注入日志)
    """
    processor = ConditionalPostProcessor(page_structure)
    new_script = processor.process(script_code, issues)
    return new_script, processor.get_injection_log()
