import React, { useState, useEffect } from 'react';
import { 
  ArrowLeft, 
  Clock, 
  CheckCircle2, 
  XCircle, 
  Loader2, 
  Calendar, 
  Search, 
  ExternalLink,
  FileText,
  PlayCircle,
  Eye,
  Trash2,
  RefreshCw,
  MoreHorizontal,
  ChevronRight
} from 'lucide-react';
import { API_BASE_URL, HistoryItem, CrawlerFormData, BatchJob } from '../types';

interface HistoryViewProps {
  onBack: () => void;
  onRerun: (config: CrawlerFormData) => void;
  onViewResult: (item: HistoryItem) => void;
}

const HistoryView: React.FC<HistoryViewProps> = ({ onBack, onRerun, onViewResult }) => {
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [selectedItem, setSelectedItem] = useState<HistoryItem | null>(null);
  const [deletingIds, setDeletingIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetchHistory();
  }, []);

  const fetchHistory = async () => {
    try {
      setLoading(true);
      const res = await fetch(`${API_BASE_URL}/api/history`);
      if (res.ok) {
        const data = await res.json();
        setHistory(data);
      }
    } catch (err) {
      console.error('Failed to fetch history:', err);
    } finally {
      setLoading(false);
    }
  };

  const deleteHistoryItem = async (item: HistoryItem) => {
    const confirmed = window.confirm('确定删除该历史记录吗？删除后不可恢复。');
    if (!confirmed) return;

    try {
      setDeletingIds((prev) => new Set(prev).add(item.id));
      const res = await fetch(`${API_BASE_URL}/api/history/${encodeURIComponent(item.id)}`, {
        method: 'DELETE'
      });

      if (!res.ok) {
        let errMsg = `HTTP ${res.status}`;
        try {
          const errData = await res.json();
          if (errData?.detail) errMsg = errData.detail;
        } catch {
          // ignore parse error, use status code
        }
        throw new Error(errMsg);
      }

      setHistory((prev) => prev.filter((historyItem) => historyItem.id !== item.id));
      if (selectedItem?.id === item.id) {
        setSelectedItem(null);
      }
    } catch (err) {
      console.error('Failed to delete history item:', err);
      alert('删除失败，请稍后重试。');
    } finally {
      setDeletingIds((prev) => {
        const next = new Set(prev);
        next.delete(item.id);
        return next;
      });
    }
  };

  const filteredHistory = history.filter(item => {
    const matchesSearch = 
      item.id.toLowerCase().includes(search.toLowerCase()) ||
      (item.taskType === 'single' && (item.config as CrawlerFormData).siteName?.toLowerCase().includes(search.toLowerCase())) ||
      (item.taskType === 'single' && (item.config as CrawlerFormData).reportUrl?.toLowerCase().includes(search.toLowerCase()));
    
    const matchesStatus = filterStatus === 'all' || item.status === filterStatus;
    
    return matchesSearch && matchesStatus;
  });

  const formatDate = (dateStr: string) => {
    try {
      return new Date(dateStr).toLocaleString();
    } catch {
      return dateStr;
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed': return <CheckCircle2 size={18} className="text-emerald-500" />;
      case 'failed': return <XCircle size={18} className="text-red-500" />;
      case 'running': return <Loader2 size={18} className="text-blue-500 animate-spin" />;
      default: return <Clock size={18} className="text-amber-500" />;
    }
  };

  const getStatusText = (status: string) => {
    switch (status) {
      case 'completed': return '已完成';
      case 'failed': return '失败';
      case 'running': return '运行中';
      case 'queued': return '排队中';
      default: return '等待中';
    }
  };

  const getStatusClass = (status: string) => {
    switch (status) {
      case 'completed': return 'bg-emerald-50 text-emerald-700 border-emerald-100';
      case 'failed': return 'bg-red-50 text-red-700 border-red-100';
      case 'running': return 'bg-blue-50 text-blue-700 border-blue-100';
      default: return 'bg-amber-50 text-amber-700 border-amber-100';
    }
  };

  return (
    <div className="flex flex-col h-full w-full bg-slate-50">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between shadow-sm z-10">
        <div className="flex items-center gap-4">
          <button 
            onClick={onBack}
            className="p-2 hover:bg-gray-100 rounded-full text-gray-500 transition-colors"
          >
            <ArrowLeft size={20} />
          </button>
          <div>
            <h1 className="text-xl font-bold text-gray-800 flex items-center gap-2">
              <Clock className="text-indigo-500" />
              历史记录
            </h1>
            <p className="text-xs text-gray-500 mt-1">查看过往的任务执行记录</p>
          </div>
        </div>
        
        <button 
          onClick={fetchHistory}
          className="p-2 hover:bg-gray-100 rounded-lg text-gray-500 transition-colors"
          title="刷新"
        >
          <RefreshCw size={20} />
        </button>
      </div>

      {/* Toolbar */}
      <div className="px-6 py-4 bg-white border-b border-gray-100 flex flex-col md:flex-row gap-4 justify-between items-center">
        <div className="relative w-full md:w-96">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
          <input 
            type="text" 
            placeholder="搜索任务 ID、网站名称、URL..." 
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-gray-50 border border-gray-200 rounded-lg focus:outline-none focus:border-indigo-500 transition-colors"
          />
        </div>
        
        <div className="flex gap-2 w-full md:w-auto overflow-x-auto pb-1 md:pb-0">
          {['all', 'completed', 'running', 'failed', 'pending'].map(status => (
            <button
              key={status}
              onClick={() => setFilterStatus(status)}
              className={`px-4 py-1.5 rounded-full text-sm font-medium whitespace-nowrap transition-colors ${
                filterStatus === status 
                  ? 'bg-indigo-600 text-white shadow-md' 
                  : 'bg-white border border-gray-200 text-gray-600 hover:bg-gray-50'
              }`}
            >
              {status === 'all' ? '全部' : getStatusText(status)}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 md:p-6">
        {loading ? (
          <div className="flex flex-col items-center justify-center h-64 text-gray-400">
            <Loader2 size={32} className="animate-spin text-indigo-500 mb-4" />
            <p>加载历史记录中...</p>
          </div>
        ) : filteredHistory.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 text-gray-400 bg-white rounded-xl border border-dashed border-gray-200">
            <div className="w-16 h-16 bg-gray-50 rounded-full flex items-center justify-center mb-4">
              <Search size={32} className="opacity-30" />
            </div>
            <p className="text-lg font-medium text-gray-500">未找到相关记录</p>
            <p className="text-sm mt-1">尝试调整搜索词或筛选条件</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4">
            {filteredHistory.map((item) => {
              const config = item.config as CrawlerFormData; // Assuming single for now
              // If batch, config is array. Simplified handling:
              const isBatch = item.taskType === 'batch';
              const title = isBatch 
                ? `批量任务 (${(item.config as BatchJob[]).length} 个子任务)`
                : (config.siteName || config.listPageName || '未命名网站');
              const subtitle = isBatch ? '' : (config.reportUrl ?? (config as { url?: string }).url ?? '');
              
              return (
                <div 
                  key={item.id}
                  className="bg-white rounded-xl border border-gray-200 shadow-sm hover:shadow-md transition-all p-5 flex flex-col md:flex-row gap-5 items-start md:items-center group"
                >
                  {/* Status & Icon */}
                  <div className={`shrink-0 p-3 rounded-xl ${
                    item.status === 'completed' ? 'bg-emerald-50 text-emerald-600' :
                    item.status === 'failed' ? 'bg-red-50 text-red-600' :
                    item.status === 'running' ? 'bg-blue-50 text-blue-600' :
                    'bg-amber-50 text-amber-600'
                  }`}>
                    {item.taskType === 'batch' ? <FileText size={24} /> : <ExternalLink size={24} />}
                  </div>

                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3 mb-1">
                      <h3 className="text-lg font-bold text-gray-800 truncate">{title}</h3>
                      <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${getStatusClass(item.status)}`}>
                        {getStatusText(item.status)}
                      </span>
                      <span className="text-xs text-gray-400 font-mono bg-gray-50 px-2 py-0.5 rounded border border-gray-100">
                        {item.id}
                      </span>
                    </div>
                    
                    <div className="text-sm text-gray-500 truncate mb-2">
                      {subtitle || '无 URL 信息'}
                    </div>
                    
                    <div className="flex flex-wrap items-center gap-4 text-xs text-gray-400">
                      <span className="flex items-center gap-1">
                        <Calendar size={12} />
                        {formatDate(item.createdAt)}
                      </span>
                      {isBatch ? (
                        <span className="bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded">批量模式</span>
                      ) : (
                        <>
                           <span className="bg-gray-100 text-gray-600 px-2 py-0.5 rounded">
                             {config.runMode === 'enterprise_report' ? '企业报告' : '新闻舆情'}
                           </span>
                           <span className="bg-gray-100 text-gray-600 px-2 py-0.5 rounded">
                             Agent 模式
                           </span>
                        </>
                      )}
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-3 w-full md:w-auto mt-2 md:mt-0 border-t md:border-t-0 border-gray-100 pt-3 md:pt-0">
                    <button
                      onClick={() => onViewResult(item)}
                      className="flex-1 md:flex-none flex items-center justify-center gap-2 px-4 py-2 bg-white border border-gray-200 text-gray-700 rounded-lg hover:bg-gray-50 hover:text-indigo-600 hover:border-indigo-200 transition-colors text-sm font-medium"
                    >
                      <Eye size={16} />
                      查看详情
                    </button>
                    
                    {!isBatch && (
                      <button
                        onClick={() => onRerun(config)}
                        className="flex-1 md:flex-none flex items-center justify-center gap-2 px-4 py-2 bg-indigo-50 text-indigo-600 border border-indigo-100 rounded-lg hover:bg-indigo-100 transition-colors text-sm font-medium"
                      >
                        <PlayCircle size={16} />
                        重新运行
                      </button>
                    )}

                    <button
                      onClick={() => deleteHistoryItem(item)}
                      disabled={deletingIds.has(item.id)}
                      className="flex-1 md:flex-none flex items-center justify-center gap-2 px-4 py-2 bg-white border border-red-100 text-red-600 rounded-lg hover:bg-red-50 transition-colors text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      <Trash2 size={16} />
                      {deletingIds.has(item.id) ? '删除中...' : '删除'}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default HistoryView;
