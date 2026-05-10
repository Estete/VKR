'use strict';

let currentChatId = null;
let currentBuilds  = null; // последний набор сборок из ML
let me = null;

// ── Init ──────────────────────────────────────────────────────

(async () => {
  me = await requireAuth();
  if (!me) return;

  document.getElementById('sidebar-username').textContent = me.username;

  await loadChatList();

  // Если chatId в URL — открываем его
  const params = new URLSearchParams(location.search);
  const chatId  = parseInt(params.get('chat'));
  if (chatId) openChat(chatId);
})();

// ── Sidebar toggle ────────────────────────────────────────────

document.getElementById('btn-toggle-sidebar').addEventListener('click', () => {
  document.getElementById('sidebar').classList.toggle('collapsed');
});

// ── New chat ──────────────────────────────────────────────────

document.getElementById('btn-new-chat').addEventListener('click', () => {
  currentChatId = null;
  document.getElementById('chat-name').textContent = 'Новый чат';
  const container = document.getElementById('messages');
  const emptyEl   = document.getElementById('messages-empty');
  container.innerHTML = '';
  container.appendChild(emptyEl);
  emptyEl.classList.remove('hidden');
  document.querySelectorAll('.chat-item').forEach(el => el.classList.remove('active'));
  closePanel();
});

// ── Load chat list ────────────────────────────────────────────

