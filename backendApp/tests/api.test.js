'use strict';

// LLM мокируется, чтобы не требовать запущенного Ollama
jest.mock('../llm', () => ({
  agentLoop: jest.fn().mockResolvedValue({
    content: 'Тестовый ответ ассистента.',
    intermediary: [],
  }),
}));

const buildApp = require('./build-app');
const db       = require('../db');

const TEST = {
  username: 'jest_test_user',
  email:    'jest@pcbuilder.test',
  password: 'testpass123',
};

let app;
let authCookie; // устанавливается после первого register/login

// ── Helpers ───────────────────────────────────────────────────

function extractCookie(response) {
  const raw = response.headers['set-cookie'];
  if (!raw) return null;
  const str = Array.isArray(raw) ? raw[0] : raw;
  const m   = str.match(/sid=[^;]+/);
  return m ? m[0] : null;
}

async function inject(app, { method, url, payload, cookie }) {
  const opts = { method, url };
  if (payload)  opts.payload = payload;
  if (cookie)   opts.headers = { cookie };
  return app.inject(opts);
}

// ── Setup / Teardown ──────────────────────────────────────────

beforeAll(async () => {
  app = await buildApp();
  // Удаляем тестового пользователя если остался от прошлого запуска
  await db.query('DELETE FROM users WHERE email = ?', [TEST.email]);
});

afterAll(async () => {
  // Чистим тестовые данные
  await db.query('DELETE FROM users WHERE email = ?', [TEST.email]);
  await app.close();
  await db.closePool();
});


// ══════════════════════════════════════════════════════════════
// AUTH — регистрация
// ══════════════════════════════════════════════════════════════

describe('POST /api/auth/register', () => {
  test('создаёт пользователя и возвращает 201 + cookie', async () => {
    const res = await inject(app, {
      method: 'POST', url: '/api/auth/register', payload: TEST,
    });
    expect(res.statusCode).toBe(201);
    const body = res.json();
    expect(body.username).toBe(TEST.username);
    expect(body.email).toBe(TEST.email);
    expect(body.id).toBeDefined();

    authCookie = extractCookie(res);
    expect(authCookie).not.toBeNull();
  });

  test('возвращает 409 при дублировании email', async () => {
    const res = await inject(app, {
      method: 'POST', url: '/api/auth/register', payload: TEST,
    });
    expect(res.statusCode).toBe(409);
  });

  test('возвращает 400 при отсутствии обязательных полей', async () => {
    const res = await inject(app, {
      method: 'POST', url: '/api/auth/register',
      payload: { email: 'x@x.com' }, // нет username и password
    });
    expect(res.statusCode).toBe(400);
  });

  test('возвращает 400 при коротком пароле', async () => {
    const res = await inject(app, {
      method: 'POST', url: '/api/auth/register',
      payload: { username: 'u2', email: 'u2@test.com', password: '123' },
    });
    expect(res.statusCode).toBe(400);
  });
});


// ══════════════════════════════════════════════════════════════
// AUTH — вход
// ══════════════════════════════════════════════════════════════

describe('POST /api/auth/login', () => {
  test('возвращает данные пользователя и cookie при верных данных', async () => {
    const res = await inject(app, {
      method: 'POST', url: '/api/auth/login',
      payload: { email: TEST.email, password: TEST.password },
    });
    expect(res.statusCode).toBe(200);
    const body = res.json();
    expect(body.username).toBe(TEST.username);

    // Обновляем cookie для дальнейших тестов
    authCookie = extractCookie(res) ?? authCookie;
  });

  test('возвращает 401 при неверном пароле', async () => {
    const res = await inject(app, {
      method: 'POST', url: '/api/auth/login',
      payload: { email: TEST.email, password: 'wrongpass' },
    });
    expect(res.statusCode).toBe(401);
  });

  test('возвращает 401 при несуществующем email', async () => {
    const res = await inject(app, {
      method: 'POST', url: '/api/auth/login',
      payload: { email: 'nobody@test.com', password: 'pass' },
    });
    expect(res.statusCode).toBe(401);
  });
});


// ══════════════════════════════════════════════════════════════
// AUTH — текущий пользователь
// ══════════════════════════════════════════════════════════════

describe('GET /api/auth/me', () => {
  test('возвращает пользователя при наличии сессии', async () => {
    const res = await inject(app, {
      method: 'GET', url: '/api/auth/me', cookie: authCookie,
    });
    expect(res.statusCode).toBe(200);
    expect(res.json().username).toBe(TEST.username);
  });

  test('возвращает 401 без cookie', async () => {
    const res = await inject(app, { method: 'GET', url: '/api/auth/me' });
    expect(res.statusCode).toBe(401);
  });
});


