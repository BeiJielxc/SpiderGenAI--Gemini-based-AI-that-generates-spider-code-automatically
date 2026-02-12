import React, { useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  ArrowLeft,
  Download,
  FileSpreadsheet,
  Image as ImageIcon,
  Paperclip,
  Plus,
  Trash2,
  Upload,
  X
} from 'lucide-react';
import DateInput from './DateInput';
import { Attachment, CrawlerFormData } from '../types';

export interface BatchConfigData {
  rows: CrawlerFormData[];
}

interface BatchConfigViewProps {
  onBack: () => void;
  onSubmit: (data: BatchConfigData) => void;
}

type BatchField =
  | 'reportUrl'
  | 'listPageName'
  | 'startDate'
  | 'endDate'
  | 'outputScriptName'
  | 'runMode'
  | 'crawlMode'
  | 'downloadReport';

interface BatchRow {
  id: string;
  reportUrl: string;
  listPageName: string;
  startDate: string;
  endDate: string;
  outputScriptName: string;
  runMode: string;
  crawlMode: string;
  downloadReport: string;
  sourceCredibility: string;
  attachments: Attachment[];
  errors: Partial<Record<BatchField | 'attachments', string>>;
  importErrors: Partial<Record<BatchField, string>>;
}

const RUN_MODE_OPTIONS = [
  { value: 'enterprise_report', label: '企业报告下载' },
  { value: 'news_report_download', label: '新闻报告下载' },
  { value: 'news_sentiment', label: '新闻舆情爬取' }
] as const;

const CRAWL_MODE_OPTIONS = [
  { value: 'single_page', label: '单一板块爬取' },
  { value: 'auto_detect', label: '自动探测板块并爬取' },
  { value: 'date_range_api', label: '日期筛选类网站爬取' }
] as const;

const DOWNLOAD_OPTIONS = [
  { value: 'yes', label: '是' },
  { value: 'no', label: '否' }
] as const;

const RUN_MODE_MAP: Record<string, string> = {
  enterprise_report: 'enterprise_report',
  news_report_download: 'news_report_download',
  news_report: 'news_report_download',
  news_sentiment: 'news_sentiment',
  '企业报告下载': 'enterprise_report',
  '新闻报告下载': 'news_report_download',
  '新闻舆情爬取': 'news_sentiment'
};

const CRAWL_MODE_MAP: Record<string, string> = {
  single_page: 'single_page',
  multi_page: 'auto_detect',
  auto_detect: 'auto_detect',
  date_range_api: 'date_range_api',
  date_filter: 'date_range_api',
  '单一板块爬取': 'single_page',
  '多板块爬取': 'auto_detect',
  '自动探测板块并爬取': 'auto_detect',
  '日期筛选类网站爬取': 'date_range_api',
  // 兼容旧模板：将“多板块爬取”按新语义映射到自动探测
};

const DOWNLOAD_MAP: Record<string, string> = {
  yes: 'yes',
  no: 'no',
  true: 'yes',
  false: 'no',
  '1': 'yes',
  '0': 'no',
  '是': 'yes',
  '否': 'no'
};

const VALID_RUN_MODE_LABELS = RUN_MODE_OPTIONS.map((item) => item.label);
const VALID_CRAWL_MODE_LABELS = CRAWL_MODE_OPTIONS.map((item) => item.label);
const VALID_DOWNLOAD_LABELS = DOWNLOAD_OPTIONS.map((item) => item.label);

const DEFAULT_ROW: BatchRow = {
  id: '',
  reportUrl: '',
  listPageName: '',
  startDate: '',
  endDate: '',
  outputScriptName: '',
  runMode: '',
  crawlMode: '',
  downloadReport: '',
  sourceCredibility: '',
  attachments: [],
  errors: {},
  importErrors: {}
};

const parseCsvLine = (line: string): string[] => {
  const result: string[] = [];
  let current = '';
  let inQuote = false;

  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (ch === '"') {
      inQuote = !inQuote;
      continue;
    }
    if (ch === ',' && !inQuote) {
      result.push(current.trim());
      current = '';
      continue;
    }
    current += ch;
  }

  result.push(current.trim());
  return result;
};

