// 只把 http/https 地址渲染为可点击链接，其他协议或空值按纯文本展示。
export function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
    return url.href;
  } catch {
    return null;
  }
}

export function urlHost(value: string | null | undefined): string | null {
  const safe = safeExternalUrl(value);
  if (!safe) return null;
  return new URL(safe).hostname;
}
