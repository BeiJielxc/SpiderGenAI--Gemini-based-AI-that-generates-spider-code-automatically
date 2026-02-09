#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
确定性脚本模板生成器（不依赖 LLM 生成核心逻辑）

目标：
- 一旦后端已经验证得到候选 API（candidate + params），就直接生成“纯 API 直连脚本”
- 避免 LLM 跑偏到 Playwright 抓表格 / 乱改接口

说明：
- 这里生成的是“独立可运行脚本”（不会 import pygen 内部模块）
- 模板具备泛化能力：支持 GET/POST、JSON/JSONP、常见分页参数、list-of-list 数据等
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


# ============ 响应结构自动分析 ============

# 日期字段特征模式（按优先级排列）
_DATE_FIELD_PATTERNS = [
    re.compile(r'(?:publish|announcement|notice|report|create|update|release|cms|sse|trade)[\s_-]*(?:time|date|day)', re.I),
    re.compile(r'(?:time|date|day)[\s_-]*(?:publish|announcement|notice|report|create|update|release)', re.I),
    re.compile(r'^(?:date|time|Date|Time|DATE|TIME)$'),
    re.compile(r'(?:Start|End|Begin|From|To)[\s_-]*(?:Date|Time)', re.I),
    re.compile(r'(?:date|time)', re.I),
]

# 标题字段特征模式
_TITLE_FIELD_PATTERNS = [
    re.compile(r'^(?:title|TITLE|Title)$'),
    re.compile(r'(?:report|announce|notice|doc|file)[\s_-]*(?:title|name|subject)', re.I),
    re.compile(r'(?:title|subject|headline|caption)', re.I),
    re.compile(r'^(?:name|text)$', re.I),
]

# URL/下载链接字段特征模式
_URL_FIELD_PATTERNS = [
    re.compile(r'(?:download|attach|file|pdf|doc)[\s_-]*(?:url|path|link|href)', re.I),
    re.compile(r'(?:url|path|link|href)[\s_-]*(?:download|attach|file|pdf|doc)', re.I),
    re.compile(r'^(?:URL|url|Url|href|link)$'),
    re.compile(r'(?:attach|file)[\s_-]*(?:path|name)', re.I),
    re.compile(r'(?:url|path|link|href)', re.I),
]

# 证券代码字段特征模式
_CODE_FIELD_PATTERNS = [
    re.compile(r'(?:sec|stock|security|bond|fund)[\s_-]*(?:code|id|no|number)', re.I),
    re.compile(r'^(?:code|Code|CODE|symbol|ticker)$'),
]

# 证券名称字段特征模式
_NAME_FIELD_PATTERNS = [
    re.compile(r'(?:sec|stock|security|bond|fund|company)[\s_-]*(?:name|abbr|short)', re.I),
]

_DATE_VALUE_RE = re.compile(r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}')
_URL_VALUE_RE = re.compile(r'^(?:https?://|/|\.\./).*\.(?:pdf|doc|docx|xls|xlsx|htm|html|txt|zip|rar)', re.I)


def _looks_like_date_value(v: Any) -> bool:
    """判断值是否看起来像日期"""
    if not isinstance(v, str) or len(v) < 8:
        return False
    return bool(_DATE_VALUE_RE.match(v.strip()))


def _looks_like_url_value(v: Any) -> bool:
    """判断值是否看起来像 URL / 文件路径"""
    if not isinstance(v, str) or len(v) < 5:
        return False
    s = v.strip()
    if _URL_VALUE_RE.match(s):
        return True
    # 也接受类似 /disc/disk03/... 的路径
    if s.startswith('/') and '/' in s[1:]:
        return True
    return False


def _match_field(key: str, patterns: List[re.Pattern]) -> int:
    """返回字段名匹配的优先级（越小越好），-1 表示不匹配"""
    for i, pat in enumerate(patterns):
        if pat.search(key):
            return i
    return -1


