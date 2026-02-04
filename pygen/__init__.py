"""
PyGen - 智能爬虫脚本生成器

根据目标列表页自动分析页面结构，生成独立可运行的Python爬虫脚本。

架构增强 v2.0:
- 结构化错误案例 Few-shot 注入
- Validator + Signals Collector + Failure Classifier
- 自动修复循环
"""

__version__ = "2.0.0"

# 核心模块导出
from .llm_agent import LLMAgent
from .error_cases import (
    ErrorCase,
    ErrorCategory,
    ErrorSeverity,
    get_error_cases_prompt,
    get_detection_patterns,
    add_error_case,
    ERROR_CASES
)
from .validator import (
    StaticCodeValidator,
    OutputValidator,
    CrawlRecord,
    CrawlOutput,
    CodeIssue,
    IssueSeverity,
    validate_code,
    validate_output
)
from .signals_collector import (
    SignalsCollector,
    PlaywrightSignalsCollector,
    ExecutionSignals,
    ExecutionStatus,
    HttpSignal,
    execute_script,
    check_page_accessibility
)
from .failure_classifier import (
    FailureClassifier,
    FailureReport,
    FailureType,
    classify_failure
)

__all__ = [
    # 版本
    "__version__",
    
    # 核心类
    "LLMAgent",
    
    # 错误案例
    "ErrorCase",
    "ErrorCategory", 
    "ErrorSeverity",
    "get_error_cases_prompt",
    "get_detection_patterns",
    "add_error_case",
    "ERROR_CASES",
    
    # 验证器
    "StaticCodeValidator",
    "OutputValidator",
    "CrawlRecord",
    "CrawlOutput",
    "CodeIssue",
    "IssueSeverity",
    "validate_code",
    "validate_output",
    
    # 信号收集器
    "SignalsCollector",
    "PlaywrightSignalsCollector",
    "ExecutionSignals",
    "ExecutionStatus",
    "HttpSignal",
    "execute_script",
    "check_page_accessibility",
    
    # 故障分类器
    "FailureClassifier",
    "FailureReport",
    "FailureType",
    "classify_failure",
]