const normalizeDate = (raw: string): string => {
  const v = raw.trim();
  if (!v) return '';

  const compact = v.replace(/[.]/g, '-').replace(/[\/]/g, '-');
  const m = compact.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (m) {
    const [, y, mo, d] = m;
    return `${y}-${mo.padStart(2, '0')}-${d.padStart(2, '0')}`;
  }

  const pureDigits = v.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (pureDigits) {
    const [, y, mo, d] = pureDigits;
    return `${y}-${mo}-${d}`;
  }

  return v;
};

const normalizeRunMode = (value: string): string => {
  const key = value.trim();
  return RUN_MODE_MAP[key] || RUN_MODE_MAP[key.toLowerCase()] || '';
};

const normalizeCrawlMode = (value: string): string => {
  const key = value.trim();
  return CRAWL_MODE_MAP[key] || CRAWL_MODE_MAP[key.toLowerCase()] || '';
};

const normalizeDownload = (value: string): string => {
  const key = value.trim();
  return DOWNLOAD_MAP[key] || DOWNLOAD_MAP[key.toLowerCase()] || '';
};

const toAttachment = (file: File): Promise<Attachment> =>
  new Promise((resolve) => {
    if (!file.type.startsWith('image/')) {
      resolve({ file, mimeType: file.type || 'application/octet-stream' });
      return;
    }

    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || '');
      const base64 = result.includes(',') ? result.split(',')[1] : '';
      resolve({
        file,
        base64,
        mimeType: file.type || 'application/octet-stream'
      });
    };
    reader.onerror = () => {
      resolve({ file, mimeType: file.type || 'application/octet-stream' });
    };
    reader.readAsDataURL(file);
  });

