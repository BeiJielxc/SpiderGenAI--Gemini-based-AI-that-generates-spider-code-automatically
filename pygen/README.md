# PyGen - 智能爬虫脚本生成器

给定任意列表页URL，自动分析页面结构并生成独立可运行的Python爬虫脚本。

## 功能特点

- 🔍 **智能分析**：自动识别页面中的表格、列表、分页、下载链接等元素
- 🌐 **API捕获**：自动捕获页面加载时的网络请求，识别数据API接口
- 🤖 **LLM生成**：使用Qwen大模型根据页面结构生成定制化爬虫代码
- 📦 **独立运行**：生成的脚本完全独立，可在任何环境中运行

## 快速开始

### 1. 安装依赖

```bash
cd pygen
pip install -r requirements.txt
playwright install chromium
```

### 2. 配置API Key

确保项目根目录或pygen目录下有 `config.yaml` 文件，包含Qwen API Key：

```yaml
qwen:
  api_key: "your-qwen-api-key-here"
  model: "qwen-max"
```

### 3. 运行

**交互模式：**
```bash
python main.py
```

**命令行模式：**
```bash
python main.py https://example.com/list "爬取所有报告下载链接" my_crawler.py
```

## 目录结构

```
pygen/
├── main.py              # 主入口
├── config.py            # 配置管理
├── chrome_launcher.py   # Chrome启动器
├── browser_controller.py # 浏览器控制器
├── llm_agent.py         # LLM代理（脚本生成核心）
├── requirements.txt     # 依赖列表
├── README.md            # 本文件
└── py/                  # 生成的爬虫脚本输出目录
    └── crawler.py       # 示例输出
```

## 工作流程

```
1. 输入目标列表页URL
         │
         ▼
2. 启动Chrome，打开页面
         │
         ▼
3. 捕获网络请求 + 分析页面结构
         │
         ▼
4. 将完整HTML + 结构信息发送给LLM
         │
         ▼
5. LLM生成定制化Python爬虫脚本
         │
         ▼
6. 保存到 py/ 目录
```

## 示例

### 中国货币网评级报告列表

```bash
python main.py "https://www.chinamoney.com.cn/chinese/zxpjbgh/"
```

生成的脚本会自动：
- 识别数据API接口
- 处理分页逻辑
- 提取报告标题、日期、下载链接
- 保存结果到JSON文件

## 注意事项

1. **首次运行**需要安装Playwright浏览器：`playwright install chromium`
2. **API Key**必须配置正确，否则无法调用LLM
3. 生成的脚本是**参考模板**，可能需要根据实际情况微调
4. 对于需要登录的网站，生成的脚本可能无法直接使用

## 依赖

- Python 3.8+
- Chrome浏览器
- Qwen API Key

