const { expect } = require('chai');
const express = require('express');
const http = require('http');
const { buildRouter } = require('../src/routes');
const { NotFound, UpstreamError } = require('../src/flibustaClient');

function startServer(client) {
  const app = express();
  app.use(buildRouter(client));
  return new Promise((resolve) => {
    const server = app.listen(0, () => resolve({ server, port: server.address().port }));
  });
}

function get(port, path) {
  return new Promise((resolve) => {
    http.get({ port, path }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, body: Buffer.concat(chunks) }));
    });
  });
}

describe('sidecar routes', () => {
  it('GET /health returns ok', async () => {
    const { server, port } = await startServer({});
    const res = await get(port, '/health');
    expect(res.status).to.equal(200);
    expect(JSON.parse(res.body).status).to.equal('ok');
    server.close();
  });

  it('GET /search proxies the client result', async () => {
    const client = {
      search: async (q, page) => ({ results: [{ flibustaId: 1, title: q }], page, hasNext: false }),
    };
    const { server, port } = await startServer(client);
    const res = await get(port, '/search?q=test&page=0');
    expect(res.status).to.equal(200);
    expect(JSON.parse(res.body).results[0].title).to.equal('test');
    server.close();
  });

  it('GET /search without q returns 400', async () => {
    const { server, port } = await startServer({ search: async () => ({}) });
    const res = await get(port, '/search');
    expect(res.status).to.equal(400);
    server.close();
  });

  it('GET /download maps NotFound to 404 and UpstreamError to 502', async () => {
    const client = {
      fetchBook: async (id) => {
        if (id === '404') throw new NotFound('nope');
        throw new UpstreamError('down');
      },
    };
    const { server, port } = await startServer(client);
    expect((await get(port, '/download/404/fb2')).status).to.equal(404);
    expect((await get(port, '/download/9/fb2')).status).to.equal(502);
    server.close();
  });
});
