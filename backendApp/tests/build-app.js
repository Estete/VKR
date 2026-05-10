'use strict';

async function buildApp() {
  const fastify = require('fastify')({ logger: false });
  await fastify.register(require('@fastify/cookie'));
  await fastify.register(require('../routers'), { prefix: '/api' });
  await fastify.ready();
  return fastify;
}

module.exports = buildApp;