def analyze_response_schema(
    sample_data: Any,
    *,
    api_url: str = "",
) -> Dict[str, Any]:
    """
    从 API 样本响应中自动推断字段映射。
    
    返回:
    {
        "items_path": "data" | "pageHelp.data" | "result" | ...,
        "total_field": "announceCount" | "total" | null,
        "date_fields": ["publishTime"],
        "title_fields": ["title"],
        "url_fields": ["attachPath"],
        "code_fields": ["secCode"],
        "name_fields": ["secName"],
        "page_count_field": "pageCount" | null,
        "confidence": 0.0 ~ 1.0,
        "unmapped": ["date_fields", ...]  # 无法自动推断的类别
    }
    """
    result: Dict[str, Any] = {
        "items_path": None,
        "total_field": None,
        "date_fields": [],
        "title_fields": [],
        "url_fields": [],
        "code_fields": [],
        "name_fields": [],
        "page_count_field": None,
        "confidence": 0.0,
        "unmapped": [],
    }
    
    if not isinstance(sample_data, dict):
        result["unmapped"] = ["items_path", "date_fields", "title_fields", "url_fields"]
        return result
    
    # ── 1. 找到 items 数组路径 + 顶层 meta 字段 ──
    items: List[Dict[str, Any]] = []
    
    # 尝试 pageHelp 结构（上交所）
    if isinstance(sample_data.get("pageHelp"), dict):
        ph = sample_data["pageHelp"]
        d = ph.get("data")
        if isinstance(d, list) and d:
            items_raw = d
            # 处理 list-of-list（SSE 常见）
            if items_raw and isinstance(items_raw[0], list):
                items = [x for sub in items_raw for x in sub if isinstance(x, dict)]
                result["items_path"] = "pageHelp.data (list-of-list)"
            elif items_raw and isinstance(items_raw[0], dict):
                items = items_raw
                result["items_path"] = "pageHelp.data"
        # 提取 pageHelp 的 meta
        for mk in ["pageCount", "totalPage", "total_page"]:
            if mk in ph:
                result["page_count_field"] = f"pageHelp.{mk}"
                break
    
    # 如果 pageHelp 没找到，尝试顶层常见 key
    if not items:
        for k in ["data", "result", "list", "items", "rows", "records", "content"]:
            v = sample_data.get(k)
            if isinstance(v, list) and v:
                if isinstance(v[0], dict):
                    items = v
                    result["items_path"] = k
                    break
                elif isinstance(v[0], list):
                    items = [x for sub in v for x in sub if isinstance(x, dict)]
                    if items:
                        result["items_path"] = f"{k} (list-of-list)"
                        break
    
    # 顶层 total 字段
    for tk in ["announceCount", "totalCount", "total", "recordCount", "count", "totalRecords"]:
        if tk in sample_data and isinstance(sample_data[tk], (int, float)):
            result["total_field"] = tk
            break
    
    if not items:
        result["unmapped"] = ["items_path", "date_fields", "title_fields", "url_fields"]
        return result
    
    # ── 2. 分析 items 的字段 ──
    # 取前 3 个 item，统计各字段的值特征
    sample_items = items[:min(3, len(items))]
    all_keys = set()
    for it in sample_items:
        if isinstance(it, dict):
            all_keys.update(it.keys())
    
    # 对每个类别，用模式匹配 + 值验证来打分
    date_candidates: List[Tuple[int, str]] = []    # (priority, field_name)
    title_candidates: List[Tuple[int, str]] = []
    url_candidates: List[Tuple[int, str]] = []
    code_candidates: List[Tuple[int, str]] = []
    name_candidates: List[Tuple[int, str]] = []
    
    for key in all_keys:
        # 跳过内部字段
        if key.startswith('_'):
            continue
        
        # 取样本值
        sample_values = [it.get(key) for it in sample_items if key in it]
        
        # --- 日期字段 ---
        p = _match_field(key, _DATE_FIELD_PATTERNS)
        if p >= 0:
            # 名称匹配，检查值是否确实像日期
            has_date_val = any(_looks_like_date_value(v) for v in sample_values if v)
            priority = p * 2 + (0 if has_date_val else 1)
            date_candidates.append((priority, key))
        elif any(_looks_like_date_value(v) for v in sample_values if v):
            # 名称不匹配但值像日期
            date_candidates.append((100, key))
        
        # --- 标题字段 ---
        p = _match_field(key, _TITLE_FIELD_PATTERNS)
        if p >= 0:
            # 验证值是非空字符串且足够长
            has_text = any(isinstance(v, str) and len(v.strip()) > 2 for v in sample_values if v)
            priority = p * 2 + (0 if has_text else 1)
            title_candidates.append((priority, key))
        
        # --- URL 字段 ---
        p = _match_field(key, _URL_FIELD_PATTERNS)
        if p >= 0:
            has_url = any(_looks_like_url_value(v) for v in sample_values if v)
            priority = p * 2 + (0 if has_url else 1)
            url_candidates.append((priority, key))
        elif any(_looks_like_url_value(v) for v in sample_values if v):
            url_candidates.append((100, key))
        
        # --- 证券代码 ---
        p = _match_field(key, _CODE_FIELD_PATTERNS)
        if p >= 0:
            code_candidates.append((p, key))
        
        # --- 证券名称 ---
        p = _match_field(key, _NAME_FIELD_PATTERNS)
        if p >= 0:
            name_candidates.append((p, key))
    
    # 按优先级排序，取最佳结果
    def _pick_best(candidates: List[Tuple[int, str]], limit: int = 3) -> List[str]:
        if not candidates:
            return []
        candidates.sort(key=lambda x: x[0])
        return [c[1] for c in candidates[:limit]]
    
    result["date_fields"] = _pick_best(date_candidates)
    result["title_fields"] = _pick_best(title_candidates)
    result["url_fields"] = _pick_best(url_candidates)
    result["code_fields"] = _pick_best(code_candidates)
    result["name_fields"] = _pick_best(name_candidates)
    
    # ── 3. 计算置信度 ──
    mapped = 0
    total_categories = 3  # date, title, url 是必须的
    unmapped = []
    
    if result["date_fields"]:
        mapped += 1
    else:
        unmapped.append("date_fields")
    if result["title_fields"]:
        mapped += 1
    else:
        unmapped.append("title_fields")
    if result["url_fields"]:
        mapped += 1
    else:
        unmapped.append("url_fields")
    
    result["unmapped"] = unmapped
    result["confidence"] = mapped / total_categories
    
    return result


