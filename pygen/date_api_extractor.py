#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日期范围 API 提取器 - 四层策略（改进版）

用于"需要选择日期类网站爬取"模式，采用四层渐进策略：

第零层：JavaScript 全局变量扫描（最精准）★ 新增
    - 在浏览器运行时执行 JS，扫描 window 对象中的 API 配置
    - 查找包含 'Url', 'url', 'api', 'query', 'param' 等关键词的全局变量
    - 直接从配置对象中提取 API 端点和日期参数名
    - 成功判定：找到包含 URL 和日期参数的配置对象
    - 优势：绕过网络抓包的缓存/延迟问题，直接读取内存中的配置

第一层：纯 API 直连
    - 分析页面初始加载的网络请求
    - 识别日期 API（通过 URL/参数特征）
    - 用用户日期重放请求
    - 成功判定：重放后返回有效数据（非空列表、无错误码）

第二层：DOM 特征检测 + 自动操作
    - 检测常见日期控件的 class/id（laydate, el-date-picker, ant-picker 等）
    - 自动点击日期控件，设置日期范围
    - 点击确定/查询按钮触发请求
    - 成功判定：捕获到新的日期 API 请求

第三层：截图 + LLM 兜底
    - 截图当前页面
    - 让 LLM（Gemini）分析日期控件位置和操作方式
    - 按 LLM 指示操作日期控件
    - 成功判定：同上
