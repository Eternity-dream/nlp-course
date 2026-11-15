import { describe, it, expect } from 'vitest';
import { formatCurrency, formatCurrencyWhole, parseCurrency, formatMinute, parseMinute } from '../utils';
import { formatApiError } from '../utils/errors';

describe('formatCurrency', () => {
  it('应该正确格式化分到元', () => {
    expect(formatCurrency(10000)).toBe('¥100.00');
    expect(formatCurrency(10050)).toBe('¥100.50');
    expect(formatCurrency(0)).toBe('¥0.00');
  });
});

describe('formatCurrencyWhole', () => {
  it('应该去除末尾零', () => {
    expect(formatCurrencyWhole(10000)).toBe('¥100');
    expect(formatCurrencyWhole(10050)).toBe('¥100.50');
    expect(formatCurrencyWhole(10055)).toBe('¥100.55');
  });

  it('应该正确显示零值', () => {
    expect(formatCurrencyWhole(0)).toBe('¥0');
  });

  it('应该保留小数精度', () => {
    expect(formatCurrencyWhole(300055)).toBe('¥3000.55');
    expect(formatCurrencyWhole(100)).toBe('¥1');
  });
});

describe('parseCurrency', () => {
  it('应该正确解析整数', () => {
    expect(parseCurrency('100')).toBe(10000);
    expect(parseCurrency('¥100')).toBe(10000);
    expect(parseCurrency('0')).toBe(0);
  });

  it('应该正确解析小数', () => {
    expect(parseCurrency('100.5')).toBe(10050);
    expect(parseCurrency('100.55')).toBe(10055);
    expect(parseCurrency('3000.55')).toBe(300055);
    expect(parseCurrency('1.2')).toBe(120);
    expect(parseCurrency('1.23')).toBe(123);
  });

  it('应该拒绝负数', () => {
    expect(parseCurrency('-100')).toBe(null);
    expect(parseCurrency('-100.5')).toBe(null);
  });

  it('应该拒绝科学计数法', () => {
    expect(parseCurrency('1e3')).toBe(null);
    expect(parseCurrency('1E3')).toBe(null);
    expect(parseCurrency('1.5e2')).toBe(null);
  });

  it('应该拒绝多个小数点', () => {
    expect(parseCurrency('1.2.3')).toBe(null);
    expect(parseCurrency('100..5')).toBe(null);
  });

  it('应该拒绝字母', () => {
    expect(parseCurrency('1abc')).toBe(null);
    expect(parseCurrency('abc')).toBe(null);
    expect(parseCurrency('12a34')).toBe(null);
  });

  it('应该拒绝超过2位小数', () => {
    expect(parseCurrency('1.234')).toBe(null);
    expect(parseCurrency('100.123')).toBe(null);
  });

  it('应该处理空字符串', () => {
    expect(parseCurrency('')).toBe(null);
    expect(parseCurrency('   ')).toBe(null);
  });

  it('应该处理零值', () => {
    expect(parseCurrency('0')).toBe(0);
    expect(parseCurrency('0.00')).toBe(0);
    expect(parseCurrency('¥0')).toBe(0);
  });

  it('应该拒绝溢出值', () => {
    const maxSafe = Math.floor(Number.MAX_SAFE_INTEGER / 100);
    expect(parseCurrency(maxSafe.toString())).not.toBe(null);
    expect(parseCurrency((maxSafe + 1).toString())).toBe(null);
  });
});

describe('formatMinute', () => {
  it('应该正确格式化当天时间', () => {
    expect(formatMinute(540)).toBe('09:00');
    expect(formatMinute(0)).toBe('00:00');
    expect(formatMinute(1439)).toBe('23:59');
  });

  it('应该正确处理跨午夜时间', () => {
    expect(formatMinute(1440)).toBe('00:00');
    expect(formatMinute(1440, true)).toBe('次日 00:00');
    expect(formatMinute(1500, true)).toBe('次日 01:00');
  });
});

describe('parseMinute', () => {
  it('应该正确解析时间', () => {
    expect(parseMinute('09:00')).toBe(540);
    expect(parseMinute('00:00')).toBe(0);
    expect(parseMinute('23:59')).toBe(1439);
  });

  it('应该拒绝无效格式', () => {
    expect(parseMinute('9:00')).toBe(null);
    expect(parseMinute('25:00')).toBe(null);
    expect(parseMinute('09:60')).toBe(null);
    expect(parseMinute('abc')).toBe(null);
    expect(parseMinute('9:0')).toBe(null);
  });
});

describe('formatApiError', () => {
  it('应该保留中文错误消息', () => {
    expect(formatApiError({ message: '数据已更新' })).toBe('数据已更新');
    expect(formatApiError({ message: '配置错误：缺少必填项' })).toBe('配置错误：缺少必填项');
  });

  it('应该转换常见英文错误', () => {
    expect(formatApiError({ message: 'Failed to fetch' })).toBe('网络连接失败');
    expect(formatApiError({ message: 'Network error' })).toBe('网络连接失败');
    expect(formatApiError({ message: 'timeout' })).toBe('请求超时');
  });

  it('应该转换包含关键词的错误', () => {
    expect(formatApiError({ message: 'json error' })).toBe('响应格式错误');
    expect(formatApiError({ message: 'parse failed' })).toBe('响应格式错误');
    expect(formatApiError({ message: 'syntax error in response' })).toBe('响应格式错误');
  });

  it('应该根据状态码返回消息', () => {
    expect(formatApiError({ status: 404 })).toBe('未找到');
    expect(formatApiError({ status: 409 })).toBe('数据已更新，请重新加载');
    expect(formatApiError({ status: 503 })).toBe('服务暂时不可用');
    expect(formatApiError({ status: 500 })).toBe('服务器错误');
  });

  it('应该处理空错误', () => {
    expect(formatApiError({})).toBe('请求失败');
  });
});