async function loadChatList() {
  try {
    const chats = await api('GET', '/api/chats');
    const list  = document.getElementById('chat-list');
    list.innerHTML = '';
    chats.forEach(chat => {
      const li = document.createElement('li');
      li.className = 'chat-item' + (chat.id === currentChatId ? ' active' : '');
      li.dataset.id = chat.id;

      const title = document.createElement('span');
      title.className = 'chat-item-title';
      title.textContent = chat.title;

      const delBtn = document.createElement('button');
      delBtn.className = 'chat-item-delete';
      delBtn.title = 'Удалить чат';
      delBtn.innerHTML = `<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
      delBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        await removeChatItem(chat.id, li);
      });

      li.appendChild(title);
      li.appendChild(delBtn);
      li.addEventListener('click', () => openChat(chat.id));
      list.appendChild(li);
    });
  } catch { /* ignore */ }
}

async function removeChatItem(chatId, element) {
  try {
    await api('DELETE', `/api/chats/${chatId}`);
    element.remove();
    if (currentChatId === chatId) {
      currentChatId = null;
      document.getElementById('chat-name').textContent = 'Новый чат';
      const container = document.getElementById('messages');
      const emptyEl   = document.getElementById('messages-empty');
      container.innerHTML = '';
      container.appendChild(emptyEl);
      emptyEl.classList.remove('hidden');
      closePanel();
      history.replaceState(null, '', '/chat.html');
    }
  } catch { /* ignore */ }
}

// ── Open existing chat ────────────────────────────────────────

async function openChat(chatId) {
  currentChatId = chatId;
  closePanel();

  document.querySelectorAll('.chat-item').forEach(el => {
    el.classList.toggle('active', parseInt(el.dataset.id) === chatId);
  });

  try {
    const chat = await api('GET', `/api/chats/${chatId}`);
    document.getElementById('chat-name').textContent = chat.title;
    renderMessages(chat.messages);
  } catch { /* ignore */ }

  btnSend.disabled = !input.value.trim();
}

// ── Render messages ───────────────────────────────────────────

function renderMessages(messages) {
  const container = document.getElementById('messages');
  const emptyEl = document.getElementById('messages-empty');
  container.innerHTML = '';
  container.appendChild(emptyEl);
  emptyEl.classList.add('hidden');

  const processed = processMessages(messages);
  processed.forEach(m => container.appendChild(createMessageEl(m)));

  container.scrollTop = container.scrollHeight;
}

/**
 * Группирует сырые сообщения из БД:
 * - скрывает промежуточные tool_call/tool сообщения
 * - прикрепляет данные сборок к финальному ответу ассистента
 */
function processMessages(messages) {
  const result = [];
  let pendingBuilds  = null;
  let pendingOptions = null; // варианты замены компонента
  let pendingTool    = null; // имя последнего вызванного инструмента

  for (const m of messages) {
    if (m.role === 'user') {
      result.push({ type: 'user', content: m.content, created_at: m.created_at, id: m.id });
      pendingBuilds = pendingOptions = pendingTool = null;

    } else if (m.role === 'assistant' && m.tool_calls) {
      pendingTool = m.tool_calls[0]?.function?.name || null;

    } else if (m.role === 'tool') {
      try {
        const data = typeof m.content === 'string' ? JSON.parse(m.content) : m.content;
        if (pendingTool === 'generate_builds' && data.builds) {
          pendingBuilds = data.builds;
        } else if (pendingTool === 'replace_component' && data.options) {
          pendingOptions = data.options;
        }
      } catch { /* ignore */ }
      pendingTool = null;

    } else if (m.role === 'assistant' && !m.tool_calls) {
      result.push({
        type:    'assistant',
        content: m.content,
        builds:  pendingBuilds,
        replaceOptions: pendingOptions,
        created_at: m.created_at,
        id: m.id,
      });
      pendingBuilds = pendingOptions = null;
    }
  }
  return result;
}

function createMessageEl(m) {
  const wrap = document.createElement('div');
  wrap.className = `message ${m.type}`;
  if (m.id) wrap.dataset.msgId = m.id;

  const avatar = document.createElement('div');
  avatar.className = 'msg-avatar';
  avatar.textContent = m.type === 'user' ? (me?.username?.[0]?.toUpperCase() || 'U') : 'AI';

  const body = document.createElement('div');
  body.className = 'msg-body';

  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  if (m.type === 'assistant') {
    bubble.innerHTML = renderMarkdown(m.content);
  } else {
    bubble.textContent = m.content;
  }
  body.appendChild(bubble);

  if (m.builds?.length) {
    m.builds.forEach((build, i) => body.appendChild(createArtifactCard(build, i, m.builds)));
  }

  if (m.replaceOptions?.length) {
    m.replaceOptions.forEach(opt => body.appendChild(createReplaceOptionCard(opt)));
  }

  if (m.created_at) {
    const time = document.createElement('div');
    time.className = 'msg-time';
    time.textContent = formatTime(m.created_at);
    body.appendChild(time);
  }

  wrap.appendChild(avatar);
  wrap.appendChild(body);
  return wrap;
}

function createArtifactCard(build, index, allBuilds) {
  const card = document.createElement('div');
  card.className = 'artifact-card';

  const purposeLabel = {
    gaming: 'Игровой', workstation: 'Рабочая станция',
    rendering: 'Рендеринг', office: 'Офисный', streaming: 'Стриминг',
  }[build.purpose ?? ''] ?? build.purpose ?? '';

  const gpu = build.gpu ? ` · ${build.gpu.name.split(' ').slice(0, 3).join(' ')}` : '';

  card.innerHTML = `
    <div class="artifact-card-header">
      <span class="artifact-card-title">Сборка #${build.rank} — ${purposeLabel}</span>
      <span class="artifact-card-price">$${build.total_price.toFixed(0)}</span>
    </div>
    <div class="artifact-card-specs">
      <span>${build.cpu.name.split(' ').slice(0, 4).join(' ')}${gpu}</span>
      <span>RAM ${build.ram_total_gb} GB DDR${build.ram_ddr_gen} · ${build.storage_type}</span>
    </div>
    <div class="artifact-card-expand">Развернуть →</div>
  `;

  card.addEventListener('click', () => openPanel(allBuilds, index));
  return card;
}

function createReplaceOptionCard(opt) {
  const card = document.createElement('div');
  card.className = 'artifact-card';
  card.innerHTML = `
    <div class="artifact-card-header">
      <span class="artifact-card-title">${opt.name}</span>
      <span class="artifact-card-price">$${Number(opt.price).toFixed(0)}</span>
    </div>
    <div class="artifact-card-specs">
      <span>Оценка: ${opt.predicted_score.toFixed(1)} / 100</span>
    </div>
  `;
  return card;
}

// ── Right panel ───────────────────────────────────────────────

document.getElementById('btn-close-panel').addEventListener('click', closePanel);

function closePanel() {
  document.getElementById('right-panel').classList.remove('open');
  currentBuilds = null;
}

function openPanel(builds, activeIndex = 0) {
  currentBuilds = builds;
  document.getElementById('right-panel').classList.add('open');
  renderPanel(builds, activeIndex);
}

function renderPanel(builds, activeIndex) {
  const tabs = document.getElementById('build-tabs');
  const body = document.getElementById('panel-body');

  // Вкладки
  tabs.innerHTML = '';
  builds.forEach((b, i) => {
    const tab = document.createElement('button');
    tab.className = 'build-tab' + (i === activeIndex ? ' active' : '');
    tab.textContent = `Сборка #${b.rank}`;
    tab.addEventListener('click', () => renderPanel(builds, i));
    tabs.appendChild(tab);
  });

  const build = builds[activeIndex];
  const purposeLabel = {
    gaming: 'Игровой ПК', workstation: 'Рабочая станция',
    rendering: 'Рендеринг / 3D', office: 'Офисный ПК', streaming: 'Стриминг',
  }[build.purpose ?? ''] ?? build.purpose ?? '';

  document.getElementById('panel-title').textContent = purposeLabel;

  const components = [
    { cat: 'Процессор',       data: build.cpu },
    { cat: 'Видеокарта',      data: build.gpu },
    { cat: 'Мат. плата',      data: build.mb },
    { cat: 'ОЗУ',             data: build.ram },
    { cat: 'Накопитель',      data: build.storage },
    { cat: 'Блок питания',    data: build.psu },
    { cat: 'Корпус',          data: build.case },
    { cat: 'Кулер',           data: build.cooler },
  ].filter(c => c.data);

  const rows = components.map(c => `
    <div class="component-row">
      <span class="component-cat">${c.cat}</span>
      <div class="component-info">
        <div class="component-name">${c.data.name}</div>
        <div class="component-price">$${c.data.price.toFixed(2)}</div>
      </div>
    </div>
  `).join('');

  body.innerHTML = `
    <div class="build-score">
      <span class="score-badge">${build.predicted_score.toFixed(1)}</span>
      <span class="score-label">/ 100 баллов</span>
    </div>
    <div class="build-total">
      $${build.total_price.toFixed(2)}
      <span>из $${build.budget?.toFixed(0) ?? '?'} · ${(build.budget_utilization * 100).toFixed(0)}%</span>
    </div>
    <div class="components-list">${rows}</div>
    <button class="btn-save-build" id="btn-save-build">Сохранить сборку</button>
  `;

  document.getElementById('btn-save-build').addEventListener('click', () => saveBuild(build));
}

async function saveBuild(build) {
  const btn = document.getElementById('btn-save-build');
  btn.disabled = true;
  btn.textContent = 'Сохранение...';

  const fieldToCategory = {
    cpu: 'cpu', mb: 'motherboard', ram: 'memory', gpu: 'video_card',
    storage: 'storage', psu: 'power_supply', case: 'case', cooler: 'cooler',
  };
  const components = {};
  for (const [field, category] of Object.entries(fieldToCategory)) {
    if (build[field]?.component_id != null) {
      components[category] = build[field].component_id;
    }
  }

  try {
    await api('POST', '/api/builds', {
      chatId:      currentChatId,
      purpose:     build.purpose,
      budget:      build.budget,
      total_price: build.total_price,
      components,
    });
    btn.textContent = '✓ Сохранено';
    setTimeout(() => { btn.disabled = false; btn.textContent = 'Сохранить сборку'; }, 2000);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = 'Ошибка. Попробуйте снова';
    setTimeout(() => { btn.textContent = 'Сохранить сборку'; }, 2500);
  }
}

// ── Send message ──────────────────────────────────────────────

const input   = document.getElementById('msg-input');
const btnSend = document.getElementById('btn-send');

input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 160) + 'px';
  btnSend.disabled = !input.value.trim();
});