"""

import re
import json
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from dataclasses import dataclass, field


# ============ 数据类 ============

@dataclass
class DateAPICandidate:
    """日期 API 候选"""
    url: str
    method: str  # GET or POST
    params: Dict[str, Any]  # URL 参数或 POST body
    date_params: Dict[str, str]  # 识别出的日期参数及其格式
    response_preview: str = ""  # 响应预览（用于判断数据结构）
    confidence: float = 0.0  # 置信度 0-1
    resource_type: str = ""  # xhr, fetch, script (JSONP)
    
    # 验证结果
    is_verified: bool = False
    verification_result: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DatePickerInfo:
    """日期控件信息"""
    found: bool = False
    picker_type: str = ""  # laydate, element-ui, ant-design, native, unknown
    selector: str = ""  # CSS 选择器
    is_range: bool = False  # 是否是日期范围选择器
    is_input: bool = False  # 是否可直接输入
    start_selector: str = ""  # 开始日期选择器
    end_selector: str = ""  # 结束日期选择器
    confirm_selector: str = ""  # 确认按钮选择器
    trigger_selector: str = ""  # 触发日期选择的元素
    extra_info: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GlobalVarAPIConfig:
    """从全局变量中提取的 API 配置"""
    var_name: str  # 全局变量名，如 'LatestAnnouncement'
    api_url: str  # API 端点 URL
    date_params: Dict[str, str]  # 日期参数名及其当前值，如 {'START_DATE': '', 'END_DATE': ''}
    all_params: Dict[str, Any]  # 完整的参数对象
    date_format: str = 'YYYY-MM-DD'  # 推断的日期格式
    is_jsonp: bool = False  # 是否是 JSONP 接口
    extra_info: Dict[str, Any] = field(default_factory=dict)  # 额外信息


@dataclass
class DateAPIExtractionResult:
    """日期 API 提取结果"""
    success: bool
    layer: int  # 成功的层级：0=全局变量, 1=网络请求, 2=DOM操作, 3=LLM, -1=全部失败
    candidates: List[DateAPICandidate]
    best_candidate: Optional[DateAPICandidate] = None
    error: Optional[str] = None
    
    # 重放结果
    replayed_data: Optional[Dict[str, Any]] = None
    data_count: int = 0
    
    # 各层结果
    layer0_result: Optional[Dict[str, Any]] = None  # 全局变量扫描结果
    layer1_result: Optional[Dict[str, Any]] = None
    layer2_result: Optional[Dict[str, Any]] = None
    layer3_result: Optional[Dict[str, Any]] = None
    
    # 从全局变量发现的 API 配置
    global_var_config: Optional[GlobalVarAPIConfig] = None
    
    # 建议
    recommendations: List[str] = field(default_factory=list)


# ============ 日期 API 提取器 ============

class DateAPIExtractor:
    """日期 API 提取器 - 三层策略"""
    
    # 常见日期参数名
    DATE_PARAM_PATTERNS = [
        # 开始日期
        r'(?i)start[_-]?date',
        r'(?i)begin[_-]?date',
        r'(?i)from[_-]?date',
        r'(?i)date[_-]?from',
        r'(?i)start[_-]?time',
        r'(?i)begin[_-]?time',
        r'(?i)searchDate',
        r'(?i)SEARCH[_-]?DATE',
        r'(?i)START_DATE',
        r'(?i)BEGIN_DATE',
        # 结束日期
        r'(?i)end[_-]?date',
        r'(?i)to[_-]?date',
        r'(?i)date[_-]?to',
        r'(?i)end[_-]?time',
        r'(?i)endDate',
        r'(?i)END[_-]?DATE',
        r'(?i)END_DATE',
        # 通用日期
        r'(?i)date',
        r'(?i)time',
        r'(?i)day',
        r'(?i)publish[_-]?date',
        r'(?i)release[_-]?date',
        r'(?i)create[_-]?date',
    ]
    
    # 日期值格式模式（用于识别参数值是否为日期）
    DATE_VALUE_PATTERNS = [
        # YYYY-MM-DD
        (r'^\d{4}-\d{2}-\d{2}$', 'YYYY-MM-DD'),
        # YYYYMMDD
        (r'^\d{8}$', 'YYYYMMDD'),
        # YYYY/MM/DD
        (r'^\d{4}/\d{2}/\d{2}$', 'YYYY/MM/DD'),
        # Unix timestamp (秒)
        (r'^\d{10}$', 'timestamp_s'),
        # Unix timestamp (毫秒)
        (r'^\d{13}$', 'timestamp_ms'),
        # YYYY-MM-DD HH:MM:SS
        (r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$', 'YYYY-MM-DD HH:MM:SS'),
    ]
    
    # 常见日期控件的 CSS 选择器
    # 注意：顺序重要！优先匹配可直接填写的输入框（泛化能力最强）
    DATE_PICKER_SELECTORS = [
        # === 优先级最高：可直接填写的日期输入框 ===
        # 原生 input[type="date"]
        {'type': 'native', 'trigger': 'input[type="date"]', 'container': None, 'confirm': None, 'require_visible': True},
        # 通用日期输入框（placeholder 含 YYYY 或日期等关键词）
        {'type': 'generic-input', 'trigger': 'input[placeholder*="YYYY"]', 'container': None, 'confirm': None, 'require_visible': True},
        {'type': 'generic-input', 'trigger': 'input[placeholder*="yyyy"]', 'container': None, 'confirm': None, 'require_visible': True},
        {'type': 'generic-input', 'trigger': 'input[placeholder*="日期"]', 'container': None, 'confirm': None, 'require_visible': True},
        {'type': 'generic-input', 'trigger': 'input[placeholder*="date" i]', 'container': None, 'confirm': None, 'require_visible': True},
        {'type': 'generic-input', 'trigger': 'input[name*="date" i]', 'container': None, 'confirm': None, 'require_visible': True},
        # === Layui Laydate ===
        {'type': 'laydate', 'trigger': '[lay-key]', 'container': '.layui-laydate', 'confirm': '.laydate-btns-confirm', 'require_visible': True},
        {'type': 'laydate', 'trigger': '.laydate-icon', 'container': '.layui-laydate', 'confirm': '.laydate-btns-confirm', 'require_visible': True},
        # === Element UI ===
        {'type': 'element-ui', 'trigger': '.el-date-editor', 'container': '.el-picker-panel', 'confirm': '.el-picker-panel__footer .el-button--primary', 'require_visible': True},
        {'type': 'element-ui', 'trigger': '.el-range-editor', 'container': '.el-picker-panel', 'confirm': '.el-picker-panel__footer .el-button--primary', 'require_visible': True},
        # === Ant Design ===
        {'type': 'ant-design', 'trigger': '.ant-picker', 'container': '.ant-picker-dropdown', 'confirm': '.ant-picker-ok button', 'require_visible': True},
        {'type': 'ant-design', 'trigger': '.ant-calendar-picker', 'container': '.ant-calendar', 'confirm': '.ant-calendar-ok-btn', 'require_visible': True},
        # === Bootstrap Datepicker ===
        # 注意：优先匹配 data-provide 属性的 INPUT 元素，而非 .datepicker 容器
        {'type': 'bootstrap', 'trigger': '[data-provide="datepicker"]', 'container': '.datepicker-dropdown', 'confirm': None, 'require_visible': True},
        # .datepicker 可能匹配隐藏的 dropdown-menu，必须严格检查可见性
        {'type': 'bootstrap', 'trigger': 'input.datepicker', 'container': '.datepicker-dropdown', 'confirm': None, 'require_visible': True},
    ]
    
    # 可能包含日期筛选的 API 路径特征
    API_PATH_PATTERNS = [
        r'query',
        r'search',
        r'list',
        r'find',
        r'get',
        r'fetch',
        r'load',
        r'data',
        r'api',
        r'\.do$',
        r'\.json$',
        r'\.action$',
    ]
    
    # 已知网站的 API 配置模式（用于快速匹配）
    KNOWN_SITE_PATTERNS = {
        'sse.com.cn': {
            'global_vars': ['LatestAnnouncement', 'queryConfigData', 'announcementSumDetail'],
            'api_url_pattern': r'query\.sse\.com\.cn.*\.do',
            'date_params': ['START_DATE', 'END_DATE'],
            'date_format': 'YYYY-MM-DD',
        },
        'szse.cn': {
            'global_vars': ['flagObj', 'pluginObj', 'queryParams', 'searchConfig'],
            'api_url_pattern': r'szse\.cn.*api|/api/disc/',
            'date_params': ['seDate', 'startDate', 'endDate', 'start', 'end'],
            'date_format': 'YYYY-MM-DD',
            # 特殊标记：historyUrl 是日期搜索端点，selfUrl 是默认端点
            'url_fields': ['historyUrl', 'selfUrl', 'url', 'Url'],
        },
        'cninfo.com.cn': {
            'global_vars': ['pageConfig', 'searchParams'],
            'api_url_pattern': r'cninfo\.com\.cn.*query',
            'date_params': ['seDate', 'seDate_'],
            'date_format': 'YYYY-MM-DD',
        },
    }
    
    # 用于扫描全局变量的关键词
    GLOBAL_VAR_KEYWORDS = [
        'url', 'api', 'query', 'param', 'config', 'search', 'list',
        'announce', 'bulletin', 'disclosure', 'report', 'news',
        'flag', 'plugin', 'option', 'setting', 'obj',  # flagObj, pluginObj 等
    ]
    
    def __init__(self, browser_controller=None, llm_agent=None):
        """
        初始化
        
        Args:
            browser_controller: BrowserController 实例（用于网络请求捕获和重放）
            llm_agent: LLMAgent 实例（用于第三层 LLM 分析）
        """
        self.browser = browser_controller
        self.llm = llm_agent
        self.candidates: List[DateAPICandidate] = []
        
        # 记录各层结果
        self._layer0_result: Optional[Dict[str, Any]] = None  # 全局变量扫描
        self._layer1_result: Optional[Dict[str, Any]] = None
        self._layer2_result: Optional[Dict[str, Any]] = None
        self._layer3_result: Optional[Dict[str, Any]] = None

        # 常见“噪声/缓存破坏”参数名（不应被识别为日期筛选）
        # 典型如：_ = 1700000000000
        self._noise_param_names = {
            "_", "__", "_t", "t", "ts", "timestamp", "_timestamp", "_ts",
        }

    def _looks_like_real_date_filter(self, candidate: "DateAPICandidate") -> Tuple[bool, str]:
        """
        候选 API 健全性检查：避免把 cachebuster/无关接口误判为“日期筛选 API”。

        规则（偏保守）：
        - 若日期参数只包含噪声参数（如 '_'）=> 判定为假阳性
        - 若日期参数中没有任何“像日期筛选字段名”的 key（start/end/date/time/day）=> 判定为可疑
        - 若 URL 明显是公告/披露类接口（bulletin/announcement/disclosure/queryCompanyBulletin）=> 放宽
        """
        try:
            url_l = (candidate.url or "").lower()
            keys = list((candidate.date_params or {}).keys())
            if not keys:
                return False, "无日期参数"

            # 1) 只有噪声参数（典型：_）
            non_noise_keys = [k for k in keys if (k or "").lower() not in self._noise_param_names]
            if not non_noise_keys:
                return False, f"仅包含噪声日期参数: {keys}"

            # 2) 是否包含明确的日期筛选字段名
            def _is_dateish_name(k: str) -> bool:
                kl = (k or "").lower()
                if kl in self._noise_param_names:
                    return False
                return any(tok in kl for tok in ["start", "begin", "from", "end", "to", "date", "time", "day"])

            has_dateish_name = any(_is_dateish_name(k) for k in non_noise_keys)

            # 3) URL 是否看起来像“公告/披露”类接口
            looks_like_bulletin_api = any(tok in url_l for tok in ["bulletin", "announcement", "disclosure", "querycompanybulletin"])

            # commonQuery.do 往往是通用查询聚合接口，极易误判（尤其只有 '_'）
            if "commonquery" in url_l and not looks_like_bulletin_api and not has_dateish_name:
                return False, "commonQuery 且缺少明确日期字段名"

            if looks_like_bulletin_api:
                return True, "URL 命中公告/披露特征"

            if not has_dateish_name:
                return False, "缺少明确日期筛选字段名（可能是噪声参数误判）"

            return True, "通过健全性检查"
        except Exception as e:
            return False, f"健全性检查异常: {e}"
        
        # 从全局变量发现的 API 配置
        self._global_var_config: Optional[GlobalVarAPIConfig] = None
    
    # ============ 主入口：四层策略 ============
    
    async def extract_with_three_layers(
        self,
        network_requests: Dict[str, Any],
        start_date: str,
        end_date: str,
        log_callback=None
    ) -> DateAPIExtractionResult:
        """
        四层策略提取日期 API（向后兼容的方法名）
        
        Args:
            network_requests: 初始捕获的网络请求
            start_date: 用户指定的开始日期 YYYY-MM-DD
            end_date: 用户指定的结束日期 YYYY-MM-DD
            log_callback: 日志回调函数 (message: str) -> None
            
        Returns:
            提取结果，包含成功的层级和 API 信息
        """
        def log(msg: str):
            print(f"[DateAPIExtractor] {msg}")
            if log_callback:
                log_callback(msg)
        
        # ========== 第零层：JavaScript 全局变量扫描（最精准） ==========
        if self.browser and hasattr(self.browser, 'page') and self.browser.page:
            log("[Layer 0] 尝试 JavaScript 全局变量扫描（最精准方法）...")
            layer0_result = await self._try_layer0_global_var_scan(
                start_date, end_date, log
            )
            self._layer0_result = layer0_result
            
            if layer0_result['success']:
                log(f"[Layer 0] ✓ 成功！从全局变量 '{layer0_result.get('var_name', '')}' 发现 API")
                log(f"[Layer 0] API: {layer0_result.get('api_url', '')[:80]}...")
                log(f"[Layer 0] 日期参数: {list(layer0_result.get('date_params', {}).keys())}")
                
                # 构建候选并验证
                candidate = self._build_candidate_from_global_var(layer0_result)
                if candidate:
                    verify_result = await self.verify_candidate(candidate, start_date, end_date)
                    if verify_result['success']:
                        self.candidates.insert(0, candidate)
                        self._global_var_config = GlobalVarAPIConfig(
                            var_name=layer0_result.get('var_name', ''),
                            api_url=layer0_result.get('api_url', ''),
                            date_params=layer0_result.get('date_params', {}),
                            all_params=layer0_result.get('all_params', {}),
                            date_format=layer0_result.get('date_format', 'YYYY-MM-DD'),
                            is_jsonp=layer0_result.get('is_jsonp', False),
                            extra_info=layer0_result.get('extra_info', {}),
                        )
                        
                        return DateAPIExtractionResult(
                            success=True,
                            layer=0,
                            candidates=self.candidates,
                            best_candidate=candidate,
                            data_count=verify_result['data_count'],
                            replayed_data=verify_result.get('parsed_data'),
                            global_var_config=self._global_var_config,
                            layer0_result=layer0_result,
                            recommendations=[
                                f"从全局变量 '{layer0_result.get('var_name', '')}' 直接获取 API 配置",
                                f"日期参数: {list(layer0_result.get('date_params', {}).keys())}",
                                "这是最精准的方法，无需网络抓包或DOM操作",
                            ]
                        )
                    else:
                        log(f"[Layer 0] API 验证失败: {verify_result.get('error', '')}")
            else:
                log(f"[Layer 0] ✗ 未找到有效的全局变量配置: {layer0_result.get('error', '')}")
        else:
            log("[Layer 0] 跳过（无浏览器实例）")
        
        # ========== 第一层：纯 API 直连 ==========
        log("[Layer 1] 尝试纯 API 直连...")
        layer1_result = await self._try_layer1_api_direct(
            network_requests, start_date, end_date, log
        )
        self._layer1_result = layer1_result
        
        if layer1_result['success']:
            log(f"[Layer 1] ✓ 成功！识别到日期 API，获取到 {layer1_result['data_count']} 条数据")
            return DateAPIExtractionResult(
                success=True,
                layer=1,
                candidates=self.candidates,
                best_candidate=layer1_result.get('best_candidate'),
                data_count=layer1_result['data_count'],
                replayed_data=layer1_result.get('replayed_data'),
                layer0_result=self._layer0_result,
                layer1_result=layer1_result,
                recommendations=[
                    f"纯 API 直连成功，获取到 {layer1_result['data_count']} 条数据",
                    f"日期参数: {list(layer1_result.get('best_candidate', {}).date_params.keys()) if layer1_result.get('best_candidate') else []}",
                ]
            )
        else:
            log(f"[Layer 1] ✗ 失败: {layer1_result.get('error', '未知错误')}")
        
        # ========== 第二层：DOM 特征检测 + 自动操作 ==========
        if not self.browser:
            log("[Layer 2] 跳过（无浏览器控制器）")
        else:
            log("[Layer 2] 尝试 DOM 特征检测...")
            layer2_result = await self._try_layer2_dom_detect(
                start_date, end_date, log
            )
            self._layer2_result = layer2_result
            
            if layer2_result['success']:
                log(f"[Layer 2] ✓ 成功！通过 DOM 操作捕获到日期 API，获取到 {layer2_result['data_count']} 条数据")
                return DateAPIExtractionResult(
                    success=True,
                    layer=2,
                    candidates=self.candidates,
                    best_candidate=layer2_result.get('best_candidate'),
                    data_count=layer2_result['data_count'],
                    replayed_data=layer2_result.get('replayed_data'),
                    layer0_result=self._layer0_result,
                    layer1_result=self._layer1_result,
                    layer2_result=layer2_result,
                    recommendations=[
                        f"DOM 自动操作成功，捕获到日期 API",
                        f"控件类型: {layer2_result.get('picker_type', 'unknown')}",
                    ]
                )
            else:
                log(f"[Layer 2] ✗ 失败: {layer2_result.get('error', '未知错误')}")
        
        # ========== 第三层：截图 + LLM 兜底 ==========
        if not self.browser or not self.llm:
            log("[Layer 3] 跳过（无浏览器控制器或 LLM）")
        else:
            log("[Layer 3] 尝试截图 + LLM 分析...")
            layer3_result = await self._try_layer3_llm_vision(
                start_date, end_date, log
            )
            self._layer3_result = layer3_result
            
            if layer3_result['success']:
                log(f"[Layer 3] ✓ 成功！通过 LLM 视觉分析捕获到日期 API")
                return DateAPIExtractionResult(
                    success=True,
                    layer=3,
                    candidates=self.candidates,
                    best_candidate=layer3_result.get('best_candidate'),
                    data_count=layer3_result['data_count'],
                    replayed_data=layer3_result.get('replayed_data'),
                    layer0_result=self._layer0_result,
                    layer1_result=self._layer1_result,
                    layer2_result=self._layer2_result,
                    layer3_result=layer3_result,
                    recommendations=[
                        "通过 LLM 视觉分析成功定位日期控件",
                        f"LLM 识别到的操作: {layer3_result.get('llm_instruction', '')}",
                    ]
                )
            else:
                log(f"[Layer 3] ✗ 失败: {layer3_result.get('error', '未知错误')}")
        
        # ========== 全部失败 ==========
        log("[ALL LAYERS] ✗ 四层策略均失败")
        return DateAPIExtractionResult(
            success=False,
            layer=-1,  # -1 表示全部失败
            candidates=self.candidates,
            error="四层策略均失败",
            layer0_result=self._layer0_result,
            layer1_result=self._layer1_result,
            layer2_result=self._layer2_result,
            layer3_result=self._layer3_result,
            recommendations=[
                "全局变量扫描失败: " + (self._layer0_result or {}).get('error', ''),
                "纯 API 直连失败: " + (self._layer1_result or {}).get('error', ''),
                "DOM 自动检测失败: " + (self._layer2_result or {}).get('error', ''),
                "LLM 视觉分析失败: " + (self._layer3_result or {}).get('error', ''),
                "建议: 检查页面是否有反爬机制，或手动分析日期控件结构",
            ]
        )
    
    # ============ 第零层：JavaScript 全局变量扫描 ============
    
    async def _try_layer0_global_var_scan(
        self,
        start_date: str,
        end_date: str,
        log
    ) -> Dict[str, Any]:
        """
        第零层：扫描页面的 JavaScript 全局变量，查找 API 配置
        
        这是最精准的方法，因为：
        1. 不依赖网络请求捕获（页面可能使用缓存数据）
        2. 直接读取前端配置，获取完整的 API URL 和参数
        3. 绕过 JSONP/动态加载等复杂场景
        """
        try:
            if not self.browser or not self.browser.page:
                return {
                    'success': False,
                    'error': '浏览器未就绪',
                }
            
            page = self.browser.page
            
            # 1. 获取当前页面 URL，判断是否有已知模式
            current_url = page.url if hasattr(page, 'url') else ''
            known_pattern = None
            for domain, pattern in self.KNOWN_SITE_PATTERNS.items():
                if domain in current_url:
                    known_pattern = pattern
                    log(f"[Layer 0] 检测到已知网站模式: {domain}")
                    break
            
            # 2. 执行 JavaScript 扫描全局变量
            scan_result = await self._execute_global_var_scan(page, known_pattern, log)
            
            # P0-3 增强：如果全局变量扫描失败，尝试从注入钩子捕获的 API 记录中发现
            if (not scan_result or not scan_result.get('found')) and self.browser:
                intercepted = await self.browser.get_intercepted_apis() if hasattr(self.browser, 'get_intercepted_apis') else []
                if intercepted:
                    log(f"[Layer 0] 从 XHR/fetch 钩子获取到 {len(intercepted)} 条 API 调用记录")
                    hook_result = self._analyze_intercepted_apis(intercepted, log)
                    if hook_result and hook_result.get('found'):
                        scan_result = hook_result
                        log(f"[Layer 0] ✓ 从钩子记录中发现 API 配置")

            if not scan_result or not scan_result.get('found'):
                return {
                    'success': False,
                    'error': scan_result.get('error', '未找到包含 API 配置的全局变量'),
                }
            
            # 3. 从扫描结果中提取 API 信息
            api_configs = scan_result.get('api_configs', [])
            
            if not api_configs:
                return {
                    'success': False,
                    'error': '扫描到全局变量但未找到有效的 API 配置',
                }
            
            # 4. 选择最佳配置（优先选择包含日期参数的）
            best_config = None
            for config in api_configs:
                date_params = config.get('date_params', {})
                if date_params and len(date_params) >= 2:
                    # 有开始和结束日期参数，最理想
                    best_config = config
                    break
                elif date_params and not best_config:
                    best_config = config
            
            if not best_config:
                best_config = api_configs[0] if api_configs else None
            
            if not best_config:
                return {
                    'success': False,
                    'error': '未找到包含日期参数的 API 配置',
                }
            
            return {
                'success': True,
                'var_name': best_config.get('var_name', ''),
                'api_url': best_config.get('api_url', ''),
                'date_params': best_config.get('date_params', {}),
                'all_params': best_config.get('all_params', {}),
                'date_format': best_config.get('date_format', 'YYYY-MM-DD'),
                'is_jsonp': best_config.get('is_jsonp', False),
                'extra_info': best_config.get('extra_info', {}),
                'all_configs': api_configs,  # 保留所有发现的配置供调试
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': f'全局变量扫描异常: {str(e)}',
            }
    
    async def _execute_global_var_scan(
        self,
        page,
        known_pattern: Optional[Dict[str, Any]],
        log
    ) -> Dict[str, Any]:
        """
        执行 JavaScript 代码扫描全局变量
        
        扫描策略：
        1. 如果有已知模式，优先查找指定的全局变量
        2. 通用扫描：遍历 window 对象，查找包含 URL 和日期参数的对象
        """
        
        # 构建 JavaScript 扫描代码
        known_vars = known_pattern.get('global_vars', []) if known_pattern else []
        known_date_params = known_pattern.get('date_params', []) if known_pattern else []
        
        js_code = f'''
        () => {{
            const results = {{
                found: false,
                api_configs: [],
                error: null,
                scanned_vars: []
            }};
            
            // 已知变量名（优先检查）
            const knownVars = {json.dumps(known_vars)};
            
            // 常见日期参数名
            const dateParamPatterns = {json.dumps(known_date_params)} || [
                'START_DATE', 'END_DATE', 'startDate', 'endDate',
                'start_date', 'end_date', 'beginDate', 'fromDate', 'toDate',
                'searchDate', 'queryDate', 'date', 'dateFrom', 'dateTo'
            ];
            
            // API URL 特征
            const apiUrlPatterns = ['.do', '/api/', '/query', '/search', '/list', '.json', 'jsonCallBack'];
            
            // 辅助函数：检查字符串是否像 API URL
            function isApiUrl(str) {{
                if (typeof str !== 'string') return false;
                // 支持绝对 URL 和相对路径（如 /api/disc/announcement/annList）
                const isUrl = str.startsWith('http') || str.startsWith('//') || str.startsWith('/');
                if (!isUrl) return false;
                // 排除明显的静态资源和页面 URL
                if (str.endsWith('.html') || str.endsWith('.htm') || str.endsWith('.css') || 
                    str.endsWith('.js') || str.endsWith('.png') || str.endsWith('.jpg')) return false;
                return apiUrlPatterns.some(p => str.includes(p));
            }}
            
            // 辅助函数：检查对象是否包含日期参数
            function findDateParams(obj) {{
                if (!obj || typeof obj !== 'object') return {{}};
                const found = {{}};
                for (const key of Object.keys(obj)) {{
                    const keyUpper = key.toUpperCase();
                    for (const pattern of dateParamPatterns) {{
                        if (keyUpper === pattern.toUpperCase() || keyUpper.includes(pattern.toUpperCase())) {{
                            found[key] = obj[key];
                            break;
                        }}
                    }}
                }}
                return found;
            }}
            
            // 辅助函数：从对象中提取 API 配置
            function extractApiConfig(varName, obj) {{
                if (!obj || typeof obj !== 'object') return null;
                
                let apiUrl = null;
                let paramObj = null;
                let isJsonp = false;
                
                // 查找 URL 字段（增强版：支持 historyUrl/selfUrl 等模式）
                const urlKeys = ['historyUrl', 'queryUrl', 'searchUrl', 'listUrl',
                                 'announceUrl', 'requestUrl', 'serviceUrl', 'endpoint',
                                 'url', 'Url', 'URL', 'api', 'Api', 'API',
                                 'selfUrl', 'dataUrl', 'ajaxUrl'];
                
                // 优先选择 historyUrl/queryUrl 类（通常是带日期筛选的搜索端点）
                let fallbackUrl = null;
                for (const key of urlKeys) {{
                    if (obj[key] && typeof obj[key] === 'string') {{
                        const val = obj[key];
                        // 检查是否像 API URL（包括相对路径）
                        if (isApiUrl(val)) {{
                            // historyUrl 优先（通常是带日期参数的搜索 API）
                            if (key.toLowerCase().includes('history') || key.toLowerCase().includes('query') || key.toLowerCase().includes('search')) {{
                                apiUrl = val;
                                isJsonp = val.includes('jsonCallBack') || val.includes('callback');
                                break;
                            }}
                            if (!fallbackUrl) {{
                                fallbackUrl = val;
                            }}
                        }}
                    }}
                }}
                
                // 如果没找到优先 URL，使用 fallback
                if (!apiUrl && fallbackUrl) {{
                    apiUrl = fallbackUrl;
                    isJsonp = fallbackUrl.includes('jsonCallBack') || fallbackUrl.includes('callback');
                }}
                
                // 如果 URL 字段没找到，检查对象本身是否有 URL
                if (!apiUrl) {{
                    for (const key of Object.keys(obj)) {{
                        if (typeof obj[key] === 'string' && isApiUrl(obj[key])) {{
                            apiUrl = obj[key];
                            isJsonp = obj[key].includes('jsonCallBack') || obj[key].includes('callback');
                            break;
                        }}
                    }}
                }}
                
                // 查找参数对象
                const paramKeys = ['param', 'Param', 'params', 'Params', 'data', 'Data',
                                   'announceParam', 'queryParam', 'searchParam'];
                
                for (const key of paramKeys) {{
                    if (obj[key] && typeof obj[key] === 'object') {{
                        paramObj = obj[key];
                        break;
                    }}
                }}
                
                // 如果没有专门的参数字段，检查对象本身
                if (!paramObj && typeof obj === 'object') {{
                    const dateParams = findDateParams(obj);
                    if (Object.keys(dateParams).length > 0) {{
                        paramObj = obj;
                    }}
                }}
                
                if (!apiUrl) return null;
                
                const dateParams = paramObj ? findDateParams(paramObj) : {{}};
                
                return {{
                    var_name: varName,
                    api_url: apiUrl,
                    date_params: dateParams,
                    all_params: paramObj || {{}},
                    is_jsonp: isJsonp,
                    date_format: 'YYYY-MM-DD'  // 默认格式
                }};
            }}
            
            // 1. 优先检查已知变量
            for (const varName of knownVars) {{
                try {{
                    if (window[varName]) {{
                        results.scanned_vars.push(varName);
                        const config = extractApiConfig(varName, window[varName]);
                        if (config && config.api_url) {{
                            config.extra_info = {{ source: 'known_pattern' }};
                            results.api_configs.push(config);
                            results.found = true;
                        }}
                    }}
                }} catch (e) {{
                    // 忽略访问错误
                }}
            }}
            
            // 2. 如果已知变量没找到，进行通用扫描
            if (!results.found) {{
                const keywords = ['url', 'api', 'query', 'param', 'config', 'search', 
                                  'announce', 'bulletin', 'disclosure', 'report', 'news', 'list'];
                
                for (const key of Object.keys(window)) {{
                    try {{
                        // 跳过原生对象
                        if (['window', 'document', 'location', 'navigator', 'console', 
                             'performance', 'history', 'screen', 'localStorage', 'sessionStorage',
                             'JSON', 'Math', 'Date', 'Array', 'Object', 'String', 'Number',
                             'Boolean', 'Function', 'RegExp', 'Error', 'Promise'].includes(key)) {{
                            continue;
                        }}
                        
                        // 检查变量名是否包含关键词
                        const keyLower = key.toLowerCase();
                        const hasKeyword = keywords.some(kw => keyLower.includes(kw));
                        if (!hasKeyword) continue;
                        
                        const val = window[key];
                        if (!val || typeof val !== 'object') continue;
                        
                        results.scanned_vars.push(key);
                        const config = extractApiConfig(key, val);
                        if (config && config.api_url) {{
                            config.extra_info = {{ source: 'generic_scan' }};
                            results.api_configs.push(config);
                            results.found = true;
                        }}
                    }} catch (e) {{
                        // 忽略访问错误
                    }}
                }}
            }}
            
            return results;
        }}
        '''
        
        try:
            result = await page.evaluate(js_code)
            
            if result:
                log(f"[Layer 0] 扫描了 {len(result.get('scanned_vars', []))} 个变量")
                log(f"[Layer 0] 发现 {len(result.get('api_configs', []))} 个 API 配置")
                
                # 输出发现的配置供调试
                for cfg in result.get('api_configs', [])[:3]:
                    log(f"[Layer 0]   - {cfg.get('var_name')}: {cfg.get('api_url', '')[:60]}...")
                    log(f"[Layer 0]     日期参数: {list(cfg.get('date_params', {}).keys())}")
            
            return result
            
        except Exception as e:
            log(f"[Layer 0] JavaScript 执行失败: {e}")
            return {
                'found': False,
                'error': str(e),
            }
    
    def _build_candidate_from_global_var(self, layer0_result: Dict[str, Any]) -> Optional[DateAPICandidate]:
        """
        从全局变量扫描结果构建 DateAPICandidate
        
        关键增强：
        1. 从 URL query string 中解析出真正的 API 参数（如 isPagination, pageHelp.pageSize）
        2. 过滤掉 JS 对象中的非参数字段（如 selfUrl, historyUrl）
        3. 合并 URL 参数和对象级参数（日期字段等），避免遗漏
        """
        try:
            api_url = layer0_result.get('api_url', '')
            if not api_url:
                return None
            
            # 处理相对路径 URL（如 /api/disc/announcement/annList）
            if api_url.startswith('/') and not api_url.startswith('//'):
                if self.browser and self.browser.page:
                    try:
                        current_url = self.browser.page.url if hasattr(self.browser.page, 'url') else ''
                        if current_url:
                            parsed = urlparse(current_url)
                            api_url = f"{parsed.scheme}://{parsed.netloc}{api_url}"
                    except:
                        pass
            
            # 处理相对协议 URL
            if api_url.startswith('//'):
                api_url = 'https:' + api_url
            
            # ── 关键：先从 URL query string 中解析出 API 参数，再剥离 query string ──
            url_query_params = {}
            if '?' in api_url:
                parsed_url = urlparse(api_url)
                qs = parse_qs(parsed_url.query, keep_blank_values=True)
                for k, v_list in qs.items():
                    # parse_qs 返回的值是列表，取第一个；跳过 JSONP 占位符
                    if k in ('jsonCallBack', 'callback') and v_list and v_list[0] == '?':
                        url_query_params[k] = 'jsonCallback'  # 固定回调名
                    else:
                        url_query_params[k] = v_list[0] if len(v_list) == 1 else v_list
                # 剥离 query string，只保留 base URL
                api_url = urlunparse((
                    parsed_url.scheme, parsed_url.netloc, parsed_url.path,
                    parsed_url.params, '', parsed_url.fragment
                ))
            
            raw_obj_params = layer0_result.get('all_params', {})
            date_params = layer0_result.get('date_params', {})
            date_format = layer0_result.get('date_format', 'YYYY-MM-DD')
            extra_info = layer0_result.get('extra_info', {})
            
            # ── 从 JS 对象中提取有效 API 参数，过滤掉 URL 字段和非参数字段 ──
            obj_params = {}
            for k, v in raw_obj_params.items():
                # 跳过值为 URL 的字段（selfUrl, historyUrl 等）
                if isinstance(v, str) and (v.startswith('http') or v.startswith('//') or v.startswith('/')):
                    if '/' in v[2:]:  # 确实是路径而非普通值
                        continue
                # 跳过键名明显是配置字段的（非 API 参数）
                k_lower = k.lower()
                if any(skip in k_lower for skip in ['url', 'template', 'selector', 'element', 'container', 'target']):
                    continue
                # 跳过值是对象/函数的
                if isinstance(v, dict):
                    continue
                obj_params[k] = v
            
            # ── 合并参数：URL query params 为基础，对象级参数补充（含日期字段）──
            all_params = {**url_query_params}
            for k, v in obj_params.items():
                if k not in all_params:  # URL 参数优先
                    all_params[k] = v
            # 确保日期参数存在（可能在对象级而不在 URL 里）
            for dp in date_params.keys():
                if dp not in all_params:
                    all_params[dp] = ''
            
            # 推断 HTTP 方法
            method = 'GET'
            var_name = layer0_result.get('var_name', '').lower()
            url_path = urlparse(api_url).path.lower()
            # JSONP 接口（包含 jsonCallBack）一定是 GET
            is_jsonp = layer0_result.get('is_jsonp', False) or 'jsonCallBack' in all_params or 'callback' in all_params
            if is_jsonp:
                method = 'GET'
            elif any(kw in var_name for kw in ['history', 'search', 'query']):
                method = 'POST'
            elif any(kw in url_path for kw in ['annlist', 'search']):
                method = 'POST'
            
            # 构建日期参数格式映射
            date_param_formats = {}
            for param_name in date_params.keys():
                date_param_formats[param_name] = date_format
            
            # 如果没有日期参数但有 extra_info 中的提示，尝试添加常见日期参数
            if not date_param_formats and extra_info.get('source') == 'flag_obj':
                date_param_formats = {'seDate': date_format}
                all_params['seDate'] = []
            
            return DateAPICandidate(
                url=api_url,
                method=method,
                params=all_params,
                date_params=date_param_formats,
                confidence=0.95,
                resource_type='global_var',
            )
            
        except Exception as e:
            print(f"构建候选失败: {e}")
            return None
    
    # ============ 第一层：纯 API 直连 ============
    
    async def _try_layer1_api_direct(
        self,
        network_requests: Dict[str, Any],
        start_date: str,
        end_date: str,
        log
    ) -> Dict[str, Any]:
        """第一层：分析初始网络请求，识别日期 API 并重放"""
        try:
            # 1. 分析请求，识别候选 API
            candidates = self.analyze_requests(network_requests)
            
            if not candidates:
                return {
                    'success': False,
                    'error': '未在网络请求中发现日期相关的 API',
                    'data_count': 0
                }
            
            log(f"[Layer 1] 发现 {len(candidates)} 个候选日期 API")
            
            # 2. 验证前几个候选
            best_candidate = None
            best_data_count = 0
            
            for i, candidate in enumerate(candidates[:3]):
                log(f"[Layer 1] 验证候选 {i+1}: {candidate.url[:60]}...")
                result = await self.verify_candidate(candidate, start_date, end_date)
                candidate.is_verified = True
                candidate.verification_result = result
                
                if result['success'] and result['data_count'] > best_data_count:
                    # 健全性检查：防止“_ 时间戳”这类假阳性
                    ok, reason = self._looks_like_real_date_filter(candidate)
                    if not ok:
                        log(f"[Layer 1] 候选 {i+1} 可能是假阳性，跳过：{reason}")
                        continue

                    best_candidate = candidate
                    best_data_count = result['data_count']
                    log(f"[Layer 1] 候选 {i+1} 验证成功且通过检查，数据条数: {best_data_count}")
                else:
                    log(f"[Layer 1] 候选 {i+1} 验证失败: {result.get('error', '')}")
            
            if best_candidate:
                return {
                    'success': True,
                    'best_candidate': best_candidate,
                    'data_count': best_data_count,
                    'replayed_data': best_candidate.verification_result.get('parsed_data'),
                    'error': None
                }
            else:
                return {
                    'success': False,
                    'error': '所有候选 API 验证失败或被判定为假阳性（重放未得到可信日期筛选数据）',
                    'data_count': 0
                }
                
        except Exception as e:
            return {
                'success': False,
                'error': f'第一层执行异常: {str(e)}',
                'data_count': 0
            }
    
    # ============ 第二层：DOM 特征检测 ============
    
    async def _try_layer2_dom_detect(
        self,
        start_date: str,
        end_date: str,
        log
    ) -> Dict[str, Any]:
        """第二层：检测 DOM 中的日期控件，自动操作"""
        try:
            if not self.browser or not self.browser.page:
                return {
                    'success': False,
                    'error': '浏览器未就绪',
                    'data_count': 0
                }
            
            # 1. 检测日期控件
            picker_info = await self._detect_date_picker()
            
            if not picker_info.found:
                return {
                    'success': False,
                    'error': '未检测到常见日期控件（laydate/element-ui/ant-design/native）',
                    'data_count': 0
                }
            
            log(f"[Layer 2] 检测到日期控件: type={picker_info.picker_type}, selector={picker_info.trigger_selector}")
            
            # 2. 清空当前网络请求记录（准备捕获新请求）
            if hasattr(self.browser, 'network_requests'):
                self.browser.network_requests.clear()
            if hasattr(self.browser, 'api_requests'):
                self.browser.api_requests.clear()
            
            # 3. 操作日期控件
            operate_success = await self._operate_date_picker(picker_info, start_date, end_date, log)
            
            if not operate_success:
                return {
                    'success': False,
                    'error': '日期控件操作失败',
                    'data_count': 0,
                    'picker_type': picker_info.picker_type
                }
            
            # 4. 等待网络请求完成（P1-5: 用 CDP 空闲检测替代硬编码 sleep）
            if hasattr(self.browser, 'wait_for_network_idle'):
                await self.browser.wait_for_network_idle(timeout=5.0, idle_time=0.5)
            else:
                await asyncio.sleep(2)
            
            # 5. 分析新捕获的请求
            new_requests = self.browser.get_captured_requests() if hasattr(self.browser, 'get_captured_requests') else {}
            candidates = self.analyze_requests(new_requests)
            
            if not candidates:
                return {
                    'success': False,
                    'error': '操作日期控件后未捕获到新的日期 API 请求',
                    'data_count': 0,
                    'picker_type': picker_info.picker_type
                }
            
            log(f"[Layer 2] 捕获到 {len(candidates)} 个新的日期 API 候选")
            
            # 输出候选详情供调试
            for i, c in enumerate(candidates[:3]):
                log(f"[Layer 2]   候选 {i+1}: {c.url[:60]}...")
                log(f"[Layer 2]   日期参数: {c.date_params}")
            
            # 6. 验证候选
            best_candidate = None
            best_data_count = 0
            last_error = None
            
            for i, candidate in enumerate(candidates[:3]):
                log(f"[Layer 2] 验证候选 {i+1}...")
                result = await self.verify_candidate(candidate, start_date, end_date)
                
                if result['success'] and result['data_count'] > best_data_count:
                    best_candidate = candidate
                    best_data_count = result['data_count']
                    log(f"[Layer 2] 候选 {i+1} 验证成功，数据条数: {best_data_count}")
                else:
                    last_error = result.get('error', '未知')
                    log(f"[Layer 2] 候选 {i+1} 验证失败: {last_error}")
            
            if best_candidate:
                return {
                    'success': True,
                    'best_candidate': best_candidate,
                    'data_count': best_data_count,
                    'replayed_data': best_candidate.verification_result.get('parsed_data'),
                    'picker_type': picker_info.picker_type,
                    'error': None
                }
            else:
                return {
                    'success': False,
                    'error': f'捕获到的 API 验证失败: {last_error}',
                    'data_count': 0,
                    'picker_type': picker_info.picker_type
                }
                
        except Exception as e:
            return {
                'success': False,
                'error': f'第二层执行异常: {str(e)}',
                'data_count': 0
            }
    
    async def _detect_date_picker(self) -> DatePickerInfo:
        """检测页面中的日期控件（增强版：可见性检查 + 多元素聚合）"""
        if not self.browser or not self.browser.page:
            return DatePickerInfo(found=False)
        
        try:
            # 遍历常见选择器
            for config in self.DATE_PICKER_SELECTORS:
                trigger_selector = config['trigger']
                require_visible = config.get('require_visible', True)
                
                # 查找所有匹配的元素
                elements = await self.browser.page.query_selector_all(trigger_selector)
                if not elements:
                    continue
                
                # 筛选可见元素
                visible_element = None
                for el in elements:
                    try:
                        if require_visible and not await el.is_visible():
                            continue
                        visible_element = el
                        break
                    except:
                        continue
                
                if not visible_element:
                    continue
                
                # 获取更多信息
                info = await self.browser.page.evaluate('''
                    (el) => {
                        const rect = el.getBoundingClientRect();
                        const isInput = el.tagName === 'INPUT' || el.tagName === 'TEXTAREA';
                        const placeholder = el.getAttribute('placeholder') || '';
                        const isReadonly = el.hasAttribute('readonly');
                        const isRange = el.classList.contains('range') || 
                                       el.classList.contains('el-range-editor');
                        
                        // 检查同一容器内是否有多个日期输入框（范围选择器）
                        const parent = el.closest('.search-form, .filter, .query, form, [class*="search"], [class*="filter"], [class*="date"]') || el.parentElement?.parentElement;
                        let siblingInputs = [];
                        if (parent) {
                            const inputs = parent.querySelectorAll('input[placeholder*="YYYY"], input[placeholder*="yyyy"], input[placeholder*="日期"], input[type="date"]');
                            siblingInputs = Array.from(inputs).map(inp => ({
                                placeholder: inp.getAttribute('placeholder') || '',
                                name: inp.getAttribute('name') || '',
                                id: inp.getAttribute('id') || '',
                                isVisible: inp.offsetParent !== null
                            })).filter(inp => inp.isVisible);
                        }
                        
                        // 查找附近的提交按钮
                        let submitButton = null;
                        if (parent) {
                            const btns = parent.querySelectorAll('button, input[type="submit"], a.btn, [class*="search-btn"], [class*="query-btn"]');
                            for (const btn of btns) {
                                const text = btn.textContent.trim();
                                if (['查询', '搜索', '查找', '检索', '提交', '确定', 'Search', 'Query', 'Submit'].some(kw => text.includes(kw))) {
                                    submitButton = {
                                        text: text,
                                        tag: btn.tagName,
                                        className: btn.className
                                    };
                                    break;
                                }
                            }
                        }
                        
                        return {
                            tag: el.tagName,
                            isInput: isInput,
                            isReadonly: isReadonly,
                            isRange: isRange || siblingInputs.length >= 2,
                            placeholder: placeholder,
                            className: el.className,
                            rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
                            siblingDateInputs: siblingInputs,
                            submitButton: submitButton
                        };
                    }
                ''', visible_element)
                
                if info:
                    return DatePickerInfo(
                        found=True,
                        picker_type=config['type'],
                        selector=trigger_selector,
                        trigger_selector=trigger_selector,
                        is_input=info.get('isInput', False),
                        is_range=info.get('isRange', False),
                        confirm_selector=config.get('confirm', ''),
                        extra_info=info
                    )
            
            return DatePickerInfo(found=False)
            
        except Exception as e:
            print(f"检测日期控件失败: {e}")
            return DatePickerInfo(found=False)
    
    async def _operate_date_picker(
        self,
        picker_info: DatePickerInfo,
        start_date: str,
        end_date: str,
        log
    ) -> bool:
        """操作日期控件设置日期范围（增强版：优先使用 fill+submit 泛化策略）"""
        if not self.browser or not self.browser.page:
            return False
        
        try:
            page = self.browser.page
            extra = picker_info.extra_info or {}
            
            # ========== 策略0（最优先）：泛化 Fill + Submit 策略 ==========
            # 对任何检测到的日期 INPUT 输入框，优先尝试 fill 日期值 + 点击提交按钮
            # readonly 输入框也能处理（通过 CDP Input.insertText 引擎级键盘输入）
            if extra.get('isInput'):
                log(f"[Layer 2] 使用泛化 Fill+Submit 策略 (控件类型: {picker_info.picker_type}, readonly: {extra.get('isReadonly', False)})")
                result = await self._operate_fill_and_submit(picker_info, start_date, end_date, log)
                if result:
                    return True
                log(f"[Layer 2] Fill+Submit 策略失败，尝试控件特定策略...")
            
            # ========== 策略1：generic-input 类型 → fill+submit ==========
            if picker_info.picker_type in ('generic-input', 'native'):
                log(f"[Layer 2] 使用 generic-input Fill+Submit 策略")
                return await self._operate_fill_and_submit(picker_info, start_date, end_date, log)
            
            # ========== 策略2：Laydate → 先尝试 fill+submit，失败再走日历点击 ==========
            elif picker_info.picker_type == 'laydate':
                log(f"[Layer 2] Laydate: 优先尝试 fill+submit 直接填写日期")
                result = await self._operate_fill_and_submit(picker_info, start_date, end_date, log)
                if result:
                    return True
                log(f"[Layer 2] Laydate fill+submit 失败，回退到日历点击")
                return await self._operate_laydate(picker_info, start_date, end_date, log)
            
            # ========== 策略3：Element UI / Ant Design ==========
            elif picker_info.picker_type in ('element-ui', 'ant-design'):
                return await self._operate_modern_picker(picker_info, start_date, end_date, log)
            
            # ========== 策略4：Bootstrap → 优先 fill+submit ==========
            elif picker_info.picker_type == 'bootstrap':
                log(f"[Layer 2] Bootstrap datepicker: 优先尝试 fill+submit")
                result = await self._operate_fill_and_submit(picker_info, start_date, end_date, log)
                if result:
                    return True
                # fallback: 点击触发
                await page.click(picker_info.trigger_selector)
                await asyncio.sleep(0.5)
                return False
            
            else:
                # ========== 兜底：尝试 fill+submit ==========
                log(f"[Layer 2] 未知控件类型 '{picker_info.picker_type}'，尝试 fill+submit")
                return await self._operate_fill_and_submit(picker_info, start_date, end_date, log)
                    
        except Exception as e:
            log(f"[Layer 2] 操作日期控件异常: {e}")
            return False
    
    async def _operate_fill_and_submit(
        self,
        picker_info: DatePickerInfo,
        start_date: str,
        end_date: str,
        log
    ) -> bool:
        """
        泛化的日期填写+提交策略
        
        核心思路：
        1. 找到所有可见的日期输入框（包括 readonly 的 Laydate 输入框）
        2. 三级递进策略填写日期值：
           - Playwright fill()（非 readonly）
           - CDP Input.insertText（引擎级键盘输入，兼容 readonly）
           - JS 强制设值（兜底）
        3. 找到附近的提交/查询按钮并点击
        
        这是最通用的策略，适用于：
        - 上交所（SSE）：Laydate readonly input[lay-key]（通过 CDP 填写）
        - 深交所（SZSE）：input[placeholder="YYYY-MM-DD"] + "查询"按钮
        - 大多数政府/金融网站的日期筛选表单
        - 任何有可见日期输入框 + 提交按钮的页面
        """
        try:
            page = self.browser.page
            extra = picker_info.extra_info or {}
            
            # 1. 查找所有可见的日期输入框
            date_inputs = await page.evaluate('''
                () => {
                    const selectors = [
                        'input[placeholder*="YYYY"]',
                        'input[placeholder*="yyyy"]',
                        'input[placeholder*="日期"]',
                        'input[placeholder*="date" i]',
                        'input[type="date"]',
                        'input[lay-key]',
                    ];
                    
                    const results = [];
                    const seen = new Set();
                    
                    for (const sel of selectors) {
                        const elements = document.querySelectorAll(sel);
                        for (const el of elements) {
                            // 跳过不可见的元素
                            if (el.offsetParent === null && getComputedStyle(el).display === 'none') continue;
                            const rect = el.getBoundingClientRect();
                            if (rect.width === 0 || rect.height === 0) continue;
                            
                            // 排除搜索框（通过 placeholder 内容排除）
                            const ph = (el.getAttribute('placeholder') || '').toLowerCase();
                            if (ph.includes('代码') || ph.includes('搜索') || ph.includes('关键') || ph.includes('code') || ph.includes('keyword')) continue;
                            
                            // 唯一性检查
                            const key = `${rect.x},${rect.y}`;
                            if (seen.has(key)) continue;
                            seen.add(key);
                            
                            results.push({
                                selector: el.id ? `#${el.id}` : null,
                                placeholder: el.getAttribute('placeholder') || '',
                                name: el.getAttribute('name') || '',
                                readonly: el.hasAttribute('readonly'),
                                x: rect.x,
                                y: rect.y,
                                index: results.length
                            });
                        }
                    }
                    
                    // 按 x 坐标排序（从左到右），通常第一个是开始日期，第二个是结束日期
                    results.sort((a, b) => a.x - b.x || a.y - b.y);
                    return results;
                }
            ''')
            
            if not date_inputs or len(date_inputs) == 0:
                log("[Layer 2] Fill+Submit: 未找到可见的日期输入框")
                return False
            
            log(f"[Layer 2] Fill+Submit: 找到 {len(date_inputs)} 个日期输入框")
            
            # 2. 获取所有匹配的输入框元素（用于 fill）
            all_input_selectors = [
                'input[placeholder*="YYYY"]',
                'input[placeholder*="yyyy"]',
                'input[placeholder*="日期"]',
                'input[type="date"]',
                'input[lay-key]',
            ]
            
            # 收集可见的日期输入框元素列表
            visible_date_elements = []
            for sel in all_input_selectors:
                elements = await page.query_selector_all(sel)
                for el in elements:
                    try:
                        if await el.is_visible():
                            # 排除搜索框
                            ph = await el.get_attribute('placeholder') or ''
                            if any(kw in ph for kw in ['代码', '搜索', '关键', '简称', '拼音']):
                                continue
                            visible_date_elements.append(el)
                    except:
                        continue
            
            if not visible_date_elements:
                log("[Layer 2] Fill+Submit: 可见日期输入框过滤后为空")
                return False
            
            # 去重（同一位置的元素只保留一个）
            unique_elements = []
            seen_positions = set()
            for el in visible_date_elements:
                try:
                    box = await el.bounding_box()
                    if box:
                        pos_key = f"{int(box['x'])},{int(box['y'])}"
                        if pos_key not in seen_positions:
                            seen_positions.add(pos_key)
                            unique_elements.append(el)
                except:
                    unique_elements.append(el)
            
            visible_date_elements = unique_elements
            log(f"[Layer 2] Fill+Submit: 去重后 {len(visible_date_elements)} 个日期输入框")
            
            # 3. 填写日期值（三级策略：Playwright fill → CDP Input.insertText → JS 兜底）
            async def _safe_fill(el, value, label):
                """
                安全填写日期值，三级递进策略：
                  1. Playwright fill()：最简单，但 readonly 会失败
                  2. CDP Input.insertText：在浏览器引擎层模拟真实键盘输入，
                     触发完整原生事件链 (keydown→input→keyup)，
                     所有前端框架（Laydate/ElementUI/AntD）都能响应，
                     且不受 readonly 限制
                  3. JS 强制设值：最后手段，直接设 el.value 并手动派发事件
                """
                try:
                    await el.click()
                    await asyncio.sleep(0.3)
                    
                    # ── 策略1: Playwright fill（非 readonly 时首选）──
                    try:
                        await el.fill(value)
                        await el.dispatch_event('change')
                        await el.dispatch_event('input')
                        log(f"[Layer 2] Fill+Submit: 填写{label} {value} (Playwright fill)")
                        return
                    except Exception:
                        pass  # readonly 或其他原因失败，继续下一策略
                    
                    # ── 策略2: CDP Input.insertText（readonly 友好）──
                    cdp = self.browser.cdp_session if self.browser and hasattr(self.browser, 'cdp_session') else None
                    if cdp:
                        try:
                            # 移除 readonly 属性，选中已有内容
                            await el.evaluate('(el) => { el.removeAttribute("readonly"); el.focus(); el.select(); }')
                            await asyncio.sleep(0.1)
                            # CDP 引擎级键盘输入：触发完整原生事件链
                            await cdp.send('Input.insertText', {'text': value})
                            await asyncio.sleep(0.1)
                            # 补发 change 事件（部分框架依赖 blur 时的 change）
                            await el.dispatch_event('change')
                            await el.dispatch_event('input')
                            log(f"[Layer 2] Fill+Submit: 填写{label} {value} (CDP insertText)")
                            return
                        except Exception:
                            pass  # CDP 不可用时继续
                    
                    # ── 策略3: JS 强制设值（兜底）──
                    await el.evaluate(
                        '(el, v) => {'
                        '  el.removeAttribute("readonly");'
                        '  const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;'
                        '  nativeSetter.call(el, v);'
                        '  el.dispatchEvent(new Event("input", {bubbles: true}));'
                        '  el.dispatchEvent(new Event("change", {bubbles: true}));'
                        '}',
                        value
                    )
                    log(f"[Layer 2] Fill+Submit: 填写{label} {value} (JS 设值)")
                except Exception as fe:
                    log(f"[Layer 2] Fill+Submit: 填写{label}失败: {fe}")

            if len(visible_date_elements) >= 2:
                await _safe_fill(visible_date_elements[0], start_date, "开始日期")
                await asyncio.sleep(0.3)
                await _safe_fill(visible_date_elements[1], end_date, "结束日期")
            elif len(visible_date_elements) == 1:
                await _safe_fill(visible_date_elements[0], start_date, "日期")
            
            # 4. 关闭可能弹出的日历面板（点击页面空白处）
            await asyncio.sleep(0.5)
            try:
                # 按 Escape 关闭可能弹出的日历面板
                await page.keyboard.press('Escape')
                await asyncio.sleep(0.3)
            except:
                pass
            
            # 5. 查找并点击提交/查询按钮
            submit_clicked = await self._click_submit_button(page, log)
            
            if submit_clicked:
                log("[Layer 2] Fill+Submit: 成功点击提交按钮")
                await asyncio.sleep(1.0)
                return True
            else:
                # 即使没找到提交按钮，也尝试按 Enter
                log("[Layer 2] Fill+Submit: 未找到提交按钮，尝试按 Enter")
                await page.keyboard.press('Enter')
                await asyncio.sleep(1.0)
                return True
            
        except Exception as e:
            log(f"[Layer 2] Fill+Submit 异常: {e}")
            return False
    
    async def _click_submit_button(self, page, log) -> bool:
        """
        查找并点击提交/查询按钮（泛化版本）
        
        搜索策略：
        1. 优先匹配包含"查询/搜索/确定"等文字的 button/a 元素
        2. 在日期输入框的父容器中查找
        3. 扩大范围到整个表单区域
        """
        # 提交按钮的文字关键词（中英文）
        submit_keywords = ['查询', '搜索', '查找', '检索', '提交', '确定', '筛选',
                           'Search', 'Query', 'Submit', 'Filter', 'Go']
        
        # 按钮选择器（从具体到通用）
        for keyword in submit_keywords:
            selectors = [
                f'button:has-text("{keyword}")',
                f'a:has-text("{keyword}")',
                f'input[value="{keyword}"]',
                f'[class*="btn"]:has-text("{keyword}")',
                f'[class*="search"]:has-text("{keyword}")',
            ]
            
            for sel in selectors:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click()
                        log(f"[Layer 2] 点击提交按钮: '{keyword}' ({sel})")
                        return True
                except:
                    continue
        
        # 兜底：使用 JavaScript 查找
        result = await page.evaluate('''
            () => {
                const keywords = ['查询', '搜索', '查找', '检索', '提交', '确定', '筛选'];
                const btns = document.querySelectorAll('button, a.btn, input[type="submit"], [class*="btn"], [role="button"]');
                for (const btn of btns) {
                    if (btn.offsetParent === null) continue;  // 不可见
                    const text = btn.textContent.trim();
                    if (text.length > 10) continue;  // 文字太长的不是按钮
                    for (const kw of keywords) {
                        if (text.includes(kw)) {
                            btn.click();
                            return { success: true, text: text };
                        }
                    }
                }
                return { success: false };
            }
        ''')
        
        if result and result.get('success'):
            log(f"[Layer 2] JS 点击提交按钮: '{result.get('text', '')}'")
            return True
        
        return False
    
    async def _operate_laydate(
        self,
        picker_info: DatePickerInfo,
        start_date: str,
        end_date: str,
        log
    ) -> bool:
        """
        操作 Layui Laydate 日期选择器（点击型，非输入型）
        
        Laydate 需要通过点击日历中的日期来选择，不支持直接输入。
        """
        try:
            page = self.browser.page
            
            # 1. 点击触发日期选择器
            await page.click(picker_info.trigger_selector)
            await asyncio.sleep(0.5)
            
            # 2. 等待日历弹层出现
            try:
                await page.wait_for_selector('.layui-laydate', timeout=3000)
                log("[Layer 2] Laydate 弹层已出现")
            except:
                log("[Layer 2] Laydate 弹层未出现")
                return False
            
            # 3. 解析日期
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            
            # 4. 直接手动选择日期（不使用"近一月"等快捷按钮，因为快捷按钮
            #    设置的日期是相对于今天的，不是用户请求的目标日期，会导致
            #    捕获的 API 带着错误日期，进而影响验证和后续流程）
            # 5. 手动点击选择日期
            log(f"[Layer 2] 没有匹配的快捷按钮，尝试手动选择: {start_date} ~ {end_date}")
            
            # 检查是否是范围选择器
            panels = await page.query_selector_all('.layui-laydate .laydate-main-list-0, .layui-laydate .laydate-main-list-1')
            is_range = len(panels) >= 2
            
            # 选择开始日期
            start_success = await self._select_date_in_laydate(page, start_dt, 0, log)
            
            if is_range:
                await asyncio.sleep(0.3)
                end_success = await self._select_date_in_laydate(page, end_dt, 1, log)
            
            # 点击确定
            await asyncio.sleep(0.3)
            confirm_btn = await page.query_selector('.laydate-btns-confirm')
            if confirm_btn and await confirm_btn.is_visible():
                await confirm_btn.click()
                await asyncio.sleep(0.5)
                log("[Layer 2] 点击确定按钮")
                return True
            else:
                log("[Layer 2] 没有确定按钮，日期选择可能已自动生效")
                return True
            
        except Exception as e:
            log(f"[Layer 2] Laydate 操作异常: {e}")
            return False
    
    async def _operate_modern_picker(
        self,
        picker_info: DatePickerInfo,
        start_date: str,
        end_date: str,
        log
    ) -> bool:
        """操作现代框架的日期选择器 (Element UI / Ant Design)"""
        try:
            page = self.browser.page
            
            # 1. 点击触发
            await page.click(picker_info.trigger_selector)
            await asyncio.sleep(0.5)
            
            # 2. 尝试直接填充输入框
            if picker_info.is_range:
                # 范围选择器通常有两个输入框
                inputs = await page.query_selector_all(f'{picker_info.trigger_selector} input')
                if len(inputs) >= 2:
                    await inputs[0].fill(start_date)
                    await inputs[1].fill(end_date)
                    log("[Layer 2] 填充范围日期输入框")
            else:
                await page.fill(picker_info.trigger_selector + ' input', start_date)
            
            # 3. 点击确认按钮（如果有）
            if picker_info.confirm_selector:
                try:
                    await page.wait_for_selector(picker_info.confirm_selector, timeout=2000)
                    await page.click(picker_info.confirm_selector)
                    log("[Layer 2] 点击确认按钮")
                except:
                    pass
            
            # 4. 按 Enter 确认
            await page.keyboard.press('Enter')
            await asyncio.sleep(0.3)
            
            return True
            
        except Exception as e:
            log(f"[Layer 2] 现代框架日期选择器操作异常: {e}")
            return False
    
    # ============ 第三层：截图 + LLM 分析 ============
    
    async def _try_layer3_llm_vision(
        self,
        start_date: str,
        end_date: str,
        log
    ) -> Dict[str, Any]:
        """第三层：截图让 LLM 分析日期控件位置和操作方式"""
        try:
            if not self.browser or not self.llm:
                return {
                    'success': False,
                    'error': '缺少浏览器或 LLM',
                    'data_count': 0
                }
            
            # 1. 截图
            screenshot_base64 = await self.browser.take_screenshot_base64()
            if not screenshot_base64:
                return {
                    'success': False,
                    'error': '截图失败',
                    'data_count': 0
                }
            
            log("[Layer 3] 截图成功，正在调用 LLM 分析...")
            
            # 2. 调用 LLM 分析日期控件
            llm_result = await self._ask_llm_for_date_picker(screenshot_base64, log)
            
            if not llm_result.get('found'):
                return {
                    'success': False,
                    'error': 'LLM 未识别到日期控件',
                    'data_count': 0,
                    'llm_instruction': llm_result.get('instruction', '')
                }
            
            log(f"[Layer 3] LLM 分析结果: {llm_result.get('instruction', '')[:100]}...")
            
            # 3. 清空网络请求记录
            if hasattr(self.browser, 'network_requests'):
                self.browser.network_requests.clear()
            if hasattr(self.browser, 'api_requests'):
                self.browser.api_requests.clear()
            
            # 4. 根据 LLM 指示执行操作
            operate_success = await self._execute_llm_instruction(llm_result, start_date, end_date, log)
            
            if not operate_success:
                return {
                    'success': False,
                    'error': '按 LLM 指示操作失败',
                    'data_count': 0,
                    'llm_instruction': llm_result.get('instruction', '')
                }
            
            # 5. 等待并捕获请求（P1-5: 用 CDP 空闲检测替代硬编码 sleep）
            if hasattr(self.browser, 'wait_for_network_idle'):
                await self.browser.wait_for_network_idle(timeout=5.0, idle_time=0.5)
            else:
                await asyncio.sleep(2)
            new_requests = self.browser.get_captured_requests() if hasattr(self.browser, 'get_captured_requests') else {}
            candidates = self.analyze_requests(new_requests)
            
            if not candidates:
                return {
                    'success': False,
                    'error': '按 LLM 指示操作后未捕获到日期 API',
                    'data_count': 0,
                    'llm_instruction': llm_result.get('instruction', '')
                }
            
            # 6. 验证候选
            for candidate in candidates[:3]:
                result = await self.verify_candidate(candidate, start_date, end_date)
                if result['success'] and result['data_count'] > 0:
                    return {
                        'success': True,
                        'best_candidate': candidate,
                        'data_count': result['data_count'],
                        'replayed_data': result.get('parsed_data'),
                        'llm_instruction': llm_result.get('instruction', ''),
                        'error': None
                    }
            
            return {
                'success': False,
                'error': '捕获的 API 验证失败',
                'data_count': 0,
                'llm_instruction': llm_result.get('instruction', '')
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': f'第三层执行异常: {str(e)}',
                'data_count': 0
            }
    
    async def _ask_llm_for_date_picker(self, screenshot_base64: str, log) -> Dict[str, Any]:
        """让 LLM 分析截图，识别日期控件"""
        try:
            from llm_agent import AttachmentData
            
            prompt = """请分析这个网页截图，找到日期选择控件（用于筛选数据的日期范围）。