// ══════════════════════════════════════════════════════════════
// BUILDS
// ══════════════════════════════════════════════════════════════

describe('GET /api/builds', () => {
  test('возвращает 401 без авторизации', async () => {
    const res = await inject(app, { method: 'GET', url: '/api/builds' });
    expect(res.statusCode).toBe(401);
  });

  test('возвращает пустой массив для нового пользователя', async () => {
    const res = await inject(app, {
      method: 'GET', url: '/api/builds', cookie: authCookie,
    });
    expect(res.statusCode).toBe(200);
    expect(Array.isArray(res.json())).toBe(true);
  });
});

describe('DELETE /api/builds/:id', () => {
  test('возвращает 404 для несуществующей сборки', async () => {
    const res = await inject(app, {
      method: 'DELETE', url: '/api/builds/999999', cookie: authCookie,
    });
    expect(res.statusCode).toBe(404);
  });
});


// ══════════════════════════════════════════════════════════════
// CHATS
// ══════════════════════════════════════════════════════════════

describe('GET /api/chats', () => {
  test('возвращает 401 без авторизации', async () => {
    const res = await inject(app, { method: 'GET', url: '/api/chats' });
    expect(res.statusCode).toBe(401);
  });

  test('возвращает массив чатов (изначально пустой)', async () => {
    const res = await inject(app, {
      method: 'GET', url: '/api/chats', cookie: authCookie,
    });
    expect(res.statusCode).toBe(200);
    expect(Array.isArray(res.json())).toBe(true);
  });
});


// ══════════════════════════════════════════════════════════════
// CHAT MESSAGE (агентский цикл с мок-LLM)
// ══════════════════════════════════════════════════════════════

let createdChatId;

describe('POST /api/chat/message', () => {
  test('возвращает 401 без авторизации', async () => {
    const res = await inject(app, {
      method: 'POST', url: '/api/chat/message',
      payload: { message: 'Привет' },
    });
    expect(res.statusCode).toBe(401);
  });

  test('возвращает 400 при пустом сообщении', async () => {
    const res = await inject(app, {
      method: 'POST', url: '/api/chat/message',
      payload: { message: '' }, cookie: authCookie,
    });
    expect(res.statusCode).toBe(400);
  });

  test('создаёт чат и возвращает ответ ассистента', async () => {
    const res = await inject(app, {
      method: 'POST', url: '/api/chat/message',
      payload: { message: 'Собери игровой ПК за 1500$' },
      cookie: authCookie,
    });
    expect(res.statusCode).toBe(200);
    const body = res.json();
    expect(body.chatId).toBeDefined();
    expect(body.role).toBe('assistant');
    expect(typeof body.content).toBe('string');
    expect(body.content.length).toBeGreaterThan(0);

    createdChatId = body.chatId;
  });

  test('новый чат появляется в списке', async () => {
    const res = await inject(app, {
      method: 'GET', url: '/api/chats', cookie: authCookie,
    });
    expect(res.statusCode).toBe(200);
    const ids = res.json().map(c => c.id);
    expect(ids).toContain(createdChatId);
  });

  test('история чата содержит сообщения user и assistant', async () => {
    const res = await inject(app, {
      method: 'GET', url: `/api/chats/${createdChatId}`, cookie: authCookie,
    });
    expect(res.statusCode).toBe(200);
    const { messages } = res.json();
    const roles = messages.map(m => m.role);
    expect(roles).toContain('user');
    expect(roles).toContain('assistant');
  });

  test('продолжает существующий чат', async () => {
    const res = await inject(app, {
      method: 'POST', url: '/api/chat/message',
      payload: { chatId: createdChatId, message: 'Сделай сборку компактнее' },
      cookie: authCookie,
    });
    expect(res.statusCode).toBe(200);
    expect(res.json().chatId).toBe(createdChatId);
  });
});


// ══════════════════════════════════════════════════════════════
// AUTH — выход
// ══════════════════════════════════════════════════════════════

describe('POST /api/auth/logout', () => {
  test('выход очищает сессию', async () => {
    const res = await inject(app, {
      method: 'POST', url: '/api/auth/logout', cookie: authCookie,
    });
    expect(res.statusCode).toBe(200);
    expect(res.json().ok).toBe(true);
  });

  test('после выхода /api/auth/me возвращает 401', async () => {
    const res = await inject(app, {
      method: 'GET', url: '/api/auth/me', cookie: authCookie,
    });
    expect(res.statusCode).toBe(401);
  });
});
