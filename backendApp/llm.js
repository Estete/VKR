'use strict';

const OLLAMA_URL = process.env.OLLAMA_URL  || 'http://localhost:11434/api/chat';
const ML_BASE    = process.env.ML_BASE_URL || 'http://localhost:8000/api/v1';
const MODEL      = 'mistral';

// ── Промпты ───────────────────────────────────────────────────
// Короткие, без списков запретов — Mistral реже эхирует их

const COMMENT_BUILD_PROMPT =
  'Ты помощник по ПК. Отвечай строго связным текстом — без списков, без разбивки по компонентам. ' +
  'Напиши 3–5 предложений на русском: чем сборки отличаются друг от друга, ' +
  'какой вариант лучше для каких задач и почему. ' +
  'Не перечисляй компоненты по одному — дай общую оценку каждой конфигурации.';

const COMMENT_REPLACE_PROMPT =
  'Ты помощник по ПК. Без приветствий, сразу по делу. ' +
  'Напиши 2–3 предложения на русском о вариантах замены компонента: ' +
  'что делает их хорошим выбором и чем они отличаются друг от друга.';

const CHAT_PROMPT =
  'Ты помощник по ПК и комплектующим. Без приветствий, отвечай сразу по существу. ' +
  'Консультируй по вопросам совместимости, производительности и выбора компонентов на русском языке. ' +
  'Для подбора конкретных сборок используй систему рекомендаций.';


// ── HTTP / Ollama ─────────────────────────────────────────────

async function httpPost(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return r.json();
}

async function ollamaChat(messages) {
  const resp = await fetch(OLLAMA_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: MODEL, messages, stream: false }),
  });
  const data = await resp.json();
  return data.message?.content || '';
}


// ── Форматирование данных для LLM (читаемый текст, не JSON) ──

function formatBuildsText(builds) {
  return builds.map(b => {
    const gpu = b.gpu
      ? `${b.gpu.name} (${b.gpu_memory} ГБ VRAM)`
      : 'интегрированная графика';
    return [
      `Сборка №${b.rank} — $${b.total_price.toFixed(0)} (оценка ${b.predicted_score.toFixed(1)}/100):`,
      `  Процессор: ${b.cpu.name} — ${b.cpu_cores} ядер, ${b.cpu_boost_clock} ГГц`,
      `  Видеокарта: ${gpu}`,
      `  RAM: ${b.ram_total_gb} ГБ DDR${b.ram_ddr_gen}`,
      `  Накопитель: ${b.storage.name} (${b.storage_type})`,
      `  Блок питания: ${b.psu_wattage} Вт`,
    ].join('\n');
  }).join('\n\n');
}

function formatReplaceText(options) {
  return options.map((o, i) =>
    `${i + 1}. ${o.name} — $${Number(o.price).toFixed(0)} (оценка ${o.predicted_score.toFixed(1)}/100)`
  ).join('\n');
}


// ── Поиск последних сборок в истории ─────────────────────────

function findLastBuildsInHistory(history) {
  for (let i = history.length - 1; i >= 0; i--) {
    if (history[i].role !== 'tool') continue;
    try {
      const data = typeof history[i].content === 'string'
        ? JSON.parse(history[i].content)
        : history[i].content;
      if (data?.builds?.length) return data.builds;
    } catch { /* пропускаем */ }
  }
  return null;
}

function findBuildInHistory(history, rank) {
  const builds = findLastBuildsInHistory(history);
  if (!builds) return null;
  return builds.find(b => b.rank === rank) ?? builds[0];
}


// ── Шаг 1: классификация с контекстом последних сообщений ───

async function extractIntent(userText, history) {
  // Берём последние 4 реплики (2 пары вопрос-ответ) для контекста
  const recentLines = (history || [])
    .filter(m => m.role === 'user' || (m.role === 'assistant' && !m.tool_calls))
    .slice(-4)
    .map(m => `${m.role === 'user' ? 'Пользователь' : 'Ассистент'}: ${(m.content || '').slice(0, 200)}`)
    .join('\n');

  const context = recentLines
    ? `История диалога:\n${recentLines}\n\n`
    : '';

  const prompt =
    'Классифицируй последнее сообщение пользователя и верни ТОЛЬКО JSON — без пояснений, без текста вокруг.\n\n' +
    context +
    'Запрос на подбор ПК (или уточнение к предыдущему запросу — бюджет/назначение из истории, budget может быть null если уже известен):\n' +
    '{"type":"build","budget":<число USD или null>,"purpose":"gaming|workstation|rendering|office|streaming или null",' +
    '"preferred_brands":[],"form_factor":"any|compact|standard","min_ram_gb":null,"prefer_ssd":null,"top_n":<сколько вариантов просит пользователь, если не указано — null>}\n\n' +
    'Запрос на замену компонента в сборке:\n' +
    '{"type":"replace","category":"cpu|motherboard|memory|video_card|storage|power_supply|case|cooler",' +
    '"build_rank":1,"constraints":{}}\n\n' +
    'Любой другой вопрос:\n' +
    '{"type":"other"}\n\n' +
    `Последнее сообщение: "${userText}"\n` +
    'JSON:';

  try {
    const text = await ollamaChat([{ role: 'user', content: prompt }]);
    const match = text.match(/\{[\s\S]*?\}/);
    if (!match) return { type: 'other' };
    return JSON.parse(match[0]);
  } catch {
    return { type: 'other' };
  }
}