注意：页面中可能有多个输入框，请准确区分：
- 日期选择控件：通常显示 YYYY-MM-DD 格式，可能有日历图标，用于筛选时间范围
- 搜索框：用于输入关键词或代码搜索，如"请输入证券代码"

【重要】日期控件分为两种类型，请务必准确判断：
1. 点击型（click）：点击后弹出日历面板，必须在日历中点击选择日期，不能直接输入
   - 特征：输入框通常是 readonly，点击后弹出日历/日期选择面板
   - 常见：laydate、部分 element-ui、很多政府/金融网站
2. 输入型（input）：可以直接在输入框中输入日期值
   - 特征：输入框可编辑，支持键盘输入
   - 常见：input[type="date"]、普通文本框

请回答以下问题，用 JSON 格式返回：

1. found: 是否找到日期控件（true/false）
2. input_mode: 【必填】操作方式，只能是 "click" 或 "input"
   - "click": 必须点击日历选择日期（不能直接输入）
   - "input": 可以直接在输入框填写日期
3. picker_type: 控件类型（日历弹窗/日期输入框/日期范围选择器）
4. position: 控件精确位置描述
5. trigger_css_hints: 用于定位的 CSS 特征
6. is_range: 是否是日期范围选择器（true/false）
7. has_confirm: 选完日期后是否需要点击确认按钮（true/false）
8. nearby_elements: 附近有什么其他元素（帮助区分）
9. instruction: 具体操作步骤描述（必须与 input_mode 一致！）
   - 如果是 click 模式：描述如何点击日历选择日期
   - 如果是 input 模式：描述如何填写日期值

