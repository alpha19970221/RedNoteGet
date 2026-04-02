/* ════════════════════════════════════════════════
   RedNote Agent — Frontend Logic
   SSE 实时进度 + 报告渲染 + 历史管理
   ════════════════════════════════════════════════ */

// ── 状态变量 ──────────────────────────────────────
let currentTaskId = null;
let currentEventSource = null;
let currentQuery = '';
let currentReportContent = '';
let currentReportFilename = '';

// 节点执行顺序（用于自动推进前置节点状态）
const NODE_ORDER = [
  'KeywordGenerator',
  'PostSearcher',
  'CommentExtractor',
  'PostSummarizer',
  'NeedsAnalyzer',
  'ReportGenerator'
];

// ── DOM 引用 ──────────────────────────────────────
const $ = id => document.getElementById(id);

// ── 页面初始化 ────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadReports();
  loadCookiesFromStorage();

  // 侧边栏开关
  $('sidebar-toggle').addEventListener('click', () => {
    document.querySelector('.sidebar').classList.toggle('collapsed');
  });

  // Enter 键触发搜索
  $('query-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') startResearch();
  });

  // 点击设置面板外部关闭
  document.addEventListener('click', e => {
    const panel = $('settings-panel');
    const btn = $('settings-btn');
    if (panel && !panel.classList.contains('hidden') &&
        !panel.contains(e.target) && !btn.contains(e.target)) {
      panel.classList.add('hidden');
    }
  });
});

// ── 快速选择标签 ──────────────────────────────────
function setQuery(text) {
  $('query-input').value = text;
  $('query-input').focus();
}

// ── Cookie 管理 ───────────────────────────────────
const COOKIE_KEY = 'xhs_cookies';

function loadCookiesFromStorage() {
  const saved = localStorage.getItem(COOKIE_KEY) || '';
  const input = $('cookie-input');
  if (input) input.value = saved;
  updateCookieStatus(saved);
}

function saveCookies() {
  const val = ($('cookie-input').value || '').trim();
  localStorage.setItem(COOKIE_KEY, val);
  updateCookieStatus(val);
  showToast(val ? 'Cookie 已保存' : 'Cookie 已清空', val ? 'success' : 'info');
  $('settings-panel').classList.add('hidden');
}

function clearCookies() {
  localStorage.removeItem(COOKIE_KEY);
  $('cookie-input').value = '';
  updateCookieStatus('');
  showToast('Cookie 已清除', 'info');
}

function updateCookieStatus(val) {
  const el = $('cookie-status');
  if (!el) return;
  if (val) {
    el.textContent = 'Cookie 已配置';
    el.className = 'cookie-status ok';
  } else {
    el.textContent = '未配置 Cookie';
    el.className = 'cookie-status warn';
  }
}

function toggleSettings() {
  $('settings-panel').classList.toggle('hidden');
}

// ── 帖子数量调节 ──────────────────────────────────
function adjustPostCount(delta) {
  const input = $('post-count');
  const val = Math.min(20, Math.max(1, parseInt(input.value || 3) + delta));
  input.value = val;
}

// ── 最低点赞数调节 ────────────────────────────────
function adjustMinLikes(delta) {
  const input = $('min-likes');
  const val = Math.max(0, parseInt(input.value || 0) + delta);
  input.value = val;
  const hint = $('min-likes-hint');
  if (hint) hint.textContent = val === 0 ? '不过滤' : `≥ ${val.toLocaleString()}`;
}

// ── 开始调研 ──────────────────────────────────────
async function startResearch() {
  const query = $('query-input').value.trim();
  if (!query) {
    showToast('请输入调研话题', 'error');
    $('query-input').focus();
    return;
  }
  if (currentEventSource) {
    showToast('当前有任务正在进行，请等待或先取消', 'error');
    return;
  }

  currentQuery = query;
  currentReportContent = '';
  currentReportFilename = '';

  // 切换 UI 到调研面板
  switchPanel('research');
  $('research-query-text').textContent = query;
  setTopbarStatus('running', '调研中');
  resetTimeline();
  clearLog();
  appendLog(`🚀 开启「${query}」市场调研...`, 'event');

  // 禁用按钮
  $('start-btn').disabled = true;

  try {
    // 1. 创建任务
    const postCount = parseInt($('post-count').value || 3);
    const minLikes = parseInt($('min-likes').value || 0);
    const cookies = localStorage.getItem(COOKIE_KEY) || '';
    if (!cookies) {
      showToast('请先在右上角设置中填写小红书 Cookie', 'error');
      $('start-btn').disabled = false;
      switchPanel('hero');
      return;
    }
    const res = await fetch('/api/research', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, cookies, post_count: postCount, min_likes: minLikes })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || '创建任务失败');
    }
    const data = await res.json();
    currentTaskId = data.task_id;

    // 2. 建立 SSE 连接
    openSSEStream(currentTaskId);

  } catch (err) {
    showToast(`启动失败：${err.message}`, 'error');
    setTopbarStatus('error', '启动失败');
    $('start-btn').disabled = false;
    switchPanel('hero');
  }
}

