"""
PyGen 故障分类器 - 规则 + LLM 混合故障诊断

根据收集到的信号对故障进行分类，并生成结构化的故障报告。

分类策略：
1. 规则优先：使用预定义规则快速分类常见故障
2. LLM 兜底：对于规则无法分类的复杂情况，使用 LLM 分析

使用方式：
    from failure_classifier import FailureClassifier, FailureReport
    
    classifier = FailureClassifier()
    report = classifier.classify(signals, code)
"""

import json
import re
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

from signals_collector import ExecutionSignals, ExecutionStatus
from validator import StaticCodeValidator, CodeIssue
from error_cases import ERROR_CASES, ErrorCase, ErrorCategory


class FailureType(Enum):
    """故障类型"""
    # 网络/反爬相关
    BLOCKED_BY_WAF = "blocked_by_waf"              # 被 WAF/反爬拦截
    RATE_LIMITED = "rate_limited"                  # 请求频率限制
    NETWORK_ERROR = "network_error"                # 网络错误
    
    # 代码逻辑相关
    SELECTOR_MISMATCH = "selector_mismatch"        # 选择器不匹配
    HARDCODED_INDEX = "hardcoded_index"            # 硬编码索引错误
    NULL_POINTER = "null_pointer"                  # 空指针错误
    
    # 日期提取相关
    DATE_EXTRACTION_FAILED = "date_extraction_failed"  # 日期提取失败
    PAGINATION_DATE_LOST = "pagination_date_lost"      # 分页日期丢失
    
    # 输出相关
    SCHEMA_MISMATCH = "schema_mismatch"            # 输出格式不匹配
    EMPTY_OUTPUT = "empty_output"                  # 输出为空
    
    # 其他
    TIMEOUT = "timeout"                            # 执行超时
    UNKNOWN = "unknown"                            # 未知错误


@dataclass
class FailureReport:
    """故障报告"""
    # 基本信息
    failure_type: FailureType
    confidence: float  # 置信度 0-1
    
    # 诊断信息
    summary: str                    # 故障摘要
    root_cause: str                 # 根因分析
    evidence: List[str]             # 证据列表
    
    # 相关错误案例
    related_error_cases: List[str] = field(default_factory=list)  # 错误案例 ID
    
    # 修复建议
    fix_suggestions: List[str] = field(default_factory=list)
    
    # 原始信号
    signals_summary: Dict[str, Any] = field(default_factory=dict)
    code_issues: List[Dict[str, str]] = field(default_factory=list)
    
    # 时间戳
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "failure_type": self.failure_type.value,
            "confidence": self.confidence,
            "summary": self.summary,
            "root_cause": self.root_cause,
            "evidence": self.evidence,
            "related_error_cases": self.related_error_cases,
            "fix_suggestions": self.fix_suggestions,
            "signals_summary": self.signals_summary,
            "code_issues": self.code_issues,
            "timestamp": self.timestamp
        }
    
    def to_repair_prompt(self) -> str:
        """生成用于 LLM 修复的提示"""
        lines = [
            "## 故障诊断报告",
            "",
            f"**故障类型**: {self.failure_type.value}",
            f"**置信度**: {self.confidence:.1%}",
            "",
            f"**故障摘要**: {self.summary}",
            "",
            f"**根因分析**: {self.root_cause}",
            "",
            "**证据**:",
        ]
        for e in self.evidence[:5]:
            lines.append(f"- {e}")
        
        if self.related_error_cases:
            lines.append("")
            lines.append("**相关错误案例**:")
            for case_id in self.related_error_cases:
                case = next((c for c in ERROR_CASES if c.id == case_id), None)
                if case:
                    lines.append(f"- [{case_id}] {case.title}")
        
        lines.append("")
        lines.append("**修复建议**:")
        for s in self.fix_suggestions:
            lines.append(f"- {s}")
        
        lines.append("")
        lines.append("请根据以上诊断修复代码。")
        
        return "\n".join(lines)


