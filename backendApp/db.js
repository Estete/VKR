'use strict';
const mysql = require('mysql2/promise');
const { v4: uuidv4 } = require('uuid');

const pool = mysql.createPool({
  host:     process.env.DB_HOST     || '127.0.0.1',
  port:     Number(process.env.DB_PORT) || 3307,
  user:     process.env.DB_USER     || 'pc_app',
  password: process.env.DB_PASSWORD || '',
  database: process.env.DB_NAME     || 'pc_parts',
  charset: 'utf8mb4',
  waitForConnections: true,
  connectionLimit: 10,
  timezone: '+00:00',
});

// ── Users ────────────────────────────────────────────────────

async function createUser(username, email, passwordHash) {
  const [r] = await pool.execute(
    'INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)',
    [username, email, passwordHash]
  );
  return { id: r.insertId, username, email };
}

async function getUserByEmail(email) {
  const [rows] = await pool.execute(
    'SELECT id, username, email, password_hash FROM users WHERE email = ?',
    [email]
  );
  return rows[0] || null;
}

async function getUserByUsername(username) {
  const [rows] = await pool.execute(
    'SELECT id, username, email, password_hash FROM users WHERE username = ?',
    [username]
  );
  return rows[0] || null;
}

// ── Sessions ─────────────────────────────────────────────────

const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000; // 7 дней

async function createSession(userId) {
  const sessionId  = uuidv4();
  const expiresAt  = new Date(Date.now() + SESSION_TTL_MS);
  await pool.execute(
    'INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)',
    [sessionId, userId, expiresAt]
  );
  return { sessionId, expiresAt };
}

async function getSession(sessionId) {
  const [rows] = await pool.execute(
    `SELECT s.user_id, s.expires_at, u.username, u.email
     FROM sessions s
     JOIN users u ON s.user_id = u.id
     WHERE s.id = ? AND s.expires_at > NOW()`,
    [sessionId]
  );
  return rows[0] || null;
}

async function deleteSession(sessionId) {
  await pool.execute('DELETE FROM sessions WHERE id = ?', [sessionId]);
}

// ── Chats ─────────────────────────────────────────────────────

async function getChats(userId) {
  const [rows] = await pool.execute(
    'SELECT id, title, created_at, updated_at FROM chats WHERE user_id = ? ORDER BY updated_at DESC',
    [userId]
  );
  return rows;
}

async function createChat(userId, title = 'Новый чат') {
  const [r] = await pool.execute(
    'INSERT INTO chats (user_id, title) VALUES (?, ?)',
    [userId, title]
  );
  return { id: r.insertId, title };
}

async function getChatById(chatId, userId) {
  const [rows] = await pool.execute(
    'SELECT id, title, created_at, updated_at FROM chats WHERE id = ? AND user_id = ?',
    [chatId, userId]
  );
  return rows[0] || null;
}

async function updateChatTitle(chatId, title) {
  await pool.execute('UPDATE chats SET title = ? WHERE id = ?', [title, chatId]);
}

// ── Messages ──────────────────────────────────────────────────

async function getMessages(chatId, limit = 20) {
  const n = Math.max(1, Math.floor(Number(limit) || 20));
  const [rows] = await pool.execute(
    `SELECT id, role, content, tool_calls, tool_call_id, created_at
     FROM messages WHERE chat_id = ?
     ORDER BY created_at DESC, id DESC LIMIT ${n}`,
    [chatId]
  );
  // хронологический порядок + разбор JSON
  // mysql2 может вернуть JSON-колонку уже как объект (не строку) — обрабатываем оба случая
  return rows.reverse().map(m => ({
    ...m,
    tool_calls: m.tool_calls
      ? (typeof m.tool_calls === 'string' ? JSON.parse(m.tool_calls) : m.tool_calls)
      : null,
  }));
}

async function addMessage(chatId, role, content, toolCalls = null, toolCallId = null) {
  const [r] = await pool.execute(
    'INSERT INTO messages (chat_id, role, content, tool_calls, tool_call_id) VALUES (?, ?, ?, ?, ?)',
    [chatId, role, content, toolCalls ? JSON.stringify(toolCalls) : null, toolCallId]
  );
  await pool.execute('UPDATE chats SET updated_at = NOW() WHERE id = ?', [chatId]);
  return { id: r.insertId };
}

// ── Builds ────────────────────────────────────────────────────

async function getBuilds(userId) {
  const [rows] = await pool.execute(
    'SELECT id, purpose, budget, total_price, created_at, version FROM builds WHERE user_id = ? ORDER BY created_at DESC',
    [userId]
  );
  return rows;
}

async function getBuildById(buildId, userId) {
  const [builds] = await pool.execute(
    'SELECT id, purpose, budget, total_price, created_at, version FROM builds WHERE id = ? AND user_id = ?',
    [buildId, userId]
  );
  if (!builds[0]) return null;

  const [components] = await pool.execute(
    `SELECT bc.category, c.id AS component_id, c.name, c.price
     FROM build_components bc
     JOIN component c ON bc.component_id = c.id
     WHERE bc.build_id = ?`,
    [buildId]
  );

  return { ...builds[0], components };
}

async function deleteBuild(buildId, userId) {
  const [r] = await pool.execute(
    'DELETE FROM builds WHERE id = ? AND user_id = ?',
    [buildId, userId]
  );
  return r.affectedRows > 0;
}

async function deleteChat(chatId, userId) {
  const chat = await getChatById(chatId, userId);
  if (!chat) return false;

  const conn = await pool.getConnection();
  try {
    await conn.beginTransaction();
    // Сохраняем сборки: обнуляем ссылку на чат (не удаляем)
    await conn.execute('UPDATE builds SET chat_id = NULL WHERE chat_id = ?', [chatId]);
    await conn.execute('DELETE FROM messages WHERE chat_id = ?', [chatId]);
    await conn.execute('DELETE FROM chats WHERE id = ? AND user_id = ?', [chatId, userId]);
    await conn.commit();
    return true;
  } catch (err) {
    await conn.rollback();
    throw err;
  } finally {
    conn.release();
  }
}

// ── Test helpers (используются только в тестах) ──────────────

async function query(sql, params = []) {
  const [rows] = await pool.execute(sql, params);
  return rows;
}

async function closePool() {
  await pool.end();
}

module.exports = {
  createUser, getUserByEmail, getUserByUsername,
  createSession, getSession, deleteSession,
  getChats, createChat, getChatById, updateChatTitle,
  getMessages, addMessage,
  deleteChat,
  getBuilds, getBuildById, deleteBuild,
  query, closePool,
};