def build_llm_cloze_prompt(
    sample_item: Dict[str, Any],
    unmapped: List[str],
) -> str:
    """
    构建 LLM "完形填空" prompt：只让 LLM 识别样本中的字段映射。
    比生成整个脚本便宜得多（~200 tokens input, ~50 tokens output）。
    """
    item_json = json.dumps(sample_item, ensure_ascii=False, indent=2)
    
    field_descriptions = {
        "date_fields": "日期/时间字段（如发布日期、公告日期等，值通常是 YYYY-MM-DD 格式的字符串）",
        "title_fields": "标题字段（如公告标题、报告名称等）",
        "url_fields": "下载链接/文件路径字段（如 PDF 下载地址、附件路径等）",
        "code_fields": "证券代码字段（如股票代码）",
        "name_fields": "证券名称字段（如公司简称）",
    }
    
    questions = []
    for field_type in unmapped:
        desc = field_descriptions.get(field_type, field_type)
        questions.append(f'"{field_type}": 请从上面的字段中找出{desc}的 key 名')
    
    return f"""以下是一个 API 返回的数据项样例：
```json
{item_json}
```

请从中识别以下字段，以 JSON 格式返回结果（值为字段名字符串数组，找不到填空数组[]）：
{json.dumps(dict.fromkeys(unmapped, []), ensure_ascii=False)}

要求：
1. 只返回 JSON，不要其他文字
2. 值是字符串数组，每个元素是上面 JSON 中的 key 名
3. 按可能性从高到低排列
"""