class FailureClassifier:
    """
    故障分类器 - 规则 + LLM 混合诊断
    """
    
    def __init__(self, llm_client=None):
        """
        初始化分类器
        
        Args:
            llm_client: OpenAI 兼容的 LLM 客户端（可选，用于复杂情况）
        """
        self.llm_client = llm_client
        self.code_validator = StaticCodeValidator()
    
    def classify(
        self,
        signals: ExecutionSignals,
        code: Optional[str] = None
    ) -> FailureReport:
        """
        对故障进行分类
        
        Args:
            signals: 执行信号
            code: 生成的代码（可选，用于静态分析）
            
        Returns:
            故障报告
        """
        # 先进行静态代码分析
        code_issues = []
        if code:
            issues = self.code_validator.validate(code)
            code_issues = [
                {"code": i.code, "severity": i.severity.value, "message": i.message}
                for i in issues
            ]
        
        # 规则分类
        report = self._classify_by_rules(signals, code_issues)
        
        # 如果规则分类置信度低，使用 LLM
        if report.confidence < 0.6 and self.llm_client:
            llm_report = self._classify_by_llm(signals, code, code_issues)
            if llm_report.confidence > report.confidence:
                report = llm_report
        
        # 添加信号摘要
        report.signals_summary = signals.to_dict()
        report.code_issues = code_issues
        
        return report
    
    def _classify_by_rules(
        self,
        signals: ExecutionSignals,
        code_issues: List[Dict[str, str]]
    ) -> FailureReport:
        """使用规则进行分类"""
        
        # =====================================================================
        # 规则1: 反爬拦截
        # =====================================================================
        if signals.challenge_detected:
            return FailureReport(
                failure_type=FailureType.BLOCKED_BY_WAF,
                confidence=0.95,
                summary="请求被网站反爬系统拦截",
                root_cause="触发了网站的 WAF/反爬机制，可能是请求频率过高或缺少必要的请求头",
                evidence=[
                    f"检测到反爬关键词: {signals.challenge_keywords}",
                    f"HTTP 状态: {[s.status_code for s in signals.http_signals if s.status_code >= 400]}"
                ],
                related_error_cases=["ERR_005"],
                fix_suggestions=[
                    "增加请求间隔（建议 1-2 秒）",
                    "添加完整的请求头（User-Agent, Referer 等）",
                    "使用 Playwright 模拟真实浏览器",
                    "考虑使用代理池"
                ]
            )
        
        # =====================================================================
        # 规则2: 超时
        # =====================================================================
        if signals.status == ExecutionStatus.TIMEOUT:
            return FailureReport(
                failure_type=FailureType.TIMEOUT,
                confidence=0.99,
                summary="脚本执行超时",
                root_cause="脚本执行时间过长，可能是网络慢、数据量大或存在死循环",
                evidence=[
                    f"执行时间: {signals.duration_seconds:.1f}秒",
                    f"异常信息: {signals.exceptions}"
                ],
                fix_suggestions=[
                    "增加请求超时设置",
                    "减少单次爬取的数据量",
                    "检查是否存在死循环",
                    "添加分页终止条件"
                ]
            )
        
        # =====================================================================
        # 规则3: 空输出
        # =====================================================================
        if signals.output_record_count == 0:
            # 进一步分析原因
            evidence = [f"输出记录数: 0"]
            
            if signals.http_signals:
                error_codes = [s.status_code for s in signals.http_signals if s.status_code >= 400]
                if error_codes:
                    evidence.append(f"HTTP 错误码: {error_codes}")
            
            if signals.exceptions:
                evidence.append(f"异常: {signals.exceptions[:2]}")
            
            # 检查是否有选择器相关的错误
            selector_issues = [i for i in code_issues if 'ERR_001' in i.get('code', '') or 'ERR_002' in i.get('code', '')]
            if selector_issues:
                return FailureReport(
                    failure_type=FailureType.SELECTOR_MISMATCH,
                    confidence=0.85,
                    summary="选择器可能不匹配目标页面结构",
                    root_cause="代码中的 CSS 选择器或 XPath 与实际页面结构不匹配，导致无法提取数据",
                    evidence=evidence + [f"代码问题: {[i['message'] for i in selector_issues]}"],
                    related_error_cases=["ERR_001", "ERR_002"],
                    fix_suggestions=[
                        "使用更宽松的选择器（如 soup.select('table tr') 替代 soup.select('table tbody tr')）",
                        "添加选择器失败的降级处理",
                        "使用开发者工具确认实际的页面结构"
                    ]
                )
            
            return FailureReport(
                failure_type=FailureType.EMPTY_OUTPUT,
                confidence=0.75,
                summary="脚本执行完成但输出为空",
                root_cause="可能是选择器不匹配、API 响应格式变化或需要登录/分类选择",
                evidence=evidence,
                related_error_cases=["ERR_002", "ERR_005"],
                fix_suggestions=[
                    "检查页面是否需要先选择分类才能加载数据",
                    "验证 API 响应格式是否与代码中的解析逻辑匹配",
                    "检查是否需要登录或 Cookie"
                ]
            )
        
        # =====================================================================
        # 规则4: 日期填充率低
        # =====================================================================
        if signals.date_fill_rate < 0.1 and signals.output_record_count > 0:
            # 检查相关代码问题
            date_issues = [i for i in code_issues if 'date' in i.get('message', '').lower() or i.get('code') in ['ERR_001', 'ERR_003', 'ERR_004', 'ERR_008']]
            
            if any(i.get('code') == 'ERR_003' for i in code_issues):
                return FailureReport(
                    failure_type=FailureType.PAGINATION_DATE_LOST,
                    confidence=0.9,
                    summary="分页导致日期丢失",
                    root_cause="日期提取逻辑只处理了第一页，后续页面的日期未被提取",
                    evidence=[
                        f"日期填充率: {signals.date_fill_rate:.1%}",
                        f"总记录数: {signals.output_record_count}",
                        "检测到分页日期丢失模式"
                    ],
                    related_error_cases=["ERR_003"],
                    fix_suggestions=[
                        "在每页数据获取时同步提取日期",
                        "不要将数据获取和日期提取分成两个独立阶段",
                        "使用 _pygen_smart_find_date_in_row_* 在循环中直接提取日期"
                    ]
                )
            
            return FailureReport(
                failure_type=FailureType.DATE_EXTRACTION_FAILED,
                confidence=0.8,
                summary="日期提取失败",
                root_cause="日期提取逻辑存在问题，可能是选择器不匹配或 API 字段解析错误",
                evidence=[
                    f"日期填充率: {signals.date_fill_rate:.1%}",
                    f"总记录数: {signals.output_record_count}",
                    f"相关代码问题: {[i['message'] for i in date_issues]}"
                ],
                related_error_cases=["ERR_001", "ERR_004", "ERR_008"],
                fix_suggestions=[
                    "使用 _pygen_smart_find_date_in_row_* 智能扫描日期",
                    "检查 API 响应中的日期字段名和格式",
                    "避免硬编码列索引提取日期"
                ]
            )
        
        # =====================================================================
        # 规则5: 空指针异常
        # =====================================================================
        null_pointer_patterns = [
            "'NoneType' object has no attribute",
            "AttributeError: 'NoneType'",
            "TypeError: 'NoneType'",
        ]
        
        combined_errors = " ".join(signals.exceptions + signals.console_errors)
        if any(p in combined_errors for p in null_pointer_patterns):
            return FailureReport(
                failure_type=FailureType.NULL_POINTER,
                confidence=0.95,
                summary="空指针异常",
                root_cause="代码中存在链式调用，但前一个调用返回了 None",
                evidence=[
                    f"异常: {[e for e in signals.exceptions if 'NoneType' in e][:2]}",
                    "常见原因: find('tbody') 返回 None 后直接调用 find_all()"
                ],
                related_error_cases=["ERR_002"],
                fix_suggestions=[
                    "使用 soup.select() 替代 find().find_all() 链式调用",
                    "对每层 find 结果做 None 检查",
                    "添加容器不存在时的降级处理"
                ]
            )
        
        # =====================================================================
        # 规则6: HTTP 4xx/5xx 错误
        # =====================================================================
        error_codes = [s.status_code for s in signals.http_signals if s.status_code >= 400]
        if error_codes:
            if 403 in error_codes or 401 in error_codes:
                return FailureReport(
                    failure_type=FailureType.BLOCKED_BY_WAF,
                    confidence=0.85,
                    summary="请求被服务器拒绝",
                    root_cause="收到 401/403 错误，可能需要认证或被反爬拦截",
                    evidence=[f"HTTP 状态码: {error_codes}"],
                    fix_suggestions=[
                        "添加完整的请求头",
                        "检查是否需要 Cookie 或登录",
                        "使用 Playwright 模拟浏览器"
                    ]
                )
            
            if 429 in error_codes:
                return FailureReport(
                    failure_type=FailureType.RATE_LIMITED,
                    confidence=0.95,
                    summary="请求频率受限",
                    root_cause="收到 429 Too Many Requests，请求过于频繁",
                    evidence=[f"HTTP 状态码: {error_codes}"],
                    fix_suggestions=[
                        "增加请求间隔（建议 2-5 秒）",
                        "添加重试机制并使用指数退避",
                        "减少并发请求数"
                    ]
                )
            
            return FailureReport(
                failure_type=FailureType.NETWORK_ERROR,
                confidence=0.75,
                summary=f"HTTP 错误: {error_codes}",
                root_cause="服务器返回错误状态码",
                evidence=[f"HTTP 状态码: {error_codes}"],
                fix_suggestions=[
                    "检查 URL 是否正确",
                    "添加重试机制",
                    "验证请求参数"
                ]
            )
        
        # =====================================================================
        # 规则7: 基于代码静态分析的问题
        # =====================================================================
        if code_issues:
            error_issues = [i for i in code_issues if i.get('severity') == 'error']
            if error_issues:
                first_issue = error_issues[0]
                issue_code = first_issue.get('code', 'UNKNOWN')
                
                # 映射到故障类型
                type_mapping = {
                    'ERR_001': FailureType.HARDCODED_INDEX,
                    'ERR_002': FailureType.NULL_POINTER,
                    'ERR_003': FailureType.PAGINATION_DATE_LOST,
                    'ERR_005': FailureType.SELECTOR_MISMATCH,
                    'ERR_006': FailureType.SCHEMA_MISMATCH,
                    'ERR_008': FailureType.DATE_EXTRACTION_FAILED,
                }
                
                failure_type = type_mapping.get(issue_code, FailureType.UNKNOWN)
                
                return FailureReport(
                    failure_type=failure_type,
                    confidence=0.7,
                    summary=f"静态分析发现问题: {first_issue.get('message', '')}",
                    root_cause=f"代码中存在已知的错误模式 [{issue_code}]",
                    evidence=[f"代码问题: {i['message']}" for i in error_issues[:3]],
                    related_error_cases=[issue_code],
                    fix_suggestions=[
                        error_issues[0].get('suggestion', '参考错误案例修复')
                    ] if 'suggestion' in error_issues[0] else ["参考相关错误案例进行修复"]
                )
        
        # =====================================================================
        # 兜底: 未知错误
        # =====================================================================
        return FailureReport(
            failure_type=FailureType.UNKNOWN,
            confidence=0.3,
            summary="未能明确分类的故障",
            root_cause="需要进一步分析日志和代码",
            evidence=[
                f"退出码: {signals.exit_code}",
                f"异常: {signals.exceptions[:2]}",
                f"错误: {signals.console_errors[:2]}"
            ],
            fix_suggestions=[
                "检查完整的错误日志",
                "使用调试模式运行脚本",
                "逐步排查代码逻辑"
            ]
        )
    
    def _classify_by_llm(
        self,
        signals: ExecutionSignals,
        code: Optional[str],
        code_issues: List[Dict[str, str]]
    ) -> FailureReport:
        """使用 LLM 进行分类（复杂情况）"""
        if not self.llm_client:
            return FailureReport(
                failure_type=FailureType.UNKNOWN,
                confidence=0.0,
                summary="LLM 客户端未配置",
                root_cause="",
                evidence=[]
            )
        
        # 构建 prompt
        prompt = f"""分析以下爬虫脚本执行失败的原因：

## 执行信号
- 状态: {signals.status.value}
- 退出码: {signals.exit_code}
- 输出记录数: {signals.output_record_count}
- 日期填充率: {signals.date_fill_rate:.1%}
- 异常: {signals.exceptions[:3]}
- 控制台错误: {signals.console_errors[:3]}
- 反爬检测: {signals.challenge_detected}

## 代码静态分析问题
{json.dumps(code_issues[:5], ensure_ascii=False, indent=2)}

## 代码片段
```python
{code[:2000] if code else '(无代码)'}
```

请分析故障原因并给出：
1. 故障类型（选择: blocked_by_waf, rate_limited, network_error, selector_mismatch, hardcoded_index, null_pointer, date_extraction_failed, pagination_date_lost, schema_mismatch, empty_output, timeout, unknown）
2. 置信度 (0-1)
3. 故障摘要（一句话）
4. 根因分析
5. 修复建议（列表）

以 JSON 格式输出。
"""
        
        try:
            response = self.llm_client.chat.completions.create(
                model="qwen-max",
                messages=[
                    {"role": "system", "content": "你是一个爬虫故障诊断专家。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=1000
            )
            
            content = response.choices[0].message.content
            # 解析 JSON
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result = json.loads(json_match.group())
                
                failure_type_str = result.get('故障类型', result.get('failure_type', 'unknown'))
                try:
                    failure_type = FailureType(failure_type_str)
                except ValueError:
                    failure_type = FailureType.UNKNOWN
                
                return FailureReport(
                    failure_type=failure_type,
                    confidence=float(result.get('置信度', result.get('confidence', 0.5))),
                    summary=result.get('故障摘要', result.get('summary', '')),
                    root_cause=result.get('根因分析', result.get('root_cause', '')),
                    evidence=["LLM 分析结果"],
                    fix_suggestions=result.get('修复建议', result.get('fix_suggestions', []))
                )
        
        except Exception as e:
            pass
        
        return FailureReport(
            failure_type=FailureType.UNKNOWN,
            confidence=0.0,
            summary="LLM 分析失败",
            root_cause=str(e) if 'e' in locals() else "未知错误",
            evidence=[]
        )


# ============================================================================
# 便捷函数
# ============================================================================

def classify_failure(
    signals: ExecutionSignals,
    code: Optional[str] = None
) -> FailureReport:
    """
    分类故障的便捷函数
    
    Args:
        signals: 执行信号
        code: 代码（可选）
        
    Returns:
        故障报告
    """
    classifier = FailureClassifier()
    return classifier.classify(signals, code)


if __name__ == "__main__":
    # 测试
    from signals_collector import ExecutionSignals, ExecutionStatus, HttpSignal
    
    # 模拟一个日期提取失败的场景
    signals = ExecutionSignals(
        status=ExecutionStatus.PARTIAL,
        exit_code=0,
        duration_seconds=15.5,
        output_record_count=50,
        date_fill_rate=0.02,  # 只有 2% 的日期
        console_errors=["警告: 部分日期提取失败"]
    )
    
    test_code = """
    for row in rows:
        tds = row.select('td')
        date = tds[4].select_one('span').get_text()  # 硬编码列索引
        reports.append({"title": name, "date": date})
    """
    
    classifier = FailureClassifier()
    report = classifier.classify(signals, test_code)
    
    print("故障报告")
    print("=" * 60)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    print("\n" + "=" * 60 + "\n")
    print("修复提示:")
    print(report.to_repair_prompt())