// ── Шаг 2: комментарий от LLM ────────────────────────────────

async function getCommentary(systemPrompt, userText, dataText) {
  try {
    return await ollamaChat([
      { role: 'system', content: systemPrompt },
      { role: 'user',   content: `${userText}\n\n${dataText}` },
    ]);
  } catch {
    return '';
  }
}


// ── Обычный чат — c контекстом последних сборок ───────────────

async function chatResponse(userText, history) {
  // Последние сборки из истории — чтобы LLM знала их параметры при follow-up вопросах
  const lastBuilds = findLastBuildsInHistory(history);
  const buildCtx   = lastBuilds
    ? `\n\nПоследние подобранные сборки:\n${formatBuildsText(lastBuilds)}`
    : '';

  // Только диалоговые сообщения (без tool/assistant+tool_calls)
  const historyMessages = history
    .filter(m => m.role === 'user' || (m.role === 'assistant' && !m.tool_calls))
    .slice(-8)
    .map(m => ({ role: m.role, content: m.content || '' }));

  try {
    return await ollamaChat([
      { role: 'system', content: CHAT_PROMPT + buildCtx },
      ...historyMessages,
      { role: 'user',   content: userText },
    ]);
  } catch (err) {
    return `Не удалось связаться с LLM: ${err.message}. Убедитесь, что Ollama запущена.`;
  }
}


// ── Server-side контекст подбора (per chat) ───────────────────
// Хранит актуальный запрос пользователя между репликами диалога.
// Ключ: "userId:chatId". Обновляется при любом уточнении параметров.

const _buildCtx = new Map(); // "userId:chatId" → { budget, purpose, preferred_brands, form_factor }

function _ctxKey(userId, chatId) {
  return `${userId ?? 'anon'}:${chatId ?? 'new'}`;
}

function clearBuildContext(userId, chatId) {
  _buildCtx.delete(_ctxKey(userId, chatId));
}


// ── Агентский цикл ────────────────────────────────────────────

function _ts() {
  return new Date().toTimeString().slice(0, 8);
}

function _log(step, detail = '') {
  console.log(`[${_ts()}] [LLM] ${step}${detail ? ' — ' + detail : ''}`);
}

