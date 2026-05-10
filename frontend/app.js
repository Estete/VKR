'use strict';

// ── API wrapper ───────────────────────────────────────────────

async function api(method, path, body) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(data.error || `HTTP ${r.status}`);
    err.status = r.status;
    throw err;
  }
  return data;
}

// ── Auth ──────────────────────────────────────────────────────

async function getMe() {
  return api('GET', '/api/auth/me');
}

async function requireAuth() {
  try {
    return await getMe();
  } catch {
    window.location.href = '/auth.html';
    return null;
  }
}

async function logout() {
  try { await api('POST', '/api/auth/logout'); } catch { /* ignore */ }
  window.location.href = '/auth.html';
}

// ── Markdown (lightweight) ────────────────────────────────────

function renderMarkdown(text) {
  if (!text) return '';
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/^### (.+)$/gm, '<strong>$1</strong>')
    .replace(/^## (.+)$/gm,  '<strong>$1</strong>')
    .replace(/^# (.+)$/gm,   '<strong>$1</strong>')
    .replace(/^- (.+)$/gm,   '<li>$1</li>')
    .replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>')
    .replace(/\n{2,}/g, '</p><p>')
    .replace(/\n/g, '<br>')
    .replace(/^(.+)$/, '<p>$1</p>');
}

// ── Date formatting ───────────────────────────────────────────

function formatDate(iso) {
  const d = new Date(iso);
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

window.api       = api;
window.getMe     = getMe;
window.requireAuth = requireAuth;
window.logout    = logout;
window.renderMarkdown = renderMarkdown;
window.formatDate = formatDate;
window.formatTime = formatTime;
