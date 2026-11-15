// 在 serve_web_acceptance.py 启动的独立实例上执行浏览器验收。
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { parseArgs } from 'node:util';
import { checkKnowledgeDetails, checkTimeConstraints } from './frontend_details.mjs';

const require = createRequire(new URL('../web/package.json', import.meta.url));
const { chromium } = require('playwright');
const { values } = parseArgs({ options: {
  'base-url': { type: 'string' },
  'missing-config-url': { type: 'string' },
  'output-dir': { type: 'string' },
} });
assert(values['base-url'] && values['output-dir'],
  '需要 --base-url 独立验收实例地址和 --output-dir 新的输出目录');
const base = values['base-url'].replace(/\/$/, '');
const output = path.resolve(values['output-dir']);
await fs.mkdir(output, { recursive: false });
const report = { checks: [], pageErrors: [], accessibility: [], layoutIssues: [], started_at: new Date().toISOString() };
const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai', reducedMotion: 'reduce',
});
const page = await context.newPage();
page.setDefaultTimeout(15000);
page.on('pageerror', error => report.pageErrors.push(error.message));
page.on('dialog', dialog => dialog.accept());
const checked = text => { report.checks.push(text); console.log(`通过：${text}`); };
const api = async (route, options = {}) => {
  const response = await fetch(base + route, {
    ...options, headers: { 'Content-Type': 'application/json', ...options.headers },
  });
  assert(response.ok, `${route}: ${response.status} ${response.ok ? '' : await response.text()}`);
  return response.status === 204 ? null : response.json();
};
const poll = async (read, predicate) => {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const data = await read();
    if (predicate(data)) return data;
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  throw new Error('等待后台结果超时');
};
async function capture(name, accessibility = false) {
  await page.mouse.move(0, 0);
  await page.waitForTimeout(200);
  await page.screenshot({ path: path.join(output, name + '.png'), fullPage: true });
  await fs.writeFile(path.join(output, name + '.txt'), await page.locator('body').innerText());
  const layout = await page.evaluate(() => ({
    viewport: innerWidth, body: document.body.scrollWidth, root: document.documentElement.scrollWidth,
  }));
  if (layout.body > layout.viewport || layout.root > layout.viewport) {
    report.layoutIssues.push({ page: name, ...layout });
  }
  if (accessibility) {
    await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
    const result = await page.evaluate(async () => window.axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] },
    }));
    report.accessibility.push({ page: name, violations: result.violations });
  }
}

