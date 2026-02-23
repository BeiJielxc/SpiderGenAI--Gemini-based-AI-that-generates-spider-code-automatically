"""
PyGen 验证器模块 - 代码静态检查 + 输出结构验证

提供两层验证：
1. StaticCodeValidator: 在代码执行前检查常见错误模式
2. OutputValidator: 验证爬虫输出的 JSON 数据结构

使用方式：
    from validator import StaticCodeValidator, OutputValidator, CrawlRecord
    
    # 静态检查
    validator = StaticCodeValidator()
    issues = validator.validate(code)
    
    # 输出验证
    output_validator = OutputValidator()
    is_valid, errors = output_validator.validate_output(json_data)
"""

import re
import ast
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass, field
from enum import Enum
from pydantic import BaseModel, field_validator, ValidationError
from datetime import datetime


# ============================================================================
# Pydantic 输出模型
# ============================================================================

class CrawlRecord(BaseModel):
    """单条爬取记录的数据模型"""
    id: Optional[str] = None
    name: str
    date: str = ""
    downloadUrl: str
    fileType: str = "pdf"
    
    @field_validator('name')
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v or len(v.strip()) < 2:
            raise ValueError('name 字段不能为空或过短（至少2个字符）')
        return v.strip()
    
    @field_validator('downloadUrl')
    @classmethod
    def url_valid(cls, v: str) -> str:
        if not v:
            raise ValueError('downloadUrl 不能为空')
        if not v.startswith(('http://', 'https://', '//')):
            raise ValueError(f'downloadUrl 必须是有效URL，当前值: {v[:50]}...')
        return v
    
    @field_validator('date')
    @classmethod
    def date_format_valid(cls, v: str) -> str:
        if not v:
            return ""  # 允许空日期
        # 标准化常见日期格式
        v = v.replace('/', '-').replace('.', '-')
        # 检查基本格式
        if not re.match(r'^\d{4}-\d{1,2}-\d{1,2}', v):
            raise ValueError(f'date 格式无效，应为 YYYY-MM-DD，当前值: {v}')
        return v


class CrawlOutput(BaseModel):
    """爬取输出的完整数据模型"""
    total: int
    crawlTime: str
    reports: List[CrawlRecord]
    
    @field_validator('total')
    @classmethod
    def total_must_be_positive(cls, v: int) -> int:
        if v < 0:
            raise ValueError('total 不能为负数')
        return v
    
    @field_validator('reports')
    @classmethod
    def reports_not_empty(cls, v: List[CrawlRecord]) -> List[CrawlRecord]:
        if len(v) == 0:
            raise ValueError('reports 列表不能为空')
        return v


# ============================================================================
# 静态代码验证器
# ============================================================================

class IssueSeverity(Enum):
    """问题严重程度"""
    ERROR = "error"       # 必须修复
    WARNING = "warning"   # 建议修复
    INFO = "info"         # 提示信息


@dataclass
class CodeIssue:
    """代码问题"""
    code: str                    # 问题代码（如 ERR_001）
    severity: IssueSeverity      # 严重程度
    message: str                 # 问题描述
    line_number: Optional[int] = None   # 行号（如果可检测）
    suggestion: str = ""         # 修复建议


