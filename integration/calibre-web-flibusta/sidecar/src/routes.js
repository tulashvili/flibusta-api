const express = require('express');
const { unwrapIfZip } = require('./unzip');
const { NotFound, UpstreamError } = require('./flibustaClient');

function translit(str) {
  return String(str || 'book')
    .replace(/[^\x20-\x7E]/g, '_')
    .replace(/[\\/:*?"<>|]/g, '_')
    .slice(0, 120);
}

function buildRouter(client) {
  const router = express.Router();

  router.get('/health', (req, res) => res.json({ status: 'ok' }));

  router.get('/search', async (req, res) => {
    const q = (req.query.q || '').trim();
    if (!q) return res.status(400).json({ error: 'q is required' });
    try {
      const out = await client.search(q, req.query.page || 0);
      return res.json(out);
    } catch (err) {
      return res.status(err instanceof UpstreamError ? 502 : 500).json({ error: err.message });
    }
  });

  router.get('/cover/:id', async (req, res) => {
    try {
      const { stream, contentType } = await client.fetchCover(req.params.id);
      res.set('Content-Type', contentType);
      res.set('Cache-Control', 'public, max-age=86400');
      return stream.pipe(res);
    } catch (err) {
      const code = err instanceof NotFound ? 404 : 502;
      return res.status(code).json({ error: err.message });
    }
  });

  router.get('/download/:id/:format', async (req, res) => {
    try {
      const raw = await client.fetchBook(req.params.id, req.params.format);
      const { buffer, ext, contentType } = await unwrapIfZip(raw, req.params.format);
      if (!buffer || buffer.length === 0) return res.status(422).json({ error: 'empty file' });
      res.set('Content-Type', contentType);
      res.set('Content-Disposition',
        `attachment; filename="${translit(req.query.title)}.${ext}"`);
      return res.send(buffer);
    } catch (err) {
      const code = err instanceof NotFound ? 404
        : err instanceof UpstreamError ? 502 : 500;
      return res.status(code).json({ error: err.message });
    }
  });

  return router;
}

module.exports = { buildRouter };
