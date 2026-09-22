// Explicit, repeatable live checks through the local API. No key is read here.
const [mode, serial] = process.argv.slice(2);
if (!['search', 'explore', 'assertion-failure', 'cancel'].includes(mode) || !serial) {
  throw new Error('Usage: node live-check.mjs search|explore|assertion-failure|cancel SERIAL [--parallel]');
}
const base = process.env.UI_TEST_API || 'http://127.0.0.1:8091/api';
if (!['localhost', '127.0.0.1'].includes(new URL(base).hostname)) throw new Error('Live check requires a local API');
async function api(path, body) {
  const response = await fetch(base + path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.timeout(30000) });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : `HTTP ${response.status}`);
  return result;
}
const step = (id, type, prompt, extra = {}) => ({ id, type, prompt, timeoutMs: 120000, ...extra });
const test = { name: `接入验证 · ${mode}`, steps: [] };
if (mode === 'search') {
  test.parameters = [{ name: 'QUERY', default: '蓝牙' }];
  test.steps = [step('open', 'action', '返回 Android 系统设置主页，确保顶部设置搜索入口可见'), step('search', 'tap', '系统设置顶部搜索框或搜索按钮'), step('input', 'input', '设置搜索输入框', { value: '${QUERY}' }), step('wait', 'wait', '搜索结果中出现 ${QUERY} 相关设置条目'), step('assert', 'assert', '设置搜索框显示 ${QUERY}，且搜索结果包含相关设置')];
} else if (mode === 'explore') {
  test.steps = [step('open', 'action', '打开 Android 系统设置的显示页面，并展开高级选项，让自动旋转屏幕开关可见'), step('explore', 'explore', 'Android 系统设置的显示设置页面', { timeoutMs: 300000, exploration: { allowedControls: ['自动旋转屏幕'], maxActions: 2 } })];
} else {
  test.steps = [step('impossible', mode === 'cancel' ? 'wait' : 'assert', '当前页面有明确写着 CODEX_UI_TEST_IMPOSSIBLE_945817 的按钮', { timeoutMs: 120000 }), step('must-not-run', 'screenshot', '此步骤应在前一步失败或取消后跳过')];
}
let performance, run;
try {
  const saved = await api('/ui/cases', test);
  if (process.argv.includes('--parallel')) performance = await api('/sessions', { serial, duration_seconds: 90, interval_ms: 1000, metrics: { cpu: true, memory: true, fps: true } });
  run = await api('/ui/runs', { serial, testCase: saved, timeoutSeconds: 360 });
  console.log(JSON.stringify({ started: run.id, performance: performance?.session_id, mode }));
  if (mode === 'cancel') {
    await new Promise(resolve => setTimeout(resolve, 3500));
    run = await api(`/ui/runs/${run.id}/cancel`, {});
  }
  while (['queued', 'running'].includes(run.state)) {
    await new Promise(resolve => setTimeout(resolve, 2000));
    run = await api(`/ui/runs/${run.id}`);
  }
  const events = await api(`/ui/runs/${run.id}/events`);
  console.log(JSON.stringify({ id: run.id, state: run.state, durationMs: run.endedAt - run.createdAt, steps: events.filter(e => ['step', 'exploration'].includes(e.type)), report: run.report }, null, 2));
  const expected = mode === 'cancel' ? 'cancelled' : mode === 'assertion-failure' ? 'failed' : 'passed';
  if (run.state !== expected) process.exitCode = 1;
} finally {
  if (run && ['queued', 'running'].includes(run.state)) await api(`/ui/runs/${run.id}/cancel`, {});
  if (performance) {
    const result = await api(`/sessions/${performance.session_id}/stop`, {});
    console.log(JSON.stringify({ performance: result.session_id, state: result.state, sampleCount: result.summary?.sample_count }));
  }
}
