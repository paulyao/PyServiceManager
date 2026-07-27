"""Qoder 账号管理服务

管理 Qoder 和 Qoder CN 两个组织的账号列表、用量监控、共享资源包统计和 AI 代码统计。
提供对外 API 按审批流程自动分配账号，Web 页面展示账号差异和用量数据。

功能模块：
1. 账号管理：SQLite 存储 qoder_accounts/qoder_cn_accounts，预置 99 个 email 槽位
2. 对外 API：POST /api/allocate 按 appId 分配账号
3. Web 页面：4 个 tab（Qoder 账号 / Qoder CN 账号 / 用量监控 / AI 代码统计）
4. 用量监控：成员配额 + 共享资源包，两套配置
5. AI 代码统计：成员排名 + commit 明细 + 趋势图表

依赖模块：http-client, ss-omc-sdk, log-enhancer, web-service, sqlite-helper
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_ALARM_DEDUP_FILE = os.path.join(os.path.dirname(__file__), ".alarm_dedup.json")
_DB_FILE = os.path.join(os.path.dirname(__file__), ".qoder.db")
POLL_INTERVAL = 10

_data_lock = threading.Lock()
_shared_data = {
    "members": [],
    "last_updated": None,
    "total_members": 0,
    "exhausted_count": 0,
    "refresh_interval": 60,
    "ai_code_last_updated": None,
    "api_members": {},
    "api_members_cn": {},
}

_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Qoder 账号管理</title>
<style>
:root {
  --bg: #f0f2f5; --card: #ffffff; --border: #e2e8f0; --text: #0f172a;
  --text2: #64748b; --text3: #94a3b8;
  --green: #10b981; --yellow: #f59e0b; --red: #ef4444;
  --blue: #3b82f6; --blue-light: #60a5fa; --blue-dark: #2563eb;
  --radius: 10px; --shadow: 0 1px 3px rgba(0,0,0,0.08); --shadow-hover: 0 4px 12px rgba(0,0,0,0.12);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg); color: var(--text); line-height: 1.6; padding: 24px; }
.container { max-width: 1280px; margin: 0 auto; }
h1 { font-size: 26px; font-weight: 700; margin-bottom: 20px; letter-spacing: -0.5px; }
h2 { font-size: 18px; font-weight: 700; margin: 24px 0 12px; }
.stats { display: flex; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }
.stat-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 16px 24px; min-width: 150px; box-shadow: var(--shadow); transition: box-shadow 0.2s; }
.stat-card:hover { box-shadow: var(--shadow-hover); }
.stat-card .label { font-size: 11px; color: var(--text3); text-transform: uppercase; letter-spacing: 0.8px; font-weight: 600; }
.stat-card .value { font-size: 32px; font-weight: 800; margin-top: 4px; letter-spacing: -1px; }
.stat-card .value.danger { color: var(--red); }
.stat-card .value.green { color: var(--green); }
.stat-card .value.muted { color: var(--text3); }
.search-box { width: 100%; padding: 10px 16px; border: 1px solid var(--border); border-radius: var(--radius);
  font-size: 14px; margin-bottom: 16px; outline: none; transition: border-color 0.2s, box-shadow 0.2s; background: var(--card); }
.search-box:focus { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(59,130,246,0.1); }
.table-wrap { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
  overflow: hidden; box-shadow: var(--shadow); margin-bottom: 24px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
thead { background: #f8fafc; }
th { padding: 12px 16px; text-align: left; font-weight: 600; font-size: 12px;
  text-transform: uppercase; letter-spacing: 0.5px; color: var(--text2);
  border-bottom: 2px solid var(--border); }
td { padding: 12px 16px; border-bottom: 1px solid #f1f5f9; }
tr:last-child td { border-bottom: none; }
tbody tr { transition: background 0.15s; }
tr:hover { background: #f8fafc; }
.bar-wrap { width: 120px; height: 8px; background: #e2e8f0; border-radius: 4px; overflow: hidden;
  display: inline-block; vertical-align: middle; margin-right: 8px; }
.bar-fill { height: 100%; border-radius: 4px; transition: width 0.4s ease; }
.bar-fill.green { background: linear-gradient(90deg, #10b981, #34d399); }
.bar-fill.yellow { background: linear-gradient(90deg, #f59e0b, #fbbf24); }
.bar-fill.red { background: linear-gradient(90deg, #ef4444, #f87171); }
.badge { display: inline-block; padding: 3px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }
.badge.active { background: #d1fae5; color: #065f46; }
.badge.restricted { background: #fee2e2; color: #991b1b; }
.badge.other { background: #f1f5f9; color: #475569; }
.badge.diff { background: #fef3c7; color: #92400e; }
.badge.missing { background: #fee2e2; color: #991b1b; }
.empty-state { text-align: center; padding: 60px 20px; color: var(--text3); }
.empty-state .icon { font-size: 48px; margin-bottom: 12px; }
.updated { font-size: 12px; color: var(--text3); margin-bottom: 16px; }
.name-cell { font-weight: 600; color: var(--text); }
.email-cell { color: var(--text2); font-size: 13px; }
.search-row { display: flex; gap: 12px; margin-bottom: 16px; }
.search-row .search-box { margin-bottom: 0; }
.tabs { display: flex; gap: 0; margin-bottom: 24px; border-bottom: 2px solid var(--border); }
.tab { padding: 10px 24px; border: none; background: none; cursor: pointer; font-size: 14px;
  color: var(--text2); border-bottom: 3px solid transparent; margin-bottom: -2px; transition: all 0.2s;
  font-weight: 500; }
.tab:hover { color: var(--text); }
.tab.active { color: var(--blue); border-bottom-color: var(--blue); font-weight: 700; }
th.sortable { cursor: pointer; user-select: none; transition: color 0.15s; }
th.sortable:hover { color: var(--blue); }
th.sort-asc::after { content: ' \u25b2'; font-size: 10px; color: var(--blue); }
th.sort-desc::after { content: ' \u25bc'; font-size: 10px; color: var(--blue); }
.ai-controls { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.period-selector { display: flex; gap: 8px; }
.period-btn { padding: 8px 20px; border: 1px solid var(--border); background: var(--card);
  border-radius: var(--radius); cursor: pointer; font-size: 13px; font-weight: 500; transition: all 0.2s; }
.period-btn:hover { border-color: var(--blue-light); color: var(--blue); }
.period-btn.active { background: var(--blue); color: #fff; border-color: var(--blue); box-shadow: 0 2px 6px rgba(59,130,246,0.3); }
.branch-toggle { display: flex; align-items: center; gap: 8px; font-size: 14px; cursor: pointer; font-weight: 500; }
.branch-toggle input { width: 16px; height: 16px; accent-color: var(--blue); cursor: pointer; }
.chart-area { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 24px; margin-bottom: 24px; box-shadow: var(--shadow); }
.chart-title { font-size: 14px; font-weight: 700; margin-bottom: 16px; color: var(--text); }
.chart-tabs { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.chart-tab { padding: 8px 16px; border: 1px solid var(--border); background: var(--card);
  border-radius: var(--radius); cursor: pointer; font-size: 13px; font-weight: 500; transition: all 0.2s; }
.chart-tab:hover { border-color: var(--blue-light); color: var(--blue); }
.chart-tab.active { background: var(--blue); color: #fff; border-color: var(--blue); box-shadow: 0 2px 6px rgba(59,130,246,0.3); }
.bar-chart { display: flex; align-items: flex-end; gap: 3px; height: 220px; overflow-x: auto; padding-top: 24px; }
.bar-item { flex: 1; min-width: 28px; display: flex; flex-direction: column; align-items: center; height: 100%; justify-content: flex-end; position: relative; }
.bar { width: 100%; max-width: 52px; border-radius: 4px 4px 0 0; min-height: 3px; transition: height 0.4s ease, opacity 0.2s, transform 0.2s; cursor: pointer; }
.bar:hover { opacity: 0.85; transform: scaleY(1.02); transform-origin: bottom; }
.bar-value { font-size: 11px; color: var(--text); font-weight: 700; margin-bottom: 4px; white-space: nowrap; }
.bar.green { background: linear-gradient(180deg, #34d399, #10b981); }
.bar.yellow { background: linear-gradient(180deg, #fbbf24, #f59e0b); }
.bar.red { background: linear-gradient(180deg, #f87171, #ef4444); }
.bar-label { font-size: 10px; color: var(--text3); margin-top: 6px; white-space: nowrap; }
.pkg-section { margin-bottom: 24px; }
</style>
</head>
<body>
<div class="container">
  <h1>Qoder 账号管理</h1>
  <div class="tabs">
    <button class="tab active" onclick="switchTab('accounts')">Qoder 账号</button>
    <button class="tab" onclick="switchTab('accounts-cn')">Qoder CN 账号</button>
    <button class="tab" onclick="switchTab('usage')">用量监控</button>
    <button class="tab" onclick="switchTab('ai-code')">AI 代码统计</button>
  </div>

  <!-- Tab 1: Qoder 账号 -->
  <div id="tab-accounts">
    <div class="updated" id="acc-updated">加载中...</div>
    <div class="pkg-section" id="acc-packages"></div>
    <div class="search-row">
      <input class="search-box" id="acc-search" type="text" placeholder="搜索邮箱...">
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>邮箱</th><th>姓名</th><th>部门</th><th>差异</th>
        </tr></thead>
        <tbody id="acc-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- Tab 2: Qoder CN 账号 -->
  <div id="tab-accounts-cn" style="display:none">
    <div class="updated" id="cn-updated">加载中...</div>
    <div class="pkg-section" id="cn-packages"></div>
    <div class="search-row">
      <input class="search-box" id="cn-search" type="text" placeholder="搜索邮箱...">
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>邮箱</th><th>姓名</th><th>部门</th><th>差异</th>
        </tr></thead>
        <tbody id="cn-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- Tab 3: 用量监控 -->
  <div id="tab-usage" style="display:none">
    <div class="updated" id="usage-updated">加载中...</div>
    <h2>Qoder 国际版</h2>
    <div class="pkg-section" id="usage-packages"></div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>邮箱</th><th>姓名</th><th>已用</th><th>总量</th><th>使用率</th><th>状态</th>
        </tr></thead>
        <tbody id="usage-tbody"></tbody>
      </table>
    </div>
    <h2>Qoder CN</h2>
    <div class="pkg-section" id="usage-packages-cn"></div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>邮箱</th><th>姓名</th><th>已用</th><th>总量</th><th>使用率</th><th>状态</th>
        </tr></thead>
        <tbody id="usage-tbody-cn"></tbody>
      </table>
    </div>
  </div>

  <!-- Tab 4: AI 代码统计 -->
  <div id="tab-ai-code" style="display:none">
    <div class="ai-controls">
      <div class="period-selector">
        <button class="period-btn active" onclick="switchPeriod(7)">7天</button>
        <button class="period-btn" onclick="switchPeriod(30)">30天</button>
      </div>
      <label class="branch-toggle">
        <input type="checkbox" id="primary-only" onchange="onPrimaryBranchChange()">
        只看主分支
      </label>
    </div>
    <div class="chart-area">
      <div class="chart-tabs">
        <button class="chart-tab active" onclick="switchChart('share')">AI代码占比趋势</button>
        <button class="chart-tab" onclick="switchChart('accept')">Tab补全接受率趋势</button>
        <button class="chart-tab" onclick="switchChart('lines')">Agent生成采纳行趋势</button>
      </div>
      <div class="bar-chart" id="trend-chart"><div class="empty-state">加载中...</div></div>
    </div>
    <div class="updated" id="ai-updated">加载中...</div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th class="sortable" onclick="sortAI('name')">姓名</th>
          <th class="sortable" onclick="sortAI('email')">邮箱</th>
          <th class="sortable" onclick="sortAI('commit_count')">提交次数</th>
          <th class="sortable" onclick="sortAI('total_added')">总新增行数</th>
          <th class="sortable" onclick="sortAI('total_deleted')">总删除行数</th>
          <th class="sortable" onclick="sortAI('ai_added')">AI新增行数</th>
          <th class="sortable" onclick="sortAI('ai_deleted')">AI删除行数</th>
          <th class="sortable" onclick="sortAI('non_ai_added')">非AI新增行数</th>
          <th class="sortable" onclick="sortAI('ide_total')">IDE新增行数</th>
          <th class="sortable" onclick="sortAI('plugin_total')">JB插件新增行数</th>
          <th class="sortable" onclick="sortAI('cli_agent_added')">CLI Agent新增</th>
          <th class="sortable" onclick="sortAI('ai_rate')">AI代码占比</th>
        </tr></thead>
        <tbody id="ai-tbody"></tbody>
      </table>
    </div>
  </div>
</div>
<script>
const base = window.location.pathname.endsWith('/')
  ? window.location.pathname
  : window.location.pathname + '/';
let activeTab = 'accounts';
let accountData = {qoder: [], qoder_cn: []};
let usageData = null;
let refreshMs = 60000;
let timer = null;

function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
function fmt(n) { return typeof n === 'number' ? (Number.isInteger(n) ? n.toString() : n.toFixed(2)) : n; }
function barClass(pct) { return pct >= 80 ? 'red' : pct >= 60 ? 'yellow' : 'green'; }

function switchTab(name) {
  activeTab = name;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  ['accounts','accounts-cn','usage','ai-code'].forEach(id => {
    document.getElementById('tab-' + id).style.display = id === name ? '' : 'none';
  });
  if (name === 'accounts' || name === 'accounts-cn') fetchAccounts();
  if (name === 'usage') fetchUsage();
  if (name === 'ai-code') { fetchAICodeData(); fetchTrendData(); }
}

// ── 账号列表 ──
async function fetchAccounts() {
  try {
    const r = await fetch(base + 'api/accounts', {method: 'POST'});
    const d = await r.json();
    accountData = d;
    renderAccounts('qoder', 'acc-tbody', 'acc-updated', 'acc-search', 'acc-packages');
    renderAccounts('qoder_cn', 'cn-tbody', 'cn-updated', 'cn-search', 'cn-packages');
  } catch(e) { console.error('accounts fetch error:', e); }
}

function renderAccounts(org, tbodyId, updatedId, searchId, pkgId) {
  const data = accountData[org] || {};
  const members = (data.members || []).filter(m => m.name);
  const packages = data.packages || [];
  const summary = data.summary || {};
  const q = document.getElementById(searchId) ? document.getElementById(searchId).value.toLowerCase() : '';
  const filtered = members.filter(m => !q || m.email.toLowerCase().includes(q) || (m.name||'').toLowerCase().includes(q));

  document.getElementById(updatedId).textContent = summary.last_updated ? '上次更新: ' + summary.last_updated : '尚未采集';

  // Render packages
  const pkgHtml = packages.length ? renderPackages(packages, summary) : '';
  document.getElementById(pkgId).innerHTML = pkgHtml;

  if (!filtered.length) {
    document.getElementById(tbodyId).innerHTML = '<tr><td colspan="4" class="empty-state">无匹配账号</td></tr>';
    return;
  }
  const rows = filtered.map(m => {
    let diffBadge = '';
    if (m.diff === '名称不一致') diffBadge = '<span class="badge diff">名称不一致</span>';
    else if (m.diff === 'API无此账号') diffBadge = '<span class="badge missing">API无此账号</span>';
    return '<tr>' +
      '<td class="email-cell">' + esc(m.email) + '</td>' +
      '<td class="name-cell">' + esc(m.name) + '</td>' +
      '<td>' + esc(m.department || '-') + '</td>' +
      '<td>' + diffBadge + '</td></tr>';
  });
  document.getElementById(tbodyId).innerHTML = rows.join('');
}

function renderPackages(packages, summary) {
  const totalLimit = summary.packageTotalLimit || 0;
  const totalUsed = summary.packageTotalUsed || 0;
  const usageRate = summary.packageUsageRate || 0;
  const bc = barClass(usageRate);
  let html = '<div class="stats">' +
    '<div class="stat-card"><div class="label">总额度</div><div class="value">' + fmt(totalLimit) + '</div></div>' +
    '<div class="stat-card"><div class="label">已用</div><div class="value">' + fmt(totalUsed) + '</div></div>' +
    '<div class="stat-card"><div class="label">使用率</div><div class="value ' + (bc==='green'?'green':bc==='red'?'danger':'') + '">' + usageRate.toFixed(1) + '%</div></div>' +
    '</div>';
  if (packages.length) {
    html += '<div class="table-wrap"><table><thead><tr>' +
      '<th>名称</th><th>来源</th><th>状态</th><th>额度</th><th>已用</th><th>剩余</th><th>到期</th>' +
      '</tr></thead><tbody>';
    packages.forEach(p => {
      const rate = p.limitValue > 0 ? (p.usedValue / p.limitValue * 100) : 0;
      html += '<tr>' +
        '<td class="name-cell">' + esc(p.name) + '</td>' +
        '<td>' + esc(p.source || '-') + '</td>' +
        '<td><span class="badge ' + (p.status==='active'?'active':'other') + '">' + esc(p.status) + '</span></td>' +
        '<td>' + fmt(p.limitValue) + '</td>' +
        '<td>' + fmt(p.usedValue) + '</td>' +
        '<td>' + fmt(p.remainingValue) + '</td>' +
        '<td class="email-cell">' + esc((p.expiresAt||'').substring(0,10)) + '</td></tr>';
    });
    html += '</tbody></table></div>';
  }
  return html;
}

// ── 用量监控 ──
async function fetchUsage() {
  try {
    const r = await fetch(base + 'api/usage', {method: 'POST'});
    const d = await r.json();
    usageData = d;
    document.getElementById('usage-updated').textContent = d.last_updated ? '上次更新: ' + d.last_updated : '尚未采集';
    renderUsageOrg('qoder', 'usage-tbody', 'usage-packages');
    renderUsageOrg('qoder_cn', 'usage-tbody-cn', 'usage-packages-cn');
  } catch(e) { console.error('usage fetch error:', e); }
}

function renderUsageOrg(org, tbodyId, pkgId) {
  const data = usageData ? (usageData[org] || {}) : {};
  const members = data.members || [];
  const packages = data.packages || [];
  const summary = data.summary || {};

  document.getElementById(pkgId).innerHTML = packages.length ? renderPackages(packages, summary) : '';

  if (!members.length) {
    document.getElementById(tbodyId).innerHTML = '<tr><td colspan="6" class="empty-state">暂无数据</td></tr>';
    return;
  }
  const rows = members.map(m => {
    const limit = m.limitValue || 0;
    const used = m.usedValue || 0;
    const pct = limit > 0 ? (used / limit * 100) : 0;
    const bc = barClass(pct);
    const badge = m.status === 'restricted' ? '<span class="badge restricted">restricted</span>'
      : m.status === 'active' ? '<span class="badge active">active</span>'
      : '<span class="badge other">' + esc(m.status||'unknown') + '</span>';
    return '<tr>' +
      '<td class="email-cell">' + esc(m.email) + '</td>' +
      '<td class="name-cell">' + esc(m.name||'-') + '</td>' +
      '<td>' + fmt(used) + '</td>' +
      '<td>' + fmt(limit) + '</td>' +
      '<td><div class="bar-wrap"><div class="bar-fill ' + bc + '" style="width:' + Math.min(pct,100) + '%"></div></div>' + pct.toFixed(1) + '%</td>' +
      '<td>' + badge + '</td></tr>';
  });
  document.getElementById(tbodyId).innerHTML = rows.join('');
}

document.getElementById('acc-search').addEventListener('input', () => renderAccounts('qoder', 'acc-tbody', 'acc-updated', 'acc-search', 'acc-packages'));
document.getElementById('cn-search').addEventListener('input', () => renderAccounts('qoder_cn', 'cn-tbody', 'cn-updated', 'cn-search', 'cn-packages'));

// ── AI 代码统计 ──
let aiMembers = [];
let aiSortField = 'ai_added';
let aiSortAsc = false;
let aiTimer = null;
let trendDays = 7;
let primaryOnly = false;
let chartType = 'share';
let trendItems = [];
let trendNextItems = [];

function switchPeriod(days) {
  trendDays = days;
  document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  fetchTrendData();
  fetchAICodeData();
}

function onPrimaryBranchChange() {
  primaryOnly = document.getElementById('primary-only').checked;
  fetchTrendData();
  fetchAICodeData();
}

function switchChart(type) {
  chartType = type;
  document.querySelectorAll('.chart-tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  renderTrendChart();
}

async function fetchTrendData() {
  try {
    const params = new URLSearchParams({days: trendDays, primary_branch_only: primaryOnly});
    const r = await fetch(base + 'api/ai-code-trend?' + params, {method: 'POST'});
    const d = await r.json();
    trendItems = d.items || [];
    trendNextItems = d.nextItems || [];
    renderTrendChart();
  } catch(e) { console.error('trend fetch error:', e); }
}

function renderTrendChart() {
  const container = document.getElementById('trend-chart');
  if (chartType === 'share') {
    if (!trendItems.length) { container.innerHTML = '<div class="empty-state">暂无趋势数据</div>'; return; }
    const maxRate = Math.max(...trendItems.map(i => i.aiShareRate || 0), 100);
    container.innerHTML = trendItems.map(item => {
      const rate = item.aiShareRate || 0;
      const h = (rate / maxRate * 100);
      const cls = rate >= 50 ? 'green' : rate >= 20 ? 'yellow' : 'red';
      const date = item.date ? item.date.substring(5, 10) : '';
      const commits = item.commitCount || 0;
      return '<div class="bar-item"><div class="bar-value">' + rate.toFixed(1) + '%</div><div class="bar ' + cls + '" style="height:' + h + '%" title="' + date + ' | AI占比: ' + rate.toFixed(1) + '% | 提交: ' + commits + '"></div><div class="bar-label">' + date + '</div></div>';
    }).join('');
  } else if (chartType === 'accept') {
    if (!trendNextItems.length) { container.innerHTML = '<div class="empty-state">暂无趋势数据</div>'; return; }
    const maxRate = Math.max(...trendNextItems.map(i => i.nextAcceptRate || 0), 100);
    container.innerHTML = trendNextItems.map(item => {
      const rate = item.nextAcceptRate || 0;
      const h = (rate / maxRate * 100);
      const cls = rate >= 50 ? 'green' : rate >= 20 ? 'yellow' : 'red';
      const date = item.date ? item.date.substring(5, 10) : '';
      const suggested = item.nextSuggestedCount || 0;
      const accepted = item.nextAcceptedCount || 0;
      return '<div class="bar-item"><div class="bar-value">' + rate.toFixed(1) + '%</div><div class="bar ' + cls + '" style="height:' + h + '%" title="' + date + ' | 接受率: ' + rate.toFixed(1) + '% | 接受: ' + accepted + ' | 建议: ' + suggested + '"></div><div class="bar-label">' + date + '</div></div>';
    }).join('');
  } else if (chartType === 'lines') {
    if (!trendItems.length) { container.innerHTML = '<div class="empty-state">暂无趋势数据</div>'; return; }
    const maxLines = Math.max(...trendItems.map(i => i.aiLinesAdded || 0), 1);
    container.innerHTML = trendItems.map(item => {
      const lines = item.aiLinesAdded || 0;
      const h = (lines / maxLines * 100);
      const date = item.date ? item.date.substring(5, 10) : '';
      return '<div class="bar-item"><div class="bar-value">' + lines + '</div><div class="bar" style="height:' + h + '%;background:var(--blue)" title="' + date + ' | AI行数: ' + lines + '"></div><div class="bar-label">' + date + '</div></div>';
    }).join('');
  }
}

async function fetchAICodeData() {
  try {
    const params = new URLSearchParams({days: trendDays, primary_branch_only: primaryOnly});
    const r = await fetch(base + 'api/ai-code?' + params, {method: 'POST'});
    const d = await r.json();
    const data = d.data || d;
    if (!data.last_updated) {
      document.getElementById('ai-tbody').innerHTML = '<tr><td colspan="12" class="empty-state"><div class="icon">\u23f3</div>数据尚未采集，请等待首次采集完成</td></tr>';
      document.getElementById('ai-updated').textContent = '尚未采集';
      return;
    }
    aiMembers = (data.members || []).map(m => {
      m.ide_total = (m.ide_next_added||0) + (m.ide_agent_added||0) + (m.ide_quest_added||0);
      m.plugin_total = (m.plugin_next_added||0) + (m.plugin_agent_added||0) + (m.jb_inline_chat_added||0);
      return m;
    });
    document.getElementById('ai-updated').textContent = '上次更新: ' + data.last_updated;
    renderAITable();
  } catch(e) { console.error('AI code fetch error:', e); }
}

function renderAITable() {
  const sorted = [...aiMembers].sort((a, b) => {
    const av = a[aiSortField]; const bv = b[aiSortField];
    if (typeof av === 'string') return aiSortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
    return aiSortAsc ? (av || 0) - (bv || 0) : (bv || 0) - (av || 0);
  });
  if (!sorted.length) {
    document.getElementById('ai-tbody').innerHTML = '<tr><td colspan="12" class="empty-state">暂无数据</td></tr>';
    return;
  }
  const rows = sorted.map(m => {
    const aiRate = m.ai_rate != null ? m.ai_rate.toFixed(1) + '%' : '-';
    const ideTotal = (m.ide_next_added||0) + (m.ide_agent_added||0) + (m.ide_quest_added||0);
    const pluginTotal = (m.plugin_next_added||0) + (m.plugin_agent_added||0) + (m.jb_inline_chat_added||0);
    return '<tr>' +
      '<td class="name-cell">' + esc(m.name || m.email || '-') + '</td>' +
      '<td class="email-cell">' + esc(m.email || '-') + '</td>' +
      '<td>' + (m.commit_count || 0) + '</td>' +
      '<td>' + (m.total_added || 0) + '</td>' +
      '<td>' + (m.total_deleted || 0) + '</td>' +
      '<td>' + (m.ai_added || 0) + '</td>' +
      '<td>' + (m.ai_deleted || 0) + '</td>' +
      '<td>' + (m.non_ai_added || 0) + '</td>' +
      '<td>' + ideTotal + '</td>' +
      '<td>' + pluginTotal + '</td>' +
      '<td>' + (m.cli_agent_added || 0) + '</td>' +
      '<td>' + aiRate + '</td></tr>';
  });
  document.getElementById('ai-tbody').innerHTML = rows.join('');
  document.querySelectorAll('#tab-ai-code th.sortable').forEach(th => {
    th.classList.remove('sort-asc', 'sort-desc');
    const field = th.getAttribute('onclick').match(/'([^']+)'/)[1];
    if (field === aiSortField) th.classList.add(aiSortAsc ? 'sort-asc' : 'sort-desc');
  });
}

function sortAI(field) {
  if (field === aiSortField) { aiSortAsc = !aiSortAsc; } else { aiSortField = field; aiSortAsc = false; }
  renderAITable();
}

// ── 自动刷新 ──
function restartTimer() { if (timer) clearInterval(timer); timer = setInterval(() => {
  if (activeTab === 'accounts' || activeTab === 'accounts-cn') fetchAccounts();
  else if (activeTab === 'usage') fetchUsage();
  else if (activeTab === 'ai-code') fetchAICodeData();
}, refreshMs); }

fetchAccounts();
restartTimer();
</script>
</body>
</html>
"""


