#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日期提取韧性层 - PyGen 通用模块

解决问题：
1. 不同网站表格列索引不一致（日期可能在第3、4、5列等任意位置）
2. 日期可能在 span、div、td 直接文本等不同元素中
3. 日期格式多样（YYYY-MM-DD、YYYY/MM/DD、YYYY年MM月DD日等）
4. 需要日期与条目的可靠关联（downloadUrl > title > 顺序）

设计原则：
1. 智能扫描：不假设固定列索引，自动在整行中搜索日期
2. 多选择器尝试：依次尝试 span、time、直接文本等
3. 多关联策略：优先用 URL 关联，其次标题精确匹配
4. 健壮降级：任何环节失败都不抛异常，返回空结果
"""

import re
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime

# 日期正则表达式（支持多种格式）
DATE_PATTERNS = [
    # YYYY-MM-DD 或 YYYY/MM/DD 或 YYYY.MM.DD
    re.compile(r'(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})'),
    # YYYY年MM月DD日
    re.compile(r'(\d{4})年(\d{1,2})月(\d{1,2})日'),
    # MM-DD-YYYY 或 DD-MM-YYYY（较少见，谨慎使用）
    re.compile(r'(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{4})'),
]

# 日期字段名关键词（用于 API 响应）
DATE_FIELD_KEYWORDS = [
    'date', 'time', 'pubdate', 'publishdate', 'publishtime', 'releasedate',
    'createtime', 'createdate', 'addtime', 'adddate', 'updatetime', 'updatedate',
    'rankdate', 'inputtime', 'newsdate', 'postdate', 'datetime', 'pub_date',
    'release_date', 'create_time', 'update_time', 'add_time', 'post_time',
    '日期', '时间', '发布日期', '发布时间', '创建时间', '更新时间'
]


def normalize_date(date_str: str) -> str:
    """
    将各种格式的日期字符串标准化为 YYYY-MM-DD 格式
    
    Args:
        date_str: 原始日期字符串
        
    Returns:
        标准化的日期字符串，如果无法解析则返回空字符串
    """
    if not date_str:
        return ""
    
    date_str = date_str.strip()
    
    for pattern in DATE_PATTERNS:
        match = pattern.search(date_str)
        if match:
            groups = match.groups()
            
            # 判断哪个是年份（4位数字）
            if len(groups[0]) == 4:
                year, month, day = groups[0], groups[1], groups[2]
            elif len(groups[2]) == 4:
                # MM-DD-YYYY 或 DD-MM-YYYY 格式
                year = groups[2]
                # 假设 MM-DD-YYYY（美式）
                month, day = groups[0], groups[1]
                # 如果月份 > 12，则是 DD-MM-YYYY（欧式）
                if int(month) > 12:
                    month, day = day, month
            else:
                continue
            
            try:
                # 验证日期有效性
                year_int = int(year)
                month_int = int(month)
                day_int = int(day)
                
                # 基本范围检查
                if not (1900 <= year_int <= 2100):
                    continue
                if not (1 <= month_int <= 12):
                    continue
                if not (1 <= day_int <= 31):
                    continue
                
                return f"{year}-{str(month).zfill(2)}-{str(day).zfill(2)}"
            except (ValueError, TypeError):
                continue
    
    return ""


def extract_date_from_text(text: str) -> str:
    """
    从文本中提取日期
    
    Args:
        text: 包含日期的文本
        
    Returns:
        标准化的日期字符串
    """
    return normalize_date(text)


def find_date_in_element_text(element_texts: List[str]) -> Tuple[str, int]:
    """
    在一组元素文本中查找日期
    
    Args:
        element_texts: 元素文本列表
        
    Returns:
        (日期字符串, 找到日期的索引)，未找到则返回 ("", -1)
    """
    for i, text in enumerate(element_texts):
        date = extract_date_from_text(text)
        if date:
            return (date, i)
    return ("", -1)


def smart_find_date_in_row(tds: List[Any], playwright_mode: bool = False) -> str:
    """
    智能在表格行中查找日期（核心函数）
    
    不假设固定列索引，扫描整行所有单元格查找日期。
    
    Args:
        tds: 表格单元格列表（可以是 BeautifulSoup 的 Tag 或 Playwright 的 ElementHandle）
        playwright_mode: 是否为 Playwright 模式
        
    Returns:
        找到的日期字符串，未找到返回空字符串
    """
    for td in tds:
        try:
            if playwright_mode:
                # Playwright 模式
                # 1. 先尝试 span、time 等常见日期容器
                for selector in ['span', 'time', '.date', '.time', '[class*="date"]', '[class*="time"]']:
                    try:
                        date_elem = td.query_selector(selector)
                        if date_elem:
                            text = date_elem.inner_text().strip()
                            date = extract_date_from_text(text)
                            if date:
                                return date
                    except Exception:
                        pass
                
                # 2. 直接获取 td 文本
                try:
                    text = td.inner_text().strip()
                    date = extract_date_from_text(text)
                    if date:
                        return date
                except Exception:
                    pass
            else:
                # BeautifulSoup 模式
                # 1. 先尝试 span、time 等常见日期容器
                for tag in ['span', 'time']:
                    date_elem = td.select_one(tag)
                    if date_elem:
                        text = date_elem.get_text(strip=True)
                        date = extract_date_from_text(text)
                        if date:
                            return date
                
                # 2. 尝试有 date/time 类名的元素
                for selector in ['.date', '.time', '[class*="date"]', '[class*="time"]']:
                    try:
                        date_elem = td.select_one(selector)
                        if date_elem:
                            text = date_elem.get_text(strip=True)
                            date = extract_date_from_text(text)
                            if date:
                                return date
                    except Exception:
                        pass
                
                # 3. 直接获取 td 文本
                text = td.get_text(strip=True)
                date = extract_date_from_text(text)
                if date:
                    return date
                    
        except Exception:
            continue
    
    return ""


def extract_dates_from_table_rows(
    rows: List[Any],
    title_col_idx: int = 0,
    download_col_idx: int = -1,
    playwright_mode: bool = False,
    base_url: str = ""
) -> List[Dict[str, str]]:
    """
    从表格行中提取日期，并与标题/下载链接关联
    
    Args:
        rows: 表格行列表
        title_col_idx: 标题所在列索引
        download_col_idx: 下载链接所在列索引（-1 表示自动查找）
        playwright_mode: 是否为 Playwright 模式
        base_url: 用于补全相对链接的基础 URL
        
    Returns:
        包含 {title, downloadUrl, date} 的字典列表
    """
    results = []
    
    for row in rows:
        try:
            if playwright_mode:
                tds = row.query_selector_all('td')
            else:
                tds = row.select('td')
            
            if len(tds) < 2:
                continue
            
            # 提取标题
            title = ""
            title_elem = None
            if title_col_idx < len(tds):
                td = tds[title_col_idx]
                if playwright_mode:
                    title_elem = td.query_selector('a')
                    title = title_elem.inner_text().strip() if title_elem else td.inner_text().strip()
                else:
                    title_elem = td.select_one('a')
                    title = title_elem.get_text(strip=True) if title_elem else td.get_text(strip=True)
            
            if not title:
                continue
            
            # 提取下载链接
            download_url = ""
            
            # 策略1：从标题列的链接提取
            if title_elem:
                try:
                    if playwright_mode:
                        href = title_elem.get_attribute('href')
                    else:
                        href = title_elem.get('href', '')
                    
                    if href:
                        if href.startswith('/'):
                            download_url = base_url.rstrip('/') + href
                        elif href.startswith('http'):
                            download_url = href
                        else:
                            download_url = href
                except Exception:
                    pass
            
            # 策略2：从指定的下载列或自动查找
            if not download_url:
                search_cols = [download_col_idx] if download_col_idx >= 0 else range(len(tds) - 1, -1, -1)
                for col_idx in search_cols:
                    if col_idx >= len(tds):
                        continue
                    td = tds[col_idx]
                    try:
                        if playwright_mode:
                            a = td.query_selector('a')
                            if a:
                                href = a.get_attribute('href')
                        else:
                            a = td.select_one('a')
                            if a:
                                href = a.get('href', '')
                        
                        if href and ('.pdf' in href.lower() or '.doc' in href.lower() or 
                                     '.xls' in href.lower() or '/download' in href.lower() or
                                     '/uploads/' in href.lower() or '/files/' in href.lower()):
                            if href.startswith('/'):
                                download_url = base_url.rstrip('/') + href
                            elif href.startswith('http'):
                                download_url = href
                            else:
                                download_url = href
                            break
                    except Exception:
                        continue
            
            # 智能提取日期
            date = smart_find_date_in_row(tds, playwright_mode)
            
            results.append({
                "title": title,
                "downloadUrl": download_url,
                "date": date
            })
            
        except Exception:
            continue
    
    return results


def merge_dates_by_association(
    reports: List[Dict[str, Any]],
    date_map: Dict[str, str]
) -> List[Dict[str, Any]]:
    """
    通过关联将日期合并到报告记录中
    
    关联策略（按优先级）：
    1. downloadUrl 精确匹配
    2. title 精确匹配（去空格后）
    
    Args:
        reports: 报告记录列表，每条记录需包含 name 和 downloadUrl
        date_map: 日期映射字典 {关联键: 日期}
        
    Returns:
        更新了 date 字段的报告列表
    """
    for report in reports:
        if report.get("date"):
            continue  # 已有日期，跳过
        
        # 尝试用 downloadUrl 关联
        download_url = report.get("downloadUrl", "")
        if download_url and download_url in date_map:
            report["date"] = date_map[download_url]
            continue
        
        # 尝试用 name/title 关联
        name = report.get("name", "") or report.get("title", "")
        name_clean = name.replace(" ", "").replace("\u3000", "")  # 去除空格
        
        if name and name in date_map:
            report["date"] = date_map[name]
            continue
        
        if name_clean and name_clean in date_map:
            report["date"] = date_map[name_clean]
            continue
        
        # 尝试模糊匹配（标题包含关系）
        for key, date in date_map.items():
            key_clean = key.replace(" ", "").replace("\u3000", "")
            if name_clean and key_clean and (name_clean in key_clean or key_clean in name_clean):
                report["date"] = date
                break
    
    return reports


def extract_date_from_api_item(item: Dict[str, Any]) -> str:
    """
    从 API 响应的单条记录中提取日期
    
    会自动检测常见的日期字段名，并处理时间戳转换。
    
    Args:
        item: API 响应中的单条记录
        
    Returns:
        标准化的日期字符串
    """
    if not item or not isinstance(item, dict):
        return ""
    
    # 按优先级检查常见日期字段
    priority_fields = [
        'rankdate', 'publishDate', 'publishdate', 'pubDate', 'pubdate',
        'releaseDate', 'releasedate', 'date', 'newsDate', 'newsdate',
        'createTime', 'createtime', 'createDate', 'createdate',
        'addTime', 'addtime', 'inputTime', 'inputtime',
        'updateTime', 'updatetime', 'postDate', 'postdate'
    ]
    
    for field in priority_fields:
        if field in item:
            value = item[field]
            date = _parse_date_value(value)
            if date:
                return date
    
    # 遍历所有字段查找可能的日期
    for key, value in item.items():
        key_lower = key.lower()
        if any(kw in key_lower for kw in ['date', 'time', '日期', '时间']):
            date = _parse_date_value(value)
            if date:
                return date
    
    return ""


def _parse_date_value(value: Any) -> str:
    """
    解析日期值（支持字符串、时间戳）
    
    Args:
        value: 日期值
        
    Returns:
        标准化的日期字符串
    """
    if value is None:
        return ""
    
    if isinstance(value, (int, float)):
        # 可能是时间戳
        try:
            # 毫秒时间戳
            if value > 10000000000:
                value = value / 1000
            dt = datetime.fromtimestamp(value)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    
    if isinstance(value, str):
        return normalize_date(value)
    
    return ""


def is_date_in_range(date_str: str, start_date: str, end_date: str) -> bool:
    """
    检查日期是否在指定范围内
    
    Args:
        date_str: 待检查的日期（YYYY-MM-DD 格式）
        start_date: 开始日期（YYYY-MM-DD 格式）
        end_date: 结束日期（YYYY-MM-DD 格式）
        
    Returns:
        是否在范围内
    """
    if not date_str:
        return False
    
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        start_obj = datetime.strptime(start_date, "%Y-%m-%d")
        end_obj = datetime.strptime(end_date, "%Y-%m-%d")
        return start_obj <= date_obj <= end_obj
    except (ValueError, TypeError):
        return False


# === 可注入的代码模板 ===

INJECTABLE_DATE_UTILS = '''
# === PyGen 注入：日期提取韧性层（通用） ===
# 解决问题：不同网站表格结构差异大，日期可能在任意列、任意元素中
# 设计原则：智能扫描整行、多选择器尝试、健壮降级
import re as _pygen_re
from datetime import datetime as _pygen_datetime

_PYGEN_DATE_PATTERNS = [
    _pygen_re.compile(r'(\\d{4})[-/\\.](\\d{1,2})[-/\\.](\\d{1,2})'),
    _pygen_re.compile(r'(\\d{4})年(\\d{1,2})月(\\d{1,2})日'),
]

def _pygen_normalize_date(date_str: str) -> str:
    """将各种格式的日期字符串标准化为 YYYY-MM-DD 格式"""
    if not date_str:
        return ""
    date_str = date_str.strip()
    for pattern in _PYGEN_DATE_PATTERNS:
        match = pattern.search(date_str)
        if match:
            groups = match.groups()
            if len(groups[0]) == 4:
                year, month, day = groups[0], groups[1], groups[2]
            else:
                continue
            try:
                if 1900 <= int(year) <= 2100 and 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
                    return f"{year}-{str(month).zfill(2)}-{str(day).zfill(2)}"
            except:
                pass
    return ""

def _pygen_smart_find_date_in_row_bs4(tds) -> str:
    """BeautifulSoup模式：智能在表格行中查找日期（扫描所有列）"""
    for td in tds:
        try:
            # 1. 先尝试 span、time 等常见日期容器
            for tag in ['span', 'time']:
                elem = td.select_one(tag) if hasattr(td, 'select_one') else None
                if elem:
                    text = elem.get_text(strip=True)
                    date = _pygen_normalize_date(text)
                    if date:
                        return date
            # 2. 尝试有 date/time 类名的元素
            for sel in ['.date', '.time', '[class*="date"]', '[class*="time"]']:
                try:
                    elem = td.select_one(sel) if hasattr(td, 'select_one') else None
                    if elem:
                        text = elem.get_text(strip=True)
                        date = _pygen_normalize_date(text)
                        if date:
                            return date
                except:
                    pass
            # 3. 直接获取 td 文本
            text = td.get_text(strip=True) if hasattr(td, 'get_text') else str(td)
            date = _pygen_normalize_date(text)
            if date:
                return date
        except:
            continue
    return ""

def _pygen_smart_find_date_in_row_pw(tds) -> str:
    """Playwright模式：智能在表格行中查找日期（扫描所有列）"""
    for td in tds:
        try:
            # 1. 先尝试 span、time 等常见日期容器
            for selector in ['span', 'time', '.date', '.time', '[class*="date"]', '[class*="time"]']:
                try:
                    elem = td.query_selector(selector)
                    if elem:
                        text = elem.inner_text().strip()
                        date = _pygen_normalize_date(text)
                        if date:
                            return date
                except:
                    pass
            # 2. 直接获取 td 文本
            try:
                text = td.inner_text().strip()
                date = _pygen_normalize_date(text)
                if date:
                    return date
            except:
                pass
        except:
            continue
    return ""

def _pygen_extract_date_from_api_item(item: dict) -> str:
    """从 API 响应的单条记录中提取日期"""
    if not item or not isinstance(item, dict):
        return ""
    priority_fields = [
        'rankdate', 'publishDate', 'publishdate', 'pubDate', 'pubdate',
        'releaseDate', 'releasedate', 'date', 'newsDate', 'newsdate',
        'createTime', 'createtime', 'createDate', 'createdate',
        'addTime', 'addtime', 'inputTime', 'inputtime',
    ]
    for field in priority_fields:
        if field in item:
            value = item[field]
            if value is None:
                continue
            if isinstance(value, (int, float)):
                try:
                    if value > 10000000000:
                        value = value / 1000
                    dt = _pygen_datetime.fromtimestamp(value)
                    return dt.strftime("%Y-%m-%d")
                except:
                    pass
            if isinstance(value, str):
                date = _pygen_normalize_date(value)
                if date:
                    return date
    # 遍历所有字段查找
    for key, value in item.items():
        if any(kw in key.lower() for kw in ['date', 'time', '日期', '时间']):
            if isinstance(value, str):
                date = _pygen_normalize_date(value)
                if date:
                    return date
    return ""

def _pygen_merge_dates_by_association(reports: list, date_map: dict) -> list:
    """通过关联将日期合并到报告记录中（downloadUrl > title 精确匹配 > 模糊匹配）"""
    for report in reports:
        if report.get("date"):
            continue
        download_url = report.get("downloadUrl", "")
        if download_url and download_url in date_map:
            report["date"] = date_map[download_url]
            continue
        name = report.get("name", "") or report.get("title", "")
        name_clean = name.replace(" ", "").replace("\\u3000", "")
        if name and name in date_map:
            report["date"] = date_map[name]
            continue
        if name_clean and name_clean in date_map:
            report["date"] = date_map[name_clean]
            continue
        for key, date in date_map.items():
            key_clean = key.replace(" ", "").replace("\\u3000", "")
            if name_clean and key_clean and (name_clean in key_clean or key_clean in name_clean):
                report["date"] = date
                break
    return reports

def _pygen_is_date_in_range(date_str: str, start_date: str, end_date: str) -> bool:
    """检查日期是否在指定范围内"""
    if not date_str:
        return False
    try:
        date_obj = _pygen_datetime.strptime(date_str, "%Y-%m-%d")
        start_obj = _pygen_datetime.strptime(start_date, "%Y-%m-%d")
        end_obj = _pygen_datetime.strptime(end_date, "%Y-%m-%d")
        return start_obj <= date_obj <= end_obj
    except:
        return False
# === PyGen 日期提取韧性层结束 ===
'''


def get_injectable_code() -> str:
    """获取可注入的日期提取工具代码"""
    return INJECTABLE_DATE_UTILS

