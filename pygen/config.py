"""
配置管理模块 - PyGen独立版
"""
import yaml
import os
from typing import Dict, Any, Optional
from pathlib import Path


class Config:
    """配置管理类"""
    
    def __init__(self, config_path: Optional[str] = None):
        """
        初始化配置
        
        Args:
            config_path: 配置文件路径，如果为None则自动查找
        """
        if config_path is None:
            # 优先查找 pygen 目录下的配置，否则使用项目根目录的配置
            pygen_config = Path(__file__).parent / "config.yaml"
            root_config = Path(__file__).parent.parent / "config.yaml"
            
            if pygen_config.exists():
                config_path = str(pygen_config)
            elif root_config.exists():
                config_path = str(root_config)
            else:
                raise FileNotFoundError(
                    "未找到配置文件！请确保以下位置之一存在 config.yaml:\n"
                    f"  - {pygen_config}\n"
                    f"  - {root_config}\n"
                    "或从 config.yaml.example 复制并配置。"
                )
        
        self.config_path = config_path
        self.config = self._load_config()
    
    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    
    def _get_active_llm_config(self) -> Dict[str, Any]:
        """
        获取当前激活的 LLM 配置
        
        优先使用新的 llm 配置结构，如果不存在则回退到旧的 qwen 配置
        """
        llm_config = self.config.get('llm', {})
        
        if llm_config:
            # 使用新配置结构
            active_model = llm_config.get('active', 'qwen')
            model_config = llm_config.get(active_model, {})
            
            if model_config:
                return {
                    'name': active_model,
                    **model_config
                }
        
        # 回退到旧的 qwen 配置
        qwen_config = self.config.get('qwen', {})
        return {
            'name': 'qwen',
            **qwen_config
        }
    
    @property
    def active_model_name(self) -> str:
        """获取当前激活的模型名称"""
        return self._get_active_llm_config().get('name', 'qwen')
    
    @property
    def qwen_api_key(self) -> str:
        """获取当前激活模型的 API Key（保持向后兼容的属性名）"""
        llm_config = self._get_active_llm_config()
        api_key = llm_config.get('api_key', '')
        
        if not api_key or api_key.startswith('YOUR_'):
            raise ValueError(
                f"请在 config.yaml 中配置 {llm_config.get('name', 'LLM')} 的 API Key\n"
                f"当前激活模型: llm.active = {self.active_model_name}"
            )
        return api_key
    
    @property
    def qwen_model(self) -> str:
        """获取当前激活模型的模型名称（保持向后兼容的属性名）"""
        return self._get_active_llm_config().get('model', 'qwen-max')
    
    @property
    def qwen_base_url(self) -> str:
        """获取当前激活模型的 API 基础 URL（保持向后兼容的属性名）"""
        return self._get_active_llm_config().get('base_url', 
            'https://dashscope.aliyuncs.com/compatible-mode/v1')
    
    @property
    def llm_display_name(self) -> str:
        """获取用于显示的 LLM 名称（包含提供商和模型名）"""
        config = self._get_active_llm_config()
        provider = config.get('name', 'unknown')
        model = config.get('model', 'unknown')
        return f"{provider}/{model}"
    
    def list_available_models(self) -> list:
        """列出所有可用的模型配置"""
        llm_config = self.config.get('llm', {})
        models = []
        active = llm_config.get('active', 'qwen')
        
        for key, value in llm_config.items():
            if key == 'active':
                continue
            if isinstance(value, dict) and 'model' in value:
                is_active = key == active
                api_key = value.get('api_key', '')
                is_configured = api_key and not api_key.startswith('YOUR_')
                models.append({
                    'name': key,
                    'model': value.get('model', ''),
                    'active': is_active,
                    'configured': is_configured
                })
        
        return models

    @property
    def llm_auto_repair(self) -> bool:
        """
        是否启用“LLM 自动修复 + 代码静态检查”。

        - true（默认）：启用修复循环与后端静态检查
        - false：不进行任何修复/检查，直接运行 LLM 原始生成代码（风险更高）
        """
        llm_config = self.config.get("llm", {})
        if isinstance(llm_config, dict):
            v = llm_config.get("auto_repair", True)
            # yaml 里可能是字符串，做一次宽松转换
            if isinstance(v, str):
                return v.strip().lower() not in ("0", "false", "no", "off")
            return bool(v)
        return True
    
    @property
    def cdp_debug_port(self) -> int:
        """获取CDP调试端口"""
        return self.config.get('cdp', {}).get('debug_port', 9222)
    
    @property
    def cdp_auto_select_port(self) -> bool:
        """是否自动选择CDP端口"""
        return self.config.get('cdp', {}).get('auto_select_port', True)
    
    @property
    def cdp_user_data_dir(self) -> str:
        """获取Chrome Profile目录"""
        default_dir = str(Path(__file__).parent / "chrome-profile")
        return self.config.get('cdp', {}).get('user_data_dir', default_dir)
    
    @property
    def cdp_timeout(self) -> int:
        """获取CDP操作超时时间（毫秒）"""
        timeout_sec = self.config.get('cdp', {}).get('timeout', 60)
        return timeout_sec * 1000

    @property
    def browser_headless(self) -> bool:
        """
        浏览器无头模式开关（全局控制所有爬取模式下的浏览器是否显示窗口）。

        - False（默认）：显示浏览器窗口，方便本地开发调试
        - True：无头模式，不显示浏览器窗口（Linux 服务器部署必须设为 True）
        """
        v = self.config.get('cdp', {}).get('headless', False)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)
    
    @property
    def output_dir(self) -> Path:
        """生成的爬虫脚本输出目录"""
        return Path(__file__).parent / "py"

