#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyGen API Server - FastAPI 后端

提供 REST API 供前端调用：
- POST /api/menu-tree: 获取目标页面的目录树
- POST /api/generate: 启动爬虫脚本生成任务
- GET /api/status/{task_id}: 获取任务状态和日志
- GET /api/download/{filename}: 下载生成的脚本文件
"""
import asyncio
import uuid
import time
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from config import Config
from chrome_launcher import ChromeLauncher
from browser_controller import BrowserController
from llm_agent import LLMAgent

# ============ Pydantic Models ============

class MenuTreeRequest(BaseModel):
    url: str
    
class MenuTreeResponse(BaseModel):
    url: str
    root: Optional[Dict[str, Any]]
    leaf_paths: List[str]
    
class AttachmentData(BaseModel):
    """附件数据（图片/文件的 base64 编码）"""
    filename: str
    base64: str
    mimeType: str

class GenerateRequest(BaseModel):
    url: str
    startDate: str
    endDate: str
    outputScriptName: str
    extraRequirements: Optional[str] = ""
    siteName: Optional[str] = ""
    listPageName: Optional[str] = ""
    sourceCredibility: Optional[str] = ""  # 信息源可信度（T1/T2/T3）
    runMode: str  # 'enterprise_report' | 'news_sentiment'
    crawlMode: str  # 'single_page' | 'multi_page' | 'auto_detect' | 'date_range_api'
    downloadReport: Optional[str] = "yes"  # 'yes' | 'no'
    selectedPaths: Optional[List[str]] = None  # 用户选中的目录路径（仅 multi_page 模式）
    attachments: Optional[List[AttachmentData]] = None  # 图片/文件附件

class ReportFile(BaseModel):
    id: str
    name: str
    date: str
    downloadUrl: str
    fileType: str
    localPath: Optional[str] = None  # 本地文件路径（如果已下载）
    isLocal: bool = False  # 是否是本地文件
    category: Optional[str] = None  # 来源板块（多页爬取时标识）

class NewsArticle(BaseModel):
    """新闻文章（新闻舆情场景）"""
    id: str
    title: str
    author: str = ""
    date: str
    source: str
    sourceUrl: str
    summary: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None  # 来源板块（多页爬取时标识）

class TaskStatusResponse(BaseModel):
    taskId: str
    status: str  # 'pending' | 'running' | 'completed' | 'failed'
    currentStep: int
    totalSteps: int
    stepLabel: str
    logs: List[str]
    resultFile: Optional[str] = None
    error: Optional[str] = None
    # 企业报告场景
    reports: Optional[List[ReportFile]] = None
    downloadedCount: Optional[int] = None  # 已下载的文件数量
    filesNotEnough: Optional[bool] = None  # 日期范围内文件是否不足5份
    pdfOutputDir: Optional[str] = None  # PDF 下载目录
    # 新闻舆情场景
    newsArticles: Optional[List[NewsArticle]] = None
    markdownFile: Optional[str] = None
    totalCount: Optional[int] = None  # 总结果数（当结果被截断时使用）

# ============ 全局状态 ============

# 任务存储（生产环境应使用 Redis）
tasks: Dict[str, Dict[str, Any]] = {}

# 任务对应的子进程（用于停止任务）
task_processes: Dict[str, Any] = {}

# 任务对应的浏览器资源（launcher, browser）
task_browsers: Dict[str, Dict[str, Any]] = {}

# 任务对应的 asyncio Task（用于取消）
task_asyncio_tasks: Dict[str, asyncio.Task] = {}

# 任务取消标志
task_cancelled: Dict[str, bool] = {}

# 配置
config: Optional[Config] = None


def _is_cancelled(task_id: str) -> bool:
    """检查任务是否已被取消"""
    return task_cancelled.get(task_id, False)

# ============ 生命周期 ============

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global config
    try:
        config = Config()
        print("✓ 配置加载成功")
    except Exception as e:
        print(f"✗ 配置加载失败: {e}")
        config = None
    yield
    # 清理
    print("应用关闭")

# ============ FastAPI App ============

app = FastAPI(
    title="PyGen API",
    description="智能爬虫脚本生成器 API",
    version="2.0",
    lifespan=lifespan
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============ 辅助函数 ============

def _add_log(task_id: str, message: str):
    """添加日志到任务"""
    if task_id in tasks:
        timestamp = datetime.now().strftime("%H:%M:%S")
        tasks[task_id]["logs"].append(f"[{timestamp}] {message}")

def _update_step(task_id: str, step: int, label: str):
    """更新任务步骤"""
    if task_id in tasks:
        tasks[task_id]["currentStep"] = step
        tasks[task_id]["stepLabel"] = label

# ============ 核心逻辑：获取目录树 ============

async def _fetch_menu_tree(url: str) -> Dict[str, Any]:
    """启动浏览器并获取目录树"""
    launcher = None
    browser = None
    
    try:
        # 使用随机端口以避免多请求间的资源冲突（防止请求A关闭了请求B正在复用的浏览器）
        import random
        random_port = random.randint(10000, 20000)
        
        # 启动 Chrome（headless 模式由 config.yaml 统一控制）
        _headless = config.browser_headless if config else False
        launcher = ChromeLauncher(
            debug_port=random_port,
            user_data_dir=None,  # 让 Launcher 自动创建临时目录，避免 Profile 锁冲突
            headless=_headless,
            auto_select_port=True
        )
        launcher.launch()
        
        # 连接浏览器
        browser = BrowserController(
            cdp_url=launcher.get_ws_endpoint(),
            timeout=config.cdp_timeout if config else 60000
        )
        
        if not await browser.connect():
            # 简单的重试一次
            await asyncio.sleep(1)
            if not await browser.connect():
                raise RuntimeError("无法连接到 Chrome 浏览器")
        
        # 打开页面
        success, error_msg = await browser.open(url)
        if not success:
            raise RuntimeError(f"无法打开目标页面: {error_msg}")
        
        # 滚动页面
        await browser.scroll_page(times=2)
        
        # 获取目录树
        menu_tree = await browser.enumerate_menu_tree(max_depth=3)
        
        return {
            "url": url,
            "root": menu_tree.get("root"),
            "leaf_paths": menu_tree.get("leaf_paths", [])
        }
        
    finally:
        if browser:
            await browser.disconnect()
        if launcher:
            launcher.terminate()

# ============ 核心逻辑：生成爬虫脚本 ============

async def _run_generation_task(task_id: str, request: GenerateRequest):
    """后台执行爬虫脚本生成"""
    launcher = None
    browser = None
    
    try:
        tasks[task_id]["status"] = "running"
        task_cancelled[task_id] = False  # 初始化取消标志

        # 读取配置：是否启用自动修复/检查/后处理
        auto_repair_enabled = config.llm_auto_repair if config else True
        
        # Step 1: 启动 Chrome
        if _is_cancelled(task_id):
            raise RuntimeError("任务已被用户取消")
            
        _update_step(task_id, 0, "正在启动Chrome浏览器")
        _add_log(task_id, "[INFO] 正在启动 Chrome 浏览器...")
        
        # 使用随机端口以避免多任务间的资源冲突（防止任务A关闭了任务B正在复用的浏览器）
        import random
        # 随机选择一个端口范围，避免与常用端口冲突
        random_port = random.randint(10000, 20000)
        
        # headless 模式由 config.yaml 统一控制
        _headless = config.browser_headless if config else False
        launcher = ChromeLauncher(
            debug_port=random_port,
            user_data_dir=None, # 让 Launcher 自动创建临时目录，避免 Profile 锁冲突
            headless=_headless,
            auto_select_port=True
        )
        launcher.launch()
        _add_log(task_id, f"[SUCCESS] Chrome 启动成功 (端口 {launcher.actual_port}, headless={_headless})")
        
        # 存储浏览器资源引用，以便停止时可以关闭
        task_browsers[task_id] = {"launcher": launcher, "browser": None}
        
        # Step 2: 连接浏览器
        if _is_cancelled(task_id):
            raise RuntimeError("任务已被用户取消")
            
        _update_step(task_id, 1, "正在连接浏览器")
        ws_url = launcher.get_ws_endpoint()
        _add_log(task_id, f"[INFO] 正在连接到 CDP: {ws_url}")
        
        browser = BrowserController(
            cdp_url=ws_url,
            timeout=config.cdp_timeout if config else 60000
        )
        
        # 增加连接重试与自动恢复机制
        connected = False
        for i in range(3):
            if await browser.connect():
                connected = True
                break
            
            _add_log(task_id, f"[WARNING] 浏览器连接尝试 {i+1}/3 失败，等待重试...")
            await asyncio.sleep(2)
            
            # 如果是最后一次尝试前，且看起来连接完全不通，尝试重启 Chrome
            if i == 1:
                _add_log(task_id, "[WARNING] 连接持续失败，尝试重启 Chrome...")
                try:
                    launcher.terminate()
                    await asyncio.sleep(1)
                    launcher.launch()
                    # 更新 WS URL
                    browser.cdp_url = launcher.get_ws_endpoint()
                    _add_log(task_id, f"[INFO] Chrome 已重启，新地址: {browser.cdp_url}")
                except Exception as e:
                    _add_log(task_id, f"[ERROR] 重启 Chrome 失败: {e}")

        if not connected:
            raise RuntimeError("无法连接到 Chrome 浏览器，请检查 Chrome 是否正确安装或端口是否被占用")

        _add_log(task_id, "[SUCCESS] 浏览器连接成功")
        
        # 更新浏览器引用
        task_browsers[task_id]["browser"] = browser
        
        # Step 3: 打开目标页面
        if _is_cancelled(task_id):
            raise RuntimeError("任务已被用户取消")
            
        _update_step(task_id, 2, "正在打开目标页面")
        _add_log(task_id, f"[INFO] 正在打开: {request.url}")
        
        # 使用 domcontentloaded 作为默认等待策略，而不是 networkidle
        # networkidle 对于不断加载资源的页面（如轮播图、即时通讯）非常容易超时
        success, error_msg = await browser.open(request.url, wait_until="domcontentloaded")
        if not success:
            _add_log(task_id, f"[ERROR] 页面打开失败: {error_msg}")
            raise RuntimeError(f"无法打开目标页面: {error_msg}")
        _add_log(task_id, "[SUCCESS] 页面加载完成")
        
        # Step 4: 滚动页面
        if _is_cancelled(task_id):
            raise RuntimeError("任务已被用户取消")
            
        _update_step(task_id, 3, "正在滚动页面以加载更多内容")
        _add_log(task_id, "[INFO] 滚动页面以触发懒加载...")
        await browser.scroll_page(times=3)
        _add_log(task_id, "[SUCCESS] 页面滚动完成")
        
        # Step 5: 分析页面结构
        if _is_cancelled(task_id):
            raise RuntimeError("任务已被用户取消")
            
        _update_step(task_id, 4, "正在分析页面结构")
        _add_log(task_id, "[INFO] 正在分析页面结构...")
        
        # 立即更新步骤状态，确保前端能及时看到
        await asyncio.sleep(0.1)  # 给前端一点时间接收更新
        
        page_info = await browser.get_page_info()
        page_html = await browser.get_full_html()
        page_structure = await browser.analyze_page_structure()
        network_requests = browser.get_captured_requests()
        
        _add_log(task_id, f"[INFO] 页面标题: {page_info.get('title', '未知')}")
        _add_log(task_id, f"[INFO] HTML 长度: {len(page_html):,} 字符")
        _add_log(task_id, f"[INFO] 捕获请求: {len(network_requests.get('all_requests', []))} 个")
        
        # Step 6: 根据模式处理
        enhanced_analysis = None
        verified_mapping = None

        # 说明：
        # - multi_page：用户手动选目录树，仅对“选中目录”抓包还原参数（可信映射），不做通用交互探测
        # - auto_detect：自动探测板块并爬取，会执行 enhanced_page_analysis() 的通用交互探测
        if request.crawlMode == "multi_page":
            _update_step(task_id, 5, "正在构建可信分类映射（选中目录）")
            _add_log(task_id, "[INFO] 多页模式：仅对选中目录抓包还原参数（不做通用交互探测）")

            # 枚举目录树（仅枚举，不抓包）
            menu_tree = await browser.enumerate_menu_tree(max_depth=3)
            leaf_paths = menu_tree.get("leaf_paths", [])

            # 根据用户选择过滤
            selected_paths = request.selectedPaths or leaf_paths
            _add_log(task_id, f"[INFO] 目录叶子总数: {len(leaf_paths)}，用户选中: {len(selected_paths)}")

            # 对选中目录抓包还原参数（可信映射）
            _add_log(task_id, "[INFO] 正在对选中目录抓包还原参数...")
            verified_mapping = await browser.capture_mapping_for_leaf_paths(selected_paths)
            menu_to_filters = (verified_mapping or {}).get("menu_to_filters", {}) if isinstance(verified_mapping, dict) else {}
            menu_to_urls = (verified_mapping or {}).get("menu_to_urls", {}) if isinstance(verified_mapping, dict) else {}
            mapping_count = len(menu_to_filters) + len(menu_to_urls)

            enhanced_analysis = {
                "menu_tree": menu_tree,
                "selected_leaf_paths": selected_paths,
                "verified_category_mapping": verified_mapping,
                # 关键：把“选中目录点击后”的真实 API 请求样本也给到 LLM（避免猜测接口/参数）
                "interaction_apis": (verified_mapping or {}).get("interaction_apis", {}),
                "recommendations": [
                    f"已对选中目录构建可信映射：{mapping_count} 个（filters={len(menu_to_filters)}，urls={len(menu_to_urls)}）"
                ],
            }

            _add_log(task_id, f"[SUCCESS] 已构建分类映射: {mapping_count} 个（filters={len(menu_to_filters)}，urls={len(menu_to_urls)}）")

        elif request.crawlMode == "auto_detect":
            _update_step(task_id, 5, "正在执行智能板块探测 (Phase 1: 决策)")
            _add_log(task_id, "[INFO] 自动探测模式：启动智能决策流程...")

            # 1. 获取目录树和截图
            menu_tree = await browser.enumerate_menu_tree(max_depth=3)
            screenshot = await browser.take_screenshot_base64()
            _add_log(task_id, f"[INFO] 已提取目录树 ({len(menu_tree.get('leaf_paths', []))} 个叶子) 和页面截图")

            # 2. 初始化 LLM (提前实例化用于决策)
            temp_llm = LLMAgent(
                api_key=config.qwen_api_key if config else "",
                model=config.qwen_model if config else "qwen-max",
                base_url=config.qwen_base_url if config else None,
                enable_auto_repair=False # 决策阶段不需要修代码
            )

            # 3. LLM 决策
            _add_log(task_id, "[INFO] 正在请求 LLM 决策探测目标...")
            selected_paths = temp_llm.analyze_menu_for_probing(menu_tree, screenshot)
            
            if not selected_paths:
                _add_log(task_id, "[WARNING] LLM 未选中任何路径，将回退到通用探测")
                # 回退到旧的增强分析
                enhanced_analysis = await browser.enhanced_page_analysis()
                verified_mapping = enhanced_analysis.get("verified_category_mapping")
            else:
                _add_log(task_id, f"[SUCCESS] LLM 决策选中 {len(selected_paths)} 个探测目标: {selected_paths}")
                
                # 4. 执行定向抓包 (Phase 2)
                _update_step(task_id, 5, f"正在执行智能板块探测 (Phase 2: 对 {len(selected_paths)} 个目标定向抓包)")
                _add_log(task_id, "[INFO] 正在执行定向点击与抓包...")
                
                verified_mapping = await browser.capture_mapping_for_leaf_paths(selected_paths)
                menu_to_filters = (verified_mapping or {}).get("menu_to_filters", {}) if isinstance(verified_mapping, dict) else {}
                menu_to_urls = (verified_mapping or {}).get("menu_to_urls", {}) if isinstance(verified_mapping, dict) else {}
                mapping_count = len(menu_to_filters) + len(menu_to_urls)
                
                # 5. 构建分析结果 (Phase 3)
                enhanced_analysis = {
                    "menu_tree": menu_tree,
                    "selected_leaf_paths": selected_paths,
                    "verified_category_mapping": verified_mapping,
                    "interaction_apis": (verified_mapping or {}).get("interaction_apis", {}),
                    "recommendations": [
                        f"智能探测完成，已构建可信映射: {mapping_count} 个（filters={len(menu_to_filters)}，urls={len(menu_to_urls)}）"
                    ],
                    # 补充基础数据状态检测（为了获取 hasData 等标记）
                    "data_status": await browser.detect_data_status()
                }

            # 兜底：确保字段存在
            if not isinstance(verified_mapping, dict):
                verified_mapping = {}
                enhanced_analysis["verified_category_mapping"] = verified_mapping

            _add_log(
                task_id,
                f"[SUCCESS] 智能探测完成，构建分类映射: "
                f"{len((verified_mapping or {}).get('menu_to_filters', {})) + len((verified_mapping or {}).get('menu_to_urls', {}))} 个"
            )
        
        elif request.crawlMode == "date_range_api":
            # 日期筛选类网站爬取模式：三层策略
            # 第一层：纯 API 直连
            # 第二层：DOM 特征检测 + 自动操作日期控件
            # 第三层：截图 + LLM 视觉分析
            _update_step(task_id, 5, "正在分析日期 API（三层策略）")
            _add_log(task_id, "[INFO] 日期筛选模式：启动三层策略...")
            _add_log(task_id, "[INFO]   第一层: 纯 API 直连")
            _add_log(task_id, "[INFO]   第二层: DOM 特征检测 + 自动操作")
            _add_log(task_id, "[INFO]   第三层: 截图 + LLM 视觉分析")
            
            # 导入日期 API 提取器
            from date_api_extractor import DateAPIExtractor, extract_date_api_with_three_layers
            
            # 准备 LLM 实例（用于第三层）
            temp_llm = LLMAgent(
                api_key=config.qwen_api_key if config else "",
                model=config.qwen_model if config else "qwen-max",
                base_url=config.qwen_base_url if config else None,
                enable_auto_repair=False
            )
            
            # 日志回调
            def log_callback(msg: str):
                _add_log(task_id, f"[DATE-API] {msg}")
            
            # 执行三层策略
            extractor = DateAPIExtractor(browser, temp_llm)
            date_api_result = await extractor.extract_with_three_layers(
                network_requests,
                request.startDate,
                request.endDate,
                log_callback=log_callback
            )
            
            if date_api_result.success and date_api_result.best_candidate:
                # 某一层成功
                candidate = date_api_result.best_candidate
                layer_name = {1: "纯 API 直连", 2: "DOM 自动操作", 3: "LLM 视觉分析"}.get(date_api_result.layer, "未知")
                
                _add_log(task_id, f"[SUCCESS] 第 {date_api_result.layer} 层（{layer_name}）成功！")
                _add_log(task_id, f"[SUCCESS] 识别到日期 API: {candidate.url[:80]}...")
                _add_log(task_id, f"[SUCCESS] 日期参数: {list(candidate.date_params.keys())}")
                _add_log(task_id, f"[SUCCESS] 参数格式: {list(candidate.date_params.values())}")
                _add_log(task_id, f"[SUCCESS] 验证通过，获取到 {date_api_result.data_count} 条数据")
                
                # 生成代码片段供 LLM 参考
                api_code_snippet = extractor.generate_api_code_snippet(
                    candidate, 
                    request.startDate, 
                    request.endDate
                )
                
                # 构建分析结果
                enhanced_analysis = {
                    "date_api_extraction": {
                        "success": True,
                        "layer": date_api_result.layer,
                        "layer_name": layer_name,
                        "api_url": candidate.url,
                        "method": candidate.method,
                        "base_params": candidate.params,
                        "date_params": candidate.date_params,
                        "data_count": date_api_result.data_count,
                        "code_snippet": api_code_snippet,
                        "replayed_url": extractor.build_replay_url(
                            candidate, request.startDate, request.endDate
                        )[0],
                        "replayed_data": date_api_result.replayed_data,
                    },
                    "recommendations": date_api_result.recommendations,
                    "mode": f"date_range_api_layer{date_api_result.layer}"
                }
                
                _add_log(task_id, f"[SUCCESS] 将生成直接调用 API 的脚本（基于第 {date_api_result.layer} 层结果）")
                
            else:
                # 三层全部失败
                _add_log(task_id, f"[ERROR] 三层策略均失败")
                
                # 输出各层失败原因
                if date_api_result.layer1_result:
                    _add_log(task_id, f"[INFO] 第一层失败: {date_api_result.layer1_result.get('error', '未知')}")
                if date_api_result.layer2_result:
                    _add_log(task_id, f"[INFO] 第二层失败: {date_api_result.layer2_result.get('error', '未知')}")
                if date_api_result.layer3_result:
                    _add_log(task_id, f"[INFO] 第三层失败: {date_api_result.layer3_result.get('error', '未知')}")
                
                # 列出发现的候选 API（供调试）
                if date_api_result.candidates:
                    _add_log(task_id, f"[INFO] 发现 {len(date_api_result.candidates)} 个候选 API:")
                    for i, c in enumerate(date_api_result.candidates[:3]):
                        _add_log(task_id, f"[INFO]   {i+1}. {c.url[:60]}... (置信度: {c.confidence:.2f})")
                
                # 构建分析结果（标记失败，但仍提供候选信息）
                enhanced_analysis = {
                    "date_api_extraction": {
                        "success": False,
                        "layer": 0,
                        "error": date_api_result.error,
                        "layer1_error": (date_api_result.layer1_result or {}).get("error"),
                        "layer2_error": (date_api_result.layer2_result or {}).get("error"),
                        "layer3_error": (date_api_result.layer3_result or {}).get("error"),
                        "candidates_count": len(date_api_result.candidates),
                        "candidates": [
                            {
                                "url": c.url,
                                "method": c.method,
                                "date_params": c.date_params,
                                "confidence": c.confidence,
                                "verification_error": c.verification_result.get("error") if c.verification_result else None
                            }
                            for c in date_api_result.candidates[:5]
                        ]
                    },
                    "recommendations": date_api_result.recommendations,
                    "mode": "date_range_api_all_failed"
                }
                
                _add_log(task_id, "[WARNING] 三层策略均失败，将尝试生成通用爬虫脚本")
        
        else:
            # 单页模式：基础分析，跳过步骤 5
            _add_log(task_id, "[INFO] 单页模式：使用基础分析")
            _update_step(task_id, 5, "已跳过（单页模式）")
        
        # Step 7: 调用 LLM 生成脚本
        if _is_cancelled(task_id):
            raise RuntimeError("任务已被用户取消")

        # ========== date_range_api：确定性模板优先（成功时跳过 LLM，避免跑偏） ==========
        script_code = None
        if request.crawlMode == "date_range_api":
            dae = (enhanced_analysis or {}).get("date_api_extraction", {}) if isinstance(enhanced_analysis, dict) else {}
            if isinstance(dae, dict) and dae.get("success") is True:
                _update_step(task_id, 6, "正在生成确定性 API 直连脚本（跳过LLM）")
                _add_log(task_id, "[INFO] date_range_api：已验证可用 API，使用确定性模板生成脚本（不调用 LLM）")
                try:
                    from deterministic_templates import render_date_range_api_script, analyze_response_schema, build_llm_cloze_prompt, parse_llm_cloze_response
                    
                    # ── 自动推断字段映射 ──
                    field_mappings = None
                    sample_data = dae.get("replayed_data")
                    if sample_data and isinstance(sample_data, dict):
                        field_mappings = analyze_response_schema(sample_data, api_url=str(dae.get("api_url", "")))
                        _add_log(task_id, f"[INFO] 字段映射自动推断: 置信度={field_mappings.get('confidence', 0):.0%}, "
                                          f"日期={field_mappings.get('date_fields', [])}, "
                                          f"标题={field_mappings.get('title_fields', [])}, "
                                          f"URL={field_mappings.get('url_fields', [])}")
                        
                        # 如果有未映射的字段类别 → LLM 完形填空补全
                        unmapped = field_mappings.get("unmapped", [])
                        if unmapped and sample_data:
                            _add_log(task_id, f"[INFO] 字段映射不完整 ({unmapped})，尝试 LLM 完形填空...")
                            try:
                                items_for_llm = []
                                for k in ["data", "result", "list", "items", "rows"]:
                                    v = sample_data.get(k)
                                    if isinstance(v, list) and v:
                                        if isinstance(v[0], dict):
                                            items_for_llm = v[:1]
                                        elif isinstance(v[0], list) and v[0] and isinstance(v[0][0], dict):
                                            items_for_llm = v[0][:1]
                                        break
                                if isinstance(sample_data.get("pageHelp"), dict):
                                    ph_data = sample_data["pageHelp"].get("data", [])
                                    if ph_data:
                                        if isinstance(ph_data[0], dict):
                                            items_for_llm = ph_data[:1]
                                        elif isinstance(ph_data[0], list) and ph_data[0]:
                                            items_for_llm = [x for x in ph_data[0] if isinstance(x, dict)][:1]
                                
                                if items_for_llm:
                                    cloze_prompt = build_llm_cloze_prompt(items_for_llm[0], unmapped)
                                    llm_for_cloze = LLMAgent(
                                        api_key=config.qwen_api_key if config else "",
                                        model=config.qwen_model if config else "qwen-max",
                                        base_url=config.qwen_base_url if config else None,
                                        enable_auto_repair=False
                                    )
                                    cloze_resp = llm_for_cloze._call_llm(
                                        system_prompt="You are a JSON field mapping assistant. Only output valid JSON.",
                                        user_prompt=cloze_prompt,
                                        temperature=0.1
                                    )
                                    cloze_result = parse_llm_cloze_response(cloze_resp)
                                    if cloze_result:
                                        for field_type, field_names in cloze_result.items():
                                            if field_names and field_type in field_mappings:
                                                field_mappings[field_type] = field_names
                                        still_unmapped = [u for u in unmapped if not cloze_result.get(u)]
                                        field_mappings["unmapped"] = still_unmapped
                                        _add_log(task_id, f"[SUCCESS] LLM 完形填空补全: {cloze_result}")
                            except Exception as cloze_err:
                                _add_log(task_id, f"[WARNING] LLM 完形填空失败（不影响生成）: {cloze_err}")
                    
                    script_code = render_date_range_api_script(
                        target_url=request.url,
                        api_url=str(dae.get("api_url", "")),
                        method=str(dae.get("method", "GET")),
                        base_params=dae.get("base_params", {}) or {},
                        date_params=dae.get("date_params", {}) or {},
                        start_date=request.startDate,
                        end_date=request.endDate,
                        output_dir=str((Path(__file__).parent / "output")),
                        extra_headers={},
                        field_mappings=field_mappings,
                    )
                    _add_log(task_id, "[SUCCESS] 已生成确定性 API 脚本（纯接口直连）")
                except Exception as tpl_err:
                    _add_log(task_id, f"[WARNING] 确定性模板生成失败，将回退到 LLM: {tpl_err}")
                    script_code = None
            
        # ========== 其他模式 / 或模板失败：走 LLM 生成 ==========
        if script_code is None:
            _update_step(task_id, 6, "正在调用LLM生成爬虫脚本")
            _add_log(task_id, f"[INFO] 正在调用 LLM: {config.qwen_model if config else 'unknown'}")
            _add_log(task_id, "[INFO] 这可能需要 20-60 秒，请耐心等待...")
            _add_log(task_id, f"[INFO] 自动修复/检查/后处理: {'开启' if auto_repair_enabled else '关闭'}")
            
            llm = LLMAgent(
                api_key=config.qwen_api_key if config else "",
                model=config.qwen_model if config else "qwen-max",
                base_url=config.qwen_base_url if config else None,
                enable_auto_repair=auto_repair_enabled
            )
            # 构建用户需求
            user_requirements = request.extraRequirements or ""
            if request.siteName:
                user_requirements += f"\n官网名称: {request.siteName}"
            if request.listPageName:
                user_requirements += f"\n列表页面名称: {request.listPageName}"
            if request.sourceCredibility:
                user_requirements += f"\n信息源可信度: {request.sourceCredibility}"
            
            # 准备附件（图片）
            llm_attachments = None
            if request.attachments:
                from llm_agent import AttachmentData
                llm_attachments = [
                    AttachmentData(
                        filename=att.filename,
                        base64_data=att.base64,
                        mime_type=att.mimeType
                    )
                    for att in request.attachments
                ]
                _add_log(task_id, f"[INFO] 已附加 {len(llm_attachments)} 个图片/文件给 LLM")
            
            try:
                script_code = llm.generate_crawler_script(
                    page_url=request.url,
                    page_html=page_html,
                    page_structure=page_structure,
                    network_requests=network_requests,
                    user_requirements=user_requirements if user_requirements.strip() else None,
                    start_date=request.startDate,
                    end_date=request.endDate,
                    enhanced_analysis=enhanced_analysis,
                    attachments=llm_attachments,
                    run_mode=request.runMode,
                    task_id=task_id
                )
                
                # 检查是否是 fallback 脚本（LLM 调用失败）
                if (
                    isinstance(script_code, str)
                    and ("fallback template" in script_code.lower() or "generated when llm fails" in script_code.lower())
                ):
                    _add_log(task_id, "[ERROR] LLM 调用失败，已使用备用模板脚本")
                    raise RuntimeError("LLM 调用失败，无法生成有效脚本。请检查网络连接或 API 配置。")
            except Exception as llm_err:
                error_msg = str(llm_err)
                _add_log(task_id, f"[ERROR] LLM 生成脚本失败: {error_msg}")
                # 如果错误信息中包含代理错误，给出更明确的提示
                if "ProxyError" in error_msg or "proxy" in error_msg.lower():
                    error_msg = "LLM API 连接失败（代理错误）。请检查网络代理设置或 VPN 配置。"
                raise RuntimeError(error_msg)
            
            token_usage = llm.get_token_usage()
            _add_log(task_id, f"[SUCCESS] LLM 生成完成，Token: {token_usage['total_tokens']:,}")
        else:
            # 确定性模板：不调用 LLM
            _add_log(task_id, "[SUCCESS] 已跳过 LLM：使用确定性模板生成脚本")
        
        # 兜底：确保 script_code 是字符串
        if not isinstance(script_code, str) or not script_code.strip():
            _add_log(task_id, "[ERROR] 脚本生成失败：script_code 为空或非字符串")
            raise RuntimeError("脚本生成失败：script_code 为空或非字符串")
        
        # 【关键】后处理：注入正确的分类映射（multi_page / auto_detect 且有可信映射时）
        # 注意：这是数据正确性的保障，必须始终执行，不受 auto_repair 开关影响
        # LLM 可能忽略 verified_category_mapping 而自己猜测 ID，此处强制覆盖为正确值
        if request.crawlMode in ("multi_page", "auto_detect") and verified_mapping:
            from main import _inject_selected_categories_and_fix_dates
            script_code = _inject_selected_categories_and_fix_dates(
                script_code=script_code,
                start_date=request.startDate,
                end_date=request.endDate,
                verified_mapping=verified_mapping
            )
            injected_filters = len((verified_mapping or {}).get("menu_to_filters", {})) if isinstance(verified_mapping, dict) else 0
            injected_urls = len((verified_mapping or {}).get("menu_to_urls", {})) if isinstance(verified_mapping, dict) else 0
            _add_log(task_id, f"[INFO] 已注入可信映射（filters={injected_filters}，urls={injected_urls}）")
        
        # 注意：HTTP 韧性层、日期提取工具等后处理已移至 llm_agent.py 中的条件性后处理
        # 这里不再需要单独调用
        
        # Step 8: 验证生成的代码
        if _is_cancelled(task_id):
            raise RuntimeError("任务已被用户取消")
            
        # Step 8: 验证生成的代码（可选）
        # 关闭 auto_repair 时：不进行任何检查，直接保存并运行
        if auto_repair_enabled:
            _update_step(task_id, 7, "🔍 正在验证生成的代码")
            _add_log(task_id, "[INFO] 正在进行静态代码检查...")
            
            try:
                from validator import StaticCodeValidator
                validator = StaticCodeValidator()
                issues = validator.validate(script_code)
                
                if validator.has_errors():
                    error_issues = [i for i in issues if i.severity.value == "error"]
                    _add_log(task_id, f"[ERROR] 代码验证失败：发现 {len(error_issues)} 个错误，已停止执行")
                    # 把最关键的错误原因写出来（避免只看到“失败”不知道为啥）
                    for it in error_issues[:5]:
                        line_info = f"（行 {it.line_number}）" if getattr(it, "line_number", None) else ""
                        _add_log(task_id, f"[ERROR] - [{it.code}]{line_info} {it.message}")
                        if getattr(it, "suggestion", ""):
                            _add_log(task_id, f"[ERROR]   建议: {it.suggestion}")
                    raise RuntimeError("生成的脚本未通过语法/规则验证，已终止任务")
                else:
                    warn_issues = [i for i in issues if i.severity.value == "warning"]
                    if warn_issues:
                        _add_log(task_id, f"[WARNING] 代码检查通过，但有 {len(warn_issues)} 个警告")
                        for it in warn_issues[:5]:
                            _add_log(task_id, f"[WARNING] - [{it.code}] {it.message}")
                    else:
                        _add_log(task_id, "[SUCCESS] 代码验证通过 ✓")
            except ImportError:
                _add_log(task_id, "[INFO] 验证器模块未安装，跳过验证")
            except Exception as val_err:
                _add_log(task_id, f"[WARNING] 验证时出现问题: {val_err}")
        else:
            _update_step(task_id, 7, "已跳过（auto_repair=false）")
            _add_log(task_id, "[INFO] 已关闭自动修复：跳过静态代码检查")
        
        # Step 9: 保存脚本
        _update_step(task_id, 8, "爬虫脚本已生成")
        
        output_dir = config.output_dir if config else Path("./py")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        output_name = request.outputScriptName
        if not output_name.endswith(".py"):
            output_name += ".py"
        
        output_path = output_dir / output_name
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(script_code)
        
        _add_log(task_id, f"[SUCCESS] 脚本已保存: {output_path}")
        _add_log(task_id, f"[SUCCESS] 文件大小: {len(script_code):,} 字符")
        
        # Step 10: 自动运行生成的爬虫脚本
        if _is_cancelled(task_id):
            raise RuntimeError("任务已被用户取消")
            
        _update_step(task_id, 9, "正在运行爬虫脚本")
        _add_log(task_id, f"[INFO] 正在运行: python {output_path}")
        
        import subprocess
        import sys
        import os as _os_env
        
        try:
            # 设置 UTF-8 编码环境，避免 Windows GBK 编码问题（LLM 生成的代码可能包含 emoji）
            script_env = _os_env.environ.copy()
            script_env["PYTHONIOENCODING"] = "utf-8"
            
            # 运行生成的脚本（使用 Popen 以便可以停止）
            process = subprocess.Popen(
                [sys.executable, str(output_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(output_dir),
                env=script_env,
                encoding="utf-8",
                errors="replace"  # 遇到无法解码的字符时替换，避免崩溃
            )
            
            # 存储进程引用以便可以停止
            task_processes[task_id] = process
            
            try:
                # 等待进程完成，最多5分钟
                stdout, stderr = process.communicate(timeout=300)
                
                if process.returncode == 0:
                    _add_log(task_id, "[SUCCESS] 爬虫脚本运行成功")
                    # 只显示最后1000字符避免日志过长
                    if stdout:
                        stdout_preview = stdout[-1000:] if len(stdout) > 1000 else stdout
                        _add_log(task_id, stdout_preview)
                elif process.returncode == -9 or process.returncode == -15:
                    _add_log(task_id, "[INFO] 脚本已被用户停止")
                else:
                    _add_log(task_id, f"[ERROR] 脚本运行失败，返回码: {process.returncode}")
                    if stderr:
                        _add_log(task_id, f"[STDERR] {stderr[-800:]}")
                    # 直接终止任务（否则会出现“脚本失败但任务完成”的误导）
                    raise RuntimeError(f"爬虫脚本运行失败（返回码 {process.returncode}）")
                        
            except subprocess.TimeoutExpired:
                _add_log(task_id, "[WARNING] 脚本运行超时（5分钟），正在终止...")
                process.kill()
                process.wait()
            finally:
                # 清理进程引用
                if task_id in task_processes:
                    del task_processes[task_id]
                    
        except Exception as run_err:
            _add_log(task_id, f"[WARNING] 运行脚本时出错: {run_err}")
        
        # Step 11: 验证爬取结果
        _update_step(task_id, 10, "📊 正在验证爬取结果")
        
        # 查找输出的文件（在 output 目录）
        # 兼容两种路径：pygen/output 和 pygen/py/output
        possible_dirs = []
        if output_dir:
            possible_dirs.append(output_dir.parent / "output") # pygen/output
            possible_dirs.append(output_dir / "output")        # pygen/py/output
        else:
            possible_dirs.append(Path("./output"))
            possible_dirs.append(Path("pygen/output"))
            possible_dirs.append(Path("pygen/py/output"))
            
        # 确定主输出目录（用于后续下载文件存放等），优先选择存在的目录
        output_json_dir = possible_dirs[0]
        for d in possible_dirs:
            if d.exists():
                output_json_dir = d
                break
            
        reports = []
        news_articles = []
        markdown_file = None
        
        all_json_files = []
        for d in possible_dirs:
            if d.exists():
                # 注意：Windows 下可能存在“以 .json 结尾的目录”（历史 bug），glob("*.json") 会把目录也返回
                # 这里显式过滤，仅保留真正的文件；若遇到 *.json 目录，则尝试读取其内部的 *.json 文件作为兜底
                for p in d.glob("*.json"):
                    try:
                        if p.is_file():
                            all_json_files.append(p)
                        elif p.is_dir():
                            # 兜底：将目录内的 json 文件也纳入候选（修复历史产物：pygen_multi_*.json/xxx.json）
                            for q in p.glob("*.json"):
                                if q.is_file():
                                    all_json_files.append(q)
                    except Exception:
                        continue
        
        # 去重
        all_json_files = list(set(all_json_files))
        
        if all_json_files:
            # 根据运行模式处理不同的结果格式
            if request.runMode == "news_sentiment":
                # 新闻舆情模式：查找 JSON 文件（优先 news_ 开头的文件）
                all_json_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                
                # 只考虑最近 5 分钟内创建的文件
                import time as time_module
                recent_cutoff = time_module.time() - 300  # 5 分钟
                recent_json_files = [f for f in all_json_files if f.stat().st_mtime > recent_cutoff]
                
                # 优先查找 news_ 开头的文件
                news_json_files = [f for f in recent_json_files if f.name.startswith("news_")]
                json_files = news_json_files if news_json_files else recent_json_files
                
                _add_log(task_id, f"[DEBUG] 找到 {len(recent_json_files)} 个最近的 JSON 文件，其中 {len(news_json_files)} 个是 news_ 开头的")
                
                latest_json = None
                for jf in json_files:
                    try:
                        with open(jf, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        # 检查是否包含 articles 或 news 字段
                        if "articles" in data or "news" in data:
                            latest_json = jf
                            _add_log(task_id, f"[DEBUG] 使用文件: {jf.name}，包含 {len(data.get('articles', data.get('news', [])))} 条数据")
                            break
                    except Exception as e:
                        _add_log(task_id, f"[DEBUG] 跳过文件 {jf.name}: {e}")
                        continue
                
                if latest_json:
                    _add_log(task_id, f"[INFO] 找到新闻 JSON 文件: {latest_json}")
                    
                    try:
                        with open(latest_json, 'r', encoding='utf-8') as f:
                            data = json.load(f)

                        def _safe_str(v: Any, default: str = "") -> str:
                            """将任意值安全转换为字符串，避免 Pydantic 因 None/非字符串类型报错"""
                            if v is None:
                                return default
                            if isinstance(v, str):
                                return v
                            try:
                                return str(v)
                            except Exception:
                                return default
                        
                        # 解析 articles 数组
                        articles_key = "articles" if "articles" in data else "news" if "news" in data else None
                        if articles_key and isinstance(data.get(articles_key), list):
                            for i, item in enumerate(data[articles_key]):
                                if not isinstance(item, dict):
                                    continue
                                
                                news_articles.append({
                                    "id": str(i + 1),
                                    "title": _safe_str(item.get("title"), "未知") or "未知",
                                    "author": _safe_str(item.get("author"), ""),
                                    "date": _safe_str(item.get("date"), ""),
                                    "source": _safe_str(item.get("source"), ""),
                                    "sourceUrl": _safe_str(item.get("sourceUrl") or item.get("url") or item.get("link"), ""),
                                    "summary": _safe_str(item.get("summary"), ""),
                                    "content": _safe_str(item.get("content"), ""),
                                    "category": _safe_str(item.get("category"), ""),  # 来源板块
                                })
                            
                            _add_log(task_id, f"[SUCCESS] 解析到 {len(news_articles)} 条新闻")
                    except Exception as parse_err:
                        _add_log(task_id, f"[WARNING] 解析新闻 JSON 失败: {parse_err}")
                else:
                    _add_log(task_id, f"[WARNING] 未找到包含新闻数据的 JSON 文件")
            else:
                # 企业报告模式：原有逻辑
                # 使用已经收集好的 all_json_files
                json_files = all_json_files
                json_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                
                if json_files:
                    latest_json = json_files[0]
                    _add_log(task_id, f"[INFO] 找到结果文件: {latest_json}")
                    
                    try:
                        with open(latest_json, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        
                        # 提取生成脚本输出的下载头（如有），供后续下载 PDF 时使用
                        _script_download_headers = data.get("downloadHeaders") if isinstance(data, dict) else None
                        
                        # 解析 reports 数组（兼容不同脚本字段命名：name/title/reportName/text）
                        if isinstance(data, dict) and "reports" in data:
                            for i, item in enumerate(data["reports"]):
                                if not isinstance(item, dict):
                                    continue

                                name = (
                                    item.get("name")
                                    or item.get("title")
                                    or item.get("reportName")
                                    or item.get("report_title")
                                    or item.get("text")
                                    or "未知"
                                )
                                download_url = (
                                    item.get("downloadUrl")
                                    or item.get("download_url")
                                    or item.get("url")
                                    or item.get("pdfUrl")
                                    or "#"
                                )
                                file_type = item.get("fileType") or item.get("file_type") or "pdf"

                                reports.append({
                                    "id": str(i + 1),
                                    "name": name,
                                    "date": item.get("date", ""),
                                    "downloadUrl": download_url,
                                    "fileType": file_type,
                                    "category": item.get("category", "")  # 来源板块
                                })
                        # 若用户提供了日期范围，则在服务端做一次"硬过滤"，避免脚本把无日期/越界数据也塞进来
                        # 规则：只保留 date 为 YYYY-MM-DD 且在 [startDate, endDate] 范围内的数据；无日期直接丢弃
                        try:
                            start_date = (request.startDate or "").strip()
                            end_date = (request.endDate or "").strip()
                            if start_date and end_date:
                                before = len(reports)
                                filtered = []
                                for r in reports:
                                    d = (r.get("date") or "").strip()
                                    if len(d) == 10 and d[4] == "-" and d[7] == "-" and start_date <= d <= end_date:
                                        filtered.append(r)
                                reports = filtered
                                dropped = before - len(reports)
                                if dropped:
                                    _add_log(task_id, f"[INFO] 已按日期范围过滤：丢弃 {dropped} 条（无日期或不在 {start_date}~{end_date}）")
                        except Exception as _filter_err:
                            _add_log(task_id, f"[WARNING] 服务端日期过滤失败（已忽略）：{_filter_err}")

                        # 【新增】结果截断：如果报告数量超过 100 且用户选了“不下载”或“新闻报告下载”等场景，为优化前端渲染，截断列表
                        # 记录总数以便前端展示提示
                        tasks[task_id]["totalCount"] = len(reports)
                        if len(reports) > 100:
                            _add_log(task_id, f"[INFO] 结果过多（{len(reports)} 条），已截断为前 100 条以优化展示")
                            reports = reports[:100]

                        _add_log(task_id, f"[SUCCESS] 解析到 {len(reports)} 条报告记录（总匹配: {tasks[task_id].get('totalCount', len(reports))}）")
                        
                        # 验证输出数据质量
                        try:
                            from validator import OutputValidator
                            output_validator = OutputValidator()
                            quality = output_validator.get_quality_report({"reports": reports})
                            
                            if quality["date_fill_rate"] < 0.3:
                                _add_log(task_id, f"[WARNING] 日期填充率较低: {quality['date_fill_rate']:.1%}")
                            else:
                                _add_log(task_id, f"[INFO] 数据质量: 日期填充率 {quality['date_fill_rate']:.1%}")
                        except ImportError:
                            pass
                        except Exception as qual_err:
                            _add_log(task_id, f"[WARNING] 质量检查失败: {qual_err}")
                            
                    except Exception as parse_err:
                        _add_log(task_id, f"[WARNING] 解析结果文件失败: {parse_err}")
        else:
            _add_log(task_id, f"[INFO] 未找到 output 目录: {output_json_dir}")
        
        # ============ PDF 下载逻辑（企业报告/新闻报告场景 + 选择下载文件）============
        downloaded_count = 0
        files_not_enough = False
        pdf_output_dir = None
        # 确保 _script_download_headers 已定义（LLM 生成的脚本可能没有此字段）
        if '_script_download_headers' not in dir():
            _script_download_headers = None
        
        if request.runMode in ["enterprise_report", "news_report_download"] and request.downloadReport == "yes" and len(reports) > 0:
            _add_log(task_id, "[INFO] 正在下载前5个PDF文件...")
            
            # 创建唯一的子文件夹：task_id + 时间戳
            import hashlib
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # 使用 URL 的哈希值作为标识（取前8位）
            url_hash = hashlib.md5(request.url.encode()).hexdigest()[:8]
            folder_name = f"{task_id}_{timestamp}_{url_hash}"
            
            pdf_output_dir = output_json_dir / "output_pdf" / folder_name
            pdf_output_dir.mkdir(parents=True, exist_ok=True)
            _add_log(task_id, f"[INFO] 下载目录: output_pdf/{folder_name}")
            
            # 最多下载5个
            max_download = 5
            to_download = reports[:max_download]
            
            # 检查是否不足5个
            if len(reports) < max_download:
                files_not_enough = True
                _add_log(task_id, f"[INFO] 日期范围内仅有 {len(reports)} 个文件，不足5份")
            
            import httpx
            import urllib.parse
            from urllib.parse import urlparse as _dl_urlparse
            
            # ── 构建防盗链请求头（泛化：从目标页面 URL 自动派生 Referer）──
            # 优先使用生成脚本输出的 downloadHeaders（更精准），否则从 request.url 派生
            _target_parsed = _dl_urlparse(request.url)
            _target_origin = f"{_target_parsed.scheme}://{_target_parsed.netloc}"
            _download_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/pdf, application/octet-stream, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": request.url,  # 用爬取的目标页面作为 Referer（最自然）
            }
            # 合并脚本输出的下载头（如果有），脚本头优先
            if _script_download_headers and isinstance(_script_download_headers, dict):
                _download_headers.update(_script_download_headers)
            
            downloaded_reports = []
            
            for i, report in enumerate(to_download):
                download_url = report.get("downloadUrl", "")
                if not download_url or download_url == "#":
                    _add_log(task_id, f"[WARNING] 报告 '{report.get('name', '未知')[:30]}...' 无有效下载链接，跳过")
                    downloaded_reports.append(report)  # 保留但不标记为本地
                    continue
                
                try:
                    # 生成安全的文件名
                    safe_name = report.get("name", f"report_{i+1}")[:50]
                    # 移除文件名中的非法字符
                    safe_name = "".join(c for c in safe_name if c.isalnum() or c in (' ', '-', '_', '.', '（', '）')).strip()
                    if not safe_name:
                        safe_name = f"report_{i+1}"
                    
                    file_ext = report.get("fileType", "pdf")
                    filename = f"{i+1}_{safe_name}.{file_ext}"
                    local_path = pdf_output_dir / filename
                    
                    # 下载文件（带防盗链头 + 403 自动重试）
                    dl_parsed = _dl_urlparse(download_url)
                    dl_headers = dict(_download_headers)
                    if dl_parsed.netloc and dl_parsed.netloc != _target_parsed.netloc:
                        dl_headers["Referer"] = f"{dl_parsed.scheme or 'https'}://{dl_parsed.netloc}/"
                    
                    # 构建候选 URL 列表（原始 URL + 子域名变体），用于 403 重试
                    candidate_urls = [download_url]
                    if dl_parsed.netloc:
                        # 常见的文件托管子域名前缀
                        # 如深交所: www.szse.cn/disc/... → disc.szse.cn/disc/...
                        path = dl_parsed.path or ""
                        base_domain = dl_parsed.netloc.replace("www.", "")
                        file_subdomains = ["disc", "download", "file", "static", "cdn", "docs"]
                        for prefix in file_subdomains:
                            if path.startswith(f"/{prefix}/"):
                                alt_host = f"{prefix}.{base_domain}"
                                if alt_host != dl_parsed.netloc:
                                    alt_url = f"{dl_parsed.scheme or 'https'}://{alt_host}{path}"
                                    candidate_urls.append(alt_url)
                                break
                    
                    dl_success = False
                    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True, verify=False) as client:
                        for try_url in candidate_urls:
                            response = await client.get(try_url, headers=dl_headers)
                            if response.status_code == 200:
                                with open(local_path, 'wb') as f:
                                    f.write(response.content)
                                _add_log(task_id, f"[SUCCESS] 已下载: {filename}")
                                downloaded_count += 1
                                relative_path = f"{folder_name}/{filename}"
                                report["localPath"] = relative_path
                                report["isLocal"] = True
                                dl_success = True
                                break
                            elif response.status_code == 403 and try_url != candidate_urls[-1]:
                                # 403 且还有备选 URL，继续尝试
                                continue
                            else:
                                _add_log(task_id, f"[WARNING] 下载失败 ({response.status_code}): {report.get('name', '未知')[:30]}...")
                            
                except Exception as dl_err:
                    _add_log(task_id, f"[WARNING] 下载出错: {str(dl_err)[:50]}...")
                
                downloaded_reports.append(report)
            
            # 只保留已下载的报告（前5个）
            reports = downloaded_reports
            _add_log(task_id, f"[SUCCESS] 共下载 {downloaded_count} 个文件到 {pdf_output_dir}")
        
        # 新闻舆情模式：如果选择下载，则保存 Markdown 文件
        if request.runMode == "news_sentiment" and request.downloadReport == "yes" and len(news_articles) > 0:
            _add_log(task_id, "[INFO] 正在生成 Markdown 文件...")
            
            # 创建 output_markdown 目录
            markdown_output_dir = config.output_dir.parent / "output" / "output_markdown"
            markdown_output_dir.mkdir(parents=True, exist_ok=True)
            
            # 生成 Markdown 内容
            md_lines = [
                f"# 新闻舆情爬取结果",
                f"",
                f"- **爬取时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"- **目标网址**: {request.url}",
                f"- **新闻数量**: {len(news_articles)}",
                f"",
                f"---",
                f""
            ]
            
            for article in news_articles:
                md_lines.append(f"## {article.get('id', '')}. {article.get('title', '未知标题')}")
                md_lines.append(f"")
                if article.get('date'):
                    md_lines.append(f"- **日期**: {article['date']}")
                if article.get('source'):
                    md_lines.append(f"- **来源**: {article['source']}")
                if article.get('author'):
                    md_lines.append(f"- **作者**: {article['author']}")
                if article.get('sourceUrl'):
                    md_lines.append(f"- **链接**: [{article['sourceUrl']}]({article['sourceUrl']})")
                if article.get('summary'):
                    md_lines.append(f"")
                    md_lines.append(f"> {article['summary']}")
                md_lines.append(f"")
                md_lines.append(f"---")
                md_lines.append(f"")
            
            # 保存 Markdown 文件
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            md_filename = f"news_{request.siteName or 'crawl'}_{timestamp}.md"
            md_filepath = markdown_output_dir / md_filename
            
            with open(md_filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(md_lines))
            
            markdown_file = str(md_filepath)
            _add_log(task_id, f"[SUCCESS] Markdown 已保存: {md_filepath}")
        
        # Step 12: 任务完成
        _update_step(task_id, 11, "🎉 任务完成")
        
        # 根据运行模式保存不同的结果
        if request.runMode == "news_sentiment":
            tasks[task_id]["newsArticles"] = news_articles
            tasks[task_id]["markdownFile"] = markdown_file
        else:
            tasks[task_id]["reports"] = reports
            tasks[task_id]["downloadedCount"] = downloaded_count
            tasks[task_id]["filesNotEnough"] = files_not_enough
            tasks[task_id]["pdfOutputDir"] = str(pdf_output_dir) if pdf_output_dir else None
        _add_log(task_id, "[SUCCESS] ✅ 任务完成！")
        
        tasks[task_id]["status"] = "completed"
        tasks[task_id]["resultFile"] = str(output_path)
        
    except asyncio.CancelledError:
        # asyncio 任务被取消（用户点击停止）
        _add_log(task_id, "[INFO] ⛔ 任务已被强制停止")
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = "任务被用户强制停止"
        # 不要重新抛出，让 finally 执行清理
        
    except Exception as e:
        error_str = str(e)
        # 如果是用户取消，使用更友好的消息
        if "任务已被用户取消" in error_str:
            _add_log(task_id, "[INFO] 任务已被用户停止")
            tasks[task_id]["error"] = "任务被用户停止"
        else:
            _add_log(task_id, f"[ERROR] 生成失败: {error_str}")
            tasks[task_id]["error"] = error_str
            import traceback
            _add_log(task_id, f"[ERROR] {traceback.format_exc()}")
        
        tasks[task_id]["status"] = "failed"
        
    finally:
        # 清理浏览器资源
        try:
            if browser:
                await browser.disconnect()
        except:
            pass
        try:
            if launcher:
                launcher.terminate()
        except:
            pass
        
        # 清理全局引用
        if task_id in task_browsers:
            del task_browsers[task_id]
        if task_id in task_cancelled:
            del task_cancelled[task_id]
        if task_id in task_asyncio_tasks:
            del task_asyncio_tasks[task_id]

# ============ API 路由 ============

@app.get("/")
async def root():
    """健康检查"""
    return {"status": "ok", "service": "PyGen API", "version": "2.0"}

@app.post("/api/menu-tree", response_model=MenuTreeResponse)
async def get_menu_tree(request: MenuTreeRequest):
    """
    获取目标页面的目录树
    
    用于"多页爬取"模式，在目录选择页展示可选目录
    """
    if not request.url:
        raise HTTPException(status_code=400, detail="URL 不能为空")
    
    url = request.url
    if not url.startswith("http"):
        url = "https://" + url
    
    try:
        result = await _fetch_menu_tree(url)
        return MenuTreeResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取目录树失败: {str(e)}")

@app.post("/api/generate")
async def start_generation(request: GenerateRequest):
    """
    启动爬虫脚本生成任务
    
    返回任务 ID，前端可通过 /api/status/{task_id} 轮询状态
    """
    if not request.url:
        raise HTTPException(status_code=400, detail="URL 不能为空")
    
    # 创建任务
    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {
        "taskId": task_id,
        "status": "pending",
        "currentStep": 0,
        "totalSteps": 12,  # 增加了代码验证和结果验证步骤
        "stepLabel": "准备中...",
        "logs": [],
        "resultFile": None,
        "error": None,
        "reports": [],  # 企业报告场景
        "downloadedCount": 0,  # 已下载文件数量
        "filesNotEnough": False,  # 文件是否不足5份
        "pdfOutputDir": None,  # PDF 下载目录
        "newsArticles": [],  # 新闻舆情场景
        "markdownFile": None,  # 新闻舆情 Markdown 文件
        "createdAt": time.time()
    }
    
    # 使用 asyncio.create_task 创建可取消的任务
    asyncio_task = asyncio.create_task(_run_generation_task(task_id, request))
    task_asyncio_tasks[task_id] = asyncio_task
    
    return {"taskId": task_id, "message": "任务已创建"}

@app.get("/api/status/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """
    获取任务状态和日志
    
    前端轮询此接口以更新进度和日志
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    task = tasks[task_id]
    return TaskStatusResponse(
        taskId=task["taskId"],
        status=task["status"],
        currentStep=task["currentStep"],
        totalSteps=task["totalSteps"],
        stepLabel=task["stepLabel"],
        logs=task["logs"],
        resultFile=task.get("resultFile"),
        error=task.get("error"),
        reports=task.get("reports"),
        downloadedCount=task.get("downloadedCount"),
        filesNotEnough=task.get("filesNotEnough"),
        pdfOutputDir=task.get("pdfOutputDir"),
        newsArticles=task.get("newsArticles"),
        markdownFile=task.get("markdownFile"),
        totalCount=task.get("totalCount")
    )

