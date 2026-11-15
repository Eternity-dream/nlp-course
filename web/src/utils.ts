// 工具函数

export function formatDate(dateStr: string): string {
  const date = new Date(dateStr + 'T00:00:00+08:00');
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'short',
    timeZone: 'Asia/Shanghai',
  }).format(date);
}

export function formatDateTime(dateStr: string): string {
  const date = new Date(dateStr);
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Asia/Shanghai',
  }).format(date);
}

export function formatCurrency(cents: number): string {
  return `¥${(cents / 100).toFixed(2)}`;
}

export function formatCurrencyWhole(cents: number): string {
  const yuan = cents / 100;
  if (yuan === Math.floor(yuan)) {
    return `¥${Math.floor(yuan)}`;
  }
  return `¥${yuan.toFixed(2)}`;
}

export function parseCurrency(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;

  // 严格校验：可选¥ + 非负整数 + 最多2位小数
  const regex = /^¥?(\d+)(?:\.(\d{1,2}))?$/;
  const match = trimmed.match(regex);
  if (!match) return null;

  const yuan = parseInt(match[1], 10);
  const cents = match[2] ? parseInt(match[2].padEnd(2, '0'), 10) : 0;

  const total = yuan * 100 + cents;

  // 检查溢出
  if (total > Number.MAX_SAFE_INTEGER) return null;

  return total;
}

// 跨午夜分钟显示次日时间
export function formatMinute(minute: number, showNextDay = false): string {
  const actualMinute = minute % 1440;
  const h = Math.floor(actualMinute / 60);
  const m = actualMinute % 60;
  const timeStr = `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}`;
  if (showNextDay && minute >= 1440) {
    return `次日 ${timeStr}`;
  }
  return timeStr;
}

export function parseMinute(timeStr: string): number | null {
  const match = timeStr.match(/^(\d{2}):(\d{2})$/);
  if (!match) return null;
  const h = parseInt(match[1], 10);
  const m = parseInt(match[2], 10);
  if (h < 0 || h >= 24 || m < 0 || m >= 60) return null;
  return h * 60 + m;
}

export function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes}分钟`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m > 0 ? `${h}小时${m}分钟` : `${h}小时`;
}

export function formatDistance(meters: number): string {
  if (meters < 1000) return `${meters}米`;
  return `${(meters / 1000).toFixed(1)}公里`;
}

export const ACTIVITY_KIND_LABELS: Record<string, string> = {
  sightseeing: '游览',
  meal: '用餐',
  leisure: '休闲',
};

export const PERIOD_LABELS: Record<string, string> = {
  morning: '上午',
  noon: '中午',
  evening: '傍晚',
};

export const TRANSPORT_LABELS: Record<string, string> = {
  walking: '步行',
  transit: '公交',
  driving: '驾车',
};

export const COST_CATEGORY_LABELS: Record<string, string> = {
  ticket: '门票',
  meal: '餐饮',
};

export const COST_BASIS_LABELS: Record<string, string> = {
  per_person: '每人',
  per_group: '每组',
  fixed: '固定',
};

export const JOB_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '进行中',
  awaiting_input: '等待补充',
  succeeded: '已完成',
  failed: '失败',
  interrupted: '已中断',
};

export const VALIDATION_STATUS_LABELS: Record<string, string> = {
  passed: '已通过',
  needs_review: '待核对',
  blocked: '存在冲突',
};

export function generateIdempotencyKey(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export function classNames(...classes: (string | boolean | undefined | null)[]): string {
  return classes.filter(Boolean).join(' ');
}