// ── SSE 连接 ──────────────────────────────────────
function openSSEStream(taskId) {
  const es = new EventSource(`/api/research/${taskId}/stream`);
  currentEventSource = es;

  es.onmessage = event => {
    try {
      const msg = JSON.parse(event.data);
      handleSSEEvent(msg);
    } catch (e) {
      console.error('SSE parse error:', e);
    }
  };

  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) return;
    appendLog('⚠️ SSE 连接中断，请检查服务器状态', 'error');
    closeSSE();
  };
}

// ── 处理 SSE 事件 ─────────────────────────────────
function handleSSEEvent(msg) {
  const { type, data } = msg;

  switch (type) {

    case 'node_start': {
      const { node, message, current, total } = data;
      // 自动将该节点之前的所有节点标记为 done
      markPreviousNodesDone(node);
      setNodeStatus(node, 'running');
      setNodeMessage(node, message);
      appendLog(`▶ [${nodeLabel(node)}] ${message}`, 'event');
      break;
    }

    case 'node_done': {
      const { node, message, keywords, posts, needs_count } = data;
      setNodeStatus(node, 'done');
      setNodeMessage(node, message);
      appendLog(`✓ [${nodeLabel(node)}] ${message}`, 'success');

      // 渲染关键词标签
      if (keywords && keywords.length) {
        const container = $(`result-${node}`);
        if (container) {
          container.innerHTML = keywords.map(k =>
            `<span class="result-tag">${escapeHtml(k)}</span>`
          ).join('');
        }
      }

      // 渲染帖子列表
      if (posts && posts.length) {
        const container = $(`result-${node}`);
        if (container) {
          container.innerHTML = posts.map(p =>
            `<span class="result-tag" title="${escapeHtml(p.title)}">👍${p.likes} ${escapeHtml(p.title.slice(0, 10))}...</span>`
          ).join('');
        }
      }
      break;
    }

    case 'log': {
      appendLog(data.message, 'info');
      break;
    }

    case 'done': {
      const { report, filename } = data;
      currentReportContent = report;
      currentReportFilename = filename;

      // 将所有节点标记为完成
      NODE_ORDER.forEach(n => setNodeStatus(n, 'done'));
      appendLog('🎉 调研完成！报告已生成并保存', 'success');
      setTopbarStatus('done', '完成');

      // 延迟 0.8s 后切换到报告面板
      setTimeout(() => {
        showReport(query = currentQuery, report, filename);
        loadReports();  // 刷新侧边栏列表
      }, 800);

      closeSSE();
      $('start-btn').disabled = false;
      break;
    }

    case 'error': {
      appendLog(`❌ 错误：${data.message}`, 'error');
      setTopbarStatus('error', '执行出错');
      showToast(`调研失败：${data.message}`, 'error');
      closeSSE();
      $('start-btn').disabled = false;
      break;
    }

    case 'end': {
      // 哨兵，关闭流
      closeSSE();
      break;
    }
  }
}

// ── 取消调研 ──────────────────────────────────────
function cancelResearch() {
  closeSSE();
  setTopbarStatus('idle', '就绪');
  switchPanel('hero');
  $('start-btn').disabled = false;
  showToast('已取消调研', 'info');
}

// ── 新建调研 ──────────────────────────────────────
function newResearch() {
  switchPanel('hero');
  setTopbarStatus('idle', '就绪');
  $('query-input').value = '';
  $('query-input').focus();
}