返回格式（点击型示例）：
```json
{
  "found": true,
  "input_mode": "click",
  "picker_type": "日期范围选择器",
  "position": "页面顶部蓝色区域",
  "trigger_css_hints": "input[lay-key], .laydate相关class",
  "is_range": true,
  "has_confirm": true,
  "nearby_elements": "下方是证券代码搜索框，不要操作那个",
  "instruction": "1. 点击日期文字触发日历 2. 在弹出的日历面板中点击选择开始日期 3. 点击选择结束日期 4. 点击确定按钮"
}
```

返回格式（输入型示例）：
```json
{
  "found": true,
  "input_mode": "input",
  "picker_type": "日期输入框",
  "position": "表单第一行",
  "trigger_css_hints": "input[type='date'], input[name='startDate']",
  "is_range": true,
  "has_confirm": false,
  "nearby_elements": "旁边是查询按钮",
  "instruction": "1. 在开始日期输入框填写 YYYY-MM-DD 格式日期 2. 在结束日期输入框填写日期 3. 点击查询按钮"
}
```

如果没有找到日期控件：
```json
{
  "found": false,
  "reason": "页面中没有明显的日期筛选控件"
}
```
"""
            
            attachments = [
                AttachmentData(
                    filename="page_screenshot.jpg",
                    base64_data=screenshot_base64,
                    mime_type="image/jpeg"
                )
            ]
            
            response = self.llm._call_llm(
                system_prompt="你是一个网页分析专家，擅长识别页面中的交互控件。",
                user_prompt=prompt,
                attachments=attachments,
                temperature=0.1
            )
            
            # 解析 JSON
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.S)
            if json_match:
                return json.loads(json_match.group(1))
            
            # 尝试直接解析
            try:
                return json.loads(response)
            except:
                return {'found': False, 'reason': 'LLM 返回格式解析失败'}
                
        except Exception as e:
            log(f"[Layer 3] LLM 分析异常: {e}")
            return {'found': False, 'reason': str(e)}
    
    async def _execute_llm_instruction(
        self,
        llm_result: Dict[str, Any],
        start_date: str,
        end_date: str,
        log
    ) -> bool:
        """
        根据 LLM 分析结果执行操作
        
        增强版：优先尝试泛化的 Fill+Submit 策略（不依赖 LLM 指令细节），
        失败后再按 LLM 指定的 input_mode 操作。
        """
        try:
            page = self.browser.page
            
            # 获取 LLM 指定的操作模式
            input_mode = llm_result.get('input_mode', 'click')
            is_range = llm_result.get('is_range', False)
            has_confirm = llm_result.get('has_confirm', False)
            trigger_css_hints = llm_result.get('trigger_css_hints', '')
            instruction = llm_result.get('instruction', '')
            
            log(f"[Layer 3] LLM 指定操作模式: {input_mode}")
            log(f"[Layer 3] 操作指令: {instruction[:80]}...")
            
            # ========== 优先策略：泛化 Fill+Submit ==========
            # 无论 LLM 指定什么模式，先尝试最通用的方式
            log("[Layer 3] 优先尝试泛化 Fill+Submit 策略...")
            fill_submit_picker = DatePickerInfo(
                found=True,
                picker_type='generic-input',
                selector='input[placeholder*="YYYY"]',
                trigger_selector='input[placeholder*="YYYY"]',
                is_input=True,
                is_range=is_range,
                extra_info={'isInput': True, 'isReadonly': False}
            )
            fill_result = await self._operate_fill_and_submit(fill_submit_picker, start_date, end_date, log)
            if fill_result:
                log("[Layer 3] Fill+Submit 策略成功")
                return True
            log("[Layer 3] Fill+Submit 策略未成功，回退到 LLM 指定模式...")
            
            # ========== 回退：按 LLM 指定模式操作 ==========
            if input_mode == 'input':
                return await self._execute_input_mode(page, start_date, end_date, is_range, has_confirm, trigger_css_hints, log)
            else:
                result = await self._execute_click_mode(page, start_date, end_date, is_range, has_confirm, trigger_css_hints, log)
                # 点击模式完成后，确保点击提交按钮
                if result:
                    await asyncio.sleep(0.5)
                    await self._click_submit_button(page, log)
                return result
            
        except Exception as e:
            log(f"[Layer 3] 执行 LLM 指示异常: {e}")
            return False
    
    async def _execute_input_mode(
        self,
        page,
        start_date: str,
        end_date: str,
        is_range: bool,
        has_confirm: bool,
        trigger_css_hints: str,
        log
    ) -> bool:
        """
        输入型日期控件：直接填写日期值
        """
        log("[Layer 3] 使用输入模式填写日期...")
        
        try:
            # 查找日期输入框（排除搜索框）
            date_input_selectors = [
                'input[type="date"]',
                'input[name*="date" i]',
                'input[name*="Date" i]',
                'input[id*="date" i]',
                'input[placeholder*="日期"]',
                'input[placeholder*="date" i]',
                'input[placeholder*="YYYY"]',
            ]
            
            # 收集所有可能的日期输入框
            date_inputs = []
            for sel in date_input_selectors:
                try:
                    elements = await page.query_selector_all(sel)
                    for el in elements:
                        # 排除搜索框
                        placeholder = await el.get_attribute('placeholder') or ''
                        name = await el.get_attribute('name') or ''
                        if '代码' in placeholder or '搜索' in placeholder or 'code' in name.lower():
                            continue
                        # 检查是否可见
                        if await el.is_visible():
                            date_inputs.append(el)
                except:
                    continue
            
            if not date_inputs:
                log("[Layer 3] 未找到可用的日期输入框")
                return False
            
            log(f"[Layer 3] 找到 {len(date_inputs)} 个日期输入框")
            
            # 填写日期
            if is_range and len(date_inputs) >= 2:
                # 范围选择：填写开始和结束日期
                await date_inputs[0].fill(start_date)
                log(f"[Layer 3] 填写开始日期: {start_date}")
                await asyncio.sleep(0.3)
                
                await date_inputs[1].fill(end_date)
                log(f"[Layer 3] 填写结束日期: {end_date}")
                await asyncio.sleep(0.3)
            else:
                # 单日期或只有一个输入框
                if is_range:
                    # 尝试填写范围格式
                    await date_inputs[0].fill(f"{start_date} - {end_date}")
                else:
                    await date_inputs[0].fill(start_date)
                log(f"[Layer 3] 填写日期: {start_date}")
            
            # 触发 change 事件
            for inp in date_inputs[:2]:
                await inp.dispatch_event('change')
            
            # 点击确认/提交按钮
            if has_confirm:
                await self._click_confirm_button(page, log)
            
            # 总是尝试点击提交按钮（查询/搜索）
            await asyncio.sleep(0.3)
            await self._click_submit_button(page, log)
            
            return True
            
        except Exception as e:
            log(f"[Layer 3] 输入模式失败: {e}")
            return False
    
    async def _execute_click_mode(
        self,
        page,
        start_date: str,
        end_date: str,
        is_range: bool,
        has_confirm: bool,
        trigger_css_hints: str,
        log
    ) -> bool:
        """
        点击型日期控件：通过日历选择日期
        """
        log("[Layer 3] 使用点击模式操作日历...")
        
        try:
            # ========== 策略1: 优先使用 laydate 特征 ==========
            date_inputs = await page.query_selector_all('input[lay-key]')
            if date_inputs:
                log(f"[Layer 3] 发现 {len(date_inputs)} 个 laydate 日期输入框")
                
                # 点击第一个日期输入框触发日历
                await date_inputs[0].click()
                await asyncio.sleep(0.5)
                
                # 等待日历弹层出现
                try:
                    await page.wait_for_selector('.layui-laydate', timeout=3000)
                    log("[Layer 3] Laydate 日历弹层已出现")
                    return await self._operate_laydate_for_llm(start_date, end_date, log)
                except:
                    log("[Layer 3] Laydate 弹层未出现，尝试其他方式")
            
            # ========== 策略2: 查找日期文字并点击 ==========
            date_text_selectors = [
                '//*[contains(text(), "年") and contains(text(), "月") and contains(text(), "日")]',
                '//*[contains(text(), "-") and contains(text(), "20")]',
                '.date-range', '[class*="date-text"]'
            ]
            
            clicked = False
            for sel in date_text_selectors:
                try:
                    elements = await page.query_selector_all(sel)
                    for el in elements[:3]:
                        text = await el.text_content() or ''
                        if re.search(r'\d{4}[年/-]\d{1,2}[月/-]\d{1,2}', text):
                            if await el.is_visible():
                                await el.click()
                                await asyncio.sleep(0.5)
                                clicked = True
                                log(f"[Layer 3] 点击了日期文字: {text[:30]}...")
                                break
                    if clicked:
                        break
                except:
                    continue
            
            if clicked:
                await asyncio.sleep(1)
                laydate = await page.query_selector('.layui-laydate')
                if laydate:
                    return await self._operate_laydate_for_llm(start_date, end_date, log)
                
                # 检查其他类型的日历弹层
                other_pickers = [
                    '.el-picker-panel', '.ant-picker-dropdown', '.datepicker',
                    '[class*="calendar"]', '[class*="picker-panel"]'
                ]
                for picker_sel in other_pickers:
                    picker = await page.query_selector(picker_sel)
                    if picker and await picker.is_visible():
                        log(f"[Layer 3] 检测到日历弹层: {picker_sel}")
                        return await self._operate_generic_calendar(page, start_date, end_date, is_range, has_confirm, log)
            
            # ========== 策略3: 查找日历图标 ==========
            icon_selectors = [
                '.laydate-icon', '[class*="calendar"]', '.date-icon',
                '.fa-calendar', '.icon-calendar', 'i[class*="date"]',
                'svg[class*="calendar"]'
            ]
            for sel in icon_selectors:
                try:
                    icon = await page.query_selector(sel)
                    if icon and await icon.is_visible():
                        await icon.click()
                        await asyncio.sleep(0.5)
                        log(f"[Layer 3] 点击了日历图标: {sel}")
                        
                        laydate = await page.query_selector('.layui-laydate')
                        if laydate:
                            return await self._operate_laydate_for_llm(start_date, end_date, log)
                        break
                except:
                    continue
            
            log("[Layer 3] 未能成功触发日期选择")
            return False
            
        except Exception as e:
            log(f"[Layer 3] 点击模式失败: {e}")
            return False
    
    async def _operate_generic_calendar(
        self,
        page,
        start_date: str,
        end_date: str,
        is_range: bool,
        has_confirm: bool,
        log
    ) -> bool:
        """操作通用日历控件（非 laydate）"""
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            
            # 尝试点击日期单元格
            day = start_dt.day
            day_clicked = await page.evaluate(f'''
                () => {{
                    // 查找可见的日历面板
                    const panels = document.querySelectorAll('[class*="picker"], [class*="calendar"], [class*="datepicker"]');
                    for (const panel of panels) {{
                        if (panel.offsetParent === null) continue;  // 不可见
                        
                        // 查找日期单元格
                        const cells = panel.querySelectorAll('td, .day, [class*="cell"]');
                        for (const cell of cells) {{
                            const text = cell.textContent.trim();
                            if (text === '{day}' && !cell.classList.contains('disabled')) {{
                                cell.click();
                                return true;
                            }}
                        }}
                    }}
                    return false;
                }}
            ''')
            
            if day_clicked:
                log(f"[Layer 3] 点击了日期: {day}")
                await asyncio.sleep(0.3)
                
                if has_confirm:
                    await self._click_confirm_button(page, log)
                
                return True
            
            log("[Layer 3] 未能在日历中点击日期")
            return False
            
        except Exception as e:
            log(f"[Layer 3] 操作通用日历失败: {e}")
            return False
    
    async def _click_confirm_button(self, page, log) -> bool:
        """点击确认/查询按钮"""
        confirm_selectors = [
            '.laydate-btns-confirm',
            'button:has-text("确定")', 'button:has-text("确认")',
            'button:has-text("查询")', 'button:has-text("搜索")',
            '.el-button--primary', '.ant-btn-primary',
            '.confirm', '.ok', '[class*="confirm"]'
        ]
        
        for sel in confirm_selectors:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click()
                    log(f"[Layer 3] 点击确认按钮: {sel}")
                    await asyncio.sleep(0.5)
                    return True
            except:
                continue
        
        return False
    
    async def _operate_laydate_for_llm(self, start_date: str, end_date: str, log) -> bool:
        """
        第三层专用：操作 laydate 日历（点击型，非输入型）
        
        SSE 等网站的 laydate 控件需要通过点击日历中的日期来选择，
        不支持直接输入日期值。
        """
        try:
            page = self.browser.page
            
            # 解析用户指定的日期
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            today = datetime.now()
            
            # 计算日期范围
            days_from_start = (today.date() - start_dt.date()).days
            
            # ========== 策略1: 尝试使用快捷按钮（最可靠） ==========
            quick_buttons = [
                ('近三月', 90),
                ('近一月', 30), 
                ('近一周', 7),
            ]
            
            for btn_text, btn_days in quick_buttons:
                # 如果用户日期范围接近某个快捷按钮的范围，就用它
                if days_from_start >= btn_days * 0.8 and days_from_start <= btn_days * 1.5:
                    try:
                        btn = await page.query_selector(f'.layui-laydate span:has-text("{btn_text}")')
                        if not btn:
                            btn = await page.query_selector(f'span:has-text("{btn_text}")')
                        
                        if btn and await btn.is_visible():
                            await btn.click()
                            await asyncio.sleep(0.5)
                            log(f"[Layer 3] 点击快捷按钮: {btn_text}")
                            
                            # 点击确定
                            confirm = await page.query_selector('.laydate-btns-confirm')
                            if confirm and await confirm.is_visible():
                                await confirm.click()
                                await asyncio.sleep(0.5)
                            
                            return True
                    except Exception as e:
                        log(f"[Layer 3] 快捷按钮 {btn_text} 点击失败: {e}")
                        continue
            
            # ========== 策略2: 手动在日历中点击选择日期 ==========
            log(f"[Layer 3] 没有匹配的快捷按钮，尝试手动选择日期: {start_date} ~ {end_date}")
            
            # 先确保日历弹层存在
            laydate = await page.query_selector('.layui-laydate')
            if not laydate:
                log("[Layer 3] 日历弹层不存在")
                return False
            
            # 检查是否是范围选择器（有两个日历面板）
            panels = await page.query_selector_all('.layui-laydate .laydate-main-list-0, .layui-laydate .laydate-main-list-1')
            is_range_picker = len(panels) >= 2
            log(f"[Layer 3] 日历类型: {'范围选择器' if is_range_picker else '单日期选择器'}")
            
            # 选择开始日期
            start_success = await self._select_date_in_laydate(page, start_dt, 0, log)
            if not start_success:
                log("[Layer 3] 选择开始日期失败")
                # 即使失败也尝试继续
            
            if is_range_picker:
                # 范围选择器：选择结束日期
                await asyncio.sleep(0.3)
                end_success = await self._select_date_in_laydate(page, end_dt, 1, log)
                if not end_success:
                    log("[Layer 3] 选择结束日期失败")
            
            # 点击确定按钮
            await asyncio.sleep(0.3)
            confirm = await page.query_selector('.laydate-btns-confirm')
            if confirm and await confirm.is_visible():
                await confirm.click()
                await asyncio.sleep(0.5)
                log("[Layer 3] 点击确定按钮")
                return True
            else:
                # 有些日历没有确定按钮，选择日期后自动关闭
                log("[Layer 3] 没有确定按钮，日期选择可能已自动生效")
                return True
            
        except Exception as e:
            log(f"[Layer 3] 操作 laydate 异常: {e}")
            return False
    
    async def _select_date_in_laydate(self, page, target_date: datetime, panel_index: int, log) -> bool:
        """
        在 laydate 日历中选择指定日期
        
        Args:
            page: Playwright page
            target_date: 目标日期
            panel_index: 面板索引（0=左侧/开始日期，1=右侧/结束日期）
            log: 日志函数
        """
        try:
            target_year = target_date.year
            target_month = target_date.month
            target_day = target_date.day
            
            # 确定面板选择器
            if panel_index == 0:
                panel_selector = '.laydate-main-list-0'
            else:
                panel_selector = '.laydate-main-list-1'
            
            # 获取当前显示的年月
            current_info = await page.evaluate(f'''
                () => {{
                    const panel = document.querySelector('{panel_selector}') || document.querySelector('.layui-laydate-main');
                    if (!panel) return null;
                    
                    const yearSpan = panel.querySelector('.laydate-set-ym span[lay-type="year"]');
                    const monthSpan = panel.querySelector('.laydate-set-ym span[lay-type="month"]');
                    
                    if (!yearSpan || !monthSpan) return null;
                    
                    return {{
                        year: parseInt(yearSpan.textContent),
                        month: parseInt(monthSpan.textContent)
                    }};
                }}
            ''')
            
            if not current_info:
                log(f"[Layer 3] 无法获取日历当前年月")
                return False
            
            current_year = current_info['year']
            current_month = current_info['month']
            
            log(f"[Layer 3] 日历当前: {current_year}年{current_month}月, 目标: {target_year}年{target_month}月")
            
            # 导航到目标年月
            # 先切换年份
            year_diff = target_year - current_year
            if year_diff != 0:
                for _ in range(abs(year_diff)):
                    if year_diff < 0:
                        # 往前翻年
                        prev_year = await page.query_selector(f'{panel_selector} .laydate-icon-prev[lay-type="year"], .layui-laydate-header .laydate-icon-prev[lay-type="year"]')
                        if prev_year:
                            await prev_year.click()
                            await asyncio.sleep(0.2)
                    else:
                        # 往后翻年
                        next_year = await page.query_selector(f'{panel_selector} .laydate-icon-next[lay-type="year"], .layui-laydate-header .laydate-icon-next[lay-type="year"]')
                        if next_year:
                            await next_year.click()
                            await asyncio.sleep(0.2)
            
            # 再切换月份
            month_diff = target_month - current_month
            if month_diff != 0:
                for _ in range(abs(month_diff)):
                    if month_diff < 0:
                        prev_month = await page.query_selector(f'{panel_selector} .laydate-icon-prev[lay-type="month"], .layui-laydate-header .laydate-icon-prev[lay-type="month"]')
                        if prev_month:
                            await prev_month.click()
                            await asyncio.sleep(0.2)
                    else:
                        next_month = await page.query_selector(f'{panel_selector} .laydate-icon-next[lay-type="month"], .layui-laydate-header .laydate-icon-next[lay-type="month"]')
                        if next_month:
                            await next_month.click()
                            await asyncio.sleep(0.2)
            
            # 点击目标日期
            day_clicked = await page.evaluate(f'''
                () => {{
                    const panel = document.querySelector('{panel_selector}') || document.querySelector('.layui-laydate-main');
                    if (!panel) return {{ success: false, error: 'no panel' }};
                    
                    // 查找日期单元格
                    const tds = panel.querySelectorAll('td');
                    for (const td of tds) {{
                        // 排除非当月的日期（通常有 laydate-day-prev 或 laydate-day-next class）
                        if (td.classList.contains('laydate-day-prev') || td.classList.contains('laydate-day-next')) {{
                            continue;
                        }}
                        
                        const dayText = td.textContent.trim();
                        if (dayText === '{target_day}') {{
                            td.click();
                            return {{ success: true, day: {target_day} }};
                        }}
                    }}
                    
                    return {{ success: false, error: 'day not found' }};
                }}
            ''')
            
            if day_clicked and day_clicked.get('success'):
                log(f"[Layer 3] 成功点击日期: {target_year}-{target_month:02d}-{target_day:02d}")
                return True
            else:
                log(f"[Layer 3] 点击日期失败: {day_clicked}")
                return False
                
        except Exception as e:
            log(f"[Layer 3] 选择日期异常: {e}")
            return False
    
    # ============ P0-3 增强：分析注入钩子捕获的 API 记录 ============

    def _analyze_intercepted_apis(self, intercepted: list, log) -> Optional[Dict[str, Any]]:
        """
        P0-3: 分析 window.__interceptedAPIs 中钩子捕获的 API 调用记录。
        
        这些记录包含 JS 调用栈，可以精准追溯 API 调用的触发源。
        """
        if not intercepted:
            return None
        
        api_configs = []
        
        for record in intercepted:
            url = record.get('url', '')
            if not url:
                continue
            
            # 跳过明显的非数据请求
            url_lower = url.lower()
            if any(url_lower.endswith(ext) for ext in ['.css', '.png', '.jpg', '.gif', '.svg', '.ico']):
                continue
            
            # 处理相对 URL
            if url.startswith('//'):
                url = 'https:' + url
            elif url.startswith('/'):
                # 需要拼接域名，但这里没有 page context，跳过相对路径
                continue
            
            if not url.startswith('http'):
                continue
            
            # 检查是否像数据 API
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)
            flat_params = {k: v[0] if len(v) == 1 else v for k, v in query_params.items()}
            
            # 检查是否有日期参数
            date_params = self._identify_date_params(flat_params)
            if not date_params:
                continue
            
            # 有日期参数，这是一个有效的 API 配置
            is_jsonp = 'callback' in url_lower or 'jsonp' in url_lower or 'jsonCallBack' in url_lower
            stack = record.get('stack', '')
            
            api_configs.append({
                'var_name': f"intercepted_{record.get('type', 'unknown')}",
                'api_url': url.split('?')[0],  # 基础 URL
                'date_params': date_params,
                'all_params': flat_params,
                'is_jsonp': is_jsonp,
                'date_format': list(date_params.values())[0] if date_params else 'YYYY-MM-DD',
                'extra_info': {
                    'source': 'xhr_fetch_hook',
                    'call_stack': stack[:500],
                    'http_method': record.get('method', 'GET'),
                }
            })
            
            if api_configs:
                log(f"[Layer 0] 从钩子记录中发现 {len(api_configs)} 个含日期参数的 API")
        
        if not api_configs:
            return {'found': False, 'error': '钩子记录中未发现含日期参数的 API'}
        
        return {
            'found': True,
            'api_configs': api_configs,
            'scanned_vars': [f"hook_{i}" for i in range(len(api_configs))],
        }

    # ============ 通用方法 ============
    
    def analyze_requests(self, network_requests: Dict[str, Any]) -> List[DateAPICandidate]:
        """
        分析捕获的网络请求，识别日期 API。
        
        P0-2 增强：使用 resourceType 精准过滤
        P1-6 增强：使用 initiator 归因调整置信度
        """
        candidates = []
        
        all_requests = network_requests.get('all_requests', [])
        api_requests = network_requests.get('api_requests', [])
        
        # 优先分析 API 请求
        for req in api_requests:
            candidate = self._analyze_single_request(req)
            if candidate:
                candidate.confidence += 0.2
                # P1-6: 使用 initiator 归因调整置信度
                self._apply_initiator_bonus(candidate, req)
                candidates.append(candidate)
        
        # 然后分析所有请求
        analyzed_urls = {c.url for c in candidates}
        for req in all_requests:
            url = req.get('url', '')
            if url in analyzed_urls:
                continue
            
            # P0-2 增强：使用 resourceType 精准过滤
            resource_type = req.get('resourceType', '')
            # CDP 返回大写 (XHR, Fetch, Script, Document)，兼容旧版小写
            resource_type_lower = resource_type.lower() if resource_type else ''
            if resource_type and resource_type_lower not in ('xhr', 'fetch', 'script', 'document', ''):
                continue
            
            candidate = self._analyze_single_request(req)
            if candidate:
                self._apply_initiator_bonus(candidate, req)
                candidates.append(candidate)
                analyzed_urls.add(url)
        
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        self.candidates = candidates
        return candidates
    
    def _apply_initiator_bonus(self, candidate: DateAPICandidate, req: Dict[str, Any]):
        """
        P1-6: 根据 initiator 信息调整候选置信度。
        
        - 由用户交互（script 类型 + 调用栈含 click/change/submit）触发的请求加分
        - 由预加载/定时器/心跳触发的请求减分
        """
        initiator = req.get('initiator')
        if not initiator or not isinstance(initiator, dict):
            return
        
        init_type = initiator.get('type', '')
        
        # 解析调用栈
        stack_info = ''
        stack_obj = initiator.get('stack', {})
        if isinstance(stack_obj, dict):
            frames = stack_obj.get('callFrames', [])
            if frames:
                # 拼接前 5 帧的函数名和 URL
                stack_info = ' '.join(
                    f"{f.get('functionName', '')}@{f.get('url', '').split('/')[-1]}"
                    for f in frames[:5]
                ).lower()
        
        # 用户交互触发的请求（高价值）
        interaction_keywords = ['click', 'change', 'submit', 'search', 'query',
                                'onchange', 'onclick', 'handleclick', 'handlechange',
                                'datepicker', 'laydate', 'calendar']
        if any(kw in stack_info for kw in interaction_keywords):
            candidate.confidence += 0.15
        
        # 定时器/心跳/预加载（低价值）
        noise_keywords = ['setinterval', 'settimeout', 'heartbeat', 'ping',
                          'beacon', 'analytics', 'tracker', 'log', 'monitor']
        if any(kw in stack_info for kw in noise_keywords):
            candidate.confidence -= 0.2
        
        # preload/prefetch 类型的请求
        if init_type in ('preload', 'preflight', 'other'):
            candidate.confidence -= 0.1
    
    def _analyze_single_request(self, req: Dict[str, Any]) -> Optional[DateAPICandidate]:
        """分析单个请求"""
        url = req.get('url', '')
        method = req.get('method', 'GET').upper()
        
        if not url:
            return None
        
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        flat_params = {k: v[0] if len(v) == 1 else v for k, v in query_params.items()}
        
        post_data = req.get('postData', '')
        if post_data and method == 'POST':
            try:
                body_params = json.loads(post_data)
                if isinstance(body_params, dict):
                    flat_params.update(body_params)
            except:
                try:
                    body_qs = parse_qs(post_data)
                    for k, v in body_qs.items():
                        flat_params[k] = v[0] if len(v) == 1 else v
                except:
                    pass
        
        date_params = self._identify_date_params(flat_params)
        
        if not date_params:
            return None
        
        confidence = self._calculate_confidence(url, method, flat_params, date_params)
        
        return DateAPICandidate(
            url=url,
            method=method,
            params=flat_params,
            date_params=date_params,
            confidence=confidence,
            resource_type=req.get('resourceType', 'unknown')
        )
    
    def _identify_date_params(self, params: Dict[str, Any]) -> Dict[str, str]:
        """识别参数中的日期字段（增强版：支持数组日期值如 seDate: ["2026-01-01","2026-01-01"]）"""
        date_params = {}
        
        for key, value in params.items():
            # 过滤典型 cachebuster/噪声参数
            key_l = (key or "").lower()
            if key_l in getattr(self, "_noise_param_names", {"_", "__", "_t", "t", "ts"}):
                continue
            
            # ===== 增强：处理数组类型的日期参数 =====
            # 例如深交所 seDate: ["2026-01-01","2026-01-01"]
            if isinstance(value, list):
                date_formats_in_array = []
                for item in value:
                    if not isinstance(item, str):
                        continue
                    for pattern, fmt in self.DATE_VALUE_PATTERNS:
                        if re.match(pattern, item.strip()):
                            date_formats_in_array.append(fmt)
                            break
                
                if date_formats_in_array:
                    name_matches_date = any(
                        re.match(pattern, key) for pattern in self.DATE_PARAM_PATTERNS
                    )
                    # seDate 中的 "se" 不完全匹配标准模式，额外检查宽松关键词
                    if name_matches_date or any(tok in key_l for tok in ["date", "time", "se", "range"]):
                        date_params[key] = date_formats_in_array[0]
                continue  # 数组处理完毕
            
            # ===== 原有逻辑：字符串值 =====
            if not isinstance(value, str):
                value = str(value)

            name_matches_date = any(
                re.match(pattern, key) for pattern in self.DATE_PARAM_PATTERNS
            )
            
            value_format = None
            for pattern, fmt in self.DATE_VALUE_PATTERNS:
                if re.match(pattern, value.strip()):
                    value_format = fmt
                    break
            
            if name_matches_date and value_format:
                date_params[key] = value_format
                continue

            # 收紧：只有当 key 本身也"像日期字段名"时才认定
            if value_format and len(value) >= 8:
                if any(tok in key_l for tok in ["date", "time", "day", "start", "end", "begin", "from", "to"]):
                    date_params[key] = value_format
        
        return date_params
    
    def _calculate_confidence(
        self, 
        url: str, 
        method: str, 
        params: Dict[str, Any], 
        date_params: Dict[str, str]
    ) -> float:
        """计算置信度"""
        confidence = 0.0
        
        date_count = len(date_params)
        if date_count == 2:
            confidence += 0.4
        elif date_count == 1:
            confidence += 0.2
        elif date_count > 2:
            confidence += 0.1

        # 强惩罚：只有 '_' 这种噪声参数被识别为日期参数时，几乎必定是假阳性
        try:
            keys_l = [(k or "").lower() for k in (date_params or {}).keys()]
            if keys_l and all(k in getattr(self, "_noise_param_names", {"_", "__", "_t", "t", "ts"}) for k in keys_l):
                confidence -= 0.5
        except Exception:
            pass
        
        path = urlparse(url).path.lower()
        for pattern in self.API_PATH_PATTERNS:
            if re.search(pattern, path, re.I):
                confidence += 0.1
                break

        # URL 命中公告/披露类关键词，加分（优先挑中真正的数据接口）
        if any(tok in path for tok in ["bulletin", "announcement", "disclosure", "querycompanybulletin"]):
            confidence += 0.2
        
        common_api_params = ['page', 'pageSize', 'limit', 'offset', 'type', 'category']
        for p in common_api_params:
            if p.lower() in [k.lower() for k in params.keys()]:
                confidence += 0.05
        
        if method == 'POST':
            confidence += 0.1
        
        return min(confidence, 1.0)
    
    def format_date(self, date_str: str, target_format: str) -> str:
        """日期格式转换"""
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            return date_str
        
        if target_format == 'YYYY-MM-DD':
            return dt.strftime('%Y-%m-%d')
        elif target_format == 'YYYYMMDD':
            return dt.strftime('%Y%m%d')
        elif target_format == 'YYYY/MM/DD':
            return dt.strftime('%Y/%m/%d')
        elif target_format == 'timestamp_s':
            return str(int(dt.timestamp()))
        elif target_format == 'timestamp_ms':
            return str(int(dt.timestamp() * 1000))
        elif target_format == 'YYYY-MM-DD HH:MM:SS':
            return dt.strftime('%Y-%m-%d 00:00:00')
        else:
            return date_str
    
    def build_replay_url(
        self, 
        candidate: DateAPICandidate, 
        start_date: str, 
        end_date: str
    ) -> Tuple[str, Dict[str, Any]]:
        """构建重放 URL（增强版：支持数组日期参数如 seDate）"""
        new_params = dict(candidate.params)
        
        # 替换日期参数
        for param_name, date_format in candidate.date_params.items():
            name_lower = param_name.lower()
            
            # 检查原始参数值是否是数组（如 seDate: ["start", "end"]）
            original_value = candidate.params.get(param_name)
            if isinstance(original_value, list) or name_lower in ('sedate', 'daterange', 'date_range'):
                # 数组格式：[start_date, end_date]
                new_params[param_name] = [
                    self.format_date(start_date, date_format),
                    self.format_date(end_date, date_format)
                ]
                continue
            
            if any(kw in name_lower for kw in ['start', 'begin', 'from']):
                new_value = self.format_date(start_date, date_format)
            elif any(kw in name_lower for kw in ['end', 'to']):
                new_value = self.format_date(end_date, date_format)
            else:
                new_value = self.format_date(start_date, date_format)
            
            new_params[param_name] = new_value
        
        # JSONP 回调：使用固定名称避免问题
        if 'jsonCallBack' in new_params:
            new_params['jsonCallBack'] = 'jsonCallback'
        if 'callback' in new_params:
            new_params['callback'] = 'jsonCallback'

        # SSE 这类接口经常是 JSONP，如果没有回调参数则补一个，避免返回纯文本/解析困难
        try:
            host = urlparse(candidate.url).netloc.lower()
            if 'sse.com.cn' in host and ('jsonCallBack' not in new_params) and ('callback' not in new_params):
                new_params['jsonCallBack'] = 'jsonCallback'
        except Exception:
            pass
        
        # 确保分页参数存在
        if 'pageHelp.pageNo' not in new_params and 'pageNo' not in new_params:
            new_params['pageHelp.pageNo'] = '1'
        if 'pageHelp.pageSize' not in new_params and 'pageSize' not in new_params:
            new_params['pageHelp.pageSize'] = '25'
        
        parsed = urlparse(candidate.url)
        
        if candidate.method == 'GET':
            new_query = urlencode(new_params, doseq=True)
            new_url = urlunparse((
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query,
                parsed.fragment
            ))
            return new_url, {}
        else:
            return candidate.url, new_params
    
    async def verify_candidate(
        self, 
        candidate: DateAPICandidate, 
        start_date: str, 
        end_date: str
    ) -> Dict[str, Any]:
        """
        验证候选 API
        
        使用服务端 httpx 发送请求，避免浏览器 CORS 限制
        """
        import httpx
        
        # 重要：很多 API 不接受 end_date 超过今天（如上交所返回空数据）
        # 验证时自动截断为今天，避免因未来日期导致误判"API无效"
        today_str = datetime.now().strftime('%Y-%m-%d')
        effective_end = min(end_date, today_str) if end_date > today_str else end_date
        # 同样确保 start_date 不超过 effective_end
        effective_start = min(start_date, effective_end)
        
        new_url, post_body = self.build_replay_url(candidate, effective_start, effective_end)
        
        # 构建请求头，模拟浏览器
        parsed = urlparse(candidate.url)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': f'{parsed.scheme}://{parsed.netloc}/',
        }
        
        # SSE 特殊处理：Referer 应为主站而非 API 子域
        if 'sse.com.cn' in parsed.netloc:
            headers['Origin'] = 'https://www.sse.com.cn'
            headers['Referer'] = 'https://www.sse.com.cn/'
        # SZSE 同理
        if 'szse.cn' in parsed.netloc:
            headers['Referer'] = 'https://www.szse.cn/'
        
        try:
            async with httpx.AsyncClient(timeout=15, verify=False) as client:
                if candidate.method == 'GET':
                    resp = await client.get(new_url, headers=headers)
                else:
                    headers['Content-Type'] = 'application/json'
                    resp = await client.post(new_url, json=post_body, headers=headers)
                
                if resp.status_code != 200:
                    return {
                        'success': False,
                        'error': f"HTTP {resp.status_code}",
                        'data_count': 0
                    }

                # 重要：不要截断再解析！JSONP/JSON 截断会导致 json.loads 失败，
                # 从而把“真实接口”误判为失败，反而选中返回很短的统计/噪声接口。
                full_text = resp.text or ""
                preview_text = full_text[:5000]

            data = self._parse_response(full_text)
            is_valid, data_count, error = self._validate_response(data)
            
            return {
                'success': is_valid,
                'data_count': data_count,
                'error': error,
                'raw_response': preview_text[:500],
                'parsed_data': data if is_valid else None
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'data_count': 0
            }
    
    def _parse_response(self, text: str) -> Any:
        """解析响应"""
        text = text.strip()
        
        if text.startswith('/**/'):
            text = text[4:].strip()

        # 兼容 jsonCallBack=? 这类占位符导致的 JSONP：?(...)
        if text.startswith('?(') and text.endswith(')'):
            text = text[2:-1]
        
        jsonp_match = re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*\s*\((.*)\)\s*;?\s*$', text, re.S)
        if jsonp_match:
            text = jsonp_match.group(1)
        
        try:
            return json.loads(text)
        except:
            return None
    
    def _validate_response(self, data: Any) -> Tuple[bool, int, Optional[str]]:
        """验证响应有效性"""
        if data is None:
            return False, 0, "无法解析响应为 JSON"
        
        if not isinstance(data, dict):
            return False, 0, "响应不是对象格式"
        
        if data.get('success') is False:
            return False, 0, data.get('message') or data.get('error') or "API 返回 success=false"
        
        if data.get('code') not in (None, 0, 200, '0', '200', 'success'):
            if data.get('code'):
                return False, 0, f"API 返回错误码: {data.get('code')}"
        
        data_count = 0
        data_keys = ['data', 'result', 'list', 'items', 'records', 'rows', 'content', 'reports', 'articles']
        
        for key in data_keys:
            if key in data:
                value = data[key]
                if isinstance(value, list):
                    data_count = len(value)
                    break
                elif isinstance(value, dict):
                    for sub_key in data_keys:
                        if sub_key in value and isinstance(value[sub_key], list):
                            data_count = len(value[sub_key])
                            break
        
        if 'pageHelp' in data:
            page_help = data['pageHelp']
            if isinstance(page_help, dict):
                data_list = page_help.get('data', [])
                if isinstance(data_list, list):
                    data_count = max(data_count, len(data_list))
                # 也检查 total 字段
                total = page_help.get('total', 0)
                if isinstance(total, int) and total > 0:
                    data_count = max(data_count, min(total, 100))  # 用 total 但限制最大值
        
        if data_count == 0:
            return False, 0, "响应中未找到数据列表或列表为空"
        
        return True, data_count, None
    
    def generate_api_code_snippet(
        self, 
        candidate: DateAPICandidate, 
        start_date: str, 
        end_date: str
    ) -> str:
        """生成 Python 代码片段"""
        new_url, post_body = self.build_replay_url(candidate, start_date, end_date)
        
        if candidate.method == 'GET':
            code = f'''
import requests

# 日期 API 直连（自动识别 - 第{self._layer1_result and 1 or self._layer2_result and 2 or 3}层）
# 日期参数: {list(candidate.date_params.keys())}
# 格式: {list(candidate.date_params.values())}

url = "{new_url}"

headers = {{
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "{urlparse(candidate.url).scheme}://{urlparse(candidate.url).netloc}/"
}}

response = requests.get(url, headers=headers, timeout=30)
data = response.json()
'''
        else:
            code = f'''
import requests
import json

# 日期 API 直连（POST）
url = "{new_url}"
payload = {json.dumps(post_body, ensure_ascii=False, indent=2)}

headers = {{
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Referer": "{urlparse(candidate.url).scheme}://{urlparse(candidate.url).netloc}/"
}}

response = requests.post(url, json=payload, headers=headers, timeout=30)
data = response.json()
'''
        
        return code


# ============ 便捷函数 ============

async def extract_date_api_with_three_layers(
    browser_controller,
    llm_agent,
    network_requests: Dict[str, Any],
    start_date: str,
    end_date: str,
    log_callback=None
) -> DateAPIExtractionResult:
    """
    便捷函数：使用三层策略提取日期 API
    """
    extractor = DateAPIExtractor(browser_controller, llm_agent)
    return await extractor.extract_with_three_layers(
        network_requests, 
        start_date, 
        end_date,
        log_callback
    )
