export interface ErrorExplainResult {
  reason: string;
  explanation: string;
}

const containsAny = (text: string, patterns: string[]): boolean =>
  patterns.some((p) => text.includes(p));

const extractStatusCode = (message: string): number | null => {
  const patterns = [
    /\bHTTP\s*(\d{3})\b/i,
    /\bapi\s*error\s*[:\s]\s*(\d{3})\b/i,
    /\bstatus(?:_?code)?\s*[:=]\s*(\d{3})\b/i
  ];

  for (const pattern of patterns) {
    const match = message.match(pattern);
    if (!match) continue;
    const code = Number(match[1]);
    if (Number.isInteger(code)) return code;
  }
  return null;
};

export const explainError = (errorMsg: string): ErrorExplainResult => {
  const reason = (errorMsg || '').trim();
  if (!reason) {
    return { reason: '', explanation: '' };
  }

  const normalized = reason.toLowerCase();
  const statusCode = extractStatusCode(reason);

  // Highest priority: hard-coded domain specific messages.
  if (normalized.includes('max iterations reached without generating code')) {
    return {
      reason,
      explanation:
        '爬取失败，agent未能在轮次内生成有效爬虫代码。请检查提交的配置信息是否正确。或者该网站是否有高级反扒机制或人机验证导致无法爬取。'
    };
  }

  if (normalized.includes('max iterations reached; generated code was rejected by critic')) {
    return {
      reason,
      explanation: '爬取失败，agent 已达到最大轮次且生成代码未通过质量校验（记录为空或质量检查未通过）。'
    };
  }

  if (
    containsAny(normalized, [
      '任务被用户停止',
      '任务被用户强制停止',
      '任务已被用户取消',
      '任务在排队中被取消',
      'user stopped',
      'stopped by user',
      'cancelled',
      'canceled'
    ])
  ) {
    return {
      reason,
      explanation: '用户手动停止了该任务'
    };
  }

  // Network priority 1: proxy / tunnel issues.
  if (
    containsAny(normalized, [
      'proxy error',
      'proxy',
      'tunnel',
      'econnreset',
      'econnrefused',
      'socket hang up'
    ])
  ) {
    return {
      reason,
      explanation: '请检查VPN或外网链接问题'
    };
  }

  // Network priority 2: DNS resolution failures.
  if (
    containsAny(normalized, [
      'nameresolutionerror',
      'temporary failure in name resolution',
      'getaddrinfo failed',
      'nodename nor servname',
      'failed to resolve',
      'dns'
    ])
  ) {
    return {
      reason,
      explanation: '域名解析失败，请检查本机 DNS、网络代理设置或公司网络策略。'
    };
  }

  // Network priority 3: HTTPS/TLS connectivity failures.
  if (
    (normalized.includes('httpsconnectionpool') && normalized.includes('port=443')) ||
    normalized.includes('connecttimeouterror') ||
    normalized.includes('max retries exceeded') ||
    normalized.includes('certificate verify failed') ||
    normalized.includes('ssl') ||
    normalized.includes('tls')
  ) {
    return {
      reason,
      explanation:
        'HTTPS(443) 连接失败。可能原因：代理/VPN/公司网关拦截或劫持 HTTPS；目标站证书异常（过期、域名不匹配、证书链不完整）；本机时间不准导致证书校验失败；TLS 协议或加密套件不兼容；防火墙或网络策略阻断 443 出站连接。'
    };
  }

  // Browser / CDP runtime errors.
  if (
    containsAny(normalized, [
      '无法连接到 chrome 浏览器',
      'cdp not ready before timeout',
      'browser not started or actual port unknown',
      'failed to start browser',
      'is already in use',
      '未连接到浏览器'
    ])
  ) {
    return {
      reason,
      explanation: '浏览器环境异常（Chrome/CDP 未就绪或端口冲突）。请检查本机 Chrome 进程与调试端口占用后重试。'
    };
  }

  if (
    containsAny(normalized, [
      '无法打开目标页面',
      '打开页面失败',
      'err_aborted',
      'interrupted by another navigation',
      'target page, context or browser has been closed',
      'has been closed'
    ])
  ) {
    return {
      reason,
      explanation: '目标页面访问失败。请检查 URL 可访问性、页面跳转稳定性或是否存在反爬/人机验证。'
    };
  }

  if (
    containsAny(normalized, [
      '脚本生成失败',
      'script_code 为空',
      '生成的脚本未通过语法/规则验证'
    ])
  ) {
    return {
      reason,
      explanation: '生成代码不可用。建议调整任务目标与配置后重试。'
    };
  }

  if (containsAny(normalized, ['爬虫脚本运行失败', 'crawl failed'])) {
    return {
      reason,
      explanation: '生成的脚本在执行阶段报错，请查看日志定位具体代码或环境问题。'
    };
  }

  if (
    containsAny(normalized, [
      '响应流解析失败',
      'response format error',
      'your response was not valid json',
      'invalid json from model',
      'gemini 响应为空'
    ])
  ) {
    return {
      reason,
      explanation: '模型返回内容格式异常或为空，导致无法继续解析。请稍后重试。'
    };
  }

  if (containsAny(normalized, ['connection error', '连接错误', 'connection aborted'])) {
    return {
      reason,
      explanation: '网络连接异常，请检查当前网络连通性、代理设置与目标服务可达性。'
    };
  }

  if (statusCode === 413 || normalized.includes('payload too large')) {
    return {
      reason: '请求体过大',
      explanation: '配置信息中上传内容/参数太大超出服务端限制。'
    };
  }

  if (
    containsAny(normalized, [
      'resource_exhausted',
      'rate limit',
      'too many requests',
      'insufficient_quota',
      'exceed quota',
      'quota exceeded'
    ]) ||
    statusCode === 429
  ) {
    return {
      reason,
      explanation: '大模型api超出速率或配额限制，请稍后重试，麻烦请检查大模型api key计费/配额设置。'
    };
  }

  if (
    containsAny(normalized, [
      'unauthenticated',
      'unauthorized',
      'invalid api key',
      'api key not valid'
    ]) ||
    statusCode === 401
  ) {
    return {
      reason,
      explanation: '认证失败，请检查 API Key 是否正确、是否过期或是否配置到当前环境。'
    };
  }

  if (normalized.includes('permission_denied') || statusCode === 403) {
    return {
      reason,
      explanation: 'API Key 权限不足，请检查密钥权限、项目绑定和相关服务启用状态。'
    };
  }

  if (
    containsAny(normalized, ['invalid_argument', 'validation error']) ||
    statusCode === 400 ||
    statusCode === 422
  ) {
    return {
      reason,
      explanation: '请求参数不合法，请检查 URL、时间范围、任务目标与附加参数格式。'
    };
  }

  if (
    containsAny(normalized, ['failed_precondition', 'billing', 'location is not supported'])
  ) {
    return {
      reason,
      explanation: '前置条件不满足，可能与计费状态、地区可用性或项目配置有关。'
    };
  }

  if (containsAny(normalized, ['url 不能为空', 'url不能为空'])) {
    return {
      reason,
      explanation: '任务缺少目标 URL，请填写后再启动。'
    };
  }

  if (containsAny(normalized, ['task id missing', '任务id缺失'])) {
    return {
      reason,
      explanation: '任务标识缺失，无法继续查询执行状态。请重新发起任务。'
    };
  }

  if (containsAny(normalized, ['读取文件失败', '文件不存在', '选中的文件均不存在', 'pdf 文件不存在', 'file not found'])) {
    return {
      reason,
      explanation: '结果文件不可读或不存在，可能是任务未产出文件或文件已被清理。'
    };
  }

  if (containsAny(normalized, ['任务不存在', 'task not found', '原任务不存在或已失效'])) {
    return {
      reason,
      explanation: '任务不存在或已失效，可能已被清理或任务 ID 不正确。'
    };
  }

  if (containsAny(normalized, ['sse 未启用', 'sse not enabled'])) {
    return {
      reason,
      explanation: '后端未启用实时事件推送能力（SSE），请检查服务端配置。'
    };
  }

  if (
    containsAny(normalized, ['model not found', 'models/', 'not found']) ||
    statusCode === 404
  ) {
    return {
      reason,
      explanation: '请求资源不存在（可能是模型名、接口路径或任务 ID 不正确）。'
    };
  }

  if (normalized.includes('internal') || statusCode === 500) {
    return {
      reason,
      explanation: '服务内部异常，请稍后重试。'
    };
  }

  if (containsAny(normalized, ['bad gateway']) || statusCode === 502) {
    return {
      reason,
      explanation: '上游网关异常，通常是服务临时故障或网络链路不稳定。'
    };
  }

  if (normalized.includes('unavailable') || statusCode === 503) {
    return {
      reason,
      explanation: '服务暂时过载或关闭，请稍后重试。'
    };
  }

  if (
    containsAny(normalized, ['deadline_exceeded', 'timed out', 'timeout']) ||
    statusCode === 408 ||
    statusCode === 504
  ) {
    return {
      reason,
      explanation: '服务处理超时，请稍后重试；若频繁出现请降低请求复杂度。'
    };
  }

  if (containsAny(normalized, ['context length', 'token limit', 'maximum context length'])) {
    return {
      reason,
      explanation: '输入内容过长触发模型上下文限制，请缩短任务描述或减少附件内容。'
    };
  }

  if (containsAny(normalized, ['local_subprocess_unavailable', 'probe_subprocess_unavailable'])) {
    return {
      reason,
      explanation: '本地子进程不可用，执行环境可能缺少依赖或权限不足。'
    };
  }

  return {
    reason,
    explanation: '未知错误，请查看日志中的报错并稍后重试'
  };
};