class StaticCodeValidator:
    """
    静态代码验证器 - 在运行前检测常见错误模式（支持上下文感知）
    
    检查项：
    1. 硬编码列索引 (tds[N]) - 结合 page_structure 智能判断
    2. 链式调用空指针 (find().find_all())
    3. 输出字段名错误 (title vs name)
    4. SPA 页面用 requests - 结合 spaHints 智能判断
    5. 日期提取策略问题 - 结合 dateColumnHints 智能判断
    
    v2.0 更新：支持 page_structure 上下文感知，避免误伤正确代码
    """
    
    def __init__(self):
        self.issues: List[CodeIssue] = []
        self.page_structure: Optional[Dict[str, Any]] = None
        
    def validate(self, code: str, page_structure: Optional[Dict[str, Any]] = None) -> List[CodeIssue]:
        """
        验证代码，返回发现的问题列表（支持上下文感知）
        
        Args:
            code: Python 代码字符串
            page_structure: 页面结构信息（来自 browser.analyze_page_structure()）
                           包含表格列数、日期列位置、SPA 线索等
            
        Returns:
            问题列表
        """
        self.issues = []
        self.page_structure = page_structure

        # 0) 语法/编译期检查（必须最先做）
        # 例如：SyntaxError: name 'X' is used prior to global declaration
        # 这类问题仅靠正则规则无法覆盖，且运行前必须拦截。
        if not self._check_python_syntax(code):
            # 语法都不通过，后续规则检查没有意义
            return self.issues
        
        # 执行各项检查（部分检查会使用 self.page_structure 进行智能判断）
        self._check_hardcoded_column_index(code)
        self._check_chained_find_calls(code)
        self._check_output_field_names(code)
        self._check_spa_requests(code)
        self._check_date_from_title(code)
        self._check_keeps_undated_records(code)
        self._check_single_page_dates(code)
        self._check_missing_date_extraction(code)
        self._check_unicode_print_chars(code)
        
        return self.issues
    
    def get_page_structure_summary(self) -> str:
        """
        生成页面结构摘要（用于 LLM 修复时提供上下文）
        
        Returns:
            页面结构的文本摘要
        """
        if not self.page_structure:
            return ""
        
        lines = ["## 页面结构参考（帮助你正确修复）\n"]
        
        # 表格信息
        tables = self.page_structure.get('tables', [])
        if tables:
            table = tables[0]  # 取第一个表格
            lines.append(f"- 表格列数: {table.get('columnCount', '未知')}")
            headers = table.get('headers', [])
            if headers:
                lines.append(f"- 表头: {headers[:8]}")
            
            # 日期列信息（关键！）
            date_indices = table.get('dateColumnIndices', [])
            date_hints = table.get('dateColumnHints', [])
            if date_indices:
                lines.append(f"- **日期列位置**: 索引 {date_indices} (从0开始)")
                for hint in date_hints[:3]:
                    lines.append(f"  - 第{hint.get('columnIndex')}列「{hint.get('headerText', '?')}」检测到{hint.get('occurrences', '?')}次日期")
            else:
                lines.append("- 日期列位置: 未检测到明确的日期列")
            
            # 下载链接列
            download_indices = table.get('downloadColumnIndices', [])
            if download_indices:
                lines.append(f"- 下载链接列: 索引 {download_indices}")
            
            # 首行预览
            first_row = table.get('firstRowPreview', [])
            if first_row:
                lines.append(f"- 首行数据预览: {first_row[:6]}")
        
        # SPA 线索
        spa_hints = self.page_structure.get('spaHints', {})
        if spa_hints:
            if spa_hints.get('hasHashRoute') or spa_hints.get('hasAppRoot'):
                lines.append(f"- **SPA 页面**: hasHashRoute={spa_hints.get('hasHashRoute')}, hasAppRoot={spa_hints.get('hasAppRoot')}")
        
        # 日期元素
        date_elements = self.page_structure.get('dateElements', [])
        if date_elements:
            lines.append(f"- 页面中检测到 {len(date_elements)} 个日期元素")
            for de in date_elements[:3]:
                lines.append(f"  - 「{de.get('dateValue')}」位于 {de.get('selector')}")
        
        return "\n".join(lines)

    def _check_python_syntax(self, code: str) -> bool:
        """检查 Python 语法是否可编译（编译期错误应直接拦截）"""
        try:
            # compile 比 ast.parse 更贴近真实执行（同样能捕获 SyntaxError/IndentationError）
            compile(code, "<pygen_generated>", "exec")
            return True
        except SyntaxError as e:
            # e.lineno / e.offset / e.text 可能为 None
            line_no = getattr(e, "lineno", None)
            msg = getattr(e, "msg", None) or str(e)
            detail = msg
            if line_no:
                detail = f"{msg}（行 {line_no}）"
            self.issues.append(CodeIssue(
                code="ERR_SYNTAX",
                severity=IssueSeverity.ERROR,
                message=f"Python 语法错误: {detail}",
                line_number=line_no,
                suggestion="修复语法/缩进/全局声明错误后再运行（例如 global 必须在函数内首次使用变量之前声明）"
            ))
            return False
        except Exception as e:
            # 极少数情况下 compile 可能抛出其他异常（保守处理为错误）
            self.issues.append(CodeIssue(
                code="ERR_SYNTAX",
                severity=IssueSeverity.ERROR,
                message=f"代码无法编译: {str(e)}",
                suggestion="请检查生成代码是否包含不完整的语句/非法字符"
            ))
            return False
    
    def has_errors(self) -> bool:
        """是否存在必须修复的错误"""
        return any(i.severity == IssueSeverity.ERROR for i in self.issues)
    
    def has_warnings(self) -> bool:
        """是否存在警告"""
        return any(i.severity == IssueSeverity.WARNING for i in self.issues)
    
    def get_summary(self) -> str:
        """获取问题摘要"""
        if not self.issues:
            return "✅ 代码检查通过，未发现问题"
        
        errors = [i for i in self.issues if i.severity == IssueSeverity.ERROR]
        warnings = [i for i in self.issues if i.severity == IssueSeverity.WARNING]
        
        lines = []
        if errors:
            lines.append(f"🔴 发现 {len(errors)} 个错误（必须修复）:")
            for e in errors:
                lines.append(f"  - [{e.code}] {e.message}")
        if warnings:
            lines.append(f"🟡 发现 {len(warnings)} 个警告（建议修复）:")
            for w in warnings:
                lines.append(f"  - [{w.code}] {w.message}")
        
        return "\n".join(lines)
    
    def get_repair_prompt(self) -> str:
        """生成用于 LLM 修复的提示"""
        if not self.issues:
            return ""
        
        lines = ["【代码检查发现以下问题，请修复】\n"]
        
        for issue in self.issues:
            severity_icon = "🔴" if issue.severity == IssueSeverity.ERROR else "🟡"
            lines.append(f"{severity_icon} [{issue.code}] {issue.message}")
            if issue.suggestion:
                lines.append(f"   修复建议: {issue.suggestion}")
            lines.append("")
        
        lines.append("请修正以上问题并重新生成完整的 Python 代码。")
        return "\n".join(lines)
    
    # -------------------------------------------------------------------------
    # 具体检查方法
    # -------------------------------------------------------------------------
    
    def _check_hardcoded_column_index(self, code: str) -> None:
        """
        检查硬编码列索引（上下文感知版本）
        
        改进：结合 page_structure 中的 dateColumnIndices 判断硬编码是否正确
        - 如果硬编码的列索引确实是日期列，则不报错
        - 如果硬编码的列索引不是日期列，则报警告并提示正确位置
        - 如果没有 page_structure，降级为警告（而非错误）
        """
        # 如果使用了智能日期扫描函数，跳过检查
        if "_pygen_smart_find_date_in_row" in code:
            return
        
        code_lower = code.lower()
        if "date" not in code_lower:
            return  # 如果代码中没有 date 相关内容，跳过
        
        # 提取代码中所有 tds[N] 或 cols[N] 的使用
        index_pattern = re.compile(r'(tds|cols)\[(\d+)\]')
        matches = list(index_pattern.finditer(code))
        
        if not matches:
            return
        
        # 获取页面结构中的日期列信息
        date_col_indices = []
        column_count = None
        headers = []
        
        if self.page_structure and self.page_structure.get('tables'):
            table = self.page_structure['tables'][0]
            date_col_indices = table.get('dateColumnIndices', [])
            column_count = table.get('columnCount')
            headers = table.get('headers', [])
        
        for match in matches:
            var_name = match.group(1)  # tds 或 cols
            col_idx = int(match.group(2))
            
            # 检查上下文是否与日期相关（前后50字符）
            start = max(0, match.start() - 50)
            end = min(len(code), match.end() + 50)
            context = code[start:end].lower()
            
            if 'date' not in context:
                continue  # 不是日期提取，跳过
            
            # 有 page_structure 时进行智能检查
            if self.page_structure and self.page_structure.get('tables'):
                # 检查1：索引是否越界？
                if column_count and col_idx >= column_count:
                    self.issues.append(CodeIssue(
                        code="ERR_001",
                        severity=IssueSeverity.ERROR,
                        message=f"列索引越界：{var_name}[{col_idx}]，表格只有 {column_count} 列（索引0-{column_count-1}）",
                        suggestion=f"请检查列索引是否正确，日期列位置: {date_col_indices}"
                    ))
                    return
                
                # 检查2：这列是否是日期列？
                if date_col_indices:
                    if col_idx in date_col_indices:
                        # ✅ 正确！这列确实是日期列，不报错
                        # 可以添加一个 INFO 级别的确认
                        pass
                    else:
                        # ⚠️ 这列不是日期列，报警告
                        header_name = headers[col_idx] if col_idx < len(headers) else '?'
                        correct_cols = [f"索引{i}「{headers[i] if i < len(headers) else '?'}」" 
                                       for i in date_col_indices]
                        self.issues.append(CodeIssue(
                            code="ERR_001",
                            severity=IssueSeverity.WARNING,
                            message=f"{var_name}[{col_idx}] 对应列「{header_name}」，但检测到的日期列是: {', '.join(correct_cols)}",
                            suggestion=f"建议改为 {var_name}[{date_col_indices[0]}] 或使用 _pygen_smart_find_date_in_row 智能扫描"
                        ))
                        return
                else:
                    # 表格中没有检测到日期列，给出提示
                    self.issues.append(CodeIssue(
                        code="ERR_001",
                        severity=IssueSeverity.INFO,
                        message=f"使用了 {var_name}[{col_idx}] 提取日期，但页面结构中未检测到明确的日期列",
                        suggestion="如果表格结构固定，硬编码可能是正确的；否则建议使用智能扫描"
                    ))
                    return
            else:
                # 没有 page_structure，降级为警告（而非错误），避免误伤
                self.issues.append(CodeIssue(
                    code="ERR_001",
                    severity=IssueSeverity.WARNING,
                    message=f"检测到硬编码列索引 {var_name}[{col_idx}] 提取日期，无法验证是否正确",
                    suggestion="建议使用 _pygen_smart_find_date_in_row_bs4(tds) 智能扫描，或确认列索引与目标网站表格结构匹配"
                ))
                return
    
    def _check_chained_find_calls(self, code: str) -> None:
        """检查链式 find 调用"""
        code_lower = code.lower()
        
        # 检测 .find(...).find_all(...) 模式
        if ".find('tbody').find_all(" in code_lower or '.find("tbody").find_all(' in code_lower:
            self.issues.append(CodeIssue(
                code="ERR_002",
                severity=IssueSeverity.ERROR,
                message="检测到 find('tbody').find_all() 链式调用，可能导致空指针错误",
                suggestion="改用 soup.select('table tbody tr') 或对 find 结果做 None 检查"
            ))
            return
        
        # 更通用的检测
        if "beautifulsoup" in code_lower or "from bs4 import" in code_lower:
            if ".find(" in code_lower and ").find_all(" in code_lower:
                self.issues.append(CodeIssue(
                    code="ERR_002",
                    severity=IssueSeverity.WARNING,
                    message="检测到 find().find_all() 链式调用，存在空指针风险",
                    suggestion="优先使用 CSS 选择器 soup.select()，或对每层 find 结果做 None 检查"
                ))
    
    def _check_output_field_names(self, code: str) -> None:
        """检查输出字段名"""
        # 检查是否使用了 title 而不是 name
        uses_title_key = '"title":' in code or "'title':" in code
        uses_name_key = '"name":' in code or "'name':" in code
        
        if uses_title_key and not uses_name_key:
            self.issues.append(CodeIssue(
                code="ERR_006",
                # 兼容策略：前端/后端可把 title 视为报告名（name）的别名，不必强制触发修复
                severity=IssueSeverity.WARNING,
                message="输出字段使用了 'title' 而不是 'name'（兼容：title 将被视为 name）",
                suggestion="建议将 'title' 改为 'name'（字段名推荐: name/date/downloadUrl/fileType）"
            ))
        
        # 检查 downloadUrl 字段名
        uses_url_key = '"url":' in code or "'url':" in code
        uses_download_url_key = '"downloadUrl":' in code or "'downloadUrl':" in code
        
        if uses_url_key and not uses_download_url_key:
            self.issues.append(CodeIssue(
                code="ERR_006",
                severity=IssueSeverity.WARNING,
                message="输出字段使用了 'url' 而不是 'downloadUrl'",
                suggestion="将 'url' 改为 'downloadUrl'"
            ))
    
    def _check_spa_requests(self, code: str) -> None:
        """
        检查 SPA 页面使用 requests 的问题（上下文感知版本）
        
        改进：结合 page_structure 中的 spaHints 判断页面是否真的是 SPA
        - 如果 spaHints 明确表示是 SPA，且代码用 requests 抓 HTML，则报错
        - 如果没有 spaHints，仅靠代码中的 /#/ 模式判断
        """
        code_lower = code.lower()
        
        # 检测是否用 requests 抓取
        if "requests.get(" not in code_lower:
            return
        
        # 判断是否是 SPA 页面
        is_spa = False
        spa_evidence = []
        
        # 方式1：从 page_structure 中获取 SPA 线索
        if self.page_structure:
            spa_hints = self.page_structure.get('spaHints', {})
            if spa_hints.get('hasHashRoute'):
                is_spa = True
                spa_evidence.append("URL 包含 /#/ 路由")
            if spa_hints.get('hasAppRoot'):
                is_spa = True
                spa_evidence.append("页面有 #app 根元素")
        
        # 方式2：从代码中检测（降级）
        if not is_spa:
            if "/#/" in code:
                is_spa = True
                spa_evidence.append("代码中包含 /#/ URL")
        
        # 如果是 SPA 且用 requests 抓 HTML（而不是 API）
        if is_spa and "playwright" not in code_lower:
            # 检查是否是调用 API（.json() 说明是调用 API，不是抓 HTML）
            if ".json()" in code_lower:
                return  # 调用 API 是正确的
            
            self.issues.append(CodeIssue(
                code="ERR_005",
                severity=IssueSeverity.ERROR,
                message=f"检测到用 requests 抓取 SPA 页面 ({', '.join(spa_evidence)})",
                suggestion="SPA 页面需要用 Playwright 渲染后提取，或直接调用 API 接口获取 JSON 数据"
            ))
    
    def _check_date_from_title(self, code: str) -> None:
        """检查从标题猜测日期的问题"""
        # 去掉单行注释，避免误伤（例如注释里包含“2026年”但并非从 title 推断日期）
        code_scan = re.sub(r"(?m)#.*$", "", code)
        # Each pattern must match within a SINGLE line (no DOTALL) to avoid
        # cross-line false positives where unrelated "title" and "date"
        # variables happen to coexist in the same file.
        patterns = [
            r"re\.search\(['\\\"].*年.*['\\\"].*title",
            r'f"{year}\s*-12-31"',
        ]

        for pattern in patterns:
            if re.search(pattern, code_scan, re.IGNORECASE):
                self.issues.append(CodeIssue(
                    code="ERR_008",
                    severity=IssueSeverity.ERROR,
                    message="检测到从标题提取年份作为日期的模式",
                    suggestion="绝对禁止从标题猜测日期，应从 API 日期字段或 HTML 日期元素获取"
                ))
                return
    
    def _check_keeps_undated_records(self, code: str) -> None:
        """检查是否保留了无日期记录"""
        code_lower = code.lower()
        
        suspicious_patterns = [
            "elif not date_str",
            "if not date_str",
            "无日期" in code and "append(" in code_lower,
        ]
        
        pattern_count = sum(1 for p in suspicious_patterns if (p if isinstance(p, bool) else p in code_lower))
        
        # 同时出现多个可疑模式才报告
        if pattern_count >= 2 or ("无日期" in code and "保留" in code):
            self.issues.append(CodeIssue(
                code="ERR_007",
                severity=IssueSeverity.WARNING,
                message="检测到可能保留无日期记录的逻辑",
                suggestion="当用户指定日期范围时，无日期记录应该丢弃而不是保留"
            ))
    
    def _check_single_page_dates(self, code: str) -> None:
        """检查是否只处理第一页日期"""
        code_lower = code.lower()
        
        # 检测模式：有分页循环，但日期提取在循环外
        has_page_loop = "for page" in code_lower or "while" in code_lower
        has_date_extraction = "extract_date" in code_lower or "get_date" in code_lower
        
        if has_page_loop and has_date_extraction:
            # 简单启发：如果日期提取函数调用不在循环内
            lines = code.split('\n')
            in_loop = False
            date_in_loop = False
            
            for line in lines:
                if 'for ' in line.lower() and 'page' in line.lower():
                    in_loop = True
                if in_loop and ('extract_date' in line.lower() or 'get_date' in line.lower()):
                    date_in_loop = True
                    break
            
            if has_date_extraction and not date_in_loop:
                self.issues.append(CodeIssue(
                    code="ERR_003",
                    severity=IssueSeverity.WARNING,
                    message="日期提取函数可能在分页循环外调用，可能导致只有第一页有日期",
                    suggestion="确保每一页都提取日期，在同一个循环中处理数据和日期"
                ))
    
    def _check_missing_date_extraction(self, code: str) -> None:
        """
        检查静态 HTML 解析中是否遗漏日期提取（上下文感知版本）
        
        改进：结合 page_structure 判断页面是否真的有日期可提取
        - 如果 page_structure 显示表格没有日期列，则不报错
        - 如果 page_structure 显示有日期列但代码没提取，则报错并提示日期列位置
        """
        code_lower = code.lower()
        
        # 检测是否是 BeautifulSoup 解析静态 HTML 的代码
        uses_bs4 = "beautifulsoup" in code_lower or "from bs4" in code_lower
        parses_table = "table" in code_lower and ("tr" in code_lower or "row" in code_lower)
        
        if not (uses_bs4 and parses_table):
            return  # 不是静态 HTML 解析，跳过
        
        # 检查是否有任何日期提取相关的代码
        has_date_extraction = any([
            "_pygen_smart_find_date" in code,
            "_smart_find_date" in code,
            "normalize_date" in code_lower,
            "extract_date" in code_lower,
            "find_date" in code_lower,
            # 检查是否有日期字段的赋值
            re.search(r'"date"\s*:', code),
            re.search(r"'date'\s*:", code),
            # 检查是否有日期正则
            re.search(r'\\d{4}.*[-/].*\\d{1,2}', code),
        ])
        
        # 检查输出字典中是否包含 date 字段
        has_date_in_output = re.search(r'["\']date["\']\s*:', code) is not None
        
        # 如果已经有日期提取逻辑，跳过
        if has_date_extraction or has_date_in_output:
            return
        
        # 结合 page_structure 判断是否需要报错
        if self.page_structure:
            tables = self.page_structure.get('tables', [])
            date_elements = self.page_structure.get('dateElements', [])
            
            # 检查表格是否有日期列
            has_date_column = False
            date_col_info = ""
            if tables:
                table = tables[0]
                date_hints = table.get('dateColumnHints', [])
                if date_hints:
                    has_date_column = True
                    date_col_info = f"（日期在第{date_hints[0].get('columnIndex')}列「{date_hints[0].get('headerText', '?')}」）"
            
            # 检查页面是否有日期元素
            if date_elements:
                has_date_column = True
                if not date_col_info:
                    date_col_info = f"（页面有 {len(date_elements)} 个日期元素，如 {date_elements[0].get('selector')}）"
            
            if has_date_column:
                # 页面有日期但代码没提取，报错
                self.issues.append(CodeIssue(
                    code="ERR_009",
                    severity=IssueSeverity.ERROR,
                    message=f"静态 HTML 表格解析未包含日期提取逻辑{date_col_info}",
                    suggestion="在遍历表格行时使用 _pygen_smart_find_date_in_row_bs4(tds) 提取日期"
                ))
            else:
                # 页面没有检测到日期列，只给提示
                self.issues.append(CodeIssue(
                    code="ERR_009",
                    severity=IssueSeverity.INFO,
                    message="静态 HTML 表格解析未包含日期提取逻辑，但页面结构中未检测到日期列",
                    suggestion="如果表格确实没有日期列，可忽略此提示；否则请检查日期提取逻辑"
                ))
        else:
            # 没有 page_structure，使用原有逻辑但降级为警告
            self.issues.append(CodeIssue(
                code="ERR_009",
                severity=IssueSeverity.WARNING,
                message="静态 HTML 表格解析未包含日期提取逻辑，可能导致所有记录日期为空",
                suggestion="在遍历表格行时使用 _pygen_smart_find_date_in_row_bs4(tds) 提取日期"
            ))
    
    def _check_unicode_print_chars(self, code: str) -> None:
        """检查 print 语句中是否包含可能导致编码错误的 Unicode 字符"""
        # 常见的问题字符
        problem_chars = ['✓', '✗', '✔', '✘', '→', '←', '↑', '↓', '●', '○', '■', '□', '★', '☆']
        
        # 只检查 print 语句
        print_pattern = re.compile(r'print\s*\([^)]*\)', re.MULTILINE | re.DOTALL)
        print_statements = print_pattern.findall(code)
        
        for stmt in print_statements:
            for char in problem_chars:
                if char in stmt:
                    self.issues.append(CodeIssue(
                        code="ERR_010",
                        severity=IssueSeverity.WARNING,
                        message=f"print 语句包含 Unicode 字符 '{char}'，可能在 Windows GBK 控制台导致编码错误",
                        suggestion="使用 ASCII 字符替代，如 [OK]、[FAIL]、-> 等"
                    ))
                    return  # 只报告一次