async function agentLoop(userText, history, userId, chatId) {
  const intermediary = [];
  const key = _ctxKey(userId, chatId);
  const ctx = _buildCtx.get(key) || {};

  _log('extractIntent', `"${userText.slice(0, 60)}"`);
  const t0 = Date.now();

  const VALID_PURPOSES = new Set(['gaming', 'workstation', 'rendering', 'office', 'streaming']);

  let intent;
  try {
    intent = await extractIntent(userText, history);
    if (intent.purpose && !VALID_PURPOSES.has(intent.purpose)) intent.purpose = null;
    if (intent.budget  && !isFinite(Number(intent.budget)))    intent.budget  = null;
    if (intent.form_factor && !['any','compact','standard'].includes(intent.form_factor))
      intent.form_factor = 'any';
  } catch {
    intent = { type: 'other' };
  }

  _log('extractIntent done', `type=${intent.type} budget=${intent.budget ?? '-'} purpose=${intent.purpose ?? '-'} (${Date.now() - t0}ms)`);

  // Если LLM вернула 'other', но бот только что задавал уточняющий вопрос
  // (бюджет или назначение) — считаем текущее сообщение продолжением build-диалога.
  if (intent.type === 'other') {
    const lastAssistant = history
      .filter(m => m.role === 'assistant' && !m.tool_calls)
      .slice(-1)[0]?.content || '';
    const clarifying = lastAssistant.includes('бюджет') ||
                       lastAssistant.includes('задач')  ||
                       lastAssistant.includes('Уточните');
    if (clarifying) {
      intent = { type: 'build', budget: null, purpose: null,
                 preferred_brands: [], form_factor: 'any', top_n: null };
    }
  }

  // ── Подбор сборки ─────────────────────────────────────────
  if (intent.type === 'build') {
    // Параметры из LLM → дополняем server-side контекстом → дополняем историей сборок
    let budget  = intent.budget  ? Number(intent.budget)  : null;
    let purpose = intent.purpose || null;
    let brands  = Array.isArray(intent.preferred_brands) && intent.preferred_brands.length
                  ? intent.preferred_brands : null;
    let ff      = intent.form_factor && intent.form_factor !== 'any'
                  ? intent.form_factor : null;

    // Дополняем из server-side контекста текущего чата
    if (!budget)  budget  = ctx.budget  || null;
    if (!purpose) purpose = ctx.purpose || null;
    if (!brands)  brands  = ctx.preferred_brands || [];
    if (!ff)      ff      = ctx.form_factor      || 'any';

    // Крайний случай — берём из последних сгенерированных сборок в истории
    if (!budget || !purpose) {
      const lastBuilds = findLastBuildsInHistory(history);
      if (lastBuilds?.length) {
        if (!budget)  budget  = lastBuilds[0].budget;
        if (!purpose) purpose = lastBuilds[0].purpose;
      }
    }

    // Сохраняем актуальный контекст (обновляем только то, что известно)
    _buildCtx.set(key, {
      budget:           budget  ?? ctx.budget,
      purpose:          purpose ?? ctx.purpose,
      preferred_brands: brands,
      form_factor:      ff,
    });

    // Обязательные поля не заполнены — уточняем у пользователя
    if (!budget && !purpose) {
      return {
        content: 'Уточните, пожалуйста: какой бюджет рассматриваете (в долларах) и для каких задач нужен ПК — игры, рабочая станция, рендеринг/3D, офис или стриминг?',
        intermediary,
      };
    }
    if (!budget) {
      return {
        content: 'Какой бюджет рассматриваете для этой сборки (в долларах)?',
        intermediary,
      };
    }
    if (!purpose) {
      return {
        content: 'Для каких задач нужен ПК? Варианты: игровой, рабочая станция, рендеринг/3D, офисный или для стриминга.',
        intermediary,
      };
    }

    const topN = Math.min(Math.max(Number(intent.top_n) || 3, 1), 10);
    const params = {
      budget,
      purpose,
      preferred_brands: brands,
      form_factor:      ff,
      min_ram_gb:       intent.min_ram_gb || null,
      prefer_ssd:       intent.prefer_ssd ?? null,
      top_n:            topN,
    };

    _log('generate_builds →', `budget=$${params.budget} purpose=${params.purpose} top_n=${params.top_n}`);
    const t1 = Date.now();
    let result;
    try {
      result = await httpPost(`${ML_BASE}/generate_builds`, params);
    } catch (err) {
      return { content: `Не удалось обратиться к ML-сервису: ${err.message}`, intermediary };
    }
    _log('generate_builds ←', `${result.builds?.length ?? 0} сборок (${Date.now() - t1}ms)`);

    const syntheticTc = [{ function: { name: 'generate_builds', arguments: params } }];
    intermediary.push({ role: 'assistant', content: '', tool_calls: syntheticTc });
    intermediary.push({ role: 'tool',      content: JSON.stringify(result) });

    if (result?.detail || result?.error) {
      return { content: result.detail || result.error, intermediary };
    }

    const dataText = result.builds?.length ? formatBuildsText(result.builds) : '';
    _log('getCommentary →');
    const t2 = Date.now();
    const content  = await getCommentary(COMMENT_BUILD_PROMPT, userText, dataText);
    _log('getCommentary ←', `${content.length} симв. (${Date.now() - t2}ms)`);
    return { content, intermediary };
  }

  // ── Замена компонента ─────────────────────────────────────
  if (intent.type === 'replace' && intent.category) {
    const rank  = Number(intent.build_rank) || 1;
    const build = findBuildInHistory(history, rank);

    if (!build) {
      return {
        content: 'Не нашёл сборку для замены — сначала подберите конфигурацию.',
        intermediary,
      };
    }

    const params = {
      build_context: build,
      category:      intent.category,
      constraints:   intent.constraints || {},
      top_n:         3,
    };

    _log('replace_component →', `category=${params.category}`);
    const t1 = Date.now();
    let result;
    try {
      result = await httpPost(`${ML_BASE}/replace_component`, params);
    } catch (err) {
      return { content: `Не удалось обратиться к ML-сервису: ${err.message}`, intermediary };
    }
    _log('replace_component ←', `${result.options?.length ?? 0} вариантов (${Date.now() - t1}ms)`);

    const syntheticTc = [{ function: { name: 'replace_component', arguments: params } }];
    intermediary.push({ role: 'assistant', content: '', tool_calls: syntheticTc });
    intermediary.push({ role: 'tool',      content: JSON.stringify(result) });

    if (result?.detail || result?.error) {
      return { content: result.detail || result.error, intermediary };
    }

    const dataText = result.options?.length ? formatReplaceText(result.options) : '';
    _log('getCommentary →');
    const t2 = Date.now();
    const content  = await getCommentary(COMMENT_REPLACE_PROMPT, userText, dataText);
    _log('getCommentary ←', `${content.length} симв. (${Date.now() - t2}ms)`);
    return { content, intermediary };
  }

  // ── Обычный вопрос ────────────────────────────────────────
  const content = await chatResponse(userText, history);
  return { content, intermediary };
}

module.exports = { agentLoop, clearBuildContext };