input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (!btnSend.disabled) sendMessage();
  }
});

btnSend.addEventListener('click', sendMessage);

async function sendMessage() {
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  input.style.height = 'auto';
  btnSend.disabled = true;

  document.getElementById('messages-empty')?.classList.add('hidden');
  const container = document.getElementById('messages');

  // Показываем сообщение пользователя
  container.appendChild(createMessageEl({ type: 'user', content: text }));

  // Индикатор загрузки
  const indicator = createTypingIndicator();
  container.appendChild(indicator);
  container.scrollTop = container.scrollHeight;

  try {
    const resp = await api('POST', '/api/chat/message', {
      chatId:  currentChatId || undefined,
      message: text,
    });

    // Обновляем chatId если чат был создан
    if (!currentChatId && resp.chatId) {
      currentChatId = resp.chatId;
      history.replaceState(null, '', `?chat=${currentChatId}`);
      await loadChatList();
    }

    indicator.remove();

    // Рендерим ответ
    // Ответ не содержит builds напрямую — загружаем свежую историю
    const chat = await api('GET', `/api/chats/${currentChatId}`);
    document.getElementById('chat-name').textContent = chat.title;
    renderMessages(chat.messages);

  } catch (err) {
    indicator.remove();
    const errEl = createMessageEl({
      type: 'assistant',
      content: err.message === 'Failed to fetch'
        ? 'Не удалось связаться с сервером. Проверьте, что сервер запущен.'
        : `Ошибка: ${err.message}`,
    });
    container.appendChild(errEl);
    container.scrollTop = container.scrollHeight;
  } finally {
    btnSend.disabled = !input.value.trim();
  }
}

function createTypingIndicator() {
  const el = document.createElement('div');
  el.className = 'typing-indicator';
  el.innerHTML = `
    <div class="msg-avatar">AI</div>
    <div class="typing-dots"><span></span><span></span><span></span></div>
  `;
  return el;
}
