import React, { useState } from 'react';
import { 
  FileJson, 
  Globe, 
  LayoutList, 
  Link as LinkIcon, 
  Terminal, 
  Bot,
  Settings2,
  Layers,
  FileDown
} from 'lucide-react';
import FormInput from './components/FormInput';
import RichInput from './components/RichInput';
import DateInput from './components/DateInput';
import SelectInput from './components/SelectInput';
import ExecutionView from './components/ExecutionView';
import TreeSelectionView from './components/TreeSelectionView';
import { CrawlerFormData, Attachment } from './types';

const App: React.FC = () => {
  const [view, setView] = useState<'form' | 'tree-selection' | 'execution'>('form');
  const [formData, setFormData] = useState<CrawlerFormData>({
    startDate: '2026-01-01',
    endDate: '2026-12-31',
    extraRequirements: '',
    siteName: '',
    listPageName: '',
    reportUrl: '',
    outputScriptName: 'crawler.py',
    runMode: '',
    crawlMode: '',
    downloadReport: '',
    attachments: []
  });

  // 用户在 TreeSelectionView 选中的目录路径
  const [selectedPaths, setSelectedPaths] = useState<string[]>([]);
  
  // 当前任务 ID（用于 ExecutionView 轮询状态）
  const [taskId, setTaskId] = useState<string>('');

  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFormData(prev => {
      const updated = { ...prev, [name]: value };
      // 当运行模式为"新闻舆情爬取"时，强制设置 downloadReport 为 "no"
      if (name === 'runMode' && value === 'news_sentiment') {
        updated.downloadReport = 'no';
      }
      return updated;
    });
  };

  const handleFileSelect = (files: Attachment[]) => {
    setFormData(prev => ({
      ...prev,
      attachments: [...prev.attachments, ...files]
    }));
  };

  const handleRemoveFile = (index: number) => {
    setFormData(prev => ({
      ...prev,
      attachments: prev.attachments.filter((_, i) => i !== index)
    }));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    
    // 如果正在提交，直接返回，防止多次触发
    if (isSubmitting) {
      return;
    }

    if (!formData.runMode) {
      alert('请选择运行模式');
      return;
    }

    if (!formData.crawlMode) {
      alert('请选择爬取模式');
      return;
    }

    if (!formData.downloadReport) {
      alert('请选择是否下载文件');
      return;
    }

    if (!formData.reportUrl) {
      alert('请输入报告页面链接');
      return;
    }

    // 自动探测模式必须提供额外需求（附件或文字说明）
    if (formData.crawlMode === 'auto_detect') {
      const hasAttachments = formData.attachments && formData.attachments.length > 0;
      const hasExtraText = formData.extraRequirements && formData.extraRequirements.trim().length > 0;
      if (!hasAttachments && !hasExtraText) {
        alert('请在额外需求中给出爬取区域说明（例如有框选爬取区域的图片）');
        return;
      }
    }

    setIsSubmitting(true);
    
    // 根据模式决定下一步
    setTimeout(() => {
      // 延迟重置状态，防止短时间内重复点击
      setTimeout(() => {
        setIsSubmitting(false);
      }, 2000); // 2秒冷却时间
      
      if (formData.crawlMode === 'multi_page') {
        // 多板块爬取：先进入目录选择页（所有运行模式都支持）
        setView('tree-selection');
      } else {
        // 其他情况：直接进入执行页（单页爬取/自动探测不需要选择目录）
        setView('execution');
      }
    }, 300);
  };

  const handleSetToday = () => {
    const today = new Date();
    const yyyy = today.getFullYear();
    const mm = String(today.getMonth() + 1).padStart(2, '0');
    const dd = String(today.getDate()).padStart(2, '0');
    const s = `${yyyy}-${mm}-${dd}`;
    setFormData(prev => ({ ...prev, startDate: s, endDate: s }));
  };

  const handleBackToForm = () => {
    setView('form');
    setTaskId('');
    setSelectedPaths([]);
  };

  // TreeSelectionView 完成选择后回调
  const handleTreeSelectionComplete = (paths: string[], newTaskId: string) => {
    setSelectedPaths(paths);
    setTaskId(newTaskId);
    setView('execution');
  };

  // 单页模式直接生成时的 taskId 回调
  const handleExecutionStart = (newTaskId: string) => {
    setTaskId(newTaskId);
  };

  return (
    // Main container forces full viewport size
    <div className="h-screen w-screen overflow-hidden bg-white text-gray-800 font-sans">
      
      {view === 'execution' ? (
        <ExecutionView 
          mode={formData.runMode} 
          formData={formData}
          selectedPaths={selectedPaths}
          taskId={taskId}
          onTaskIdChange={handleExecutionStart}
          onBack={handleBackToForm} 
        />
      ) : view === 'tree-selection' ? (
        <TreeSelectionView 
          url={formData.reportUrl || "https://www.ccxi.com.cn/creditrating/result"}
          formData={formData}
          onGenerate={handleTreeSelectionComplete}
          onBack={handleBackToForm}
        />
      ) : (
        // Form view acts as a scrollable full page without floating card margins
        <div className="h-full w-full overflow-y-auto bg-white">
          <div className="min-h-full w-full px-4 md:px-12 py-8 md:py-12">
            
            {/* Constrain content width for readability, but background is full */}
            <div className="max-w-6xl mx-auto space-y-10">
              
              {/* Header Section */}
              <div className="flex items-center gap-5 pb-8 border-b border-gray-100">
                <div className="p-4 bg-emerald-50 rounded-2xl text-emerald-600 shrink-0">
                  <Bot size={32} />
                </div>
                <div>
                  <h1 className="text-3xl font-bold text-gray-900 tracking-tight mb-2">
                    基于qwen的爬虫代码自动生成脚本
                  </h1>
                  <p className="text-gray-500 text-base">
                    配置您的爬虫参数以生成自动化脚本
                  </p>
                </div>
              </div>

              {/* Form Section */}
              <form onSubmit={handleSubmit} className="space-y-10">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-x-10 gap-y-8">
                  
                  {/* Row 1: 日期 */}
                  <DateInput
                    label="开始时间:"
                    name="startDate"
                    placeholder="YYYY-MM-DD"
                    value={formData.startDate}
                    onChange={handleChange}
                  />
                  
                  <DateInput
                    label="结束时间:"
                    name="endDate"
                    placeholder="YYYY-MM-DD"
                    value={formData.endDate}
                    onChange={handleChange}
                  />

                  {/* Quick Action: 仅今日数据 */}
                  <div className="md:col-span-2 flex justify-start -mt-4">
                    <button
                      type="button"
                      onClick={handleSetToday}
                      className="
                        inline-flex items-center justify-center
                        px-4 py-2 rounded-lg
                        border border-gray-200 bg-white
                        text-sm font-medium text-gray-700
                        shadow-sm hover:border-gray-300
                        hover:bg-gray-50
                        transition-colors duration-200
                      "
                    >
                      仅今日数据
                    </button>
                  </div>

                  {/* Row 2: 额外需求 */}
                  <RichInput
                    label="额外需求"
                    name="extraRequirements"
                    placeholder="请输入任何额外的处理逻辑或需求..."
                    value={formData.extraRequirements}
                    onChange={handleChange}
                    onFileSelect={handleFileSelect}
                    onRemoveFile={handleRemoveFile}
                    attachedFiles={formData.attachments}
                    className="md:col-span-2"
                  />

                  {/* Row 3: 站点信息 */}
                  <FormInput
                    label="官网名称"
                    name="siteName"
                    type="text"
                    placeholder="例如：中诚信国际"
                    value={formData.siteName}
                    onChange={handleChange}
                    icon={<Globe size={18} />}
                  />
                  
                  <FormInput
                    label="列表页面名称"
                    name="listPageName"
                    type="text"
                    placeholder="例如：中诚信国际-评级结果发布"
                    value={formData.listPageName}
                    onChange={handleChange}
                    icon={<LayoutList size={18} />}
                  />

                  {/* Row 4: URL（必填） */}
                  <FormInput
                    label="报告页面链接 *"
                    name="reportUrl"
                    type="url"
                    placeholder="https://example.com/reports"
                    fullWidth
                    value={formData.reportUrl}
                    onChange={handleChange}
                    icon={<LinkIcon size={18} />}
                    clearable
                    onClear={() => setFormData(prev => ({ ...prev, reportUrl: '' }))}
                  />

                  {/* Row 5: 输出脚本名称 */}
                  <FormInput
                    label="输出脚本名称"
                    name="outputScriptName"
                    type="text"
                    placeholder="例如：test.py"
                    fullWidth
                    value={formData.outputScriptName}
                    onChange={handleChange}
                    icon={<Terminal size={18} />}
                    clearable
                    onClear={() => setFormData(prev => ({ ...prev, outputScriptName: '' }))}
                  />

                  {/* Row 6: Configuration Group (3 Columns) */}
                  <div className="md:col-span-2 grid grid-cols-1 md:grid-cols-3 gap-x-6 gap-y-8">
                    <SelectInput
                      label="运行模式 *"
                      name="runMode"
                      value={formData.runMode}
                      onChange={handleChange}
                      icon={<Settings2 size={18} />}
                      options={[
                        { value: 'enterprise_report', label: '企业报告下载' },
                        { value: 'news_report_download', label: '新闻报告下载' },
                        { value: 'news_sentiment', label: '新闻舆情爬取' }
                      ]}
                    />
                    {/* 重新启用：爬取模式（用于 multi_page 触发目录树枚举 + 真实抓包分类映射） */}
                    <SelectInput
                      label="爬取模式"
                      name="crawlMode"
                      value={formData.crawlMode}
                      onChange={handleChange}
                      icon={<Layers size={18} />}
                      options={[
                        { value: 'single_page', label: '单一板块爬取' },
                        { value: 'multi_page', label: '多板块爬取 (手动选目录树+抓包映射)' },
                        { value: 'auto_detect', label: '自动探测板块并爬取 (通用交互探测)' }
                      ]}
                    />
                    <SelectInput
                      label="是否下载文件"
                      name="downloadReport"
                      value={formData.downloadReport}
                      onChange={handleChange}
                      icon={<FileDown size={18} />}
                      options={[
                        { 
                          value: 'yes', 
                          label: '是 (下载前5个PDF/文件并展示)', 
                          disabled: formData.runMode === 'news_sentiment' 
                        },
                        { value: 'no', label: '否 (仅展示结果列表)' }
                      ]}
                    />
                  </div>
                </div>

                <div className="pt-6">
                  <button
                    type="submit"
                    disabled={isSubmitting}
                    className={`
                      w-full py-4 rounded-xl font-semibold text-lg text-white shadow-lg shadow-emerald-200
                      transition-all duration-300 transform active:scale-[0.99]
                      ${isSubmitting 
                        ? 'bg-emerald-400 cursor-not-allowed' 
                        : 'bg-emerald-500 hover:bg-emerald-600 hover:shadow-emerald-300'
                      }
                    `}
                  >
                    {isSubmitting ? '正在初始化...' : '提交配置'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default App;
