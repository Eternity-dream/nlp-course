// 在 serve_web_acceptance.py 启动的独立实例上执行浏览器验收。
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { parseArgs } from 'node:util';
import { checkDateBounds, checkFailedSource, checkInlineImports, checkKnowledgeDetails, checkTimeConstraints } from './frontend_details.mjs';

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
const report = { checks: [], pageErrors: [], consoleErrors: [], expectedHttpErrors: [], accessibility: [], layoutIssues: [], started_at: new Date().toISOString() };
const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai', reducedMotion: 'reduce',
});
const page = await context.newPage();
page.setDefaultTimeout(15000);
page.on('pageerror', error => report.pageErrors.push(error.message));
let dismissNext = false;
page.on('dialog', dialog => {
  const dismiss = dismissNext;
  dismissNext = false;
  return dismiss ? dialog.dismiss() : dialog.accept();
});
page.on('console', message => {
  if (message.type() !== 'error') return;
  const url = new URL(message.location().url || base);
  const record = { path: url.pathname, message: message.text() };
  const expected = {
    404: /^\/api\/trips\/[^/]+\/itinerary$/,
    409: /^\/api\/(preferences|trips\/[^/]+)$/,
    422: /^\/api\/trips$/,
    503: /^\/api\/(config\/status|jobs\/[^/]+\/resume|knowledge)$/,
  };
  const status = /Failed to load resource: the server responded with a status of (\d+)/.exec(record.message)?.[1];
  (expected[status]?.test(url.pathname) ? report.expectedHttpErrors : report.consoleErrors).push(record);
});
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
async function capture(name, accessibility = true) {
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
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22a', 'wcag22aa'] },
    }));
    report.accessibility.push({ page: name, violations: result.violations });
  }
}

async function checkProviders(modes) {
  const rows = page.locator('.status-provider');
  assert.deepEqual(await rows.locator('.status-provider-name').allTextContents(), ['Exa', 'DeepSeek', 'Tavily', 'AnySearch']);
  assert.deepEqual(await rows.locator('.badge').allTextContents(), modes);
  const body = await page.locator('body').innerText();
  const payload = await page.evaluate(async () => (await fetch('/api/config/status')).text());
  for (const secret of ['exa-secret', 'deepseek-acceptance-key', 'model-secret', 'map-secret']) {
    assert(!body.includes(secret) && !payload.includes(secret), '状态页暴露了验收密钥');
  }
}