@app.get("/api/download/{filename}")
async def download_file(filename: str):
    """
    下载生成的脚本文件
    """
    output_dir = config.output_dir if config else Path("./py")
    file_path = output_dir / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="text/x-python"
    )


@app.get("/api/pdf/{filepath:path}")
async def view_pdf(filepath: str):
    """
    查看本地下载的 PDF 文件
    
    filepath 格式: "子文件夹名/文件名.pdf" (相对于 output/output_pdf 目录)
    """
    import urllib.parse
    from starlette.responses import Response
    
    try:
        # 解码 URL 编码的路径
        decoded_path = urllib.parse.unquote(filepath)
        
        # 与保存文件时使用相同的路径逻辑
        # output_dir 是 py 目录，output_dir.parent / "output" 是 output 目录
        output_dir = config.output_dir if config else Path("./py")
        pdf_base_dir = output_dir.parent / "output" / "output_pdf"
        
        # 调试日志
        print(f"[DEBUG] 请求路径: {filepath}")
        print(f"[DEBUG] 解码路径: {decoded_path}")
        print(f"[DEBUG] output_dir: {output_dir}")
        print(f"[DEBUG] pdf_base_dir: {pdf_base_dir}")
        print(f"[DEBUG] pdf_base_dir 绝对路径: {pdf_base_dir.absolute()}")
        print(f"[DEBUG] pdf_base_dir 存在: {pdf_base_dir.exists()}")
        
        # 首先尝试直接拼接路径
        file_path = pdf_base_dir / decoded_path
        print(f"[DEBUG] 尝试路径: {file_path}")
        print(f"[DEBUG] 文件存在: {file_path.exists()}")
        
        if not file_path.exists() and pdf_base_dir.exists():
            # 如果找不到，尝试遍历子文件夹
            filename_only = Path(decoded_path).name
            for subdir in pdf_base_dir.iterdir():
                if subdir.is_dir():
                    potential_path = subdir / filename_only
                    if potential_path.exists():
                        file_path = potential_path
                        break
        
        if not file_path.exists():
            raise HTTPException(
                status_code=404, 
                detail=f"PDF 文件不存在: {filepath}, 查找目录: {pdf_base_dir}, 完整路径: {file_path}"
            )
        
        # 根据文件扩展名确定 MIME 类型
        ext = file_path.suffix.lower()
        mime_types = {
            ".pdf": "application/pdf",
            ".doc": "application/msword",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xls": "application/vnd.ms-excel",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        media_type = mime_types.get(ext, "application/octet-stream")
        
        # 读取文件内容
        with open(file_path, 'rb') as f:
            content = f.read()
        
        # 对文件名进行 URL 编码，处理中文字符
        # 使用 RFC 5987 格式: filename*=UTF-8''encoded_filename
        encoded_filename = urllib.parse.quote(file_path.name)
        
        # 使用 inline 模式让浏览器直接显示而不是下载
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取文件失败: {str(e)}")


@app.post("/api/stop/{task_id}")
async def stop_task(task_id: str):
    """
    停止正在运行的任务
    
    终止任务相关的所有进程（浏览器、爬虫脚本等）
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    task = tasks[task_id]
    
    # 如果任务已经完成或失败，无需停止
    if task["status"] in ["completed", "failed"]:
        return {"message": "任务已结束", "status": task["status"]}
    
    _add_log(task_id, "[INFO] 收到停止请求，正在强制终止任务...")
    
    # 设置取消标志
    task_cancelled[task_id] = True
    
    # 【关键】取消 asyncio 任务（立即中断正在等待的异步操作）
    if task_id in task_asyncio_tasks:
        asyncio_task = task_asyncio_tasks[task_id]
        if not asyncio_task.done():
            asyncio_task.cancel()
            _add_log(task_id, "[INFO] 异步任务已取消")
        if task_id in task_asyncio_tasks:
            del task_asyncio_tasks[task_id]
    
    # 尝试终止正在运行的子进程（爬虫脚本）
    if task_id in task_processes:
        process = task_processes[task_id]
        try:
            import os
            
            # Windows 上使用 terminate()，Unix 上发送 SIGTERM
            if os.name == 'nt':
                process.terminate()
            else:
                import signal
                process.send_signal(signal.SIGTERM)
            
            # 等待一小段时间
            try:
                process.wait(timeout=3)
            except:
                # 如果还没结束，强制杀死
                process.kill()
                process.wait()
            
            _add_log(task_id, "[INFO] 爬虫脚本已终止")
        except Exception as e:
            _add_log(task_id, f"[WARNING] 终止进程时出错: {e}")
        finally:
            if task_id in task_processes:
                del task_processes[task_id]
    
    # 尝试关闭浏览器
    if task_id in task_browsers:
        browser_info = task_browsers[task_id]
        try:
            browser = browser_info.get("browser")
            launcher = browser_info.get("launcher")
            
            if browser:
                try:
                    await browser.disconnect()
                    _add_log(task_id, "[INFO] 浏览器连接已断开")
                except Exception as e:
                    _add_log(task_id, f"[WARNING] 断开浏览器时出错: {e}")
            
            if launcher:
                try:
                    launcher.terminate()
                    _add_log(task_id, "[INFO] Chrome 进程已终止")
                except Exception as e:
                    _add_log(task_id, f"[WARNING] 终止 Chrome 时出错: {e}")
                    
        except Exception as e:
            _add_log(task_id, f"[WARNING] 清理浏览器资源时出错: {e}")
        finally:
            if task_id in task_browsers:
                del task_browsers[task_id]
    
    # 更新任务状态
    task["status"] = "failed"
    task["error"] = "任务被用户停止"
    _add_log(task_id, "[INFO] ✅ 任务已停止")
    
    return {"message": "任务已停止", "taskId": task_id}

# ============ 主入口 ============

if __name__ == "__main__":
    # Windows + Python 3.12+ 默认 ProactorEventLoop 在进程退出/CTRL+C 时
    # 偶发出现 "unclosed transport" / "I/O operation on closed pipe" 的清理噪音。
    # 切换为 SelectorEventLoop 可显著减少该类报错（不影响功能）。
    import os as _os
    import asyncio as _asyncio
    if _os.name == "nt":
        try:
            _asyncio.set_event_loop_policy(_asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]
        except Exception:
            pass

    import uvicorn
    print("🚀 启动 PyGen API Server...")
    print("   访问 http://localhost:8000/docs 查看 API 文档")
    uvicorn.run(app, host="0.0.0.0", port=8000)

