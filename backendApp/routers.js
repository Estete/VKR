'use strict';
const bcrypt     = require('bcrypt');
const db         = require('./db');
const { agentLoop, clearBuildContext } = require('./llm');

const SALT_ROUNDS  = 10;
const COOKIE_NAME  = 'sid';
const COOKIE_OPTS  = { httpOnly: true, sameSite: 'lax', path: '/', maxAge: 7 * 24 * 3600 };

const SECURITY = [{ cookieAuth: [] }];


// ── Middleware аутентификации ─────────────────────────────────

async function authenticate(request, reply) {
  const sessionId = request.cookies[COOKIE_NAME];
  if (!sessionId) return reply.code(401).send({ error: 'Не авторизован' });

  const session = await db.getSession(sessionId);
  if (!session) return reply.code(401).send({ error: 'Сессия истекла' });

  request.user = {
    id:       session.user_id,
    username: session.username,
    email:    session.email,
  };
}


// ── Плагин роутов ─────────────────────────────────────────────

async function routes(fastify) {

  // ── POST /api/auth/register ────────────────────────────────
  fastify.post('/auth/register', {
    schema: {
      tags: ['Auth'],
      summary: 'Регистрация нового пользователя',
      body: {
        type: 'object', required: ['username', 'email', 'password'],
        properties: {
          username: { type: 'string', example: 'ivan' },
          email:    { type: 'string', example: 'ivan@example.com' },
          password: { type: 'string', minLength: 6, example: '123456' },
        },
      },
    },
  }, async (request, reply) => {
    const { username, email, password } = request.body || {};

    if (!username || !email || !password) {
      return reply.code(400).send({ error: 'username, email и password обязательны' });
    }
    if (password.length < 6) {
      return reply.code(400).send({ error: 'Пароль должен быть не менее 6 символов' });
    }

    const existingEmail    = await db.getUserByEmail(email);
    if (existingEmail) return reply.code(409).send({ error: 'Email уже занят' });
    const existingUsername = await db.getUserByUsername(username);
    if (existingUsername) return reply.code(409).send({ error: 'Имя пользователя уже занято' });

    const passwordHash = await bcrypt.hash(password, SALT_ROUNDS);
    const user         = await db.createUser(username, email, passwordHash);
    const { sessionId, expiresAt } = await db.createSession(user.id);

    reply.setCookie(COOKIE_NAME, sessionId, COOKIE_OPTS);
    return reply.code(201).send({ id: user.id, username: user.username, email: user.email });
  });


  // ── POST /api/auth/login ───────────────────────────────────
  fastify.post('/auth/login', {
    schema: {
      tags: ['Auth'],
      summary: 'Вход в систему',
      body: {
        type: 'object', required: ['email', 'password'],
        properties: {
          email:    { type: 'string', example: 'ivan@example.com' },
          password: { type: 'string', example: '123456' },
        },
      },
    },
  }, async (request, reply) => {
    const { email, password } = request.body || {};
    if (!email || !password) {
      return reply.code(400).send({ error: 'email и password обязательны' });
    }

    const user = await db.getUserByEmail(email);
    if (!user) return reply.code(401).send({ error: 'Неверный email или пароль' });

    const match = await bcrypt.compare(password, user.password_hash);
    if (!match) return reply.code(401).send({ error: 'Неверный email или пароль' });

    const { sessionId } = await db.createSession(user.id);
    reply.setCookie(COOKIE_NAME, sessionId, COOKIE_OPTS);
    return { id: user.id, username: user.username, email: user.email };
  });


  // ── GET /api/auth/me ───────────────────────────────────────
  fastify.get('/auth/me', {
    preHandler: authenticate,
    schema: {
      tags: ['Auth'],
      summary: 'Текущий авторизованный пользователь',
      security: SECURITY,
    },
  }, async (request) => {
    return { id: request.user.id, username: request.user.username, email: request.user.email };
  });


  // ── POST /api/auth/logout ──────────────────────────────────
  fastify.post('/auth/logout', {
    preHandler: authenticate,
    schema: {
      tags: ['Auth'],
      summary: 'Выход из системы',
      security: SECURITY,
    },
  }, async (request, reply) => {
    const sessionId = request.cookies[COOKIE_NAME];
    await db.deleteSession(sessionId);
    reply.clearCookie(COOKIE_NAME, { path: '/' });
    return { ok: true };
  });


  // ── GET /api/builds ────────────────────────────────────────
  fastify.get('/builds', {
    preHandler: authenticate,
    schema: {
      tags: ['Builds'],
      summary: 'Список сохранённых сборок пользователя',
      security: SECURITY,
    },
  }, async (request) => {
    return db.getBuilds(request.user.id);
  });


  // ── GET /api/builds/:id ────────────────────────────────────
  fastify.get('/builds/:id', {
    preHandler: authenticate,
    schema: {
      tags: ['Builds'],
      summary: 'Конкретная сборка по ID',
      security: SECURITY,
      params: {
        type: 'object',
        properties: { id: { type: 'integer', description: 'ID сборки' } },
      },
    },
  }, async (request, reply) => {
    const build = await db.getBuildById(Number(request.params.id), request.user.id);
    if (!build) return reply.code(404).send({ error: 'Сборка не найдена' });
    return build;
  });


  // ── POST /api/builds ───────────────────────────────────────
  fastify.post('/builds', {
    preHandler: authenticate,
    schema: {
      tags: ['Builds'],
      summary: 'Сохранить сборку',
      security: SECURITY,
      body: {
        type: 'object', required: ['purpose', 'budget', 'total_price', 'components'],
        properties: {
          purpose:     { type: 'string', example: 'gaming' },
          budget:      { type: 'number', example: 1500 },
          total_price: { type: 'number', example: 1480 },
          chatId:      { type: 'integer', example: 1 },
          components:  { type: 'object' },
        },
      },
    },
  }, async (request, reply) => {
    const { purpose, budget, total_price, components, chatId } = request.body || {};
    if (!purpose || budget == null || total_price == null || !components) {
      return reply.code(400).send({ error: 'Недостаточно данных для сохранения сборки' });
    }
    try {
      const r = await fetch('http://localhost:8000/api/v1/save_build', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id:     request.user.id,
          chat_id:     chatId,
          purpose,
          budget,
          total_price,
          components,
        }),
      });
      const data = await r.json();
      if (!r.ok) return reply.code(r.status).send(data);
      return data;
    } catch (err) {
      return reply.code(500).send({ error: `Ошибка сохранения: ${err.message}` });
    }
  });


  // ── DELETE /api/builds/:id ─────────────────────────────────
  fastify.delete('/builds/:id', {
    preHandler: authenticate,
    schema: {
      tags: ['Builds'],
      summary: 'Удалить сборку',
      security: SECURITY,
      params: {
        type: 'object',
        properties: { id: { type: 'integer', description: 'ID сборки' } },
      },
    },
  }, async (request, reply) => {
    const deleted = await db.deleteBuild(Number(request.params.id), request.user.id);
    if (!deleted) return reply.code(404).send({ error: 'Сборка не найдена' });
    return { ok: true };
  });


  // ── GET /api/chats ─────────────────────────────────────────
  fastify.get('/chats', {
    preHandler: authenticate,
    schema: {
      tags: ['Chats'],
      summary: 'Список чатов пользователя',
      security: SECURITY,
    },
  }, async (request) => {
    return db.getChats(request.user.id);
  });


  // ── DELETE /api/chats/:id ─────────────────────────────────
  fastify.delete('/chats/:id', {
    preHandler: authenticate,
    schema: {
      tags: ['Chats'],
      summary: 'Удалить чат',
      security: SECURITY,
      params: {
        type: 'object',
        properties: { id: { type: 'integer', description: 'ID чата' } },
      },
    },
  }, async (request, reply) => {
    const chatId  = Number(request.params.id);
    const deleted = await db.deleteChat(chatId, request.user.id);
    if (!deleted) return reply.code(404).send({ error: 'Чат не найден' });
    clearBuildContext(request.user.id, chatId);
    return { ok: true };
  });


  // ── GET /api/chats/:id ─────────────────────────────────────
  fastify.get('/chats/:id', {
    preHandler: authenticate,
    schema: {
      tags: ['Chats'],
      summary: 'История чата',
      security: SECURITY,
      params: {
        type: 'object',
        properties: { id: { type: 'integer', description: 'ID чата' } },
      },
    },
  }, async (request, reply) => {
    const chatId = Number(request.params.id);
    const chat   = await db.getChatById(chatId, request.user.id);
    if (!chat) return reply.code(404).send({ error: 'Чат не найден' });

    const messages = await db.getMessages(chatId, 50);
    return { ...chat, messages };
  });


  // ── POST /api/chat/message ─────────────────────────────────
  fastify.post('/chat/message', {
    preHandler: authenticate,
    schema: {
      tags: ['Chat'],
      summary: 'Отправить сообщение ИИ-ассистенту',
      security: SECURITY,
      body: {
        type: 'object', required: ['message'],
        properties: {
          message: { type: 'string', example: 'Подбери игровой ПК за 1500 долларов' },
          chatId:  { type: 'integer', example: 1, description: 'ID существующего чата (null — создать новый)' },
        },
      },
    },
  }, async (request, reply) => {
    const { message, chatId: rawChatId } = request.body || {};
    if (!message || typeof message !== 'string' || !message.trim()) {
      return reply.code(400).send({ error: 'Поле message обязательно' });
    }

    const userId = request.user.id;
    let chatId   = rawChatId ? Number(rawChatId) : null;

    if (!chatId) {
      const title = message.slice(0, 60);
      const chat  = await db.createChat(userId, title);
      chatId = chat.id;
    } else {
      const chat = await db.getChatById(chatId, userId);
      if (!chat) return reply.code(404).send({ error: 'Чат не найден' });
    }

    await db.addMessage(chatId, 'user', message.trim());

    const history = await db.getMessages(chatId, 20);
    const contextHistory = history.slice(0, -1);

    const { content, intermediary } = await agentLoop(
      message.trim(),
      contextHistory,
      userId,
      chatId,
    );

    for (const m of intermediary) {
      await db.addMessage(chatId, m.role, m.content, m.tool_calls || null);
    }
    await db.addMessage(chatId, 'assistant', content);

    return { chatId, role: 'assistant', content };
  });
}

module.exports = routes;