try {
  await page.goto(base);
  await page.getByRole('heading', { name: '我的行程' }).waitFor();
  await capture('desktop-home', true);
  assert.deepEqual(await page.locator('header nav a').allTextContents(), ['行程', '设置']);
  await page.getByRole('link', { name: '设置', exact: true }).click();
  assert.deepEqual(await page.locator('.settings-links a').allTextContents(), ['旅行偏好', '参考资料', '服务状态']);
  await capture('desktop-settings');
  await page.getByRole('link', { name: '旅行偏好', exact: true }).click();
  await page.getByRole('heading', { name: '旅行偏好', exact: true }).waitFor();
  const clear = page.getByRole('button', { name: '清空', exact: true });
  await page.getByRole('textbox', { name: '旅行习惯' }).fill('喜欢历史建筑，慢节奏，不吃辣');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await poll(() => api('/api/preferences'), data => data.notes === '喜欢历史建筑，慢节奏，不吃辣');
  await capture('desktop-preferences', true);
  const stored = await api('/api/preferences');
  const concurrent = await api('/api/preferences', { method: 'PUT', body: JSON.stringify({
    expected_revision: stored.revision, notes: '想慢慢看展览，午后留时间休息。',
  }) });
  await page.getByRole('textbox', { name: '旅行习惯' }).fill('较早页面的草稿');
  const conflict = page.waitForResponse(r => r.url().endsWith('/api/preferences') && r.request().method() === 'PUT');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  assert.equal((await conflict).status(), 409);
  await page.getByRole('alert').filter({ hasText: '偏好已更新' }).waitFor();
  await page.getByRole('button', { name: '重新加载' }).click();
  await poll(() => page.getByRole('textbox', { name: '旅行习惯' }).inputValue(), text => text === concurrent.notes);
  checked('并发偏好旧版本返回409、显示冲突并重新载入新原文');
  dismissNext = true;
  const confirmation = page.waitForEvent('dialog');
  await clear.click();
  assert.equal((await confirmation).message(), '确定清空旅行偏好？');
  assert.deepEqual(await api('/api/preferences'), concurrent);
  await clear.click();
  await poll(() => api('/api/preferences'), data => data.notes === '');
  checked('偏好保存与清空');

  await page.getByRole('link', { name: '设置', exact: true }).click();
  await page.getByRole('link', { name: '参考资料', exact: true }).click();
  await page.getByRole('button', { name: '添加资料', exact: true }).first().click();
  const title = `浏览器西安笔记 ${Date.now()}`;
  await page.getByLabel('标题（选填）', { exact: true }).fill(title);
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
  await checkFailedSource({ page, base, api, poll, checked });
  await checkDateBounds({ page, base, api, checked });

  await page.getByRole('link', { name: '行程', exact: true }).click();
  await page.getByRole('button', { name: '新建行程', exact: true }).first().click();
  await page.getByLabel('目的地', { exact: true }).fill('西安');
  await page.getByLabel('开始日期', { exact: true }).fill('2026-12-01');
  await page.getByLabel('结束日期', { exact: true }).fill('2026-12-03');
  await page.getByLabel('同行人数', { exact: true }).fill('2');
  await page.getByLabel(/预算/).fill('3000.55');
  await checkInlineImports({ page, base, api, poll, checked });
  await page.getByRole('button', { name: '＋ 添加参考', exact: true }).click();
  const inlineTitle = `西安表单参考 ${Date.now()}`;
  await page.getByRole('dialog').getByLabel('正文', { exact: true }).fill(inlineTitle);
  await page.getByRole('dialog').getByRole('button', { name: '添加', exact: true }).click();
  await page.getByRole('link', { name: inlineTitle, exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, '/');
  assert.equal(await page.getByLabel('目的地', { exact: true }).inputValue(), '西安');
  await page.getByRole('button', { name: `移除${inlineTitle}`, exact: true }).click();
  await page.getByRole('button', { name: '＋ 添加参考', exact: true }).click();
  await page.getByRole('button', { name: '已有资料', exact: true }).click();
  const removed = page.getByRole('checkbox', { name: new RegExp(inlineTitle) });
  await removed.check();
  assert(await removed.isChecked());
  await page.getByRole('button', { name: '完成', exact: true }).click();
  await page.getByRole('button', { name: `移除${inlineTitle}`, exact: true }).click();
  await page.getByRole('button', { name: '＋ 添加参考', exact: true }).click();
  await page.getByRole('button', { name: '已有资料', exact: true }).click();
  await removed.waitFor();
  await capture('desktop-reference-dialog');
  checked('表单内导入参考、自动标题、保留输入与移除关联');
  await page.getByRole('checkbox', { name: new RegExp(title) }).check();
  await page.getByRole('button', { name: '完成', exact: true }).click();
  await capture('desktop-trip-form', true);
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await page.waitForURL(/\/trips\//);
  const tripId = new URL(page.url()).pathname.split('/')[2];
  report.trip_id = tripId;
  assert.deepEqual((await api(`/api/trips/${tripId}`)).reference_ids, [report.source_id]);
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
  const clarification = page.getByRole('dialog', { name: '补充说明', exact: true });
  await clarification.waitFor();
  await page.getByLabel('开始日期', { exact: true }).fill('2026-12-01');
  await page.getByLabel('结束日期', { exact: true }).fill('2026-12-03');
  await page.getByLabel('同行人数', { exact: true }).fill('2');
  await clarification.getByLabel('你的补充', { exact: true }).fill('住南稍门附近，喜欢历史文化');
  // 跨越一次后台轮询，验证输入保持且继续任务使用新的条件版本。
  await page.waitForTimeout(2200);
  assert.equal(await page.getByLabel('同行人数', { exact: true }).inputValue(), '2');
  await clarification.getByRole('button', { name: '稍后补充', exact: true }).click();
  await page.waitForTimeout(2200);
  assert.equal(await clarification.isVisible(), false);
  await page.getByRole('button', { name: '补充说明', exact: true }).click();
  assert.equal(await clarification.getByLabel('你的补充').inputValue(), '住南稍门附近，喜欢历史文化');
  await capture('desktop-clarification', true);
  await page.setViewportSize({ width: 320, height: 844 });
  await capture('mobile-320-clarification', true);
  await page.setViewportSize({ width: 1440, height: 1000 });
  let rejectPatch = true;
  let rejectResume = true;
  await page.route(`**/api/trips/${incomplete.id}`, async route => {
    if (route.request().method() === 'PATCH' && rejectPatch) {
      rejectPatch = false;
      return route.fulfill({ status: 409, json: { error: { code: 'revision_conflict', message: '条件已更新，请重试' } } });
    }
    return route.continue();
  });
  await page.route(`**/api/jobs/${job.id}/resume`, async route => {
    if (rejectResume) {
      rejectResume = false;
      return route.fulfill({ status: 503, json: { error: { code: 'unavailable', message: '暂时无法继续，请重试' } } });
    }
    return route.continue();
  });
  await clarification.getByRole('button', { name: '保存并继续', exact: true }).click();
  await clarification.getByRole('alert').filter({ hasText: '条件已更新' }).waitFor();
  assert.equal(await clarification.getByLabel('你的补充').inputValue(), '住南稍门附近，喜欢历史文化');
  assert.equal((await api(`/api/trips/${incomplete.id}`)).revision, 1);
  await clarification.getByRole('button', { name: '保存并继续', exact: true }).click();
  await clarification.getByRole('alert').filter({ hasText: '暂时无法继续' }).waitFor();
  const savedClarification = await api(`/api/trips/${incomplete.id}`);
  assert.equal(savedClarification.revision, 2);
  assert.equal(savedClarification.clarifications.length, 1);
  await clarification.getByRole('button', { name: '保存并继续', exact: true }).click();
  const resumed = await poll(() => api(`/api/jobs/${job.id}`), data => data.status === 'succeeded');
  assert.equal(resumed.request_revision, 2);
  assert.deepEqual((await api(`/api/trips/${incomplete.id}`)).clarifications, savedClarification.clarifications);
  await page.unroute(`**/api/trips/${incomplete.id}`);
  await page.unroute(`**/api/jobs/${job.id}/resume`);
  assert((await api(`/api/trips/${incomplete.id}`)).known_conditions.join('\n').includes('住南稍门附近，喜欢历史文化'));
  await page.getByRole('heading', { name: '行程草案', exact: true }).waitFor();
  checked('补充条件保留输入、原任务继续');

  assert.equal(await page.locator('.trip-request-details').getAttribute('open'), null);
  assert.equal(await page.locator('.itinerary-details').getAttribute('open'), null);
  assert.equal(await page.locator('.validation-issues').count(), 0);
  const progressJobs = url => url.pathname === `/api/trips/${incomplete.id}/jobs`;
  await page.route(progressJobs, route => route.fulfill({ json: [{
    ...resumed, status: 'running', stage: 'routes', progress_estimate: 72,
  }, resumed] }));
  await page.reload();
  const progress = page.getByRole('progressbar', { name: '预计进度' });
  await progress.waitFor();
  assert.equal(await progress.getAttribute('value'), '72');
  await page.getByText('预计 72%', { exact: true }).waitFor();
  await capture('desktop-estimated-progress', true);
  await page.unroute(progressJobs);
  await page.reload();
  await page.getByRole('heading', { name: '行程草案', exact: true }).waitFor();
  await capture('desktop-itinerary-list', true);
  checked('预计进度与完成后行程优先展示');

  await page.goto(base + '/status');
  await page.getByRole('heading', { name: '服务状态', exact: true }).waitFor();
  await checkProviders(['密钥认证 · REST', '密钥认证 · REST', '未配置', '匿名访问 · REST']);
  checked('四家搜索名称、顺序、密钥与匿名模式、禁用状态及响应脱敏');
  const checksBefore = (await api('/api/config/checks')).length;
  await page.getByRole('button', { name: '检查', exact: true }).last().click();
  await poll(() => api('/api/config/checks'), data => data.length > checksBefore);
  await page.getByRole('heading', { name: '服务状态', exact: true }).waitFor();
  await capture('desktop-services', true);
  checked('显式服务检查');

  await checkTimeConstraints({ page, base, api, capture, checked });

  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    for (const [route, name] of [['/', 'home'], ['/settings', 'settings'], ['/knowledge', 'knowledge'], ['/preferences', 'preferences'], ['/status', 'services'], [`/trips/${tripId}`, 'itinerary']]) {
      await page.goto(base + route);
      await page.locator('h1').waitFor();
      if (name === 'itinerary') await page.getByRole('heading', { name: '行程草案', exact: true }).waitFor();
      await capture(`mobile-${width}-${name}`);
    }
    await page.goto(base);
    await page.getByRole('button', { name: '新建行程', exact: true }).first().click();
    await capture(`mobile-${width}-trip-form`);
    await page.getByRole('button', { name: '＋ 添加参考', exact: true }).click();
    await page.getByRole('dialog').waitFor();
    await capture(`mobile-${width}-reference-dialog`);
  }
  if (report.layoutIssues.length === 0) checked('390px、320px页面布局和路由直达');

  if (values['missing-config-url']) {
    await page.goto(values['missing-config-url'].replace(/\/$/, '') + '/status');
    await page.getByRole('heading', { name: '服务状态', exact: true }).waitFor();
    await checkProviders(['未配置', '未配置', '未配置', '未配置']);
    assert.deepEqual(await page.locator('.status-missing-item').allTextContents(),
      ['模型地址', '模型密钥', '模型名称', '地图密钥']);
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
  assert.deepEqual(report.consoleErrors, [], '存在意外控制台错误');
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