def parse_llm_cloze_response(response_text: str) -> Dict[str, List[str]]:
    """解析 LLM 完形填空的响应"""
    text = response_text.strip()
    # 提取 JSON 块
    m = re.search(r'\{[^{}]*\}', text, re.S)
    if m:
        text = m.group(0)
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return {k: v for k, v in result.items() if isinstance(v, list)}
    except Exception:
        pass
    return {}


def render_date_range_api_script(
    *,
    target_url: str,
    api_url: str,
    method: str,
    base_params: Dict[str, Any],
    date_params: Dict[str, str],
    start_date: str,
    end_date: str,
    output_dir: str,
    extra_headers: Optional[Dict[str, str]] = None,
    field_mappings: Optional[Dict[str, Any]] = None,
) -> str:
    """
    生成确定性“纯 API 直连”脚本。

    关键点：
    - 这里必须用“纯字符串模板 + 占位符替换”，避免花括号在渲染期被求值。
    - field_mappings 来自 analyze_response_schema() + LLM 完形填空，
      为模板提供具体网站的字段名，避免硬编码猜测。
    """
    fm = field_mappings or {}

    payload = {
        "TARGET_URL": target_url,
        "API_URL": api_url,
        "METHOD": (method or "GET").upper(),
        "BASE_PARAMS": base_params or {},
        "DATE_PARAM_FORMATS": date_params or {},
        "START_DATE": start_date,
        "END_DATE": end_date,
        "OUTPUT_DIR": output_dir,
        "EXTRA_HEADERS": extra_headers or {},
        "FIELD_MAPPINGS": {
            "items_path": fm.get("items_path"),
            "total_field": fm.get("total_field"),
            "date_fields": fm.get("date_fields") or [],
            "title_fields": fm.get("title_fields") or [],
            "url_fields": fm.get("url_fields") or [],
            "code_fields": fm.get("code_fields") or [],
            "name_fields": fm.get("name_fields") or [],
            "page_count_field": fm.get("page_count_field"),
        },
    }

    template = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
确定性日期范围 API 爬虫（由 PyGen 后端模板生成）

