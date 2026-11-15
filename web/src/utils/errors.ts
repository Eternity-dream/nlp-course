// 网络与接口错误转换为可读中文；后端返回的中文业务原因优先保留。
export function formatApiError(error: any): string {
  const message = typeof error?.message === 'string' ? error.message.trim() : '';
  if (message && /[\u4e00-\u9fa5]/.test(message)) return message;

  const status = typeof error?.status === 'number' ? error.status : 0;
  if (status === 404) return '未找到';
  if (status === 409) return '数据已更新，请重新加载';
  if (status === 503) return '服务暂时不可用';
  if (status >= 500) return '服务器错误';

  const lower = message.toLowerCase();
  if (lower.includes('failed to fetch') || lower.includes('network')) return '网络连接失败';
  if (lower.includes('timeout')) return '请求超时';
  if (lower.includes('json') || lower.includes('parse') || lower.includes('syntax')) {
    return '响应格式错误';
  }
  return '请求失败';
}