try {
  await page.goto(base);
  await page.getByRole('heading', { name: '我的行程' }).waitFor();
  await capture('desktop-home', true);
  await page.getByRole('link', { name: '偏好', exact: true }).click();
  await page.getByRole('heading', { name: '偏好', exact: true }).waitFor();
  const clear = page.getByRole('button', { name: '清空', exact: true });
  if (await clear.isEnabled()) {
    await clear.click();
    await poll(() => api('/api/preferences'), data => Object.keys(data.preferences).length === 0);
  }
  await page.getByRole('button', { name: '添加偏好' }).click();
  await page.getByPlaceholder('节奏', { exact: true }).fill('节奏');
  await page.getByPlaceholder('慢节奏', { exact: true }).fill('慢节奏');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await poll(() => api('/api/preferences'), data => data.preferences['节奏'] === '慢节奏');
  await capture('desktop-preferences', true);
  await clear.click();
  await poll(() => api('/api/preferences'), data => Object.keys(data.preferences).length === 0);
  checked('偏好保存与清空');

  await page.getByRole('link', { name: '资料', exact: true }).click();
  await page.getByRole('button', { name: '添加资料', exact: true }).first().click();
  const title = `浏览器西安笔记 ${Date.now()}`;
  await page.getByLabel('标题', { exact: true }).fill(title);
  await page.getByLabel('正文', { exact: true }).fill(`西安碑林博物馆适合参观。${title}。`);
  await page.getByRole('button', { name: '添加', exact: true }).click();
  const sources = await poll(() => api('/api/knowledge'), data => data.some(s => s.title === title && s.status === 'ready'));
  report.source_id = sources.find(s => s.title === title).id;
  await page.getByText(title, { exact: true }).waitFor();
  await capture('desktop-knowledge', true);
  const original = await fetch(base + `/api/knowledge/${report.source_id}/original`);
  assert((await original.text()).includes('西安碑林博物馆'));
  checked('文本导入、后台解析和原文下载');
  await checkKnowledgeDetails({ page, base, api, poll, capture, checked, title, sourceId: report.source_id });

  await page.getByRole('link', { name: '行程', exact: true }).click();
  await page.getByRole('button', { name: '新建行程', exact: true }).first().click();
  await page.getByLabel('目的地', { exact: true }).fill('西安');
  await page.getByLabel('开始日期', { exact: true }).fill('2026-12-01');
  await page.getByLabel('结束日期', { exact: true }).fill('2026-12-03');
  await page.getByLabel('同行人数', { exact: true }).fill('2');
  await page.getByLabel(/预算/).fill('3000.55');
  await capture('desktop-trip-form', true);
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await page.waitForURL(/\/trips\//);
  const tripId = new URL(page.url()).pathname.split('/')[2];
  report.trip_id = tripId;
  assert.equal((await api(`/api/trips/${tripId}`)).budget_cents, 300055);
  await page.getByText('¥3000.55', { exact: true }).waitFor();
  await page.getByRole('button', { name: '生成行程', exact: true }).click();
  await poll(() => api(`/api/trips/${tripId}/jobs`), data => data.some(job => job.status === 'succeeded'));
  await page.getByRole('heading', { name: '行程草案', exact: true }).waitFor();
  const generated = await api(`/api/trips/${tripId}/itinerary`);
  const firstActivity = generated.days[0].activities[0];
  const firstFacts = generated.evidence.filter(fact =>
    fact.place_id === firstActivity.place_id && fact.on_date === generated.days[0].on_date);
  assert(firstFacts.length > 0, '验收样例缺少事实依据');
  const factsPanel = page.locator('.activity-facts').first();
  await factsPanel.getByRole('button').press('Enter');
  assert.equal(await factsPanel.locator('.fact-item').count(), firstFacts.length);
  for (const fact of firstFacts) assert((await factsPanel.innerText()).includes(fact.quote));
  for (const href of await factsPanel.locator('a').evaluateAll(links => links.map(link => link.href))) {
    assert(/^https?:\/\//.test(href), '事实来源网址无效');
  }
  checked('活动与日期对应的事实、引用、来源和键盘展开');
  await capture('desktop-itinerary', true);
  await page.reload();
  await page.getByRole('heading', { name: '行程草案', exact: true }).waitFor();
  checked('新建、金额精度、生成任务、轮询和刷新恢复');

  const before = await api(`/api/trips/${tripId}/itinerary`);
  await page.getByRole('button', { name: '删除活动', exact: true }).first().click();
  const after = await poll(() => api(`/api/trips/${tripId}/itinerary`), data => data.revision > before.revision);
  assert.equal(after.days[0].activities.length, before.days[0].activities.length - 1);
  assert.deepEqual(after.days.slice(1), before.days.slice(1));
  checked('删除活动、局部重排和新版本保存');

  const refreshItinerary = async () => {
    await page.goto(base + `/trips/${tripId}`);
    await page.getByRole('heading', { name: '行程草案', exact: true }).waitFor();
  };
  const readItinerary = () => api(`/api/trips/${tripId}/itinerary`);
  await refreshItinerary();
  await page.getByRole('button', { name: '添加活动', exact: true }).first().click();
  await page.getByRole('textbox', { name: '地点搜索', exact: true }).fill('公园');
  await page.locator('.activity-editor').getByRole('button', { name: '搜索', exact: true }).click();
  await page.getByRole('option', { name: /公园/ }).first().press('Enter');
  await page.getByLabel('开始时间', { exact: true }).fill('21:00');
  await page.getByLabel('结束时间', { exact: true }).fill('22:00');
  await page.getByLabel('门票（元）', { exact: true }).fill('0');
  await capture('desktop-add-activity', true);
  await page.locator('.activity-editor').getByRole('button', { name: '添加', exact: true }).click();
  let current = await poll(readItinerary, data => data.revision > after.revision);
  assert.equal(current.days[0].activities.length, after.days[0].activities.length + 1);
  assert.deepEqual(current.days.slice(1), after.days.slice(1));
  checked('地点查询、键盘选择、添加活动和零费用');

  await refreshItinerary();
  const beforeTimeEdit = current;
  const originalActivity = current.days[0].activities[0];
  await page.getByRole('button', { name: '编辑活动', exact: true }).first().click();
  await page.getByLabel('开始时间', { exact: true }).fill('12:10');
  await page.getByLabel('结束时间', { exact: true }).fill('13:10');
  const timeRequest = page.waitForRequest(request =>
    request.method() === 'PATCH' && new URL(request.url()).pathname === `/api/trips/${tripId}/itinerary`);
  await page.locator('.activity-editor').getByRole('button', { name: '保存', exact: true }).click();
  assert.deepEqual((await timeRequest).postDataJSON().operations[0].activity.costs, originalActivity.costs,
    '仅修改时间时费用依据变化');
  current = await poll(readItinerary, data => data.revision > beforeTimeEdit.revision);
  const timeJob = (await api(`/api/trips/${tripId}/jobs`))[0];
  assert.equal(timeJob.kind, 'edit');
  assert.equal(current.days[0].activities[0].id, originalActivity.id);
  assert.deepEqual(current.days.slice(1), beforeTimeEdit.days.slice(1));
  checked('活动时间修改、费用依据保留、稳定标识与局部保存');

  await refreshItinerary();
  const beforeReplace = current;
  await page.getByRole('button', { name: '编辑活动', exact: true }).first().click();
  await page.getByRole('textbox', { name: '地点搜索', exact: true }).fill('博物馆');
  await page.locator('.activity-editor').getByRole('button', { name: '搜索', exact: true }).click();
  await page.getByRole('option', { name: /博物馆/ }).first().press('Enter');
  await page.getByLabel('门票（元）', { exact: true }).fill('100');
  await page.locator('.activity-editor').getByRole('button', { name: '保存', exact: true }).click();
  current = await poll(readItinerary, data => data.revision > beforeReplace.revision);
  assert.equal(current.days[0].activities[0].id, originalActivity.id);
  assert.notEqual(current.days[0].activities[0].place_id, originalActivity.place_id);
  assert.deepEqual(current.days.slice(1), beforeReplace.days.slice(1));
  checked('地点替换与费用更新');

  await refreshItinerary();
  const beforeMove = current;
  await page.getByRole('button', { name: '下移活动', exact: true }).first().click();
  current = await poll(readItinerary, data => data.revision > beforeMove.revision);
  assert.equal(current.days[0].activities[1].id, beforeMove.days[0].activities[0].id);
  assert.deepEqual(current.days.slice(1), beforeMove.days.slice(1));
  await refreshItinerary();
  const beforeCrossDay = current;
  const movedId = current.days[0].activities[0].id;
  await page.getByRole('combobox', { name: '移至其他日期', exact: true }).first().selectOption('2026-12-02');
  current = await poll(readItinerary, data => data.revision > beforeCrossDay.revision);
  assert(!current.days[0].activities.some(item => item.id === movedId));
  assert(current.days[1].activities.some(item => item.id === movedId));
  assert.deepEqual(current.days[2], beforeCrossDay.days[2]);
  checked('同日排序、指定跨日移动和第三天保持');

  await refreshItinerary();
  const versions = page.getByRole('combobox', { name: '行程版本', exact: true });
  await versions.selectOption('1');
  await poll(() => versions.isEnabled(), enabled => enabled);
  await poll(() => page.locator('.activity-card').count(), count =>
    count === before.days.reduce((sum, day) => sum + day.activities.length, 0));
  assert.equal(await page.getByRole('button', { name: '编辑活动', exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: '添加活动', exact: true }).count(), 0);
  assert.equal(await page.locator('.activity-card').count(), before.days.reduce((sum, day) => sum + day.activities.length, 0));
  await capture('desktop-history', true);
  await versions.selectOption(String(current.revision));
  await page.getByRole('button', { name: '编辑活动', exact: true }).first().waitFor();
  checked('历史草案查询、只读和返回最新版');

  const incomplete = await api('/api/trips', { method: 'POST', body: JSON.stringify({ destination: '西安' }) });
  const job = await api(`/api/trips/${incomplete.id}/jobs`, {
    method: 'POST', body: JSON.stringify({ expected_revision: 1, idempotency_key: `browser-${Date.now()}` }),
  });
  await poll(() => api(`/api/jobs/${job.id}`), data => data.status === 'awaiting_input');
  await page.goto(base + `/trips/${incomplete.id}`);
  await page.getByRole('button', { name: '补充要求', exact: true }).click();
  await page.getByLabel('开始日期', { exact: true }).fill('2026-12-01');
  await page.getByLabel('结束日期', { exact: true }).fill('2026-12-03');
  await page.getByLabel('同行人数', { exact: true }).fill('2');
  // 跨越一次后台轮询，验证输入保持且继续任务使用新的条件版本。
  await page.waitForTimeout(2200);
  assert.equal(await page.getByLabel('同行人数', { exact: true }).inputValue(), '2');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  const resumed = await poll(() => api(`/api/jobs/${job.id}`), data => data.status === 'succeeded');
  assert.equal(resumed.request_revision, 2);
  await page.getByRole('heading', { name: '行程草案', exact: true }).waitFor();
  checked('补充条件保留输入、原任务继续');

  await page.goto(base + '/status');
  await page.getByRole('heading', { name: '服务状态', exact: true }).waitFor();
  const checksBefore = (await api('/api/config/checks')).length;
  await page.getByRole('button', { name: '检查', exact: true }).last().click();
  await poll(() => api('/api/config/checks'), data => data.length > checksBefore);
  await page.getByRole('heading', { name: '服务状态', exact: true }).waitFor();
  await capture('desktop-services', true);
  checked('显式服务检查');

  await checkTimeConstraints({ page, base, api, capture, checked });

  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    for (const [route, name] of [['/', 'home'], ['/knowledge', 'knowledge'], ['/preferences', 'preferences'], ['/status', 'services'], [`/trips/${tripId}`, 'itinerary']]) {
      await page.goto(base + route);
      await page.locator('h1').waitFor();
      if (name === 'itinerary') await page.getByRole('heading', { name: '行程草案', exact: true }).waitFor();
      await capture(`mobile-${width}-${name}`);
    }
  }
  if (report.layoutIssues.length === 0) checked('390px、320px页面布局和路由直达');

  if (values['missing-config-url']) {
    await page.goto(values['missing-config-url'].replace(/\/$/, '') + '/status');
    await page.getByRole('heading', { name: '服务状态', exact: true }).waitFor();
    assert(!/LLM_API_KEY|AMAP_API_KEY|auth_mode/.test(await page.locator('body').innerText()));
    await capture('missing-config', true);
    checked('缺配置状态与用户文案');
  }
  await page.route('**/api/config/status', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { message: '暂时无法连接服务' } }) }));
  await page.goto(base + '/status');
  await page.getByRole('button', { name: '重试', exact: true }).waitFor();
  await capture('service-error');
  checked('服务错误提示与重试入口');
  assert.deepEqual(report.pageErrors, []);
  assert.deepEqual(report.layoutIssues, [], '页面存在横向溢出');
  assert.equal(report.accessibility.reduce((sum, item) => sum + item.violations.length, 0), 0,
    '页面存在无障碍问题，见 accessibility 记录');
  report.status = 'passed';
} catch (error) {
  report.status = 'failed';
  report.error = error.stack;
  await capture('failure').catch(() => {});
} finally {
  report.finished_at = new Date().toISOString();
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ status: report.status, checks: report.checks, error: report.error }, null, 2));
process.exitCode = report.status === 'passed' ? 0 : 1;