# ============================================================================
# 输出验证器
# ============================================================================

class OutputValidator:
    """
    输出验证器 - 验证爬虫输出的 JSON 数据
    
    验证项：
    1. 结构完整性（Pydantic 模型验证）
    2. 数据质量检查（日期填充率、URL 有效性等）
    """
    
    def __init__(self, min_date_fill_rate: float = 0.3):
        """
        初始化验证器
        
        Args:
            min_date_fill_rate: 最小日期填充率要求 (0-1)
        """
        self.min_date_fill_rate = min_date_fill_rate
        self.errors: List[str] = []
        self.warnings: List[str] = []
    
    def validate_output(self, data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        验证输出数据
        
        Args:
            data: 爬虫输出的 JSON 数据
            
        Returns:
            (是否通过验证, 错误列表)
        """
        self.errors = []
        self.warnings = []
        
        # 1. Pydantic 结构验证
        try:
            output = CrawlOutput(**data)
        except ValidationError as e:
            for error in e.errors():
                field = ".".join(str(x) for x in error['loc'])
                self.errors.append(f"字段 {field}: {error['msg']}")
            return False, self.errors
        
        # 2. 数据质量检查
        reports = data.get('reports', [])
        if not reports:
            self.errors.append("reports 列表为空")
            return False, self.errors
        
        # 日期填充率
        date_count = sum(1 for r in reports if r.get('date'))
        date_fill_rate = date_count / len(reports) if reports else 0
        
        if date_fill_rate < self.min_date_fill_rate:
            self.warnings.append(
                f"日期填充率较低: {date_fill_rate:.1%} (期望 >= {self.min_date_fill_rate:.1%})"
            )
        
        # 检查 total 与实际数量是否匹配
        if data.get('total', 0) != len(reports):
            self.warnings.append(
                f"total ({data.get('total')}) 与 reports 数量 ({len(reports)}) 不匹配"
            )
        
        # URL 格式检查
        invalid_urls = []
        for i, r in enumerate(reports):
            url = r.get('downloadUrl', '')
            if url and not url.startswith(('http://', 'https://', '//')):
                invalid_urls.append(f"#{i+1}: {url[:50]}")
        
        if invalid_urls:
            self.warnings.append(f"发现 {len(invalid_urls)} 个无效 URL: {invalid_urls[:3]}")
        
        # 只有错误才返回失败
        return len(self.errors) == 0, self.errors + self.warnings
    
    def get_quality_report(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        生成数据质量报告
        
        Args:
            data: 爬虫输出的 JSON 数据
            
        Returns:
            质量报告字典
        """
        reports = data.get('reports', [])
        
        if not reports:
            return {
                "total_records": 0,
                "date_fill_rate": 0,
                "url_valid_rate": 0,
                "avg_name_length": 0,
                "status": "empty"
            }
        
        # 统计各项指标
        date_count = sum(1 for r in reports if r.get('date'))
        valid_url_count = sum(
            1 for r in reports 
            if r.get('downloadUrl', '').startswith(('http://', 'https://'))
        )
        name_lengths = [len(r.get('name', '')) for r in reports]
        
        return {
            "total_records": len(reports),
            "date_fill_rate": date_count / len(reports),
            "url_valid_rate": valid_url_count / len(reports),
            "avg_name_length": sum(name_lengths) / len(name_lengths),
            "empty_date_count": len(reports) - date_count,
            "invalid_url_count": len(reports) - valid_url_count,
            "status": "ok" if date_count > 0 and valid_url_count == len(reports) else "warning"
        }


# ============================================================================
# 便捷函数
# ============================================================================

def validate_code(code: str, page_structure: Optional[Dict[str, Any]] = None) -> Tuple[bool, List[CodeIssue]]:
    """
    验证代码的便捷函数（支持上下文感知）
    
    Args:
        code: Python 代码字符串
        page_structure: 页面结构信息（可选，来自 browser.analyze_page_structure()）
        
    Returns:
        (是否通过, 问题列表)
    """
    validator = StaticCodeValidator()
    issues = validator.validate(code, page_structure=page_structure)
    return not validator.has_errors(), issues


def validate_output(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    验证输出的便捷函数
    
    Args:
        data: JSON 数据
        
    Returns:
        (是否通过, 错误列表)
    """
    validator = OutputValidator()
    return validator.validate_output(data)


if __name__ == "__main__":
    # 测试代码验证器
    test_code = """
    def fetch_data():
        soup = BeautifulSoup(html, 'html.parser')
        table = soup.find('table')
        rows = table.find('tbody').find_all('tr')  # 这会被检测到
        
        for row in rows:
            tds = row.find_all('td')
            date = tds[4].select_one('span').get_text()  # 这也会被检测到
            reports.append({
                "title": name,  # 应该是 name
                "date": date,
                "url": url  # 应该是 downloadUrl
            })
    """
    
    validator = StaticCodeValidator()
    issues = validator.validate(test_code)
    print(validator.get_summary())
    print("\n" + "=" * 60 + "\n")
    print("修复提示:")
    print(validator.get_repair_prompt())