const BatchConfigView: React.FC<BatchConfigViewProps> = ({ onBack, onSubmit }) => {
  const [rows, setRows] = useState<BatchRow[]>([]);
  const [globalError, setGlobalError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const fileInputId = 'batch-config-upload-input';

  const validateRows = (input: BatchRow[]): BatchRow[] => {
    const scriptNameCount = new Map<string, number>();
    input.forEach((row) => {
      const key = row.outputScriptName.trim();
      if (key) {
        scriptNameCount.set(key, (scriptNameCount.get(key) || 0) + 1);
      }
    });

    return input.map((row) => {
      const errors: BatchRow['errors'] = { ...row.importErrors };

      if (!row.reportUrl.trim()) errors.reportUrl = '必填';
      if (!row.listPageName.trim()) errors.listPageName = '必填';
      if (!row.startDate.trim()) errors.startDate = '必填';
      if (!row.endDate.trim()) errors.endDate = '必填';
      if (!row.outputScriptName.trim()) errors.outputScriptName = '必填';
      if (!row.runMode) errors.runMode = '必选';
      if (!row.crawlMode) errors.crawlMode = '必选';
      if (!row.downloadReport) errors.downloadReport = '必选';

      if (row.startDate && row.endDate) {
        const start = new Date(row.startDate);
        const end = new Date(row.endDate);
        if (!Number.isNaN(start.getTime()) && !Number.isNaN(end.getTime()) && start > end) {
          errors.startDate = '开始时间不能晚于结束时间';
          errors.endDate = '结束时间不能早于开始时间';
        }
      }

      const scriptName = row.outputScriptName.trim();
      if (scriptName && (scriptNameCount.get(scriptName) || 0) > 1) {
        errors.outputScriptName = '脚本名称重复';
      }

      if (row.crawlMode === 'auto_detect' && row.attachments.length === 0) {
        errors.attachments = '自动探测模式需上传参考图';
      }

      return { ...row, errors };
    });
  };

  const hasErrors = useMemo(
    () => rows.some((row) => Object.keys(row.errors).length > 0),
    [rows]
  );

  const addRow = () => {
    setRows((prev) =>
      validateRows([
        ...prev,
        {
          ...DEFAULT_ROW,
          id: `row_${Date.now()}_${prev.length}`
        }
      ])
    );
    setGlobalError('');
  };

  const removeRow = (id: string) => {
    setRows((prev) => validateRows(prev.filter((row) => row.id !== id)));
  };

  const updateRow = (id: string, field: keyof BatchRow, value: string) => {
    setRows((prev) =>
      validateRows(
        prev.map((row) => {
          if (row.id !== id) return row;

          const nextImportErrors = { ...row.importErrors };
          if (field in nextImportErrors) {
            delete nextImportErrors[field as BatchField];
          }

          return {
            ...row,
            [field]: value,
            importErrors: nextImportErrors
          };
        })
      )
    );
  };

  const addAttachments = async (id: string, files: File[]) => {
    const attachments = await Promise.all(files.map(toAttachment));
    setRows((prev) =>
      validateRows(
        prev.map((row) =>
          row.id === id
            ? { ...row, attachments: [...row.attachments, ...attachments] }
            : row
        )
      )
    );
  };

  const removeAttachment = (id: string, index: number) => {
    setRows((prev) =>
      validateRows(
        prev.map((row) =>
          row.id === id
            ? {
                ...row,
                attachments: row.attachments.filter((_, i) => i !== index)
              }
            : row
        )
      )
    );
  };

  const downloadTemplate = () => {
    const csv = [
      '页面链接,页面名称,开始时间,结束时间,支持图片（提交配置后在前端上传）,输出脚本名称,运行模式,爬取模式,是否下载报告',
      'https://www.ccxi.com.cn/creditrating/result,中诚信-信用评级-企业评级板块所有子板块,2026/1/1,2026/1/31,无,spider1.py,企业报告下载,自动探测板块并爬取,是',
      'https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice,巨潮咨询网-公告-深市,2026/2/11,2026/2/11,无,spider2.py,新闻报告下载,单一板块爬取,否',
      'http://www.sse.com.cn/disclosure/listedinfo/announcement/,上海证券交易所-披露-上市公司公告,2026/1/1,2026/1/1,无,spider3.py,新闻舆情爬取,日期筛选类网站爬取,否'
    ].join('\n');

    const file = new File([`\uFEFF${csv}`], 'crawler_batch_template.csv', {
      type: 'text/csv;charset=utf-8'
    });
    const url = URL.createObjectURL(file);
    const a = document.createElement('a');
    a.href = url;
    a.setAttribute('download', 'crawler_batch_template.csv');
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const importCsv = (text: string) => {
    const lines = text.split(/\r?\n/).filter((line) => line.trim());
    if (lines.length <= 1) {
      setGlobalError('未识别到有效配置行');
      return;
    }

    const parsedRows: BatchRow[] = [];
    for (let i = 1; i < lines.length; i += 1) {
      const cols = parseCsvLine(lines[i]);
      if (cols.length < 5) continue;

      const hasSupportImageColumn = cols.length >= 9;
      const scriptIndex = hasSupportImageColumn ? 5 : 4;
      const runModeIndex = hasSupportImageColumn ? 6 : 5;
      const crawlModeIndex = hasSupportImageColumn ? 7 : 6;
      const downloadIndex = hasSupportImageColumn ? 8 : 7;

      const rawRunMode = (cols[runModeIndex] || '').trim();
      const rawCrawlMode = (cols[crawlModeIndex] || '').trim();
      const rawDownload = (cols[downloadIndex] || '').trim();

      const runMode = normalizeRunMode(rawRunMode);
      const crawlMode = normalizeCrawlMode(rawCrawlMode);
      const downloadReport = normalizeDownload(rawDownload);

      const importErrors: BatchRow['importErrors'] = {};
      if (!runMode) {
        importErrors.runMode = `值错误：应为 [${VALID_RUN_MODE_LABELS.join('、')}]`;
      }
      if (!crawlMode) {
        importErrors.crawlMode = `值错误：应为 [${VALID_CRAWL_MODE_LABELS.join('、')}]`;
      }
      if (!downloadReport) {
        importErrors.downloadReport = `值错误：应为 [${VALID_DOWNLOAD_LABELS.join('、')}]`;
      }

      parsedRows.push({
        id: `row_${Date.now()}_${i}`,
        reportUrl: (cols[0] || '').trim(),
        listPageName: (cols[1] || '').trim(),
        startDate: normalizeDate(cols[2] || ''),
        endDate: normalizeDate(cols[3] || ''),
        outputScriptName: (cols[scriptIndex] || '').trim(),
        runMode,
        crawlMode,
        downloadReport,
        sourceCredibility: '',
        attachments: [],
        errors: {},
        importErrors
      });
    }

    if (!parsedRows.length) {
      setGlobalError('CSV 解析失败，请检查模板格式');
      return;
    }

    setRows((prev) => validateRows([...prev, ...parsedRows]));
    setGlobalError('');
  };

  const handleCsvUpload: React.ChangeEventHandler<HTMLInputElement> = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = () => {
      importCsv(String(reader.result || ''));
      e.target.value = '';
    };
    reader.readAsText(file);
  };

  const handleSubmit = () => {
    const validated = validateRows(rows);
    setRows(validated);

    if (!validated.length) {
      setGlobalError('请至少添加一条任务配置');
      return;
    }

    if (validated.some((row) => Object.keys(row.errors).length > 0)) {
      setGlobalError('配置中存在字段错误或缺失项，请修正红字标注内容后提交');
      return;
    }

    setIsSubmitting(true);
    setGlobalError('');

    const result: CrawlerFormData[] = validated.map((row) => ({
      startDate: row.startDate,
      endDate: row.endDate,
      extraRequirements: '',
      siteName: '',
      listPageName: row.listPageName,
      sourceCredibility: row.sourceCredibility,
      reportUrl: row.reportUrl,
      outputScriptName: row.outputScriptName,
      runMode: row.runMode,
      crawlMode: row.crawlMode,
      downloadReport: row.downloadReport,
      attachments: row.attachments
    }));

    onSubmit({ rows: result });
  };

  const renderError = (msg?: string) => {
    if (!msg) return null;
    return (
      <div className="flex items-center gap-1 mt-1 text-[10px] leading-tight text-red-500 font-medium">
        <AlertCircle size={10} />
        <span>{msg}</span>
      </div>
    );
  };

  return (
    <div className="h-full w-full bg-white flex flex-col">
      <input
        ref={fileInputRef}
        id={fileInputId}
        type="file"
        className="hidden"
        accept=".csv,text/csv,application/vnd.ms-excel"
        onChange={handleCsvUpload}
      />

      <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between bg-gray-50">
        <div className="flex items-center gap-4">
          <button onClick={onBack} className="p-2 hover:bg-gray-100 rounded-full text-gray-500">
            <ArrowLeft size={20} />
          </button>
          <div className="p-2 bg-indigo-50 text-indigo-600 rounded-lg">
            <FileSpreadsheet size={20} />
          </div>
          <div>
            <h1 className="text-[34px] leading-none font-bold text-gray-900">批量报告爬取配置</h1>
            <p className="text-sm text-gray-500 mt-1">导入并校验配置文件</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => fileInputRef.current?.click()}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm text-gray-700 border border-gray-200 rounded-xl hover:bg-white"
          >
            <Upload size={14} />
            重新上传
          </button>
          <button
            onClick={addRow}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm text-white bg-indigo-600 rounded-xl hover:bg-indigo-700"
          >
            <Plus size={14} />
            新增一行
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-auto bg-gray-50 p-6">
        {!rows.length ? (
          <div className="h-full min-h-[300px] flex flex-col items-center justify-center gap-4 text-gray-500 bg-white rounded-2xl border border-gray-100">
            <FileSpreadsheet size={40} className="text-indigo-400" />
            <p>先导入 CSV，或点击“新增一行”手动录入</p>
            <div className="flex items-center gap-3 pt-2">
              <button
                type="button"
                onClick={downloadTemplate}
                className="inline-flex items-center gap-2 px-4 py-2 text-sm text-gray-700 border border-gray-200 rounded-xl hover:bg-gray-50"
              >
                <Download size={14} />
                下载模板
              </button>
              <label
                htmlFor={fileInputId}
                className="inline-flex items-center gap-2 px-4 py-2 text-sm text-white bg-indigo-600 rounded-xl hover:bg-indigo-700 cursor-pointer"
              >
                <Upload size={14} />
                上传配置文件
              </label>
            </div>
          </div>
        ) : (
          <div className="bg-white rounded-2xl border border-gray-200 overflow-x-auto">
            <table className="w-full text-sm table-fixed">
              <thead className="bg-gray-50 text-gray-600">
                <tr>
                  <th className="px-2 py-3 w-10">#</th>
                  <th className="px-2 py-3 w-72">页面链接 *</th>
                  <th className="px-2 py-3 w-64">页面名称 *</th>
                  <th className="px-2 py-3 w-40">开始时间 *</th>
                  <th className="px-2 py-3 w-40">结束时间 *</th>
                  <th className="px-2 py-3 w-36">支持图片</th>
                  <th className="px-2 py-3 w-40">输出脚本名称 *</th>
                  <th className="px-2 py-3 w-44">运行模式 *</th>
                  <th className="px-2 py-3 w-44">爬取模式 *</th>
                  <th className="px-2 py-3 w-36">是否下载报告 *</th>
                  <th className="px-2 py-3 w-14">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {rows.map((row, index) => (
                  <tr key={row.id} className="align-top">
                    <td className="px-2 py-3 text-gray-400 text-center">{index + 1}</td>

                    <td className="px-2 py-3">
                      <input
                        value={row.reportUrl}
                        onChange={(e) => updateRow(row.id, 'reportUrl', e.target.value)}
                        className="w-full border border-gray-200 rounded-2xl px-3 py-2.5"
                        placeholder="https://..."
                      />
                      {renderError(row.errors.reportUrl)}
                    </td>

                    <td className="px-2 py-3">
                      <input
                        value={row.listPageName}
                        onChange={(e) => updateRow(row.id, 'listPageName', e.target.value)}
                        className="w-full border border-gray-200 rounded-2xl px-3 py-2.5"
                        placeholder="页面名称"
                      />
                      {renderError(row.errors.listPageName)}
                    </td>

                    <td className="px-2 py-3">
                      <DateInput
                        label=""
                        name={`startDate_${row.id}`}
                        placeholder="YYYY-MM-DD"
                        value={row.startDate}
                        onChange={(e) => updateRow(row.id, 'startDate', e.target.value)}
                        className={`h-[42px] ${row.errors.startDate ? 'border-red-300' : ''}`}
                      />
                      {renderError(row.errors.startDate)}
                    </td>

                    <td className="px-2 py-3">
                      <DateInput
                        label=""
                        name={`endDate_${row.id}`}
                        placeholder="YYYY-MM-DD"
                        value={row.endDate}
                        onChange={(e) => updateRow(row.id, 'endDate', e.target.value)}
                        className={`h-[42px] ${row.errors.endDate ? 'border-red-300' : ''}`}
                      />
                      {renderError(row.errors.endDate)}
                    </td>

                    <td className="px-2 py-3">
                      <div className="w-full">
                        <button
                          type="button"
                          onClick={() => {
                            document.getElementById(`batch-file-${row.id}`)?.click();
                          }}
                          className={`
                            w-full h-[42px] inline-flex items-center justify-center gap-2 rounded-2xl border text-sm transition-colors
                            ${row.errors.attachments
                              ? 'border-red-300 text-red-500 bg-red-50'
                              : 'border-gray-200 text-gray-600 hover:border-emerald-200 hover:bg-emerald-50'}
                          `}
                        >
                          <ImageIcon size={16} />
                          {row.attachments.length > 0 ? `${row.attachments.length} 张` : '上传'}
                        </button>
                        <input
                          id={`batch-file-${row.id}`}
                          type="file"
                          className="hidden"
                          accept="image/*"
                          multiple
                          onChange={async (e) => {
                            const files = Array.from(e.target.files || []) as File[];
                            if (!files.length) return;
                            await addAttachments(row.id, files);
                            e.target.value = '';
                          }}
                        />
                        {renderError(row.errors.attachments)}
                        {!!row.attachments.length && (
                          <div className="mt-1 flex flex-wrap gap-1">
                            {row.attachments.map((att, idx) => (
                              <span
                                key={`${att.file.name}_${idx}`}
                                className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 bg-emerald-50 text-emerald-700 rounded border border-emerald-100"
                              >
                                <Paperclip size={10} />
                                <span className="max-w-[90px] truncate">{att.file.name}</span>
                                <button
                                  type="button"
                                  onClick={() => removeAttachment(row.id, idx)}
                                  className="text-red-500"
                                >
                                  <X size={10} />
                                </button>
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    </td>

                    <td className="px-2 py-3">
                      <input
                        value={row.outputScriptName}
                        onChange={(e) => updateRow(row.id, 'outputScriptName', e.target.value)}
                        className="w-full border border-gray-200 rounded-2xl px-3 py-2.5"
                        placeholder="spider.py"
                      />
                      {renderError(row.errors.outputScriptName)}
                    </td>

                    <td className="px-2 py-3">
                      <select
                        value={row.runMode}
                        onChange={(e) => updateRow(row.id, 'runMode', e.target.value)}
                        className="w-full border border-gray-200 rounded-2xl px-3 py-2.5"
                      >
                        <option value="">请选择</option>
                        {RUN_MODE_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                      {renderError(row.errors.runMode)}
                    </td>

                    <td className="px-2 py-3">
                      <select
                        value={row.crawlMode}
                        onChange={(e) => updateRow(row.id, 'crawlMode', e.target.value)}
                        className="w-full border border-gray-200 rounded-2xl px-3 py-2.5"
                      >
                        <option value="">请选择</option>
                        {CRAWL_MODE_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                      {renderError(row.errors.crawlMode)}
                    </td>

                    <td className="px-2 py-3">
                      <select
                        value={row.downloadReport}
                        onChange={(e) => updateRow(row.id, 'downloadReport', e.target.value)}
                        className="w-full border border-gray-200 rounded-2xl px-3 py-2.5"
                      >
                        <option value="">请选择</option>
                        {DOWNLOAD_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                      {renderError(row.errors.downloadReport)}
                    </td>

                    <td className="px-2 py-3 text-center">
                      <button
                        type="button"
                        onClick={() => removeRow(row.id)}
                        className="p-2 text-gray-500 hover:text-red-500 hover:bg-red-50 rounded-xl"
                        title="删除该行"
                      >
                        <Trash2 size={14} />
                      </button>
                    </td>
                  </tr>
                ))}

                <tr>
                  <td colSpan={11} className="px-2 py-3 border-t border-dashed border-gray-200">
                    <button
                      type="button"
                      onClick={addRow}
                      className="w-full py-2 text-sm text-gray-400 hover:text-indigo-600"
                    >
                      + 添加新任务
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="p-4 border-t border-gray-100 bg-white sticky bottom-0">
        {globalError ? <p className="text-sm text-red-600 mb-2">{globalError}</p> : null}
        <button
          onClick={handleSubmit}
          disabled={isSubmitting || !rows.length || hasErrors}
          className="w-full py-3 rounded-xl bg-indigo-600 text-white font-medium disabled:opacity-50 disabled:cursor-not-allowed hover:bg-indigo-700"
        >
          {isSubmitting ? '提交中...' : '提交批量配置'}
        </button>
      </div>
    </div>
  );
};

export default BatchConfigView;
