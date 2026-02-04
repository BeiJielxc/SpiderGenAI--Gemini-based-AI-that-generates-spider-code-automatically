#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyGen - 智能爬虫脚本生成器（增强版）

给定列表页URL，自动分析页面结构并生成独立可运行的Python爬虫脚本。

增强功能：
- 空数据检测：识别需要交互才能加载数据的SPA页面
- 交互式API捕获：自动点击菜单探测完整的API参数
- 分类参数分析：识别必需的分类/筛选参数

使用方法：
    python main.py

或直接指定URL：
    python main.py https://example.com/list
"""
import asyncio
import sys
import os
import time
import json
import argparse
import re
import fnmatch
from datetime import datetime
from pathlib import Path

# Rich库用于美化输出
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich.progress import Progress, SpinnerColumn, TextColumn
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("提示：安装 rich 库可获得更好的显示效果: pip install rich")

from config import Config
from chrome_launcher import ChromeLauncher
from browser_controller import BrowserController
from llm_agent import LLMAgent
from date_extractor import get_injectable_code as get_date_extractor_code

def _match_path(pattern: str, path: str) -> bool:
    """
    路径匹配规则：
    - 前缀子树：`xxx/**` 匹配 xxx 及其子路径
    - 正则：`re:<pattern>`
    - 其他：fnmatch（支持 * ? []）
    """
    if not pattern:
        return False
    pattern = pattern.strip()
    if pattern.startswith("re:"):
        try:
            return re.search(pattern[3:], path) is not None
        except re.error:
            return False
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return fnmatch.fnmatchcase(path, pattern)

def _parse_menu_select(requirements: str) -> dict:
    """
    从“额外需求”中解析 menu_select 规则（推荐 JSON/YAML-like）。
    支持：
      - JSON：{"menu_select":{"include":[...],"exclude":[...]}}
      - 简化文本：
          include: a/**,b/**
          exclude: c/**,d/**
    """
    result = {"include": [], "exclude": []}
    if not requirements:
        return result
    txt = requirements.strip()
    # 1) JSON
    try:
        obj = json.loads(txt)
        ms = obj.get("menu_select", {}) if isinstance(obj, dict) else {}
        inc = ms.get("include", []) if isinstance(ms, dict) else []
        exc = ms.get("exclude", []) if isinstance(ms, dict) else []
        if isinstance(inc, list):
            result["include"] = [str(x) for x in inc]
        if isinstance(exc, list):
            result["exclude"] = [str(x) for x in exc]
        return result
    except Exception:
        pass
    # 2) 简化文本
    for line in txt.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().lower()
        items = [x.strip() for x in re.split(r"[,\s]+", v.strip()) if x.strip()]
        if k == "include":
            result["include"].extend(items)
        elif k == "exclude":
            result["exclude"].extend(items)
    return result

def _filter_leaf_paths(leaf_paths: list, include: list, exclude: list) -> list:
    """按 include/exclude 规则筛选叶子路径。include 为空表示全选。"""
    selected = []
    for p in leaf_paths:
        if include:
            ok = any(_match_path(rule, p) for rule in include)
            if not ok:
                continue
        if exclude and any(_match_path(rule, p) for rule in exclude):
            continue
        selected.append(p)
    return selected

def _remove_late_hardcoded_dates(script: str) -> str:
    """
    删除/注释掉脚本中后续再次赋值 START_DATE/END_DATE 的行，避免覆盖注入块。
    规则：保留注入块里的 START_DATE, END_DATE；其后遇到裸赋值行就注释掉。
    """
    lines = script.splitlines()
    out = []
    saw_injected = False
    for line in lines:
        if line.startswith("# === PyGen 注入：日期范围"):
            saw_injected = True
            out.append(line)
            continue
        if saw_injected and re.match(r'^\s*START_DATE\s*=\s*["\']', line):
            out.append("# " + line + "  # (disabled by PyGen: injected dates take precedence)")
            continue
        if saw_injected and re.match(r'^\s*END_DATE\s*=\s*["\']', line):
            out.append("# " + line + "  # (disabled by PyGen: injected dates take precedence)")
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if script.endswith("\n") else "")

def _inject_dates_and_categories(script_code: str, start_date: str, end_date: str, verified_mapping: dict) -> str:
    """
    生成后后处理：
    - 强制注入 START_DATE / END_DATE（默认=本次输入，允许命令行/环境变量覆盖）
    - 强制覆盖 CATEGORIES（只使用真实抓包得到的映射，避免模型猜）
    """
    # 1) 日期：插入一个“权威日期块”，并尽量替换脚本内的硬编码
    date_block = f'''
# === PyGen 注入：日期范围（权威来源：本次输入） ===
# 允许通过环境变量覆盖：PYGEN_START_DATE / PYGEN_END_DATE
# 允许通过命令行覆盖：--start-date / --end-date
import argparse as _argparse
import os as _os

def _pygen_resolve_dates():
    parser = _argparse.ArgumentParser(add_help=False)
    parser.add_argument("--start-date", dest="start_date")
    parser.add_argument("--end-date", dest="end_date")
    args, _ = parser.parse_known_args()
    sd = args.start_date or _os.getenv("PYGEN_START_DATE") or "{start_date}"
    ed = args.end_date or _os.getenv("PYGEN_END_DATE") or "{end_date}"
    return sd, ed

START_DATE, END_DATE = _pygen_resolve_dates()
# === PyGen 注入结束 ===
'''

    # 若脚本里已经有 START_DATE/END_DATE，直接在最前面注入并覆盖即可（后续引用将拿到注入值）
    # 为了避免重复定义导致误读，也做一次简单替换把硬编码改成占位（不追求完美，注入覆盖已足够）
    script = script_code
    script = script.replace('START_DATE = "2026-01-01"', f'START_DATE = "{start_date}"')
    script = script.replace('END_DATE = "2026-12-31"', f'END_DATE = "{end_date}"')

    # 在第一个 import 之后插入 date_block（保持 shebang/encoding 在最顶部）
    lines = script.splitlines()
    insert_at = 0
    for i, line in enumerate(lines[:60]):
        if line.startswith("import ") or line.startswith("from "):
            insert_at = i
            break
    else:
        insert_at = min(2, len(lines))
    lines.insert(insert_at, date_block.strip("\n"))
    script = "\n".join(lines) + "\n"

    # 2) 分类映射（两种形态）：
    # - menu_to_filters：同一个列表接口 + 不同 filters 参数（SPA/接口型）
    # - menu_to_urls：不同板块对应不同列表页 URL（服务端渲染/跳转型）
    menu_to_filters = (verified_mapping or {}).get("menu_to_filters") if isinstance(verified_mapping, dict) else None
    menu_to_urls = (verified_mapping or {}).get("menu_to_urls") if isinstance(verified_mapping, dict) else None

    converted_categories = None
    categories_comment = None

    if menu_to_filters:
        converted_categories = {}
        for cat_name, cat_params in menu_to_filters.items():
            filter_obj = {"launchedstatus": "启用"}
            filter_obj.update(cat_params)
            orderby = {"rankdate": "desc"}
            converted_categories[cat_name] = {"filters": filter_obj, "orderby": orderby}
        categories_comment = "可信分类映射（来源：真实交互抓包 filters）"
    elif menu_to_urls:
        # URL 型分类映射：让脚本遍历 URL 抓取（不依赖 filters API）
        converted_categories = {}
        for cat_name, url in menu_to_urls.items():
            if not url:
                continue
            converted_categories[cat_name] = {"url": url}
        categories_comment = "可信分类映射（来源：目录树跳转 URL）"

    if converted_categories:
        categories_block = f"\n# === PyGen 注入：{categories_comment} ===\n"
        categories_block += "CATEGORIES = " + json.dumps(converted_categories, ensure_ascii=False, indent=2) + "\n"
        categories_block += "# === PyGen 注入结束 ===\n"

        # 额外兜底：URL 型分类映射时，强制遍历 CATEGORIES（避免 LLM 忽略 CATEGORIES 仍只爬单页）
        # 思路：
        # - 暂时劫持脚本里的 save_results()：改为“只累积不落盘”
        # - 暂时劫持脚本里的 main()：改为遍历 CATEGORIES.url，每个 URL 都执行一次原 main()
        # - 最后用原 save_results() 一次性把合并结果写入一个 JSON 文件
        if menu_to_urls:
            categories_block += """
# === PyGen 注入：URL 分类遍历兜底（合并输出）===
try:
    _pygen__has_urls = isinstance(globals().get("CATEGORIES"), dict) and any(
        isinstance(v, dict) and v.get("url") for v in globals().get("CATEGORIES", {}).values()
    )
except Exception:
    _pygen__has_urls = False

if _pygen__has_urls:
    import os as _os2
    import json as _json2
    from datetime import datetime as _dt2

    _pygen__orig_main = globals().get("main")
    _pygen__accumulated = []

    def _pygen__get_output_dir():
        try:
            _default = _os2.path.abspath(_os2.path.join(_os2.path.dirname(__file__), "..", "output"))
        except Exception:
            _default = "."
        out_dir = globals().get("OUTPUT_DIR") or _default
        if not isinstance(out_dir, str) or not out_dir.strip():
            out_dir = _default
        out_dir = out_dir.strip()
        if out_dir.lower().endswith(".json"):
            out_dir = _os2.path.dirname(out_dir) or _default
        try:
            _os2.makedirs(out_dir, exist_ok=True)
        except Exception:
            out_dir = _default or "."
        return out_dir

    def _pygen__list_json_files(out_dir):
        try:
            return {
                f for f in _os2.listdir(out_dir)
                if f.endswith(".json") and _os2.path.isfile(_os2.path.join(out_dir, f))
            }
        except Exception:
            return set()

    def _pygen__read_articles_from_json(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = _json2.load(f)
            articles = data.get("articles") or data.get("news") or data.get("reports") or []
            if isinstance(articles, list):
                return articles
        except Exception:
            pass
        return []

    def _pygen_run_all_categories():
        global _pygen__accumulated
        try:
            out_dir = _pygen__get_output_dir()
            cats = globals().get("CATEGORIES", {})

            for _name, _cfg in (cats or {}).items():
                _url = _cfg.get("url") if isinstance(_cfg, dict) else None
                if not _url or not isinstance(_url, str):
                    continue

                before_files = _pygen__list_json_files(out_dir)

                try:
                    globals()["BASE_URL"] = _url if _url.endswith("/") else (_url + "/")
                except Exception:
                    pass

                try:
                    if callable(_pygen__orig_main):
                        _pygen__orig_main()
                except Exception as e:
                    print(f"[WARN] 分类 {_name} 执行失败: {e}")
                    continue

                after_files = _pygen__list_json_files(out_dir)
                new_files = after_files - before_files

                for fname in new_files:
                    fpath = _os2.path.join(out_dir, fname)
                    articles = _pygen__read_articles_from_json(fpath)
                    if articles:
                        # 给每条数据添加 category 字段，记录来源板块
                        for art in articles:
                            if isinstance(art, dict) and "category" not in art:
                                art["category"] = _name
                        _pygen__accumulated.extend(articles)
                        print(f"[INFO] 从 {fname} 读取 {len(articles)} 条数据 (分类: {_name})")

            ts = _dt2.now().strftime("%Y%m%d_%H%M%S")
            out_path = _os2.path.join(out_dir, f"pygen_multi_{ts}.json")
            with open(out_path, "w", encoding="utf-8") as f:
                _json2.dump({
                    "total": len(_pygen__accumulated),
                    "crawlTime": _dt2.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "dateRange": {
                        "start": globals().get("START_DATE", ""),
                        "end": globals().get("END_DATE", ""),
                    },
                    "articles": _pygen__accumulated,
                }, f, ensure_ascii=False, indent=2)
            print(f"[SUCCESS] 已合并保存 {len(_pygen__accumulated)} 条数据: {out_path}")

        except Exception as e:
            print(f"[WARN] 多分类遍历失败: {e}")

    def main():  # type: ignore
        return _pygen_run_all_categories()

# === PyGen 注入结束 ===
"""

        marker = 'if __name__ == "__main__":'
        idx = script.rfind(marker)
        if idx != -1:
            script = script[:idx] + categories_block + script[idx:]
        else:
            script += categories_block

    return script

def _inject_selected_categories_and_fix_dates(script_code: str, start_date: str, end_date: str, verified_mapping: dict) -> str:
    """组合后处理：注入日期/分类映射 + 禁用后续硬编码日期覆盖。"""
    s = _inject_dates_and_categories(script_code, start_date, end_date, verified_mapping)
    s = _remove_late_hardcoded_dates(s)
    return s

# =============================================================================
# 以下函数已移至 post_processor.py，保留存根以保持向后兼容
# =============================================================================

def _inject_http_resilience(script_code: str) -> str:
    """[已废弃] 请使用 post_processor.inject_http_resilience"""
    from post_processor import inject_http_resilience
    return inject_http_resilience(script_code)

def _inject_date_extraction_resilience(script_code: str) -> str:
    """[已废弃] 请使用 post_processor.inject_date_extraction_tools"""
    from post_processor import inject_date_extraction_tools
    return inject_date_extraction_tools(script_code)

def _fix_brittle_table_selectors(script_code: str) -> str:
    """[已废弃] 请使用 post_processor.fix_brittle_table_selectors"""
    from post_processor import fix_brittle_table_selectors
    return fix_brittle_table_selectors(script_code)

def _fix_hardcoded_date_extraction(script_code: str) -> str:
    """[已废弃] 该函数已删除，与 LLM 智能修复冲突"""
    # 不再执行任何操作，返回原代码
    return script_code



# 初始化控制台
if RICH_AVAILABLE:
    console = Console()
else:
    class FakeConsole:
        def print(self, *args, **kwargs):
            text = str(args[0]) if args else ""
            import re
            text = re.sub(r'\[.*?\]', '', text)
            print(text)
    console = FakeConsole()


def print_banner():
    """打印欢迎横幅"""
    if RICH_AVAILABLE:
        banner = """
[bold cyan]╭──────────────────────────────────────────────────────────╮
│                                                          │
│      🕷️ PyGen - 智能爬虫脚本生成器 v2.0                  │
│                                                          │
│      给定列表页URL，自动生成Python爬虫脚本               │
│      [增强版] 支持SPA页面分类参数自动识别               │
│                                                          │
╰──────────────────────────────────────────────────────────╯[/bold cyan]
"""
        console.print(banner)
    else:
        print("=" * 60)
        print("  PyGen - 智能爬虫脚本生成器 v2.0")
        print("  给定列表页URL，自动生成Python爬虫脚本")
        print("  [增强版] 支持SPA页面分类参数自动识别")
        print("=" * 60)


def get_user_input() -> dict:
    """获取用户输入"""
    console.print("\n[bold]请输入目标列表页信息：[/bold]\n")

    # URL输入
    if RICH_AVAILABLE:
        url = Prompt.ask("[cyan]目标列表页URL[/cyan]")
    else:
        url = input("目标列表页URL: ")

    if not url.startswith("http"):
        url = "https://" + url

    # 爬取时间范围
    console.print("\n[dim]设置爬取的时间范围（格式：YYYY-MM-DD）[/dim]")
    if RICH_AVAILABLE:
        start_date = Prompt.ask("[cyan]开始时间[/cyan]", default="2026-01-01")
        end_date = Prompt.ask("[cyan]结束时间[/cyan]", default="2026-12-31")
    else:
        start_date = input("开始时间 [2026-01-01]: ") or "2026-01-01"
        end_date = input("结束时间 [2026-12-31]: ") or "2026-12-31"

    # 额外需求（可选）
    console.print("\n[dim]可选：描述你想要爬取的具体内容（留空则自动分析）[/dim]")
    if RICH_AVAILABLE:
        requirements = Prompt.ask("[cyan]额外需求[/cyan]", default="")
    else:
        requirements = input("额外需求（可选）: ")

    # 输出文件名
    if RICH_AVAILABLE:
        output_name = Prompt.ask(
            "[cyan]输出脚本名称[/cyan]",
            default="crawler"
        )
    else:
        output_name = input("输出脚本名称 [crawler]: ") or "crawler"

    if not output_name.endswith(".py"):
        output_name += ".py"

    return {
        "url": url,
        "start_date": start_date,
        "end_date": end_date,
        "requirements": requirements,
        "output_name": output_name,
    }


async def run_generation(
    url: str,
    requirements: str,
    output_name: str,
    config: Config,
    start_date: str = "",
    end_date: str = "",
    include_rules: list | None = None,
    exclude_rules: list | None = None,
    dump_menu_tree: bool = False,
    menu_tree_only: bool = False
):
    """执行爬虫脚本生成流程（增强版）"""

    launcher = None
    browser = None

    try:
        # 1. 启动Chrome
        console.print("\n[yellow]🌐 正在启动Chrome浏览器...[/yellow]")

        launcher = ChromeLauncher(
            debug_port=config.cdp_debug_port,
            user_data_dir=config.cdp_user_data_dir,
            headless=False,
            auto_select_port=config.cdp_auto_select_port
        )
        launcher.launch()
        console.print("[green]✓ Chrome启动成功[/green]")

        # 2. 连接浏览器
        console.print("\n[yellow]🔗 正在连接浏览器...[/yellow]")

        browser = BrowserController(
            cdp_url=launcher.get_ws_endpoint(),
            timeout=config.cdp_timeout
        )
        await browser.connect()
        console.print("[green]✓ 浏览器连接成功[/green]")

        # 3. 打开目标页面
        console.print(f"\n[yellow]📄 正在打开目标页面...[/yellow]")
        console.print(f"   URL: {url}")

        success, error_msg = await browser.open(url)
        if not success:
            raise RuntimeError(f"无法打开目标页面: {error_msg}")

        console.print("[green]✓ 页面加载完成[/green]")

        # 4. 滚动页面以触发懒加载
        console.print("\n[yellow]📜 正在滚动页面以加载更多内容...[/yellow]")
        await browser.scroll_page(times=3)
        console.print("[green]✓ 页面滚动完成[/green]")

        # 5. 基础页面分析
        console.print("\n[yellow]🔍 正在分析页面结构...[/yellow]")

        page_info = await browser.get_page_info()
        page_html = await browser.get_full_html()
        page_structure = await browser.analyze_page_structure()
        network_requests = browser.get_captured_requests()

        console.print(f"   页面标题: {page_info.get('title', '未知')}")
        console.print(f"   HTML长度: {len(page_html):,} 字符")
        console.print(f"   捕获请求: {len(network_requests.get('all_requests', []))} 个")
        console.print(f"   API请求: {len(network_requests.get('api_requests', []))} 个")

        tables = page_structure.get("tables", [])
        lists = page_structure.get("lists", [])
        links = page_structure.get("links", {})

        console.print(f"   检测到表格: {len(tables)} 个")
        console.print(f"   检测到列表: {len(lists)} 个")
        console.print(f"   PDF/下载链接: {len(links.get('pdfLinks', []))} 个")

        console.print("[green]✓ 基础页面分析完成[/green]")

        # 6. 【增强功能】执行深度页面分析
        console.print("\n[yellow]🔬 正在执行增强页面分析...[/yellow]")
        console.print("   [增强功能] 检测数据加载状态、交互探测、参数分析")
        
        # Step 1: 枚举目录树（不抓包）
        menu_tree = await browser.enumerate_menu_tree(max_depth=3)
        root = menu_tree.get("root")
        leaf_paths = menu_tree.get("leaf_paths", []) or []

        if dump_menu_tree:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = config.output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            menu_path = output_dir / f"menu_tree_{ts}.json"
            with open(menu_path, "w", encoding="utf-8") as f:
                json.dump({"url": url, "root": root, "leaf_paths": leaf_paths}, f, ensure_ascii=False, indent=2)
            console.print(f"[cyan]   已导出目录树: {menu_path}（leaf_paths={len(leaf_paths)}）[/cyan]")

        # 如果只想要目录树就到此结束
        if menu_tree_only:
            console.print("[green]✓ 已完成目录树枚举（按需选择后可再生成脚本）[/green]")
            return True

        # Step 2: 解析选择规则（额外需求 + CLI）
        req_rules = _parse_menu_select(requirements)
        include_all = (include_rules or []) + req_rules.get("include", [])
        exclude_all = (exclude_rules or []) + req_rules.get("exclude", [])

        selected_leaf_paths = _filter_leaf_paths(leaf_paths, include_all, exclude_all)
        console.print(f"   目录叶子总数: {len(leaf_paths)}，本次选中: {len(selected_leaf_paths)}")
        if include_all:
            console.print(f"   include: {include_all[:5]}{'...' if len(include_all)>5 else ''}")
        if exclude_all:
            console.print(f"   exclude: {exclude_all[:5]}{'...' if len(exclude_all)>5 else ''}")

        # Step 3: 只对选中叶子抓包还原参数（可信映射）
        console.print("   正在对选中目录抓包还原参数（仅选中项）...")
        verified_mapping = await browser.capture_mapping_for_leaf_paths(selected_leaf_paths)

        # 仍然保留 enhanced_analysis 给 LLM（但分类映射以 verified_mapping 为准）
        enhanced_analysis = await browser.enhanced_page_analysis()
        enhanced_analysis["menu_tree"] = {"root": root, "leaf_paths": leaf_paths}
        enhanced_analysis["selected_leaf_paths"] = selected_leaf_paths
        enhanced_analysis["verified_category_mapping"] = verified_mapping
        
        # 显示增强分析结果
        data_status = enhanced_analysis.get("data_status", {})
        has_data = data_status.get("hasData", False)
        table_rows = data_status.get("tableRowCount", 0)
        list_items = data_status.get("listItemCount", 0)
        menus = data_status.get("potentialMenus", [])
        
        console.print(f"   数据状态: {'✓ 有数据' if has_data else '⚠ 无数据（需要交互）'}")
        console.print(f"   表格数据行: {table_rows}, 列表项: {list_items}")
        console.print(f"   检测到菜单项: {len(menus)} 个")
        
        # 显示分类参数分析结果
        param_analysis = enhanced_analysis.get("param_analysis", {})
        category_params = param_analysis.get("category_params", [])
        if category_params:
            console.print(f"   [bold yellow]⚠ 识别到分类参数: {len(category_params)} 个[/bold yellow]")
            for cat in category_params[:3]:
                console.print(f"      - {cat.get('param_name')}: {cat.get('sample_values', [])[:3]}")
        
        # 显示建议
        recommendations = enhanced_analysis.get("recommendations", [])
        if recommendations:
            console.print("   [yellow]系统建议:[/yellow]")
            for rec in recommendations:
                console.print(f"      ⚠️ {rec}")
        
        console.print("[green]✓ 增强页面分析完成[/green]")

        # 7. 调用LLM生成爬虫脚本
        console.print("\n[yellow]🤖 正在调用LLM生成爬虫脚本...[/yellow]")
        console.print(f"   模型: {config.qwen_model}")
        console.print("   这可能需要20-60秒，请耐心等待...")

        llm = LLMAgent(
            api_key=config.qwen_api_key,
            model=config.qwen_model,
            base_url=config.qwen_base_url,
            enable_auto_repair=config.llm_auto_repair
        )

        script_code = llm.generate_crawler_script(
            page_url=url,
            page_html=page_html,
            page_structure=page_structure,
            network_requests=network_requests,
            user_requirements=requirements if requirements else None,
            start_date=start_date,
            end_date=end_date,
            enhanced_analysis=enhanced_analysis  # 传入增强分析结果
        )

        # 7.5 生成后后处理
        # 注意：HTTP 韧性层、日期提取工具、脆弱选择器修复已移至 llm_agent.py 中的条件性后处理
        # 这里只保留用户指定的日期范围和分类映射注入
        if config.llm_auto_repair:
            # 7.6 生成后后处理：强制注入日期 / 可信分类映射（防止模型幻觉） + 禁用后续硬编码日期覆盖
            script_code = _inject_selected_categories_and_fix_dates(
                script_code=script_code,
                start_date=start_date,
                end_date=end_date,
                verified_mapping=verified_mapping
            )
        else:
            console.print("[yellow]⚠ 已关闭自动修复/后处理（llm.auto_repair=false）：将直接保存 LLM 原始生成代码[/yellow]")

        token_usage = llm.get_token_usage()
        console.print(f"   Token使用: {token_usage['total_tokens']:,} (输入: {token_usage['prompt_tokens']:,}, 输出: {token_usage['completion_tokens']:,})")
        console.print("[green]✓ 脚本生成完成[/green]")

        # 8. 保存脚本
        output_dir = config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = output_dir / output_name

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(script_code)

        console.print(f"\n[bold green]🎉 爬虫脚本已生成！[/bold green]")
        console.print(f"   保存路径: {output_path}")
        console.print(f"   文件大小: {len(script_code):,} 字符")

        # 9. 显示使用说明
        if RICH_AVAILABLE:
            console.print(Panel.fit(
                f"""[bold]使用方法：[/bold]

1. 进入脚本目录：
   [cyan]cd {output_dir}[/cyan]

2. 安装依赖（如需要）：
   [cyan]pip install requests beautifulsoup4[/cyan]
   或
   [cyan]pip install playwright && playwright install chromium[/cyan]

3. 运行脚本：
   [cyan]python {output_name}[/cyan]
""",
                title="[bold cyan]下一步[/bold cyan]",
                border_style="cyan"
            ))
        else:
            print("\n" + "=" * 50)
            print("使用方法：")
            print(f"1. cd {output_dir}")
            print("2. pip install requests beautifulsoup4")
            print(f"3. python {output_name}")
            print("=" * 50)

        return True

    except Exception as e:
        console.print(f"\n[bold red]❌ 生成失败: {e}[/bold red]")
        import traceback
        console.print(traceback.format_exc())
        return False

    finally:
        # 清理资源
        if browser:
            await browser.disconnect()
        if launcher:
            launcher.terminate()


async def main():
    """主函数"""
    print_banner()

    try:
        # 1. 加载配置
        console.print("\n[yellow]📄 正在加载配置...[/yellow]")
        config = Config()
        console.print("[green]✓ 配置加载成功[/green]")

        # 2. 获取用户输入（支持命令行参数，且命令行模式默认非交互）
        parser = argparse.ArgumentParser(prog="pygen", add_help=True)
        parser.add_argument("url", nargs="?", help="目标列表页URL")
        parser.add_argument("start_date", nargs="?", default="2026-01-01", help="开始日期 YYYY-MM-DD")
        parser.add_argument("end_date", nargs="?", default="2026-12-31", help="结束日期 YYYY-MM-DD")
        parser.add_argument("--requirements", default="", help="额外需求（可选）")
        parser.add_argument("--output", default="crawler.py", help="输出脚本文件名（默认 crawler.py）")
        parser.add_argument("--include", action="append", default=[], help="菜单选择 include 规则（可重复）")
        parser.add_argument("--exclude", action="append", default=[], help="菜单选择 exclude 规则（可重复）")
        parser.add_argument("--dump-menu-tree", action="store_true", help="导出目录树 JSON 到 output/ 目录")
        parser.add_argument("--menu-tree-only", action="store_true", help="只枚举/导出目录树，不生成脚本")
        parser.add_argument("-y", "--yes", action="store_true", help="命令行模式下跳过确认，直接开始")
        args, _ = parser.parse_known_args()

        if args.url:
            url = args.url
            if not url.startswith("http"):
                url = "https://" + url

            output_name = args.output
            if not output_name.endswith(".py"):
                output_name += ".py"

            user_input = {
                "url": url,
                "start_date": args.start_date,
                "end_date": args.end_date,
                "requirements": args.requirements,
                "output_name": output_name,
            }
            cli_mode = True
        else:
            user_input = get_user_input()
            cli_mode = False

        # 3. 显示任务信息
        if RICH_AVAILABLE:
            console.print("\n")
            console.print(Panel.fit(
                f"""[bold]生成任务确认：[/bold]

🌐 目标URL：[cyan]{user_input['url']}[/cyan]
📅 时间范围：[cyan]{user_input['start_date']} ~ {user_input['end_date']}[/cyan]
📝 额外需求：[cyan]{user_input['requirements'] or '（无）'}[/cyan]
📄 输出文件：[cyan]{user_input['output_name']}[/cyan]
""",
                title="[bold cyan]PyGen[/bold cyan]",
                border_style="cyan"
            ))
        else:
            print("\n" + "-" * 50)
            print(f"目标URL: {user_input['url']}")
            print(f"时间范围: {user_input['start_date']} ~ {user_input['end_date']}")
            print(f"额外需求: {user_input['requirements'] or '（无）'}")
            print(f"输出文件: {user_input['output_name']}")
            print("-" * 50)

        # 4. 确认执行（命令行模式默认非交互，除非显式不传 -y 且为交互环境）
        if cli_mode:
            if not args.yes:
                console.print("[yellow]提示：命令行模式建议加 -y 跳过确认（例如：python main.py <url> <start> <end> -y --output xxx.py）[/yellow]")
                console.print("[yellow]本次未加 -y，为避免卡在交互输入，已自动继续执行。[/yellow]")
        else:
            if RICH_AVAILABLE:
                if not Confirm.ask("\n是否开始生成？", default=True):
                    console.print("[yellow]已取消操作[/yellow]")
                    return
            else:
                confirm = input("\n是否开始生成？[Y/n]: ")
                if confirm.lower() == 'n':
                    print("已取消操作")
                    return

        # 开始计时
        start_time = time.time()

        # 5. 执行生成
        success = await run_generation(
            url=user_input['url'],
            requirements=user_input['requirements'],
            output_name=user_input['output_name'],
            config=config,
            start_date=user_input['start_date'],
            end_date=user_input['end_date'],
            include_rules=getattr(args, "include", []) if cli_mode else None,
            exclude_rules=getattr(args, "exclude", []) if cli_mode else None,
            dump_menu_tree=getattr(args, "dump_menu_tree", False) if cli_mode else False,
            menu_tree_only=getattr(args, "menu_tree_only", False) if cli_mode else False
        )

        # 计算耗时
        elapsed_time = time.time() - start_time

        if success:
            console.print("\n[bold green]✅ 任务完成！[/bold green]")
            console.print(f"[cyan]⏱️  总耗时: {elapsed_time:.2f} 秒[/cyan]")
        else:
            console.print("\n[bold red]❌ 任务失败[/bold red]")
            console.print(f"[cyan]⏱️  耗时: {elapsed_time:.2f} 秒[/cyan]")
            sys.exit(1)

    except FileNotFoundError as e:
        console.print(f"\n[bold red]错误：{e}[/bold red]")
        console.print("\n请确保配置文件存在：")
        console.print("  - pygen/config.yaml")
        console.print("  - 或项目根目录的 config.yaml")
        sys.exit(1)

    except ValueError as e:
        console.print(f"\n[bold red]配置错误：{e}[/bold red]")
        sys.exit(1)

    except KeyboardInterrupt:
        console.print("\n\n[yellow]用户中断操作[/yellow]")
        sys.exit(0)

    except Exception as e:
        console.print(f"\n[bold red]发生错误：{e}[/bold red]")
        import traceback
        console.print(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
