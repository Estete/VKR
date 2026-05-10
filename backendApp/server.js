'use strict';
const path    = require('path');
const fastify = require('fastify')({
  logger: true,
  ajv: { customOptions: { strict: false } },
});

fastify.register(require('@fastify/swagger'), {
  openapi: {
    info: {
      title:       'PC Builder API',
      description: 'Веб-сервер системы подбора комплектующих ПК',
      version:     '1.0.0',
    },
    components: {
      securitySchemes: {
        cookieAuth: { type: 'apiKey', in: 'cookie', name: 'sid' },
      },
    },
  },
});

fastify.register(require('@fastify/swagger-ui'), {
  routePrefix: '/documentation',
  uiConfig:    { docExpansion: 'list', deepLinking: true },
});

fastify.register(require('@fastify/cookie'));

fastify.register(require('@fastify/static'), {
  root:     path.join(__dirname, '../frontend'),
  prefix:   '/',
  wildcard: false,
});

fastify.register(require('./routers'), { prefix: '/api' });

fastify.setNotFoundHandler((request, reply) => {
  if (request.method === 'GET' && !request.url.startsWith('/api/')) {
    return reply.sendFile('index.html');
  }
  reply.code(404).send({ error: 'Not found' });
});

fastify.listen({ port: 3000, host: '0.0.0.0' }, (err) => {
  if (err) {
    fastify.log.error(err);
    process.exit(1);
  }
});
