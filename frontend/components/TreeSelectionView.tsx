import React, { useState, useEffect, useRef } from 'react';
import { 
  ArrowLeft, 
  Search, 
  ChevronRight, 
  ChevronDown, 
  Folder, 
  FileText, 
  CheckSquare, 
  Loader2,
  Download,
  Play,
  Check,
  Minus,
  AlertCircle
} from 'lucide-react';
import { TreeNode, CrawlerFormData, API_BASE_URL, GenerateRequest } from '../types';

interface TreeSelectionViewProps {
  url: string;
  formData: CrawlerFormData;
  onGenerate: (selectedPaths: string[], taskId: string) => void;
  onBack: () => void;
}

const TreeSelectionView: React.FC<TreeSelectionViewProps> = ({ url, formData, onGenerate, onBack }) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusText, setStatusText] = useState("正在获取页面目录树...");
  
  // 目录树数据（从后端获取）
  const [treeData, setTreeData] = useState<TreeNode | null>(null);
  const [leafPaths, setLeafPaths] = useState<string[]>([]);
  
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set(['root']));
  const [selectedNodes, setSelectedNodes] = useState<Set<string>>(new Set());
  const [searchQuery, setSearchQuery] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  
  // AbortController 用于取消之前的请求（防止 React StrictMode 双重渲染导致的重复请求）
  const abortControllerRef = useRef<AbortController | null>(null);

  // 从后端获取目录树
  const fetchMenuTree = async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    setStatusText("正在获取页面目录树...");
    let aborted = false;
    
    try {
      const response = await fetch(`${API_BASE_URL}/api/menu-tree`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
        signal  // 传入 AbortSignal，支持取消请求
      });
      
      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP ${response.status}`);
      }
      
      const data = await response.json();
      
      if (data.root) {
        setTreeData(data.root);
        setLeafPaths(data.leaf_paths || []);
        setStatusText(`获取完成，共 ${data.leaf_paths?.length || 0} 个叶子节点`);
        
        // 自动展开第一层
        const firstLevelIds = data.root.children?.map((c: TreeNode) => c.id) || [];
        setExpandedNodes(new Set(['root', ...firstLevelIds]));
      } else {
        setStatusText("未检测到目录结构");
      }
    } catch (err: any) {
      // 如果是请求被取消，忽略错误
      if (err.name === 'AbortError') {
        console.log('请求已取消（可能是组件重新渲染）');
        aborted = true;
        return;
      }
      setError(err.message || '获取目录树失败');
      setStatusText("获取失败");
    } finally {
      // 被取消的请求不应把 UI 切到“未检测到目录结构”，保持 loading 状态等待下一次请求结果
      if (!aborted) {
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    // 取消之前的请求（如果有）
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    
    // 创建新的 AbortController
    const controller = new AbortController();
    abortControllerRef.current = controller;
    
    fetchMenuTree(controller.signal);
    
    // 清理函数：组件卸载时取消请求
    return () => {
      controller.abort();
    };
  }, [url]);

  // 手动刷新（按钮点击）
  const handleRefresh = () => {
    // 取消之前的请求
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    
    // 创建新的 AbortController
    const controller = new AbortController();
    abortControllerRef.current = controller;
    
    fetchMenuTree(controller.signal);
  };

  // Helper: Get all descendant leaf IDs
  const getAllDescendantLeafIds = (node: TreeNode, ids: string[] = []): string[] => {
    if (node.isLeaf) {
      ids.push(node.id);
    }
    node.children?.forEach(child => getAllDescendantLeafIds(child, ids));
    return ids;
  };

  // Helper: Get all descendant leaf paths
  const getAllDescendantLeafPaths = (node: TreeNode, paths: string[] = []): string[] => {
    if (node.isLeaf) {
      paths.push(node.path);
    }
    node.children?.forEach(child => getAllDescendantLeafPaths(child, paths));
    return paths;
  };

  // Helper: Check node status
  const getNodeStatus = (node: TreeNode): 'checked' | 'unchecked' | 'indeterminate' => {
    if (node.isLeaf) {
      return selectedNodes.has(node.id) ? 'checked' : 'unchecked';
    }
    
    const descendantIds = getAllDescendantLeafIds(node);
    if (descendantIds.length === 0) return 'unchecked';
    
    const checkedCount = descendantIds.filter(id => selectedNodes.has(id)).length;
    
    if (checkedCount === 0) return 'unchecked';
    if (checkedCount === descendantIds.length) return 'checked';
    return 'indeterminate';
  };

  const toggleExpand = (id: string) => {
    const newExpanded = new Set(expandedNodes);
    if (newExpanded.has(id)) newExpanded.delete(id);
    else newExpanded.add(id);
    setExpandedNodes(newExpanded);
  };

  const toggleSelect = (node: TreeNode) => {
    const status = getNodeStatus(node);
    const newSelected = new Set(selectedNodes);
    const descendants = getAllDescendantLeafIds(node);

    if (status === 'checked') {
      // Uncheck all
      descendants.forEach(id => newSelected.delete(id));
      if (node.isLeaf) newSelected.delete(node.id);
    } else {
      // Check all
      descendants.forEach(id => newSelected.add(id));
      if (node.isLeaf) newSelected.add(node.id);
    }
    setSelectedNodes(newSelected);
  };

  // 生成爬虫脚本
  const handleGenerate = async () => {
    if (selectedNodes.size === 0) {
      alert("请至少选择一个爬取目标");
      return;
    }
    
    setIsGenerating(true);
    
    try {
      // 构建选中的路径列表
      const selectedPathsList: string[] = [];
      const findPaths = (node: TreeNode) => {
        if (node.isLeaf && selectedNodes.has(node.id)) {
          selectedPathsList.push(node.path);
        }
        node.children?.forEach(findPaths);
      };
      if (treeData) findPaths(treeData);
      
      // 准备附件数据（转换为 API 可用的格式，与 ExecutionView 保持一致）
      const attachmentData = formData.attachments
        .filter(att => att.base64 && att.mimeType)
        .map(att => ({
          filename: att.file.name,
          base64: att.base64!,
          mimeType: att.mimeType!
        }));
      
      // 调用后端生成接口
      const request: GenerateRequest = {
        url: formData.reportUrl,
        startDate: formData.startDate,
        endDate: formData.endDate,
        outputScriptName: formData.outputScriptName,
        extraRequirements: formData.extraRequirements,
        siteName: formData.siteName,
        listPageName: formData.listPageName,
        sourceCredibility: formData.sourceCredibility || undefined,
        runMode: formData.runMode,
        crawlMode: formData.crawlMode,
        // 关键：TreeSelectionView 之前没有传 downloadReport，后端会使用默认值 "yes" 导致即使用户选了不下载仍会下载
        downloadReport: formData.downloadReport,
        selectedPaths: selectedPathsList,
        // 多页模式也要把截图/附件传给 LLM（否则进目录树后附件会丢失）
        attachments: attachmentData.length > 0 ? attachmentData : undefined
      };
      
      const response = await fetch(`${API_BASE_URL}/api/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request)
      });
      
      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP ${response.status}`);
      }
      
      const data = await response.json();
      
      // 通知父组件进入执行页
      onGenerate(selectedPathsList, data.taskId);
      
    } catch (err: any) {
      alert(`启动失败: ${err.message}`);
      setIsGenerating(false);
    }
  };

  // 导出选择配置
  const handleExportConfig = () => {
    const selectedPathsList: string[] = [];
    const findPaths = (node: TreeNode) => {
      if (node.isLeaf && selectedNodes.has(node.id)) {
        selectedPathsList.push(node.path);
      }
      node.children?.forEach(findPaths);
    };
    if (treeData) findPaths(treeData);
    
    const config = {
      url,
      startDate: formData.startDate,
      endDate: formData.endDate,
      selectedPaths: selectedPathsList
    };
    
    const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json' });
    const downloadUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = downloadUrl;
    a.download = 'crawl_config.json';
    a.click();
    URL.revokeObjectURL(downloadUrl);
  };

  // Recursive Tree Renderer
  const renderTree = (node: TreeNode, level: number = 0) => {
    // 搜索过滤
    if (searchQuery) {
      const matches = node.name.toLowerCase().includes(searchQuery.toLowerCase());
      const hasMatchingDescendants = node.children?.some(c => 
        c.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        c.children?.some(gc => gc.name.toLowerCase().includes(searchQuery.toLowerCase()))
      );
      if (!matches && !hasMatchingDescendants && level > 0) {
        return null;
      }
    }

    const status = getNodeStatus(node);
    const isExpanded = expandedNodes.has(node.id);
    const hasChildren = node.children && node.children.length > 0;

    return (
      <div key={node.id} className="select-none">
        <div 
          className={`flex items-center py-1.5 px-2 hover:bg-gray-50 rounded-lg cursor-pointer transition-colors ${selectedNodes.has(node.id) ? 'bg-emerald-50/50' : ''}`}
          style={{ paddingLeft: `${level * 20 + 8}px` }}
        >
          {/* Expander Icon */}
          <div 
            onClick={(e) => { e.stopPropagation(); toggleExpand(node.id); }}
            className={`mr-1 p-0.5 rounded hover:bg-gray-200 text-gray-400 transition-colors ${!hasChildren ? 'invisible' : ''}`}
          >
            {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </div>

          {/* Custom Checkbox */}
          <div 
            onClick={(e) => { e.stopPropagation(); toggleSelect(node); }}
            className={`
               mr-2.5 w-5 h-5 shrink-0 flex items-center justify-center rounded border transition-all duration-200 cursor-pointer
               ${status === 'checked' || status === 'indeterminate' 
                 ? 'bg-emerald-500 border-emerald-500 shadow-sm shadow-emerald-200' 
                 : 'bg-white border-gray-300 hover:border-emerald-400'
               }
            `}
          >
            {status === 'checked' && <Check size={14} className="text-white" strokeWidth={3} />}
            {status === 'indeterminate' && <Minus size={14} className="text-white" strokeWidth={3} />}
          </div>

          {/* Label */}
          <div className="flex items-center gap-2 flex-1" onClick={() => toggleSelect(node)}>
             {node.isLeaf ? <FileText size={16} className="text-gray-400" /> : <Folder size={16} className="text-blue-400" />}
             <span className={`text-sm ${status === 'checked' ? 'text-gray-900 font-medium' : 'text-gray-700'}`}>
               {node.name}
             </span>
             {node.isLeaf && (
               <span className="text-xs text-gray-400 ml-2 font-mono hidden md:inline-block opacity-60">
                 {node.path}
               </span>
             )}
          </div>
        </div>

        {/* Children */}
        {hasChildren && isExpanded && (
          <div className="border-l border-gray-100 ml-[17px]">
            {node.children!.map(child => renderTree(child, level + 1))}
          </div>
        )}
      </div>
    );
  };

  // 计算选中的路径
  const getSelectedPaths = (): string[] => {
    const paths: string[] = [];
    const findPaths = (node: TreeNode) => {
      if (node.isLeaf && selectedNodes.has(node.id)) {
        paths.push(node.path);
      }
      node.children?.forEach(findPaths);
    };
    if (treeData) findPaths(treeData);
    return paths;
  };

  const selectedCount = selectedNodes.size;
  const selectedPaths = getSelectedPaths();

  return (
    <div className="h-full w-full bg-white flex flex-col">
      {/* 1. Header with Status Bar */}
      <div className="border-b border-gray-100 bg-white">
        <div className="px-6 py-4 flex items-center justify-between">
           <div className="flex items-center gap-4">
               <button onClick={onBack} className="p-2 hover:bg-gray-100 rounded-full text-gray-500 transition-colors">
                   <ArrowLeft size={20} />
               </button>
               <div>
                   <h1 className="text-xl font-bold text-gray-900">选择爬取目录</h1>
                   <div className="flex items-center gap-2 mt-1">
                       {loading ? (
                         <Loader2 size={12} className="animate-spin text-emerald-500"/>
                       ) : error ? (
                         <AlertCircle size={12} className="text-red-500" />
                       ) : (
                         <div className="w-2 h-2 rounded-full bg-emerald-500" />
                       )}
                       <p className={`text-xs ${error ? 'text-red-500' : 'text-gray-500'}`}>{error || statusText}</p>
                   </div>
               </div>
           </div>
           
           {/* Date Range Display */}
           <div className="hidden md:flex items-center gap-3 bg-gray-50 p-1.5 rounded-lg border border-gray-200">
               <input type="text" value={formData.startDate} readOnly className="bg-transparent text-sm w-24 text-center outline-none text-gray-600" />
               <span className="text-gray-400">-</span>
               <input type="text" value={formData.endDate} readOnly className="bg-transparent text-sm w-24 text-center outline-none text-gray-600" />
           </div>
        </div>

        {/* Read-only URL Bar */}
        <div className="px-6 pb-4">
            <div className="flex items-center gap-3 px-4 py-2.5 bg-slate-50 border border-gray-200 rounded-lg text-sm text-gray-600 font-mono">
                <span className="text-gray-400 select-none">Target URL:</span>
                <span className="truncate select-all">{url}</span>
            </div>
        </div>
      </div>

      {/* 2. Main Body: Split View */}
      <div className="flex-1 flex overflow-hidden">
        
        {/* Left: Tree Viewer */}
        <div className="flex-1 flex flex-col border-r border-gray-100 min-w-0">
            {/* Toolbar */}
            <div className="px-4 py-3 border-b border-gray-100 flex gap-2">
                <div className="relative flex-1">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={16} />
                    <input 
                        type="text" 
                        placeholder="搜索目录..." 
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="w-full pl-9 pr-3 py-1.5 bg-gray-50 border border-gray-200 rounded-md text-sm outline-none focus:border-emerald-500 transition-colors"
                    />
                </div>
                <button 
                    onClick={handleRefresh}
                    disabled={loading}
                    className="px-3 py-1.5 text-xs font-medium text-gray-600 bg-white border border-gray-200 rounded-md hover:bg-gray-50 disabled:opacity-50"
                >
                    {loading ? '加载中...' : '重新加载'}
                </button>
            </div>

            {/* Tree Content */}
            <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
                {loading ? (
                    <div className="flex flex-col items-center justify-center h-40 text-gray-400 gap-3">
                        <Loader2 size={24} className="animate-spin text-emerald-500" />
                        <span className="text-sm">正在分析页面结构...</span>
                        <span className="text-xs text-gray-400">这可能需要 30-60 秒</span>
                    </div>
                ) : error ? (
                    <div className="flex flex-col items-center justify-center h-40 text-red-400 gap-3">
                        <AlertCircle size={24} />
                        <span className="text-sm">{error}</span>
                        <button 
                          onClick={handleRefresh}
                          className="px-4 py-2 text-sm bg-red-50 text-red-600 rounded-lg hover:bg-red-100"
                        >
                          重试
                        </button>
                    </div>
                ) : treeData ? (
                    renderTree(treeData)
                ) : (
                    <div className="flex flex-col items-center justify-center h-40 text-gray-400 gap-3">
                        <Folder size={24} />
                        <span className="text-sm">未检测到目录结构</span>
                    </div>
                )}
            </div>
        </div>

        {/* Right: Selection Summary */}
        <div className="w-80 bg-white flex flex-col shrink-0">
            <div className="px-5 py-4 border-b border-gray-100 bg-gray-50">
                <h3 className="font-semibold text-gray-800">已选内容</h3>
                <p className="text-xs text-gray-500 mt-1">
                    已选择 <span className="text-emerald-600 font-bold">{selectedCount}</span> 个叶子节点
                </p>
            </div>
            
            <div className="flex-1 overflow-y-auto p-4">
                {selectedCount === 0 ? (
                    <div className="text-center mt-10 text-gray-400 text-sm">
                        <div className="bg-gray-50 rounded-full w-12 h-12 flex items-center justify-center mx-auto mb-3">
                             <CheckSquare size={20} className="opacity-20" />
                        </div>
                        <p>暂无选择</p>
                        <p className="text-xs opacity-60 mt-1">请在左侧勾选需要爬取的目录</p>
                    </div>
                ) : (
                    <ul className="space-y-2">
                        {selectedPaths.map((path, idx) => (
                            <li key={idx} className="text-xs bg-emerald-50 text-emerald-800 px-3 py-2 rounded border border-emerald-100 flex items-start gap-2">
                                <FileText size={14} className="mt-0.5 shrink-0 opacity-70" />
                                <span className="break-all leading-relaxed">{path}</span>
                            </li>
                        ))}
                    </ul>
                )}
            </div>

            {/* Actions Footer */}
            <div className="p-4 border-t border-gray-100 bg-white space-y-3">
                <button 
                    onClick={handleExportConfig}
                    disabled={selectedCount === 0}
                    className="w-full flex items-center justify-center gap-2 py-2.5 text-sm font-medium text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50"
                >
                    <Download size={16} />
                    导出选择配置 (JSON)
                </button>
                <button 
                    onClick={handleGenerate}
                    disabled={selectedCount === 0 || isGenerating || loading}
                    className={`
                        w-full flex items-center justify-center gap-2 py-3 rounded-xl text-white font-semibold shadow-lg shadow-emerald-200 transition-all
                        ${selectedCount === 0 || isGenerating || loading ? 'bg-emerald-300 cursor-not-allowed' : 'bg-emerald-500 hover:bg-emerald-600 hover:shadow-emerald-300 transform active:scale-[0.98]'}
                    `}
                >
                    {isGenerating ? (
                        <>
                           <Loader2 size={18} className="animate-spin" />
                           正在启动...
                        </>
                    ) : (
                        <>
                           <Play size={18} />
                           生成爬虫脚本
                        </>
                    )}
                </button>
            </div>
        </div>
      </div>
    </div>
  );
};

export default TreeSelectionView;