// ── 下载报告 ──────────────────────────────────────
function downloadReport() {
  if (!currentReportContent) return;
  const blob = new Blob([currentReportContent], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = currentReportFilename || 'report.md';
  a.click();
  URL.revokeObjectURL(url);
}

// ── 加载历史报告 ──────────────────────────────────
async function loadReports() {
  try {
    const res = await fetch('/api/reports');
    const data = await res.json();
    renderReportList(data.reports || []);
  } catch (e) {
    console.error('加载报告列表失败:', e);
  }
}

function renderReportList(reports) {
  const el = $('report-list');
  if (!reports.length) {
    el.innerHTML = '<div class="report-list-empty"><span>暂无历史报告</span></div>';
    return;
  }
  el.innerHTML = reports.map(r => {
    // 从文件名提取话题
    const name = r.filename.replace(/_调研报告_\d{8}_\d{6}\.md$/, '').replace(/_/g, ' ');
    return `
      <div class="report-item" onclick="loadHistoryReport('${escapeHtml(r.filename)}')" title="${escapeHtml(r.filename)}">
        <div class="report-item-name">${escapeHtml(name)}</div>
        <div class="report-item-meta">${r.modified} · ${formatSize(r.size)}</div>
      </div>
    `;
  }).join('');
}

async function loadHistoryReport(filename) {
  try {
    // 高亮选中项
    document.querySelectorAll('.report-item').forEach(el => el.classList.remove('active'));
    const items = document.querySelectorAll('.report-item');
    items.forEach(el => { if (el.title === filename) el.classList.add('active'); });

    const res = await fetch(`/api/reports/${encodeURIComponent(filename)}`);
    if (!res.ok) throw new Error('报告不存在');
    const data = await res.json();

    currentReportContent = data.content;
    currentReportFilename = data.filename;

    const name = filename.replace(/_调研报告_\d{8}_\d{6}\.md$/, '').replace(/_/g, ' ');
    showReport(name, data.content, filename, true);
    setTopbarStatus('idle', '就绪');
  } catch (e) {
    showToast(`加载失败：${e.message}`, 'error');
  }
}

// ── 显示报告 ──────────────────────────────────────
function showReport(title, markdown, filename, isHistory = false) {
  switchPanel('report');
  $('report-panel-title').textContent = `${title} — 市场调研报告`;
  $('report-panel-info').textContent = isHistory
    ? `历史报告 · ${filename}`
    : `刚刚生成 · ${filename}`;

  // 渲染 Markdown
  $('report-markdown').innerHTML = marked.parse(markdown);
}

// ── 面板切换 ──────────────────────────────────────
function switchPanel(name) {
  $('hero-section').classList.toggle('hidden', name !== 'hero');
  $('research-panel').classList.toggle('hidden', name !== 'research');
  $('report-panel').classList.toggle('hidden', name !== 'report');
}

// ── Timeline 工具函数 ─────────────────────────────
function resetTimeline() {
  NODE_ORDER.forEach(n => {
    setNodeStatus(n, 'pending');
    setNodeMessage(n, '等待中...');
    const r = $(`result-${n}`);
    if (r) r.innerHTML = '';
  });
}

function setNodeStatus(node, status) {
  const el = $(`step-${node}`);
  if (el) el.setAttribute('data-status', status);
}

function setNodeMessage(node, msg) {
  const el = $(`msg-${node}`);
  if (el) el.textContent = msg;
}

function markPreviousNodesDone(currentNode) {
  const idx = NODE_ORDER.indexOf(currentNode);
  for (let i = 0; i < idx; i++) {
    const n = NODE_ORDER[i];
    const el = $(`step-${n}`);
    if (el && el.getAttribute('data-status') !== 'done') {
      setNodeStatus(n, 'done');
    }
  }
}

function nodeLabel(node) {
  const labels = {
    KeywordGenerator: '搜索准备',
    PostSearcher: '热帖搜索',
    CommentExtractor: '评论抓取',
    PostSummarizer: 'AI 内容总结',
    NeedsAnalyzer: '需求分析',
    ReportGenerator: '报告生成'
  };
  return labels[node] || node;
}

// ── 日志工具 ──────────────────────────────────────
function clearLog() {
  $('log-stream').innerHTML = '';
}

function appendLog(message, level = 'info') {
  const stream = $('log-stream');

  // 移除占位符
  const placeholder = stream.querySelector('.log-placeholder');
  if (placeholder) placeholder.remove();

  const now = new Date();
  const time = now.toTimeString().slice(0, 8);

  const entry = document.createElement('div');
  entry.className = `log-entry ${level}`;
  entry.innerHTML = `<span class="log-time">${time}</span>${escapeHtml(message)}`;
  stream.appendChild(entry);

  // 自动滚动到底部
  stream.scrollTop = stream.scrollHeight;
}

// ── Topbar 状态 ───────────────────────────────────
function setTopbarStatus(status, text) {
  const dot = document.querySelector('.status-dot');
  const txt = document.querySelector('.status-text');
  if (dot) dot.className = `status-dot ${status}`;
  if (txt) txt.textContent = text;
}

// ── SSE 管理 ──────────────────────────────────────
function closeSSE() {
  if (currentEventSource) {
    currentEventSource.close();
    currentEventSource = null;
  }
}

// ── Toast 通知 ────────────────────────────────────
let toastTimer = null;
function showToast(message, type = 'info') {
  const el = $('toast');
  el.textContent = message;
  el.className = `toast ${type} show`;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.classList.remove('show');
  }, 3500);
}

// ── 工具函数 ──────────────────────────────────────
function escapeHtml(text) {
  if (!text) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes}B`;
  return `${(bytes / 1024).toFixed(1)}KB`;
}