def _next_run_timestamp(schedule_cfg, after=None):
    """计算下次执行的 Unix 时间戳。"""
    now = after if after is not None else time.time()
    now_dt = datetime.fromtimestamp(now)

    cron_expr = schedule_cfg.get("cron")
    if cron_expr:
        try:
            from croniter import croniter
            parts = cron_expr.strip().split()
            if len(parts) == 6:
                cron_expr = " ".join(parts[1:])
            cron = croniter(cron_expr, now_dt)
            next_dt = cron.get_next(datetime)
            logger.info("cron 调度: %s，下次执行 %s", schedule_cfg.get("cron"), next_dt.isoformat())
            return next_dt.timestamp()
        except ImportError:
            logger.warning("croniter 未安装，回退到 run_hour 调度")
        except Exception as e:
            logger.warning("cron 解析失败，回退到 run_hour: %s", e)

    run_hour = schedule_cfg.get("run_hour", 9)
    next_dt = now_dt.replace(hour=run_hour, minute=0, second=0, microsecond=0)
    if next_dt <= now_dt:
        next_dt += timedelta(days=1)
    logger.info("每日调度: %02d:00，下次执行 %s", run_hour, next_dt.isoformat())
    return next_dt.timestamp()

def run(config, modules):
    """服务主入口。"""
    http_mod = modules.get("http-client")
    omc_mod = modules.get("ss-omc-sdk")
    log_mod = modules.get("log-enhancer")
    sqlite_mod = modules.get("sqlite-helper")

    if not http_mod:
        logger.error("http-client 模块为必需模块，请先绑定")
        return
    if not sqlite_mod:
        logger.error("sqlite-helper 模块为必需模块，请先绑定")
        return
    if not omc_mod:
        logger.warning("ss-omc-sdk 模块未启用，告警和心跳功能将不可用")

    qoder_cfg = config.get("qoder", {})
    api_base_url = qoder_cfg.get("api_base_url", "https://api.qoder.com")
    api_key = qoder_cfg.get("api_key", "")
    org_id = qoder_cfg.get("organization_id", "")

    cn_cfg = config.get("qoder_cn", {})
    cn_base_url = cn_cfg.get("api_base_url", "https://api.qoder.cn")
    cn_api_key = cn_cfg.get("api_key", "")
    cn_org_id = cn_cfg.get("organization_id", "")

    schedule_cfg = config.get("schedule", {})
    monitor_cfg = config.get("monitor", {})
    rate_limit_delay = monitor_cfg.get("rate_limit_delay", 0.5)
    alarm_dedup_days = monitor_cfg.get("alarm_dedup_days", 1)

    heartbeat_cfg = config.get("heartbeat", {})
    heartbeat_interval_sec = heartbeat_cfg.get("interval_minutes", 10) * 60

    accounts_cfg = config.get("accounts", {})
    app_id_cn = accounts_cfg.get("app_id_cn", 56)
    app_id_global = accounts_cfg.get("app_id_global", 57)
    excel_qoder_path = accounts_cfg.get("excel_qoder", "")
    excel_qoder_cn_path = accounts_cfg.get("excel_qoder_cn", "")

    _shared_data["refresh_interval"] = monitor_cfg.get("refresh_interval", 60)
    ai_code_days = config.get("ai_code", {}).get("days", 30)

    def _log(msg, level="INFO"):
        if log_mod:
            log_mod.log(msg, level=level)
        else:
            getattr(logger, level.lower(), logger.info)(msg)

    # ── SQLite 初始化 ──

    def _init_db():
        """初始化所有 SQLite 表。"""
        sqlite_mod.execute(db_path=_DB_FILE, sql="""CREATE TABLE IF NOT EXISTS quota_members (
            name TEXT, email TEXT PRIMARY KEY,
            used_value REAL, limit_value REAL, unit TEXT,
            status TEXT, next_reset_at TEXT, collected_at TEXT)""", commit=True)
        sqlite_mod.execute(db_path=_DB_FILE, sql="""CREATE TABLE IF NOT EXISTS qoder_accounts (
            email TEXT PRIMARY KEY, name TEXT DEFAULT '', department TEXT DEFAULT '', updated_at TEXT)""", commit=True)
        sqlite_mod.execute(db_path=_DB_FILE, sql="""CREATE TABLE IF NOT EXISTS qoder_cn_accounts (
            email TEXT PRIMARY KEY, name TEXT DEFAULT '', department TEXT DEFAULT '', updated_at TEXT)""", commit=True)
        sqlite_mod.execute(db_path=_DB_FILE, sql="""CREATE TABLE IF NOT EXISTS resource_packages (
            pkg_id TEXT PRIMARY KEY, name TEXT, source TEXT, status TEXT,
            activated_at TEXT, expires_at TEXT,
            limit_value REAL, used_value REAL, remaining_value REAL,
            unit TEXT, collected_at TEXT)""", commit=True)
        sqlite_mod.execute(db_path=_DB_FILE, sql="""CREATE TABLE IF NOT EXISTS resource_packages_cn (
            pkg_id TEXT PRIMARY KEY, name TEXT, source TEXT, status TEXT,
            activated_at TEXT, expires_at TEXT,
            limit_value REAL, used_value REAL, remaining_value REAL,
            unit TEXT, collected_at TEXT)""", commit=True)
        sqlite_mod.execute(db_path=_DB_FILE, sql="""CREATE TABLE IF NOT EXISTS ai_code_ranking (
            user_id TEXT, email TEXT, display_name TEXT,
            total_lines_added INTEGER, ai_lines_added INTEGER,
            ai_share_rate REAL, commit_count INTEGER, collected_at TEXT)""", commit=True)
        sqlite_mod.execute(db_path=_DB_FILE, sql="""CREATE TABLE IF NOT EXISTS ai_code_trend_cache (
            days INTEGER, primary_branch_only INTEGER,
            items_json TEXT, next_items_json TEXT, collected_at TEXT,
            PRIMARY KEY (days, primary_branch_only))""", commit=True)
        sqlite_mod.execute(db_path=_DB_FILE, sql="""CREATE TABLE IF NOT EXISTS ai_code_commits (
            commit_hash TEXT PRIMARY KEY,
            user_id TEXT, user_email TEXT, repo_name TEXT, branch_name TEXT,
            is_primary_branch INTEGER,
            total_lines_added INTEGER, total_lines_deleted INTEGER,
            ide_next_added INTEGER, ide_next_deleted INTEGER,
            plugin_next_added INTEGER, plugin_next_deleted INTEGER,
            ide_agent_added INTEGER, ide_agent_deleted INTEGER,
            plugin_agent_added INTEGER, plugin_agent_deleted INTEGER,
            cli_agent_added INTEGER, cli_agent_deleted INTEGER,
            ide_quest_added INTEGER, ide_quest_deleted INTEGER,
            ide_inline_chat_added INTEGER, ide_inline_chat_deleted INTEGER,
            jb_inline_chat_added INTEGER, jb_inline_chat_deleted INTEGER,
            non_ai_added INTEGER, non_ai_deleted INTEGER,
            message TEXT, commit_ts TEXT, created_at TEXT, collected_at TEXT)""", commit=True)

    def _init_preset_accounts():
        """预置 99 个 email 槽位到两张账号表。"""
        qoder_rows = [(f"ai{i:02d}@wsgjp.com", "", "", None) for i in range(1, 100)]
        cn_rows = [(f"ai_cn{i:02d}@wsgjp.com", "", "", None) for i in range(1, 100)]
        sqlite_mod.batch_insert(db_path=_DB_FILE,
            sql="INSERT OR IGNORE INTO qoder_accounts VALUES (?,?,?,?)", rows=qoder_rows)
        sqlite_mod.batch_insert(db_path=_DB_FILE,
            sql="INSERT OR IGNORE INTO qoder_cn_accounts VALUES (?,?,?,?)", rows=cn_rows)
        _log(f"预置账号: qoder {len(qoder_rows)} 条, qoder_cn {len(cn_rows)} 条")

    def _import_excel(excel_path, table_name):
        """首次导入 Excel 数据到账号表。"""
        if not excel_path or not os.path.exists(excel_path):
            _log(f"Excel 文件不存在: {excel_path}", "WARN")
            return
        # 检查是否已有非空 name（已导入过）
        check = sqlite_mod.query(db_path=_DB_FILE,
            sql=f"SELECT COUNT(*) AS cnt FROM {table_name} WHERE name != '' AND name IS NOT NULL")
        if check.get("data", {}).get("rows", [{}])[0].get("cnt", 0) > 0:
            _log(f"{table_name} 已有 Excel 数据，跳过导入")
            return
        try:
            from openpyxl import load_workbook
            wb = load_workbook(excel_path, read_only=True)
            ws = wb.worksheets[0]
            rows_data = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                email = row[0] if len(row) > 0 else None
                name = row[1] if len(row) > 1 else None
                dept = row[2] if len(row) > 2 else None
                if email and name:
                    rows_data.append((str(name).strip(), str(dept or "").strip(),
                                      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                      str(email).strip()))
            wb.close()
            if rows_data:
                sqlite_mod.batch_insert(db_path=_DB_FILE,
                    sql=f"UPDATE {table_name} SET name=?, department=?, updated_at=? WHERE email=?",
                    rows=rows_data)
                _log(f"Excel 导入 {table_name}: {len(rows_data)} 条记录")
        except Exception as e:
            _log(f"Excel 导入 {table_name} 失败: {e}", "WARN")

    # ── API 调用 ──

    def list_all_members(base_url, key, o_id):
        """分页获取组织下所有活跃成员。"""
        url = f"{base_url}/v1/organizations/{o_id}/members"
        headers = {"Authorization": f"Bearer {key}"}
        all_members = []
        next_token = ""
        while True:
            params = {"maxResults": 100}
            if next_token:
                params["nextToken"] = next_token
            result = http_mod.get(url, params=params, headers=headers)
            if not result.get("success"):
                _log(f"获取成员列表失败: {result.get('error', 'unknown')}", "ERROR")
                break
            data = result.get("json", {})
            all_members.extend(data.get("members", []))
            next_token = data.get("nextToken", "")
            if not next_token:
                break
        return all_members

    def get_member_quota(base_url, key, o_id, member_id):
        """查询指定成员的配额使用情况。"""
        url = f"{base_url}/v1/organizations/{o_id}/members/{member_id}/quota"
        headers = {"Authorization": f"Bearer {key}"}
        result = http_mod.get(url, headers=headers)
        if not result.get("success"):
            return None
        return result.get("json")

    def list_resource_packages(base_url, key, o_id):
        """分页获取组织共享资源包。"""
        url = f"{base_url}/v1/organizations/{o_id}/resource-packages"
        headers = {"Authorization": f"Bearer {key}"}
        all_pkgs = []
        next_token = ""
        while True:
            params = {"maxResults": 100}
            if next_token:
                params["nextToken"] = next_token
            result = http_mod.get(url, params=params, headers=headers)
            if not result.get("success"):
                _log(f"获取资源包失败: {result.get('error', 'unknown')}", "WARN")
                break
            data = result.get("json", {})
            all_pkgs.extend(data.get("resourcePackages", []))
            next_token = data.get("nextToken", "")
            if not next_token:
                break
        return all_pkgs

    def is_quota_exhausted(quota):
        if quota.get("status") == "restricted":
            return True
        total = quota.get("totalQuota", {}).get("quotaSummary", {})
        used = total.get("usedValue", 0)
        limit = total.get("limitValue", 0)
        return limit > 0 and used >= limit

    def format_alarm_message(member, quota):
        total = quota.get("totalQuota", {}).get("quotaSummary", {})
        used = total.get("usedValue", 0)
        limit = total.get("limitValue", 0)
        unit = total.get("unit", "credits")
        usage_pct = (used / limit * 100) if limit > 0 else 0
        return (
            "Qoder 成员配额耗尽告警\n\n"
            "| 字段 | 值 |\n| --- | --- |\n"
            f"| 成员 | {member.get('name', 'N/A')} |\n"
            f"| 邮箱 | {member.get('email', 'N/A')} |\n"
            f"| 状态 | {quota.get('status', 'unknown')} |\n"
            f"| 已使用 | {used:.2f} {unit} |\n"
            f"| 配额上限 | {limit:.2f} {unit} |\n"
            f"| 使用率 | {usage_pct:.1f}% |\n"
            f"| 检测时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |\n"
        )

    # ── 告警去重 ──

    def _load_alarm_dedup():
        try:
            if os.path.exists(_ALARM_DEDUP_FILE):
                with open(_ALARM_DEDUP_FILE, "r") as f:
                    return json.load(f)
        except Exception as e:
            _log(f"加载告警去重文件失败: {e}", "WARN")
        return {}

    def _save_alarm_dedup(records):
        try:
            with open(_ALARM_DEDUP_FILE, "w") as f:
                json.dump(records, f, indent=2)
        except Exception as e:
            _log(f"保存告警去重文件失败: {e}", "WARN")

    def _already_alerted(member_id):
        cutoff = datetime.now() - timedelta(days=alarm_dedup_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        records = _load_alarm_dedup()
        alerted_date = records.get(member_id)
        return alerted_date is not None and alerted_date >= cutoff_str

    def _mark_alerted(member_id):
        today = datetime.now().strftime("%Y-%m-%d")
        records = _load_alarm_dedup()
        cutoff = datetime.now() - timedelta(days=alarm_dedup_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        records = {k: v for k, v in records.items() if v >= cutoff_str}
        records[member_id] = today
        _save_alarm_dedup(records)

    def send_alarm(member, quota):
        member_id = member.get("id", "")
        if _already_alerted(member_id):
            _log(f"成员 {member.get('name', member_id)} 在去重窗口内已告警，跳过")
            return
        if not omc_mod:
            _log("ss-omc-sdk 未启用，跳过告警发送", "WARN")
            return
        alert_cfg = config.get("alert", {})
        msg = format_alarm_message(member, quota)
        result = omc_mod.alarm_push(
            from_value=alert_cfg.get("from", "Qoder账号管理"),
            name=alert_cfg.get("name", "Qoder配额耗尽告警"),
            alarm_type="alert", msg=msg)
        if result.get("success"):
            _log(f"告警发送成功: {member.get('name', member_id)}")
            _mark_alerted(member_id)
        else:
            _log(f"告警发送失败 ({member.get('name', member_id)}): {result.get('error', 'unknown')}", "ERROR")

    def send_heartbeat():
        if not omc_mod:
            return
        alert_cfg = config.get("alert", {})
        result = omc_mod.heartbeat_push(
            from_value=alert_cfg.get("from", "Qoder账号管理"),
            name=alert_cfg.get("name", "Qoder账号管理"))
        if result.get("success"):
            _log("心跳发送成功")
        else:
            _log(f"心跳发送失败: {result.get('error', 'unknown')}", "ERROR")

    # ── 采集任务 ──

    def do_check():
        """执行一轮完整的用量检查（两套配置）。"""
        _log("开始检查成员用量")
        collected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Qoder 国际版
        qoder_quota_data = _check_org_members(api_base_url, api_key, org_id)
        _collect_resource_packages(api_base_url, api_key, org_id, "resource_packages")

        # Qoder CN
        cn_quota_data = _check_org_members(cn_base_url, cn_api_key, cn_org_id) if cn_api_key and cn_org_id else []
        if cn_api_key and cn_org_id:
            _collect_resource_packages(cn_base_url, cn_api_key, cn_org_id, "resource_packages_cn")

        # 更新 API 成员映射（用于差异比对）
        api_members = list_all_members(api_base_url, api_key, org_id)
        with _data_lock:
            _shared_data["api_members"] = {m.get("email", ""): m.get("name", "") for m in api_members}
        if cn_api_key and cn_org_id:
            api_members_cn = list_all_members(cn_base_url, cn_api_key, cn_org_id)
            with _data_lock:
                _shared_data["api_members_cn"] = {m.get("email", ""): m.get("name", "") for m in api_members_cn}

        # 持久化 Qoder 用量到 SQLite
        with _data_lock:
            _shared_data["members"] = qoder_quota_data
            _shared_data["last_updated"] = collected_at
            _shared_data["total_members"] = len(qoder_quota_data)
            _shared_data["exhausted_count"] = sum(1 for m in qoder_quota_data if m["status"] == "restricted")

        try:
            sqlite_mod.execute(db_path=_DB_FILE, sql="DELETE FROM quota_members", commit=True)
            rows = [(m["name"], m["email"], m["usedValue"], m["limitValue"],
                     m["unit"], m["status"], m["nextResetAt"], collected_at) for m in qoder_quota_data]
            sqlite_mod.batch_insert(db_path=_DB_FILE,
                sql="INSERT OR REPLACE INTO quota_members VALUES (?,?,?,?,?,?,?,?)", rows=rows)
            _log(f"用量数据已写入 SQLite: {len(rows)} 条记录")
        except Exception as e:
            _log(f"写入 SQLite 失败: {e}", "WARN")

        _log(f"本轮检查完成: Qoder {len(qoder_quota_data)} 个成员, Qoder CN {len(cn_quota_data)} 个成员")

        # 同步采集 AI 代码统计
        try:
            do_ai_code_check()
        except Exception as e:
            _log(f"AI 代码统计采集异常: {e}", "ERROR")

    def _check_org_members(base_url, key, o_id):
        """检查单个组织的成员配额。"""
        members = list_all_members(base_url, key, o_id)
        if not members:
            _log(f"组织 {o_id} 未获取到任何成员", "WARN")
            return []
        _log(f"组织 {o_id}: 共 {len(members)} 个成员，开始检查配额")
        quota_data = []
        for member in members:
            member_id = member.get("id")
            if not member_id:
                continue
            quota = get_member_quota(base_url, key, o_id, member_id)
            if quota is None:
                continue
            total = quota.get("totalQuota", {}).get("quotaSummary", {})
            quota_data.append({
                "name": member.get("name", ""),
                "email": member.get("email", ""),
                "usedValue": total.get("usedValue", 0),
                "limitValue": total.get("limitValue", 0),
                "unit": total.get("unit", "credits"),
                "status": quota.get("status", "unknown"),
                "nextResetAt": quota.get("nextResetAt", ""),
            })
            if is_quota_exhausted(quota):
                send_alarm(member, quota)
            time.sleep(rate_limit_delay)
        return quota_data

    def _collect_resource_packages(base_url, key, o_id, table_name):
        """采集共享资源包并写入 SQLite。"""
        pkgs = list_resource_packages(base_url, key, o_id)
        if not pkgs:
            _log(f"组织 {o_id} 无资源包数据")
            return
        collected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            sqlite_mod.execute(db_path=_DB_FILE, sql=f"DELETE FROM {table_name}", commit=True)
            rows = [(p.get("id", ""), p.get("name", ""), p.get("source", ""), p.get("status", ""),
                     p.get("activatedAt", ""), p.get("expiresAt", ""),
                     p.get("limitValue", 0), p.get("usedValue", 0), p.get("remainingValue", 0),
                     p.get("unit", "credits"), collected_at) for p in pkgs]
            sqlite_mod.batch_insert(db_path=_DB_FILE,
                sql=f"INSERT OR REPLACE INTO {table_name} VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows=rows)
            _log(f"资源包写入 {table_name}: {len(rows)} 条")
        except Exception as e:
            _log(f"资源包写入失败: {e}", "WARN")

    def do_ai_code_check():
        """采集 AI 代码排名和 commit 明细，存入 SQLite。"""
        _log("开始采集 AI 代码统计")
        headers = {"Authorization": f"Bearer {api_key}"}
        now_dt = datetime.now()
        end_date = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        start_date = (now_dt - timedelta(days=ai_code_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        collected_at = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        # API 1: 成员排名
        ranking_url = f"{api_base_url}/v1/organizations/{org_id}/ai-code/stats/member-ranking"
        ranking_result = http_mod.get(ranking_url, params={
            "start_date": start_date, "end_date": end_date, "limit": 200}, headers=headers)
        if ranking_result.get("success"):
            items = ranking_result.get("json", {}).get("items", [])
            sqlite_mod.execute(db_path=_DB_FILE, sql="DELETE FROM ai_code_ranking", commit=True)
            for item in items:
                sqlite_mod.execute(db_path=_DB_FILE,
                    sql="INSERT INTO ai_code_ranking VALUES (?,?,?,?,?,?,?,?)",
                    params=(item.get("userId", ""), item.get("email", ""), item.get("displayName", ""),
                            item.get("totalLinesAdded", 0), item.get("aiLinesAdded", 0),
                            item.get("aiShareRate", 0), item.get("commitCount", 0), collected_at), commit=True)
            _log(f"AI 代码排名: {len(items)} 个成员")
        else:
            _log(f"获取 AI 代码排名失败: {ranking_result.get('error', 'unknown')}", "WARN")

        # API 2: Commit 明细（分页）
        commits_url = f"{api_base_url}/v1/organizations/{org_id}/ai-code-tracking/commits"
        page = 1
        total_pages = 1
        total_commits = 0
        while page <= total_pages:
            result = http_mod.get(commits_url, params={
                "startDate": start_date, "endDate": end_date,
                "page": page, "pageSize": 200}, headers=headers)
            if not result.get("success"):
                break
            data = result.get("json", {}).get("data", {})
            items = data.get("items", [])
            pagination = data.get("pagination", {})
            total_pages = pagination.get("totalPages", 1)
            for item in items:
                sqlite_mod.execute(db_path=_DB_FILE,
                    sql="INSERT OR REPLACE INTO ai_code_commits VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    params=(item.get("commitHash", ""), item.get("userId", ""), item.get("userEmail", ""),
                            item.get("repoName", ""), item.get("branchName", ""), int(item.get("isPrimaryBranch", False)),
                            item.get("totalLinesAdded", 0), item.get("totalLinesDeleted", 0),
                            item.get("ideNextLinesAdded", 0), item.get("ideNextLinesDeleted", 0),
                            item.get("pluginNextLinesAdded", 0), item.get("pluginNextLinesDeleted", 0),
                            item.get("ideAgentLinesAdded", 0), item.get("ideAgentLinesDeleted", 0),
                            item.get("pluginAgentLinesAdded", 0), item.get("pluginAgentLinesDeleted", 0),
                            item.get("cliAgentLinesAdded", 0), item.get("cliAgentLinesDeleted", 0),
                            item.get("ideQuestLinesAdded", 0), item.get("ideQuestLinesDeleted", 0),
                            item.get("ideInlineChatLinesAdded", 0), item.get("ideInlineChatLinesDeleted", 0),
                            item.get("jbInlineChatLinesAdded", 0), item.get("jbInlineChatLinesDeleted", 0),
                            item.get("nonAiLinesAdded", 0), item.get("nonAiLinesDeleted", 0),
                            item.get("message", ""), item.get("commitTs", ""), item.get("createdAt", ""),
                            collected_at), commit=True)
            total_commits += len(items)
            page += 1
            time.sleep(rate_limit_delay)

        with _data_lock:
            _shared_data["ai_code_last_updated"] = collected_at
        _log(f"AI 代码统计采集完成: {total_commits} 条 commit 记录")

    # ── 从 SQLite 加载缓存数据 ──
    _init_db()
    _init_preset_accounts()
    _import_excel(excel_qoder_path, "qoder_accounts")
    _import_excel(excel_qoder_cn_path, "qoder_cn_accounts")

    try:
        result = sqlite_mod.query(db_path=_DB_FILE,
            sql="SELECT name, email, used_value, limit_value, unit, status, next_reset_at, collected_at FROM quota_members")
        cached_members = []
        for r in result.get("data", {}).get("rows", []):
            cached_members.append({"name": r.get("name", ""), "email": r.get("email", ""),
                "usedValue": r.get("used_value", 0), "limitValue": r.get("limit_value", 0),
                "unit": r.get("unit", "credits"), "status": r.get("status", "unknown"),
                "nextResetAt": r.get("next_reset_at", "")})
        if cached_members:
            with _data_lock:
                _shared_data["members"] = cached_members
                _shared_data["total_members"] = len(cached_members)
                _shared_data["exhausted_count"] = sum(1 for m in cached_members if m["status"] == "restricted")
                _shared_data["last_updated"] = cached_members[0].get("collected_at")
            _log(f"已从 SQLite 加载缓存: {len(cached_members)} 个成员")
    except Exception as e:
        _log(f"加载 SQLite 缓存失败: {e}", "WARN")

    try:
        ai_result = sqlite_mod.query_one(db_path=_DB_FILE,
            sql="SELECT collected_at FROM ai_code_ranking ORDER BY collected_at DESC LIMIT 1")
        if ai_result.get("success") and ai_result.get("data", {}).get("row"):
            with _data_lock:
                _shared_data["ai_code_last_updated"] = ai_result["data"]["row"]["collected_at"]
            _log(f"已恢复 AI 代码统计时间戳: {_shared_data['ai_code_last_updated']}")
    except Exception as e:
        _log(f"恢复 AI 代码统计时间戳失败: {e}", "WARN")

    # ── 注册 Web 路由 ──
    _log("Qoder 账号管理服务启动")

    web_mod = modules.get("web-service")
    if web_mod:
        web_mod.register_page("/", _DASHBOARD_HTML)

        def _get_accounts_snapshot(request_info):
            """返回两个组织的账号列表 + 资源包 + 差异标记。"""
            with _data_lock:
                api_members = dict(_shared_data.get("api_members", {}))
                api_members_cn = dict(_shared_data.get("api_members_cn", {}))
            return {"status_code": 200, "content_type": "application/json; charset=utf-8",
                    "body": json.dumps({
                        "qoder": _build_account_data("qoder_accounts", api_members, "resource_packages"),
                        "qoder_cn": _build_account_data("qoder_cn_accounts", api_members_cn, "resource_packages_cn"),
                    }, ensure_ascii=False)}

        def _build_account_data(table_name, api_map, pkg_table):
            """构建单个组织的账号数据。"""
            acc_result = sqlite_mod.query(db_path=_DB_FILE,
                sql=f"SELECT email, name, department FROM {table_name} ORDER BY email")
            members = []
            for r in acc_result.get("data", {}).get("rows", []):
                email = r.get("email", "")
                name = r.get("name", "")
                diff = None
                if name and email in api_map:
                    if api_map[email] and api_map[email] != name:
                        diff = "名称不一致"
                elif name and email not in api_map:
                    diff = "API无此账号"
                members.append({"email": email, "name": name,
                                "department": r.get("department", ""), "diff": diff})
            pkg_result = sqlite_mod.query(db_path=_DB_FILE,
                sql=f"SELECT name, source, status, limit_value, used_value, remaining_value, expires_at FROM {pkg_table}")
            packages = [_pkg_row_to_dict(r) for r in pkg_result.get("data", {}).get("rows", [])]
            summary = _calc_pkg_summary(packages)
            summary["last_updated"] = _shared_data.get("last_updated")
            return {"members": members, "packages": packages, "summary": summary}

        def _pkg_row_to_dict(r):
            limit = r.get("limit_value", 0) or 0
            used = r.get("used_value", 0) or 0
            return {"name": r.get("name", ""), "source": r.get("source", ""), "status": r.get("status", ""),
                    "limitValue": limit, "usedValue": used,
                    "remainingValue": r.get("remaining_value", 0) or 0,
                    "expiresAt": r.get("expires_at", ""),
                    "usageRate": round(used / limit * 100, 1) if limit > 0 else 0}

        def _calc_pkg_summary(packages):
            total_limit = sum(p["limitValue"] for p in packages)
            total_used = sum(p["usedValue"] for p in packages)
            return {"packageTotalLimit": total_limit, "packageTotalUsed": total_used,
                    "packageUsageRate": round(total_used / total_limit * 100, 1) if total_limit > 0 else 0}

        web_mod.register_handler("/api/accounts", "POST", _get_accounts_snapshot)

        def handle_usage(request_info):
            """返回两个组织的完整用量数据。"""
            with _data_lock:
                snapshot = dict(_shared_data)
            qoder_members = snapshot.get("members", [])
            # Qoder CN 用量（从 API 获取，不在 SQLite 持久化）
            cn_members = []
            if cn_api_key and cn_org_id:
                cn_api_members = list_all_members(cn_base_url, cn_api_key, cn_org_id)
                for m in cn_api_members:
                    mid = m.get("id")
                    if not mid:
                        continue
                    q = get_member_quota(cn_base_url, cn_api_key, cn_org_id, mid)
                    if q:
                        total = q.get("totalQuota", {}).get("quotaSummary", {})
                        cn_members.append({"email": m.get("email", ""), "name": m.get("name", ""),
                            "usedValue": total.get("usedValue", 0), "limitValue": total.get("limitValue", 0),
                            "status": q.get("status", "unknown")})
                    time.sleep(rate_limit_delay)
            q_pkgs = _get_pkgs_from_db("resource_packages")
            cn_pkgs = _get_pkgs_from_db("resource_packages_cn")
            return {"status_code": 200, "content_type": "application/json; charset=utf-8",
                    "body": json.dumps({
                        "qoder": {"members": qoder_members, "packages": q_pkgs,
                                  "summary": _calc_pkg_summary(q_pkgs)},
                        "qoder_cn": {"members": cn_members, "packages": cn_pkgs,
                                     "summary": _calc_pkg_summary(cn_pkgs)},
                        "last_updated": snapshot.get("last_updated"),
                    }, ensure_ascii=False)}

        def _get_pkgs_from_db(table_name):
            result = sqlite_mod.query(db_path=_DB_FILE,
                sql=f"SELECT name, source, status, limit_value, used_value, remaining_value, expires_at FROM {table_name}")
            return [_pkg_row_to_dict(r) for r in result.get("data", {}).get("rows", [])]

        web_mod.register_handler("/api/usage", "POST", handle_usage)

        def handle_allocate(request_info):
            """对外账号分配 API。"""
            body = request_info.get("body", "{}")
            try:
                data = json.loads(body) if isinstance(body, str) else body
            except json.JSONDecodeError:
                return _alloc_response(False, 0, "Invalid JSON", None)
            oa = data.get("oaApproval", {})
            app_id = oa.get("appId")
            submitter_name = oa.get("submitterName", "")
            dept_list = data.get("departmentNameList", [])

            if app_id == app_id_cn:
                table = "qoder_cn_accounts"
            elif app_id == app_id_global:
                table = "qoder_accounts"
            else:
                return _alloc_response(False, 0, f"不支持的 appId: {app_id}", None)

            if not submitter_name:
                return _alloc_response(False, 0, "submitterName 不能为空", None)

            # 1. 按 name 匹配
            result = sqlite_mod.query_one(db_path=_DB_FILE,
                sql=f"SELECT email FROM {table} WHERE name = ?", params=(submitter_name,))
            if result.get("data", {}).get("row"):
                email = result["data"]["row"]["email"]
                _log(f"账号分配: appId={app_id}, name={submitter_name} → {email} (已有)")
                return _alloc_response(True, 1, None, email)

            # 2. 取 name 为空且 email 最小的
            result = sqlite_mod.query_one(db_path=_DB_FILE,
                sql=f"SELECT email FROM {table} WHERE (name = '' OR name IS NULL) ORDER BY email ASC LIMIT 1")
            if result.get("data", {}).get("row"):
                email = result["data"]["row"]["email"]
                dept_str = ";".join(dept_list)
                sqlite_mod.execute(db_path=_DB_FILE,
                    sql=f"UPDATE {table} SET name = ?, department = ?, updated_at = ? WHERE email = ?",
                    params=(submitter_name, dept_str,
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), email), commit=True)
                _log(f"账号分配: appId={app_id}, name={submitter_name} → {email} (新分配)")
                return _alloc_response(True, 1, None, email)

            return _alloc_response(False, 0, "无可用账号", None)

        def _alloc_response(success, code, message, email):
            data = [{"fieldName": "email", "fieldValue": email}] if email else []
            return {"status_code": 200, "content_type": "application/json; charset=utf-8",
                    "body": json.dumps({"message": message, "code": code, "data": data,
                        "success": success, "version": "1.0.3",
                        "timestamp": int(time.time() * 1000)}, ensure_ascii=False)}

        web_mod.register_handler("/api/allocate", "POST", handle_allocate)

        # AI 代码统计 API
        def handle_ai_code_stats(request_info):
            if not os.path.exists(_DB_FILE):
                return {"status_code": 200, "content_type": "application/json; charset=utf-8",
                        "body": json.dumps({"members": [], "last_updated": None}, ensure_ascii=False)}
            query = request_info.get("query", {})
            primary_only = query.get("primary_branch_only", ["false"])[0] == "true" if query.get("primary_branch_only") else False
            days = int(query.get("days", [str(ai_code_days)])[0]) if query.get("days") else ai_code_days
            cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
            conditions = [f"commit_ts >= '{cutoff_date}'"]
            if primary_only:
                conditions.append("is_primary_branch = 1")
            where_clause = "WHERE " + " AND ".join(conditions)
            result = sqlite_mod.query(db_path=_DB_FILE, sql=f"""SELECT
                COALESCE(NULLIF(user_email, ''), user_id) AS member_key, user_email AS email,
                COUNT(*) AS commit_count, SUM(total_lines_added) AS total_added,
                SUM(total_lines_deleted) AS total_deleted,
                SUM(ide_next_added + plugin_next_added + ide_agent_added + plugin_agent_added +
                    cli_agent_added + ide_quest_added + ide_inline_chat_added + jb_inline_chat_added) AS ai_added,
                SUM(ide_next_deleted + plugin_next_deleted + ide_agent_deleted + plugin_agent_deleted +
                    cli_agent_deleted + ide_quest_deleted + ide_inline_chat_deleted + jb_inline_chat_deleted) AS ai_deleted,
                SUM(non_ai_added) AS non_ai_added, SUM(non_ai_deleted) AS non_ai_deleted,
                SUM(ide_next_added) AS ide_next_added, SUM(ide_agent_added) AS ide_agent_added,
                SUM(ide_quest_added) AS ide_quest_added, SUM(plugin_next_added) AS plugin_next_added,
                SUM(plugin_agent_added) AS plugin_agent_added, SUM(jb_inline_chat_added) AS jb_inline_chat_added,
                SUM(cli_agent_added) AS cli_agent_added
                FROM ai_code_commits {where_clause}
                GROUP BY COALESCE(NULLIF(user_email, ''), user_id)""")
            rows = result.get("data", {}).get("rows", [])
            rk_result = sqlite_mod.query(db_path=_DB_FILE,
                sql="SELECT email, display_name, ai_share_rate FROM ai_code_ranking")
            ranking_map = {r["email"]: r for r in rk_result.get("data", {}).get("rows", [])}
            members = []
            for r in rows:
                email = r.get("email") or ""
                rk = ranking_map.get(email, {})
                name = rk.get("display_name") if rk else (email or r.get("member_key"))
                ai_added = r.get("ai_added") or 0
                non_ai_added = r.get("non_ai_added") or 0
                ai_rate = (ai_added / (ai_added + non_ai_added) * 100) if (ai_added + non_ai_added) > 0 else 0
                members.append({"name": name, "email": email, "commit_count": r.get("commit_count"),
                    "total_added": r.get("total_added"), "total_deleted": r.get("total_deleted"),
                    "ai_added": ai_added, "ai_deleted": r.get("ai_deleted"),
                    "non_ai_added": non_ai_added, "non_ai_deleted": r.get("non_ai_deleted"),
                    "ide_next_added": r.get("ide_next_added") or 0, "ide_agent_added": r.get("ide_agent_added") or 0,
                    "ide_quest_added": r.get("ide_quest_added") or 0, "plugin_next_added": r.get("plugin_next_added") or 0,
                    "plugin_agent_added": r.get("plugin_agent_added") or 0, "jb_inline_chat_added": r.get("jb_inline_chat_added") or 0,
                    "cli_agent_added": r.get("cli_agent_added") or 0, "ai_rate": round(ai_rate, 1)})
            return {"status_code": 200, "content_type": "application/json; charset=utf-8",
                    "body": json.dumps({"members": members,
                        "last_updated": _shared_data.get("ai_code_last_updated")}, ensure_ascii=False)}

        web_mod.register_handler("/api/ai-code", "POST", handle_ai_code_stats)

        def handle_ai_code_trend(request_info):
            query = request_info.get("query", {})
            days = int(query.get("days", ["7"])[0]) if query.get("days") else 7
            primary_only = query.get("primary_branch_only", ["false"])[0] == "true" if query.get("primary_branch_only") else False
            primary_int = 1 if primary_only else 0
            cache_result = sqlite_mod.query_one(db_path=_DB_FILE,
                sql="SELECT items_json, next_items_json, collected_at FROM ai_code_trend_cache WHERE days = ? AND primary_branch_only = ?",
                params=(days, primary_int))
            if cache_result.get("success"):
                row = cache_result.get("data", {}).get("row")
                if row:
                    try:
                        cached_time = datetime.strptime(row.get("collected_at", ""), "%Y-%m-%d %H:%M:%S")
                        if (datetime.now() - cached_time).total_seconds() < 3600:
                            return {"status_code": 200, "content_type": "application/json; charset=utf-8",
                                    "body": json.dumps({"items": json.loads(row.get("items_json", "[]")),
                                        "nextItems": json.loads(row.get("next_items_json", "[]"))}, ensure_ascii=False)}
                    except Exception:
                        pass
            now_dt = datetime.now()
            end_date = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            start_date = (now_dt - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            url = f"{api_base_url}/v1/organizations/{org_id}/ai-code/stats/daily-trend"
            result = http_mod.get(url, params={"start_date": start_date, "end_date": end_date,
                "primary_branch_only": "true" if primary_only else "false"},
                headers={"Authorization": f"Bearer {api_key}"})
            if not result.get("success"):
                return {"status_code": 502, "content_type": "application/json; charset=utf-8",
                        "body": json.dumps({"error": result.get("error", "API error")}, ensure_ascii=False)}
            json_data = result.get("json", {})
            items = json_data.get("items", [])
            next_items = json_data.get("nextItems", [])
            collected_at = now_dt.strftime("%Y-%m-%d %H:%M:%S")
            try:
                sqlite_mod.execute(db_path=_DB_FILE,
                    sql="INSERT OR REPLACE INTO ai_code_trend_cache (days, primary_branch_only, items_json, next_items_json, collected_at) VALUES (?,?,?,?,?)",
                    params=(days, primary_int, json.dumps(items, ensure_ascii=False),
                            json.dumps(next_items, ensure_ascii=False), collected_at), commit=True)
            except Exception as e:
                _log(f"趋势数据缓存写入失败: {e}", "WARN")
            return {"status_code": 200, "content_type": "application/json; charset=utf-8",
                    "body": json.dumps({"items": items, "nextItems": next_items}, ensure_ascii=False)}

        web_mod.register_handler("/api/ai-code-trend", "POST", handle_ai_code_trend)

        port_info = web_mod.get_port()
        if port_info.get("success"):
            _log(f"Web 仪表盘: {port_info['data']['base_url']}")
    else:
        _log("web-service 模块未启用，Web 仪表盘不可用", "WARN")

    # ── 主循环 ──
    now = time.time()
    next_check_at = _next_run_timestamp(schedule_cfg, after=now)
    next_heartbeat_at = now
    _log(f"下次采集将在 {datetime.fromtimestamp(next_check_at).isoformat()} 执行")

    while True:
        now = time.time()
        if now >= next_check_at:
            try:
                do_check()
            except Exception as e:
                _log(f"检查任务异常: {e}", "ERROR")
            next_check_at = _next_run_timestamp(schedule_cfg)
            _log(f"下次采集将在 {datetime.fromtimestamp(next_check_at).isoformat()} 执行")
        if now >= next_heartbeat_at:
            try:
                send_heartbeat()
            except Exception as e:
                _log(f"心跳发送异常: {e}", "ERROR")
            next_heartbeat_at = time.time() + heartbeat_interval_sec
        time.sleep(POLL_INTERVAL)


def on_config_reload(new_config):
    logger.info("配置已重新加载")


def on_shutdown():
    logger.info("Qoder 账号管理服务正在关闭")