特点：
- 字段映射由 analyze_response_schema() 自动推断 + LLM 完形填空（非硬编码）
- 支持 GET/POST、JSON/JSONP、通用分页、list-of-list 数据结构
"""

import json
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import urlsplit, urlunsplit

import requests

CONFIG = json.loads(r"""__CONFIG_JSON__""")

TARGET_URL = CONFIG["TARGET_URL"]
API_URL = CONFIG["API_URL"]
METHOD = (CONFIG.get("METHOD") or "GET").upper()
BASE_PARAMS: Dict[str, Any] = dict(CONFIG.get("BASE_PARAMS") or {})
DATE_PARAM_FORMATS: Dict[str, str] = dict(CONFIG.get("DATE_PARAM_FORMATS") or {})
_RAW_START = CONFIG["START_DATE"]
_RAW_END = CONFIG["END_DATE"]
# 重要：某些 API（如上交所）不接受 END_DATE 超过今天，会返回空数据
# 自动截断为今天，确保请求能返回数据
_TODAY = datetime.now().strftime("%Y-%m-%d")
END_DATE = min(_RAW_END, _TODAY) if _RAW_END > _TODAY else _RAW_END
START_DATE = min(_RAW_START, END_DATE)
OUTPUT_DIR = CONFIG["OUTPUT_DIR"]
EXTRA_HEADERS: Dict[str, str] = dict(CONFIG.get("EXTRA_HEADERS") or {})

# ============ 字段映射（由 analyze_response_schema 自动推断，非硬编码） ============
_FM = CONFIG.get("FIELD_MAPPINGS") or {}

# 自动推断的字段 + 通用兜底（仅在推断为空时启用）
_FALLBACK_DATE = ["publishDate", "publishTime", "date", "Date", "time", "cmsOpDate"]
_FALLBACK_TITLE = ["title", "TITLE", "Title", "name", "text"]
_FALLBACK_URL = ["url", "URL", "downloadUrl", "href", "link", "attachPath", "filePath"]
_FALLBACK_CODE = ["secCode", "SECURITY_CODE", "security_Code", "code", "Code"]
_FALLBACK_NAME = ["secName", "SECURITY_NAME", "security_Name"]

DATE_FIELDS: List[str] = _FM.get("date_fields") or _FALLBACK_DATE
TITLE_FIELDS: List[str] = _FM.get("title_fields") or _FALLBACK_TITLE
URL_FIELDS: List[str] = _FM.get("url_fields") or _FALLBACK_URL
CODE_FIELDS: List[str] = _FM.get("code_fields") or _FALLBACK_CODE
NAME_FIELDS: List[str] = _FM.get("name_fields") or _FALLBACK_NAME
TOTAL_FIELD: str = _FM.get("total_field") or ""

MAX_PAGES = 300
SLEEP_SECONDS = 0.4


def normalize_date(date_str: str) -> str:
    if not date_str:
        return ""
    s = str(date_str).strip()
    s = s.replace("\u5e74", "-").replace("\u6708", "-").replace("\u65e5", "")
    s = s.replace("/", "-").replace(".", "-")
    m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", s)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").strftime("%Y-%m-%d")
        except Exception:
            return m.group(1)
    if re.fullmatch(r"\d{8}", s):
        return "{}-{}-{}".format(s[:4], s[4:6], s[6:8])
    return ""


def is_date_in_range(d: str, start: str, end: str) -> bool:
    try:
        dd = datetime.strptime(d, "%Y-%m-%d")
        ss = datetime.strptime(start, "%Y-%m-%d")
        ee = datetime.strptime(end, "%Y-%m-%d")
        return ss <= dd <= ee
    except Exception:
        return False


def parse_json_or_jsonp(text: str) -> Any:
    if text is None:
        return None
    t = str(text).strip()
    if t.startswith("/**/"):
        t = t[4:].strip()
    if t.startswith("?(") and t.endswith(")"):
        t = t[2:-1]
    m = re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*\s*\((.*)\)\s*;?\s*$", t, re.S)
    if m:
        t = m.group(1)
    try:
        return json.loads(t)
    except Exception:
        return None


def flatten_list_of_list(x: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if x is None:
        return out
    if isinstance(x, dict):
        return [x]
    if isinstance(x, list):
        for it in x:
            if isinstance(it, dict):
                out.append(it)
            elif isinstance(it, list):
                for sub in it:
                    if isinstance(sub, dict):
                        out.append(sub)
    return out


def find_items_in_response(data: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {}
    if not isinstance(data, dict):
        return items, meta
    # total: prioritize auto-detected field, fallback to common names
    if TOTAL_FIELD and TOTAL_FIELD in data and isinstance(data[TOTAL_FIELD], (int, float)):
        meta["total"] = int(data[TOTAL_FIELD])
    else:
        for tk in ["announceCount", "totalCount", "total", "recordCount", "count", "totalRecords"]:
            if tk in data and isinstance(data[tk], (int, float)):
                meta["total"] = int(data[tk])
                break
    if isinstance(data.get("pageHelp"), dict):
        ph = data["pageHelp"]
        meta["pageCount"] = ph.get("pageCount")
        meta["pageNo"] = ph.get("pageNo")
        meta["pageSize"] = ph.get("pageSize")
        if ph.get("total") is not None:
            meta["total"] = ph.get("total")
        items = flatten_list_of_list(ph.get("data"))
        if items:
            return items, meta
    for k in ["data", "result", "list", "items", "rows", "records", "content"]:
        cand = flatten_list_of_list(data.get(k))
        if cand:
            return cand, meta
    return items, meta


def set_date_params(params: Dict[str, Any], start: str, end: str) -> None:
    for name, fmt in (DATE_PARAM_FORMATS or {}).items():
        n = (name or "")
        nl = n.lower()
        original = params.get(n)
        if isinstance(original, list):
            fmt_s = start.replace("-", "") if fmt == "YYYYMMDD" else start
            fmt_e = end.replace("-", "") if fmt == "YYYYMMDD" else end
            params[n] = [fmt_s, fmt_e]
        elif any(tok in nl for tok in ["start", "begin", "from"]):
            params[n] = start
            if fmt == "YYYYMMDD":
                params[n] = start.replace("-", "")
        elif any(tok in nl for tok in ["end", "to"]):
            params[n] = end
            if fmt == "YYYYMMDD":
                params[n] = end.replace("-", "")
        else:
            if any(tok in nl for tok in ["se", "range"]):
                fmt_s = start.replace("-", "") if fmt == "YYYYMMDD" else start
                fmt_e = end.replace("-", "") if fmt == "YYYYMMDD" else end
                params[n] = [fmt_s, fmt_e]
            else:
                params[n] = start
                if fmt == "YYYYMMDD":
                    params[n] = start.replace("-", "")


def set_paging_params(params: Dict[str, Any], page_no: int, page_size: int) -> None:
    use_int = METHOD == "POST"
    def _val(v):
        return v if use_int else str(v)
    for k in ["pageHelp.pageNo", "pageNo", "page", "current", "p", "pageNum"]:
        if k in params:
            params[k] = _val(page_no)
            break
    for k in ["pageHelp.pageSize", "pageSize", "limit", "per_page", "page_size"]:
        if k in params:
            params[k] = _val(page_size)
            break
    for k in ["offset", "start", "startRow"]:
        if k in params:
            params[k] = _val((page_no - 1) * page_size)
            break


def build_headers() -> Dict[str, str]:
    from urllib.parse import urlsplit as _us
    _p = _us(API_URL)
    origin = "{}://{}".format(_p.scheme or "https", _p.netloc)
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": TARGET_URL or origin + "/",
        "X-Request-Type": "ajax",
        "X-Requested-With": "XMLHttpRequest",
    }
    if METHOD == "POST":
        h["Origin"] = origin
    h.update(EXTRA_HEADERS or {})
    return h


def request_page(page_no: int, page_size: int) -> Any:
    u = urlsplit(API_URL)
    base_url = urlunsplit((u.scheme, u.netloc, u.path, "", ""))

    params = dict(BASE_PARAMS)
    set_date_params(params, START_DATE, END_DATE)
    set_paging_params(params, page_no, page_size)

    if "jsonCallBack" in params:
        params["jsonCallBack"] = "jsonCallback"
    elif "callback" in params:
        params["callback"] = "jsonCallback"
    elif "sse.com.cn" in (u.netloc or ""):
        params["jsonCallBack"] = "jsonCallback"

    headers = build_headers()
    if METHOD == "POST":
        headers["Content-Type"] = "application/json"
        resp = requests.post(base_url, json=params, headers=headers, timeout=20)
    else:
        resp = requests.get(base_url, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    return parse_json_or_jsonp(resp.text)


def _safe_str(v):
    """Safely convert value to string (handles list, None, etc.)"""
    if v is None:
        return ""
    if isinstance(v, list):
        return str(v[0]).strip() if v else ""
    return str(v).strip()


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    page_no = 1
    page_size = int(BASE_PARAMS.get("pageSize") or BASE_PARAMS.get("pageHelp.pageSize") or BASE_PARAMS.get("limit") or 25)
    all_items: List[Dict[str, Any]] = []

    for _ in range(MAX_PAGES):
        print("[INFO] request page {}...".format(page_no))
        try:
            data = request_page(page_no, page_size)
        except Exception as e:
            print("[ERROR] request failed:", e)
            break

        items, meta = find_items_in_response(data)
        if not items:
            print("[INFO] no data on this page, stop")
            break

        # Filter by date using auto-detected DATE_FIELDS
        kept: List[Dict[str, Any]] = []
        for it in items:
            matched = False
            for dk in DATE_FIELDS:
                if dk in it and it.get(dk):
                    d = normalize_date(str(it.get(dk)))
                    if d and is_date_in_range(d, START_DATE, END_DATE):
                        it["_pygen_date"] = d
                        kept.append(it)
                    matched = True
                    break
            if not matched:
                kept.append(it)

        all_items.extend(kept)
        print("[INFO] page raw={}, kept={}, total={}".format(len(items), len(kept), len(all_items)))

        pc = meta.get("pageCount")
        if isinstance(pc, int) and pc > 0 and page_no >= pc:
            print("[INFO] reached last page")
            break
        total = meta.get("total")
        if isinstance(total, (int, float)) and total > 0:
            max_page = int((total + page_size - 1) / page_size)
            if page_no >= max_page:
                print("[INFO] reached last page (total={}, pageSize={})".format(int(total), page_size))
                break
        if len(items) < page_size:
            print("[INFO] partial page, stop")
            break

        page_no += 1
        time.sleep(SLEEP_SECONDS)

    # Convert to reports using auto-detected FIELD_MAPPINGS
    reports: List[Dict[str, Any]] = []
    for it in all_items:
        # Title: use auto-detected TITLE_FIELDS
        name = ""
        for nk in TITLE_FIELDS:
            if nk in it and it.get(nk):
                name = _safe_str(it[nk])
                break

        # Security code/name: use auto-detected CODE_FIELDS / NAME_FIELDS
        sec_code = ""
        for ck in CODE_FIELDS:
            if ck in it and it.get(ck):
                sec_code = _safe_str(it[ck])
                break
        sec_name = ""
        for nk2 in NAME_FIELDS:
            if nk2 in it and it.get(nk2):
                sec_name = _safe_str(it[nk2])
                break
        if sec_code and name and sec_code not in name:
            name = "[{}] {}".format(sec_code, name)

        # Date: use auto-detected DATE_FIELDS
        date_val = it.get("_pygen_date") or ""
        if not date_val:
            for dk in DATE_FIELDS:
                if dk in it and it.get(dk):
                    date_val = normalize_date(str(it[dk]))
                    break

        # Download URL: use auto-detected URL_FIELDS
        download_url = ""
        for uk in URL_FIELDS:
            if uk in it and it.get(uk):
                u = _safe_str(it[uk])
                if u.startswith("//"):
                    u = "https:" + u
                elif u.startswith("/"):
                    from urllib.parse import urlsplit as _us
                    _p = _us(TARGET_URL or API_URL)
                    host = _p.netloc
                    # 智能域名映射：某些网站 PDF 文件托管在子域名上
                    # 如深交所: /disc/... 路径的文件在 disc.szse.cn 而非 www.szse.cn
                    if host and u.startswith("/disc/") and "szse.cn" in host:
                        host = "disc.szse.cn"
                    u = "{}://{}{}".format(_p.scheme or "https", host, u)
                download_url = u
                break

        file_type = ""
        if download_url:
            ext = download_url.rsplit(".", 1)[-1].lower() if "." in download_url else ""
            if ext and len(ext) <= 5:
                file_type = ext
        if not file_type:
            file_type = "pdf"

        reports.append({
            "name": name or "unknown",
            "date": date_val,
            "downloadUrl": download_url,
            "fileType": file_type,
        })

    # 构建下载头信息（供后续下载 PDF/附件时使用，绕过防盗链）
    # 从目标页面 URL 派生 Referer，这是最自然的反防盗链策略
    _dl_parsed = urlsplit(TARGET_URL or API_URL)
    _dl_origin = "{}://{}".format(_dl_parsed.scheme or "https", _dl_parsed.netloc)
    download_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": TARGET_URL or _dl_origin + "/",
    }

    out = {
        "total": len(reports),
        "crawlTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dateRange": "{} ~ {}".format(START_DATE, END_DATE),
        "downloadHeaders": download_headers,
        "reports": reports,
    }

    out_path = os.path.join(OUTPUT_DIR, "date_api_items_{}_{}.json".format(START_DATE, END_DATE))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("[SUCCESS] saved:", out_path, "(reports={})".format(len(reports)))


if __name__ == "__main__":
    main()
'''

    return template.replace("__CONFIG_JSON__", _json_dumps(payload))
