import React from 'react';

// 附件类型（支持 base64 编码用于传给 API）
export interface Attachment {
  file: File;
  base64?: string;  // 图片的 base64 编码
  mimeType?: string;
}

export interface CrawlerFormData {
  startDate: string;
  endDate: string;
  extraRequirements: string;
  siteName: string;
  listPageName: string;
  sourceCredibility: string; // 信息源可信度（T1/T2/T3）
  reportUrl: string;
  outputScriptName: string;
  runMode: string;
  crawlMode: string;
  downloadReport: string;
  attachments: Attachment[];
}

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label: string;
  icon?: React.ReactNode;
  fullWidth?: boolean;
}

export type StepStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface ProcessStep {
  id: number;
  label: string;
  status: StepStatus;
}

export interface TreeNode {
  id: string;
  name: string;
  path: string;
  children?: TreeNode[];
  isLeaf?: boolean;
}

// ============ 报告文件类型 ============
export interface ReportFile {
  id: string;
  name: string;
  date: string;
  downloadUrl: string;
  fileType: string;
  localPath?: string;  // 本地文件路径（如果已下载到本地）
  isLocal?: boolean;   // 是否是本地文件
  category?: string;   // 来源板块（多页爬取时标识）
}

// ============ 新闻文章类型（新闻舆情场景）============
export interface NewsArticle {
  id: string;
  title: string;
  author: string;
  date: string;
  source: string;
  sourceUrl: string;
  summary?: string;
  content?: string;
  category?: string;   // 来源板块（多页爬取时标识）
}

// ============ API Types ============

export interface MenuTreeRequest {
  url: string;
}

export interface MenuTreeResponse {
  url: string;
  root: TreeNode | null;
  leaf_paths: string[];
}

// 附件数据（用于 API 传输）
export interface AttachmentData {
  filename: string;
  base64: string;
  mimeType: string;
}

export interface GenerateRequest {
  url: string;
  startDate: string;
  endDate: string;
  outputScriptName: string;
  extraRequirements?: string;
  siteName?: string;
  listPageName?: string;
  sourceCredibility?: string; // 信息源可信度（T1/T2/T3）
  runMode: string;
  crawlMode: string;
  downloadReport?: string;
  selectedPaths?: string[];
  attachments?: AttachmentData[];  // 图片/文件附件（base64 编码）
}

export interface GenerateResponse {
  taskId: string;
  message: string;
}

export interface TaskStatusResponse {
  taskId: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  currentStep: number;
  totalSteps: number;
  stepLabel: string;
  logs: string[];
  resultFile?: string;
  error?: string;
  // 爬取结果（报告下载场景）
  reports?: ReportFile[];
  // 下载的文件数量（前5个）
  downloadedCount?: number;
  // 日期范围内文件是否不足5份
  filesNotEnough?: boolean;
  // 文件下载目录
  pdfOutputDir?: string;
  // 爬取结果（新闻舆情场景）
  newsArticles?: NewsArticle[];
  // Markdown 文件路径（新闻舆情场景）
  markdownFile?: string;
}

// ============ API Base URL ============
export const API_BASE_URL = 'http://localhost:8000';
