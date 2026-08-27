# Calibre-Web Flibusta Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a native "Download Books" section to a self-hosted Calibre-Web that searches Flibusta and imports a chosen book straight into the Calibre library via Calibre-Web's own upload pipeline.

**Architecture:** A stateless Node sidecar wraps the `flibusta-api` library and exposes a small REST contract (search / cover / download-with-unzip). A Python package mounted into the Calibre-Web image adds a Flask blueprint that proxies the sidecar and, on "add", reuses Calibre-Web's internal `file_handling_on_upload` + `create_book_on_upload` helpers. Two tiny pinned patches register the blueprint and add the nav item. Everything ships via a custom Docker image + compose file.

**Tech Stack:** Node 20 + Express + `flibusta-api` (sidecar); Python 3 + Flask + `requests` (blueprint); pytest, mocha + nock; Docker, docker-compose; base image `lscr.io/linuxserver/calibre-web`.

**Spec:** `docs/superpowers/specs/2026-08-27-calibre-web-flibusta-design.md`

## Global Constraints

- Base Calibre-Web image: `lscr.io/linuxserver/calibre-web`, pinned by digest in the custom `Dockerfile`. Never `:latest`.
- Calibre-Web app path inside the image: `/app/calibre-web` (confirm in Task 6; adjust all patch paths if different).
- Core patches touch exactly two files: `cps/main.py` and `cps/templates/layout.html`. All other feature code lives under `integration/calibre-web-flibusta/`.
- Sidecar is never published outside the compose network. No auth between blueprint and sidecar; isolation is by network only.
- Sidecar env vars: `FLIBUSTA_BASE_URL` (default `http://flibusta.is/`), `FLIBUSTA_PROXY` (optional), `PORT` (default `8080`), `REQUEST_TIMEOUT_MS` (default `20000`), `RETRY_COUNT` (default `1`), `USER_AGENT`.
- Blueprint env var: `FLIBUSTA_SIDECAR_URL` (e.g. `http://flibusta-sidecar:8080`).
- Flibusta book identifier in Calibre stored as identifier type `flibusta`, value `str(flibustaId)`.
- Format short names (MIME → name): `application/fb2+zip`→`fb2`, `application/epub`→`epub`, `application/x-mobipocket-ebook`→`mobi`, `application/pdf`→`pdf`, `application/txt+zip`→`txt`, `application/rtf+zip`→`rtf`, `application/html+zip`→`html`, `application/djvu`→`djvu`, `application/msword`→`doc`. Unknown MIME → dropped.
- Permission gate on every blueprint route and on the nav item: user must have upload permission AND `config.config_uploading` must be true.
- Commit after every green test cycle. Conventional commit messages.

---

## File Structure

```
integration/calibre-web-flibusta/
  sidecar/
    package.json
    server.js                  # Express app wiring only
    src/
      flibustaClient.js        # thin wrapper over flibusta-api (search, cover URL, raw download)
      formats.js               # MIME <-> short-name mapping
      mapSearchResult.js       # OPDS result -> sidecar /search contract
      unzip.js                 # unwrap single-file zip archives
      routes.js                # route handlers
    test/
      formats.test.js
      mapSearchResult.test.js
      unzip.test.js
      routes.test.js
      fixtures/                # recorded OPDS responses, sample zip
    Dockerfile
  cps_flibusta/
    __init__.py                # exports `flibusta` blueprint
    views.py                   # routes: index, search, cover, add
    sidecar.py                 # HTTP client for the sidecar + exceptions
    metadata.py                # enrich BookMeta from OPDS fields
    dedup.py                   # look up existing book by flibusta identifier
    templates/flibusta.html
    static/flibusta.js
    static/flibusta.css
  patches/
    0001-register-flibusta-blueprint.patch
    0002-nav-menu-item.patch
  tests/
    conftest.py
    test_sidecar_client.py
    test_metadata.py
    test_dedup.py
    test_views.py
    test_add_flow.py
  Dockerfile                   # FROM lscr.io/linuxserver/calibre-web@sha256:...
  docker-compose.yml
  README.md
```

---

## Phase 1 — Node sidecar

### Task 1: Sidecar scaffold + format mapping

**Files:**
- Create: `integration/calibre-web-flibusta/sidecar/package.json`
- Create: `integration/calibre-web-flibusta/sidecar/src/formats.js`
- Test: `integration/calibre-web-flibusta/sidecar/test/formats.test.js`

**Interfaces:**
- Produces:
  - `mimeToFormat(mime: string): string | null`
  - `MIME_TO_FORMAT: Record<string,string>` (the table from Global Constraints)

- [ ] **Step 1: Write `package.json`**

```json
{
  "name": "flibusta-sidecar",
  "version": "0.1.0",
  "private": true,
  "type": "commonjs",
  "scripts": {
    "start": "node server.js",
    "test": "mocha --recursive test"
  },
  "dependencies": {
    "express": "^4.19.2",
    "flibusta-api": "^0.5.1"
  },
  "devDependencies": {
    "chai": "^4.4.1",
    "mocha": "^10.4.0",
    "nock": "^13.5.4"
  }
}
```

Run: `cd integration/calibre-web-flibusta/sidecar && npm install`

- [ ] **Step 2: Write the failing test** — `test/formats.test.js`

```js
const { expect } = require('chai');
const { mimeToFormat } = require('../src/formats');

describe('mimeToFormat', () => {
  it('maps known Flibusta MIME types to short names', () => {
    expect(mimeToFormat('application/fb2+zip')).to.equal('fb2');
    expect(mimeToFormat('application/epub')).to.equal('epub');
    expect(mimeToFormat('application/x-mobipocket-ebook')).to.equal('mobi');
    expect(mimeToFormat('application/pdf')).to.equal('pdf');
  });

  it('returns null for unknown MIME types', () => {
    expect(mimeToFormat('application/octet-stream')).to.equal(null);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd integration/calibre-web-flibusta/sidecar && npx mocha test/formats.test.js`
Expected: FAIL — `Cannot find module '../src/formats'`

- [ ] **Step 4: Write minimal implementation** — `src/formats.js`

```js
const MIME_TO_FORMAT = {
  'application/fb2+zip': 'fb2',
  'application/epub': 'epub',
  'application/epub+zip': 'epub',
  'application/x-mobipocket-ebook': 'mobi',
  'application/pdf': 'pdf',
  'application/pdf+zip': 'pdf',
  'application/pdf+rar': 'pdf',
  'application/txt+zip': 'txt',
  'application/rtf+zip': 'rtf',
  'application/html+zip': 'html',
  'application/djvu': 'djvu',
  'application/msword': 'doc',
};

function mimeToFormat(mime) {
  return MIME_TO_FORMAT[mime] || null;
}

module.exports = { MIME_TO_FORMAT, mimeToFormat };
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd integration/calibre-web-flibusta/sidecar && npx mocha test/formats.test.js`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add integration/calibre-web-flibusta/sidecar
git commit -m "feat(sidecar): scaffold + MIME-to-format mapping"
```

---

### Task 2: OPDS result → `/search` contract mapper

**Files:**
- Create: `integration/calibre-web-flibusta/sidecar/src/mapSearchResult.js`
- Test: `integration/calibre-web-flibusta/sidecar/test/mapSearchResult.test.js`
- Create: `integration/calibre-web-flibusta/sidecar/test/fixtures/opds-search.json` (paste a real `getBooksByNameFromOpdsPaginated` result — capture with `npm run example-search-book-by-name-opds -- "Богатый папа"` from the `flibusta-api` repo)

**Interfaces:**
- Consumes: `mimeToFormat` from Task 1.
- Produces: `mapSearchResult(opdsItem: object): object` returning
  `{ flibustaId, title, authors: [{name, flibustaId}], series, categories, description, coverUrl, formats: [{format, mime}] }`
  and `parseAuthorId(uri: string): number | null` (`/a/5778` → `5778`).

- [ ] **Step 1: Write the failing test** — `test/mapSearchResult.test.js`

```js
const { expect } = require('chai');
const fixture = require('./fixtures/opds-search.json');
const { mapSearchResult, parseAuthorId } = require('../src/mapSearchResult');

describe('parseAuthorId', () => {
  it('extracts numeric id from an /a/ uri', () => {
    expect(parseAuthorId('/a/5778')).to.equal(5778);
  });
  it('returns null when there is no id', () => {
    expect(parseAuthorId(undefined)).to.equal(null);
  });
});

describe('mapSearchResult', () => {
  it('maps an OPDS entry to the sidecar contract', () => {
    const out = mapSearchResult(fixture[0]);
    expect(out.flibustaId).to.be.a('number');
    expect(out.title).to.be.a('string');
    expect(out.authors[0]).to.have.keys(['name', 'flibustaId']);
    expect(out.coverUrl).to.equal(`/cover/${out.flibustaId}`);
    out.formats.forEach((f) => {
      expect(f).to.have.keys(['format', 'mime']);
      expect(f.format).to.be.a('string');
    });
  });

  it('drops downloads whose MIME type is unknown', () => {
    const item = JSON.parse(JSON.stringify(fixture[0]));
    item.downloads.push({ link: '/b/1/xxx', type: 'application/octet-stream' });
    const out = mapSearchResult(item);
    expect(out.formats.map((f) => f.mime)).to.not.include('application/octet-stream');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd integration/calibre-web-flibusta/sidecar && npx mocha test/mapSearchResult.test.js`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation** — `src/mapSearchResult.js`

```js
const { mimeToFormat } = require('./formats');

function parseAuthorId(uri) {
  if (!uri) return null;
  const m = String(uri).match(/\/a\/(\d+)/);
  return m ? Number(m[1]) : null;
}

// A Flibusta OPDS download link looks like /b/416925/fb2
function parseBookId(item) {
  for (const d of item.downloads || []) {
    const m = String(d.link || '').match(/\/b\/(\d+)\//);
    if (m) return Number(m[1]);
  }
  return null;
}

function mapSearchResult(item) {
  const flibustaId = parseBookId(item);
  const authors = (item.author || []).map((a) => ({
    name: a.name,
    flibustaId: parseAuthorId(a.uri),
  }));
  const formats = (item.downloads || [])
    .map((d) => ({ format: mimeToFormat(d.type), mime: d.type }))
    .filter((f) => f.format !== null);

  return {
    flibustaId,
    title: item.title,
    authors,
    series: extractSeries(item.description) || null,
    categories: item.categories || [],
    description: item.description || '',
    coverUrl: flibustaId ? `/cover/${flibustaId}` : null,
    formats,
  };
}

// Flibusta OPDS puts "Серия: X" inside the HTML description
function extractSeries(description) {
  if (!description) return null;
  const m = description.match(/Серия:\s*([^<]+)</);
  return m ? m[1].trim() : null;
}

module.exports = { mapSearchResult, parseAuthorId };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd integration/calibre-web-flibusta/sidecar && npx mocha test/mapSearchResult.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add integration/calibre-web-flibusta/sidecar
git commit -m "feat(sidecar): map OPDS results to search contract"
```

---

### Task 3: Single-file zip unwrapping

**Files:**
- Create: `integration/calibre-web-flibusta/sidecar/src/unzip.js`
- Test: `integration/calibre-web-flibusta/sidecar/test/unzip.test.js`
- Create: `integration/calibre-web-flibusta/sidecar/test/fixtures/single.fb2.zip` (a zip containing exactly one `*.fb2` file with body `<?xml version="1.0"?><FictionBook/>`)

**Interfaces:**
- Produces: `async unwrapIfZip(buffer: Buffer, requestedFormat: string): Promise<{ buffer: Buffer, ext: string, contentType: string }>`
  - If `buffer` is a zip with a single entry, return that entry's bytes and its extension.
  - Otherwise return the input buffer, `ext = requestedFormat`, and a content type from a small lookup.
- Uses Node built-in `zlib`? No — use the `adm-zip` dependency (add to `package.json` dependencies: `"adm-zip": "^0.5.12"`, then `npm install`).

- [ ] **Step 1: Add dependency**

Edit `package.json` dependencies to include `"adm-zip": "^0.5.12"`, then run `cd integration/calibre-web-flibusta/sidecar && npm install`.

- [ ] **Step 2: Write the failing test** — `test/unzip.test.js`

```js
const fs = require('fs');
const path = require('path');
const { expect } = require('chai');
const { unwrapIfZip } = require('../src/unzip');

describe('unwrapIfZip', () => {
  it('extracts the single file from a zip archive', async () => {
    const zip = fs.readFileSync(path.join(__dirname, 'fixtures/single.fb2.zip'));
    const out = await unwrapIfZip(zip, 'fb2');
    expect(out.ext).to.equal('fb2');
    expect(out.buffer.toString()).to.contain('FictionBook');
    expect(out.contentType).to.contain('xml');
  });

  it('passes a non-zip buffer through unchanged', async () => {
    const raw = Buffer.from('%PDF-1.4 ...');
    const out = await unwrapIfZip(raw, 'pdf');
    expect(out.buffer).to.equal(raw);
    expect(out.ext).to.equal('pdf');
    expect(out.contentType).to.equal('application/pdf');
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd integration/calibre-web-flibusta/sidecar && npx mocha test/unzip.test.js`
Expected: FAIL — module not found

- [ ] **Step 4: Write minimal implementation** — `src/unzip.js`

```js
const AdmZip = require('adm-zip');

const CONTENT_TYPES = {
  fb2: 'application/x-fictionbook+xml',
  epub: 'application/epub+zip',
  mobi: 'application/x-mobipocket-ebook',
  pdf: 'application/pdf',
  txt: 'text/plain',
  rtf: 'application/rtf',
  html: 'text/html',
  djvu: 'image/vnd.djvu',
  doc: 'application/msword',
};

function isZip(buffer) {
  return buffer.length > 4 && buffer[0] === 0x50 && buffer[1] === 0x4b
    && (buffer[2] === 0x03 || buffer[2] === 0x05 || buffer[2] === 0x07);
}

async function unwrapIfZip(buffer, requestedFormat) {
  if (isZip(buffer)) {
    const entries = new AdmZip(buffer).getEntries().filter((e) => !e.isDirectory);
    if (entries.length === 1) {
      const entry = entries[0];
      const ext = (entry.entryName.split('.').pop() || requestedFormat).toLowerCase();
      return {
        buffer: entry.getData(),
        ext,
        contentType: CONTENT_TYPES[ext] || 'application/octet-stream',
      };
    }
    // keep multi-entry archives (e.g. real .epub) as-is
    return { buffer, ext: requestedFormat, contentType: CONTENT_TYPES[requestedFormat] || 'application/octet-stream' };
  }
  return {
    buffer,
    ext: requestedFormat,
    contentType: CONTENT_TYPES[requestedFormat] || 'application/octet-stream',
  };
}

module.exports = { unwrapIfZip };
```

Note: real `.epub` files are themselves zips. Flibusta's `epub` endpoint already returns
a valid epub, so `requestedFormat === 'epub'` short-circuits before entry inspection:

- [ ] **Step 5: Guard epub** — prepend to `unwrapIfZip` body, before `isZip`:

```js
  if (requestedFormat === 'epub') {
    return { buffer, ext: 'epub', contentType: 'application/epub+zip' };
  }
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd integration/calibre-web-flibusta/sidecar && npx mocha test/unzip.test.js`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add integration/calibre-web-flibusta/sidecar
git commit -m "feat(sidecar): unwrap single-file zip downloads"
```

---

### Task 4: Flibusta client + route handlers + server

**Files:**
- Create: `integration/calibre-web-flibusta/sidecar/src/flibustaClient.js`
- Create: `integration/calibre-web-flibusta/sidecar/src/routes.js`
- Create: `integration/calibre-web-flibusta/sidecar/server.js`
- Test: `integration/calibre-web-flibusta/sidecar/test/routes.test.js`

**Interfaces:**
- Consumes: `mapSearchResult` (Task 2), `unwrapIfZip` (Task 3).
- Produces:
  - `flibustaClient.search(q, page)` → `{ results, page, hasNext }` (results already mapped)
  - `flibustaClient.fetchCover(flibustaId)` → `{ stream, contentType }` or throws `NotFound`
  - `flibustaClient.fetchBook(flibustaId, format)` → `Buffer` of the raw download or throws `NotFound` / `UpstreamError`
  - `buildRouter(client)` → Express router mounting `GET /health`, `/search`, `/cover/:id`, `/download/:id/:format`
- Error classes `NotFound`, `UpstreamError` exported from `flibustaClient.js`.

- [ ] **Step 1: Write the failing test** — `test/routes.test.js`

```js
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd integration/calibre-web-flibusta/sidecar && npx mocha test/routes.test.js`
Expected: FAIL — module not found

- [ ] **Step 3: Write `src/flibustaClient.js`**

```js
const FlibustaAPI = require('flibusta-api').default || require('flibusta-api');
const { mapSearchResult } = require('./mapSearchResult');

class NotFound extends Error {}
class UpstreamError extends Error {}

const BASE_URL = process.env.FLIBUSTA_BASE_URL || 'http://flibusta.is/';
const TIMEOUT = Number(process.env.REQUEST_TIMEOUT_MS || 20000);
const RETRIES = Number(process.env.RETRY_COUNT || 1);
const UA = process.env.USER_AGENT || 'flibusta-sidecar/0.1';

function makeApi() {
  return new FlibustaAPI(BASE_URL, { timeout: TIMEOUT, headers: { 'User-Agent': UA } });
}

async function withRetry(fn) {
  let lastErr;
  for (let i = 0; i <= RETRIES; i += 1) {
    try {
      return await fn();
    } catch (err) {
      lastErr = err;
    }
  }
  throw new UpstreamError(lastErr ? lastErr.message : 'upstream failed');
}

async function search(q, page = 0) {
  const api = makeApi();
  const raw = await withRetry(() => api.getBooksByNameFromOpdsPaginated(q, Number(page) || 0, 20));
  // flibusta-api paginated result shape: { books: [...], pagesInformation: {...} } (confirm in impl)
  const items = raw.books || raw.result || raw;
  const results = (items || []).map(mapSearchResult).filter((r) => r.flibustaId);
  const hasNext = Boolean(raw.pagesInformation && raw.pagesInformation.next);
  return { results, page: Number(page) || 0, hasNext };
}

// direct HTTP for binary endpoints; flibusta-api does not expose file download
async function rawGet(pathname, responseType) {
  const axios = require('axios');
  const res = await withRetry(() => axios.get(new URL(pathname, BASE_URL).toString(), {
    responseType,
    timeout: TIMEOUT,
    headers: { 'User-Agent': UA },
    proxy: false,
    validateStatus: (s) => s < 500,
  }));
  if (res.status === 404) throw new NotFound(pathname);
  if (res.status >= 400) throw new UpstreamError(`status ${res.status}`);
  return res;
}

async function fetchCover(flibustaId) {
  const res = await rawGet(`/i/${String(flibustaId).slice(0, 2)}/${flibustaId}/cover.jpg`, 'stream')
    .catch(() => rawGet(`/b/${flibustaId}/cover`, 'stream'));
  return { stream: res.data, contentType: res.headers['content-type'] || 'image/jpeg' };
}

async function fetchBook(flibustaId, format) {
  const res = await rawGet(`/b/${flibustaId}/${format}`, 'arraybuffer');
  return Buffer.from(res.data);
}

module.exports = { search, fetchCover, fetchBook, NotFound, UpstreamError };
```

Note: add `axios` to `package.json` dependencies (it is already a transitive dep of `flibusta-api`, but declare it explicitly: `"axios": "1.2.6"`). Run `npm install`.
Note: the exact shape returned by `getBooksByNameFromOpdsPaginated` MUST be confirmed by
running the example in the `flibusta-api` repo; adjust `raw.books` / `raw.pagesInformation`
accessors and update the Task 2 fixture accordingly.

- [ ] **Step 4: Write `src/routes.js`**

```js
const express = require('express');
const { unwrapIfZip } = require('./unzip');
const { NotFound, UpstreamError } = require('./flibustaClient');

function translit(str) {
  return String(str || 'book').replace(/[^\x20-\x7E]/g, '_').replace(/[\\/:*?"<>|]/g, '_').slice(0, 120);
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
```

- [ ] **Step 5: Write `server.js`**

```js
const express = require('express');
const client = require('./src/flibustaClient');
const { buildRouter } = require('./src/routes');

const app = express();
app.use(buildRouter(client));

const port = Number(process.env.PORT || 8080);
app.listen(port, () => console.log(`flibusta-sidecar listening on ${port}`));
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd integration/calibre-web-flibusta/sidecar && npm test`
Expected: PASS (all sidecar test files)

- [ ] **Step 7: Manual smoke against real Flibusta**

Run:
```bash
cd integration/calibre-web-flibusta/sidecar && node server.js &
curl -s 'http://localhost:8080/search?q=Богатый%20папа' | head -c 400
curl -s -o /tmp/b.fb2 'http://localhost:8080/download/416925/fb2' && file /tmp/b.fb2
kill %1
```
Expected: JSON with results; `/tmp/b.fb2` is XML (FictionBook), not a zip.

- [ ] **Step 8: Commit**

```bash
git add integration/calibre-web-flibusta/sidecar
git commit -m "feat(sidecar): client, routes, and server entrypoint"
```

---

### Task 5: Sidecar Dockerfile

**Files:**
- Create: `integration/calibre-web-flibusta/sidecar/Dockerfile`
- Create: `integration/calibre-web-flibusta/sidecar/.dockerignore`

**Interfaces:**
- Produces: an image that runs `node server.js` and answers `GET /health` on `$PORT`.

- [ ] **Step 1: Write `.dockerignore`**

```
node_modules
test
```

- [ ] **Step 2: Write `Dockerfile`**

```dockerfile
FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --omit=dev
COPY server.js ./
COPY src ./src
ENV PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://localhost:8080/health || exit 1
CMD ["node", "server.js"]
```

- [ ] **Step 3: Build and run**

Run:
```bash
cd integration/calibre-web-flibusta/sidecar
docker build -t flibusta-sidecar:dev .
docker run --rm -p 8080:8080 flibusta-sidecar:dev &
sleep 2 && curl -s http://localhost:8080/health
docker stop $(docker ps -q --filter ancestor=flibusta-sidecar:dev)
```
Expected: `{"status":"ok"}`

- [ ] **Step 4: Commit**

```bash
git add integration/calibre-web-flibusta/sidecar/Dockerfile integration/calibre-web-flibusta/sidecar/.dockerignore
git commit -m "build(sidecar): container image"
```

---

## Phase 2 — Calibre-Web blueprint

> Phase 2 tests run with `pytest` against the `calibre-web` source. Set up once:
> clone the pinned Calibre-Web release into `integration/calibre-web-flibusta/.calibre-web-src/`
> (gitignored) and `pip install -r requirements.txt` from it into a venv, OR run these
> tests inside the built image (Task 10). `conftest.py` inserts that path on `sys.path`.

### Task 6: Confirm Calibre-Web internals + pin the release

**Files:**
- Create: `integration/calibre-web-flibusta/README.md` (record findings)
- Create: `integration/calibre-web-flibusta/.gitignore` (`.calibre-web-src/`, `*.pyc`, `__pycache__/`)

**Interfaces:**
- Produces: documented facts the later tasks depend on — the pinned image digest, the
  app path, and the exact current signatures of `file_handling_on_upload`,
  `create_book_on_upload`, `uploader.upload`, and the `BookMeta` fields.

- [ ] **Step 1: Pin the base image**

Run:
```bash
docker pull lscr.io/linuxserver/calibre-web:latest
docker inspect --format='{{index .RepoDigests 0}}' lscr.io/linuxserver/calibre-web:latest
```
Record the `@sha256:...` digest in `README.md`.

- [ ] **Step 2: Locate the app + read the helpers**

Run:
```bash
docker run --rm --entrypoint sh lscr.io/linuxserver/calibre-web:latest -c \
  'ls -d /app/calibre-web && sed -n "1,40p;/def file_handling_on_upload/,/return meta, None/p;/def create_book_on_upload/,/return db_book, input_authors, title_dir/p" /app/calibre-web/cps/editbooks.py'
```
Confirm the app path and paste the two function bodies into `README.md`. If they differ
from the versions quoted in the spec, note every difference — Tasks 8 and 9 must match
the pinned release, not the spec.

- [ ] **Step 3: Read `BookMeta` definition**

Run:
```bash
docker run --rm --entrypoint sh lscr.io/linuxserver/calibre-web:latest -c \
  'grep -n "BookMeta" /app/calibre-web/cps/uploader.py | head; sed -n "1,60p" /app/calibre-web/cps/uploader.py'
```
Record the field list of `BookMeta` (namedtuple) in `README.md` — `metadata.py` (Task 8)
depends on the exact names.

- [ ] **Step 4: Commit**

```bash
git add integration/calibre-web-flibusta/README.md integration/calibre-web-flibusta/.gitignore
git commit -m "docs: pin Calibre-Web release and record upload internals"
```

---

### Task 7: Sidecar HTTP client (`sidecar.py`)

**Files:**
- Create: `integration/calibre-web-flibusta/cps_flibusta/__init__.py`
- Create: `integration/calibre-web-flibusta/cps_flibusta/sidecar.py`
- Create: `integration/calibre-web-flibusta/tests/conftest.py`
- Test: `integration/calibre-web-flibusta/tests/test_sidecar_client.py`

**Interfaces:**
- Produces:
  - `sidecar.search(q: str, page: int = 0) -> dict` (raises `SidecarUnavailable`, `UpstreamError`)
  - `sidecar.download(flibusta_id: int, fmt: str, title: str = "") -> tuple[bytes, str]` → `(content, filename)` (raises `BookNotFound`, `SidecarUnavailable`, `UpstreamError`)
  - `sidecar.cover_response(flibusta_id: int) -> requests.Response` (streamed; caller pipes it)
  - exceptions: `SidecarError`, `SidecarUnavailable(SidecarError)`, `BookNotFound(SidecarError)`, `UpstreamError(SidecarError)`
  - `SIDECAR_URL` read from `os.environ["FLIBUSTA_SIDECAR_URL"]`, default `http://flibusta-sidecar:8080`

- [ ] **Step 1: Write `conftest.py`**

```python
import os
import sys

# Allow importing the package under test and (optionally) calibre-web source
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
CW_SRC = os.path.abspath(os.path.join(HERE, "..", ".calibre-web-src"))
if os.path.isdir(CW_SRC):
    sys.path.insert(0, CW_SRC)

os.environ.setdefault("FLIBUSTA_SIDECAR_URL", "http://sidecar.test")
```

- [ ] **Step 2: Write the failing test** — `tests/test_sidecar_client.py`

```python
import pytest
import responses
from cps_flibusta import sidecar


@responses.activate
def test_search_returns_parsed_json():
    responses.add(responses.GET, "http://sidecar.test/search",
                  json={"results": [{"flibustaId": 1, "title": "X"}], "page": 0, "hasNext": False},
                  status=200)
    out = sidecar.search("x")
    assert out["results"][0]["flibustaId"] == 1


@responses.activate
def test_search_connection_error_raises_unavailable():
    responses.add(responses.GET, "http://sidecar.test/search",
                  body=responses.ConnectionError())
    with pytest.raises(sidecar.SidecarUnavailable):
        sidecar.search("x")


@responses.activate
def test_download_404_raises_book_not_found():
    responses.add(responses.GET, "http://sidecar.test/download/9/fb2",
                  json={"error": "nope"}, status=404)
    with pytest.raises(sidecar.BookNotFound):
        sidecar.download(9, "fb2")


@responses.activate
def test_download_returns_bytes_and_filename():
    responses.add(responses.GET, "http://sidecar.test/download/5/fb2",
                  body=b"<FictionBook/>", status=200,
                  headers={"Content-Disposition": 'attachment; filename="bogatyj_papa.fb2"'})
    content, filename = sidecar.download(5, "fb2", title="Богатый папа")
    assert content == b"<FictionBook/>"
    assert filename == "bogatyj_papa.fb2"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd integration/calibre-web-flibusta && python -m pytest tests/test_sidecar_client.py -q`
Expected: FAIL — `ModuleNotFoundError: cps_flibusta`
(Install test deps first: `pip install pytest responses requests flask`.)

- [ ] **Step 4: Write `cps_flibusta/__init__.py`**

```python
from .views import flibusta  # noqa: F401
```

Temporarily make it `from .sidecar import *` if `views` does not exist yet; restore in Task 9.
Simplest: leave `__init__.py` empty until Task 9, and import `cps_flibusta.sidecar` directly in the test (already done above).

- [ ] **Step 5: Write `cps_flibusta/sidecar.py`**

```python
import os
import re
import requests

SIDECAR_URL = os.environ.get("FLIBUSTA_SIDECAR_URL", "http://flibusta-sidecar:8080").rstrip("/")
TIMEOUT = float(os.environ.get("FLIBUSTA_SIDECAR_TIMEOUT", "30"))


class SidecarError(Exception):
    pass


class SidecarUnavailable(SidecarError):
    pass


class BookNotFound(SidecarError):
    pass


class UpstreamError(SidecarError):
    pass


def _get(path, **kwargs):
    try:
        return requests.get(f"{SIDECAR_URL}{path}", timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise SidecarUnavailable(str(exc)) from exc


def _raise_for_status(resp):
    if resp.status_code == 404:
        raise BookNotFound(_error_message(resp))
    if resp.status_code in (502, 504):
        raise UpstreamError(_error_message(resp))
    if resp.status_code >= 400:
        raise SidecarError(_error_message(resp))


def _error_message(resp):
    try:
        return resp.json().get("error", resp.text)
    except ValueError:
        return resp.text or f"HTTP {resp.status_code}"


def search(q, page=0):
    resp = _get("/search", params={"q": q, "page": page})
    _raise_for_status(resp)
    return resp.json()


def cover_response(flibusta_id):
    resp = _get(f"/cover/{int(flibusta_id)}", stream=True)
    _raise_for_status(resp)
    return resp


def download(flibusta_id, fmt, title=""):
    resp = _get(f"/download/{int(flibusta_id)}/{fmt}", params={"title": title})
    _raise_for_status(resp)
    return resp.content, _filename_from_disposition(resp) or f"book.{fmt}"


def _filename_from_disposition(resp):
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename="([^"]+)"', cd)
    return m.group(1) if m else None
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd integration/calibre-web-flibusta && python -m pytest tests/test_sidecar_client.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add integration/calibre-web-flibusta/cps_flibusta/sidecar.py integration/calibre-web-flibusta/cps_flibusta/__init__.py integration/calibre-web-flibusta/tests/conftest.py integration/calibre-web-flibusta/tests/test_sidecar_client.py
git commit -m "feat(blueprint): sidecar HTTP client"
```

---

### Task 8: Metadata enrichment + dedup

**Files:**
- Create: `integration/calibre-web-flibusta/cps_flibusta/metadata.py`
- Create: `integration/calibre-web-flibusta/cps_flibusta/dedup.py`
- Test: `integration/calibre-web-flibusta/tests/test_metadata.py`
- Test: `integration/calibre-web-flibusta/tests/test_dedup.py`

**Interfaces:**
- Consumes: `BookMeta` field names confirmed in Task 6.
- Produces:
  - `metadata.enrich(meta, opds_item: dict, flibusta_id: int)` → a new `BookMeta` with
    OPDS authors/series/tags/description filled in **only where `meta`'s value is empty**,
    and `identifiers` extended with `("flibusta", str(flibusta_id))`.
  - `dedup.find_existing(calibre_db, flibusta_id: int) -> int | None` — returns an existing
    Calibre book id whose identifier `flibusta` matches, else `None`.

- [ ] **Step 1: Write the failing test** — `tests/test_metadata.py`

```python
from collections import namedtuple
from cps_flibusta import metadata

# Mirror of cps.uploader.BookMeta — keep field list in sync with Task 6 findings
BookMeta = namedtuple("BookMeta", "file_path, extension, title, author, cover, description, "
                                  "tags, series, series_id, languages, publisher, pubdate, identifiers")


def _blank(**over):
    base = dict(file_path="/tmp/x.fb2", extension=".fb2", title="", author="", cover=None,
                description="", tags="", series="", series_id="", languages="",
                publisher="", pubdate="", identifiers=[])
    base.update(over)
    return BookMeta(**base)


def test_enrich_fills_empty_fields_from_opds():
    meta = _blank(title="Богатый папа")
    opds = {"authors": [{"name": "Кийосаки Роберт"}], "series": "Богатый папа",
            "categories": ["Финансы", "Карьера"], "description": "<p>desc</p>"}
    out = metadata.enrich(meta, opds, 416925)
    assert out.author == "Кийосаки Роберт"
    assert out.series == "Богатый папа"
    assert "Финансы" in out.tags
    assert out.description == "<p>desc</p>"
    assert ("flibusta", "416925") in list(out.identifiers)


def test_enrich_does_not_overwrite_existing_file_metadata():
    meta = _blank(title="T", author="File Author", series="File Series",
                  description="from file")
    opds = {"authors": [{"name": "OPDS Author"}], "series": "OPDS Series",
            "categories": [], "description": "from opds"}
    out = metadata.enrich(meta, opds, 1)
    assert out.author == "File Author"
    assert out.series == "File Series"
    assert out.description == "from file"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd integration/calibre-web-flibusta && python -m pytest tests/test_metadata.py -q`
Expected: FAIL — module not found

- [ ] **Step 3: Write `cps_flibusta/metadata.py`**

```python
def _is_empty(value):
    return value is None or (isinstance(value, str) and not value.strip()) or value == []


def enrich(meta, opds_item, flibusta_id):
    updates = {}

    if _is_empty(meta.author):
        names = [a.get("name") for a in opds_item.get("authors", []) if a.get("name")]
        if names:
            updates["author"] = " & ".join(names)

    if _is_empty(meta.series) and opds_item.get("series"):
        updates["series"] = opds_item["series"]

    if _is_empty(meta.tags) and opds_item.get("categories"):
        updates["tags"] = ", ".join(opds_item["categories"])

    if _is_empty(meta.description) and opds_item.get("description"):
        updates["description"] = opds_item["description"]

    identifiers = list(meta.identifiers or [])
    if not any(k == "flibusta" for k, _ in identifiers):
        identifiers.append(("flibusta", str(flibusta_id)))
    updates["identifiers"] = identifiers

    return meta._replace(**updates)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd integration/calibre-web-flibusta && python -m pytest tests/test_metadata.py -q`
Expected: PASS

- [ ] **Step 5: Write the failing test** — `tests/test_dedup.py`

```python
from cps_flibusta import dedup


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def join(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeSession:
    def __init__(self, result):
        self._result = result

    def query(self, *a, **k):
        return _FakeQuery(self._result)


class _FakeCalibreDb:
    def __init__(self, result):
        self.session = _FakeSession(result)


def test_find_existing_returns_book_id_when_identifier_matches():
    class Book:  # noqa
        id = 42
    assert dedup.find_existing(_FakeCalibreDb(Book()), 416925) == 42


def test_find_existing_returns_none_when_no_match():
    assert dedup.find_existing(_FakeCalibreDb(None), 416925) is None
```

- [ ] **Step 6: Run test to verify it fails, then write `cps_flibusta/dedup.py`**

```python
def find_existing(calibre_db, flibusta_id):
    """Return the Calibre book id carrying identifier flibusta:<id>, or None."""
    from cps import db  # imported lazily so tests can run without calibre-web

    row = (
        calibre_db.session.query(db.Books)
        .join(db.Identifiers)
        .filter(db.Identifiers.type == "flibusta")
        .filter(db.Identifiers.val == str(flibusta_id))
        .first()
    )
    return row.id if row is not None else None
```

The fake in the test bypasses the `from cps import db` line only if it is not reached;
since the fakes short-circuit `query().join().filter().first()`, move the import to the
top of the function BUT guard the test by monkeypatching. Simpler: keep the import inside
and have the test patch it:

Update `tests/test_dedup.py` top:

```python
import sys, types
cps = types.ModuleType("cps")
cps.db = types.SimpleNamespace(Books=object, Identifiers=types.SimpleNamespace(type=None, val=None))
sys.modules.setdefault("cps", cps)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd integration/calibre-web-flibusta && python -m pytest tests/test_metadata.py tests/test_dedup.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add integration/calibre-web-flibusta/cps_flibusta/metadata.py integration/calibre-web-flibusta/cps_flibusta/dedup.py integration/calibre-web-flibusta/tests/test_metadata.py integration/calibre-web-flibusta/tests/test_dedup.py
git commit -m "feat(blueprint): OPDS metadata enrichment + dedup lookup"
```

---

### Task 9: The blueprint (`views.py`) + templates + static

**Files:**
- Create: `integration/calibre-web-flibusta/cps_flibusta/views.py`
- Modify: `integration/calibre-web-flibusta/cps_flibusta/__init__.py` (restore `from .views import flibusta`)
- Create: `integration/calibre-web-flibusta/cps_flibusta/templates/flibusta.html`
- Create: `integration/calibre-web-flibusta/cps_flibusta/static/flibusta.js`
- Create: `integration/calibre-web-flibusta/cps_flibusta/static/flibusta.css`
- Test: `integration/calibre-web-flibusta/tests/test_views.py`
- Test: `integration/calibre-web-flibusta/tests/test_add_flow.py`

**Interfaces:**
- Consumes: `sidecar` (Task 7), `metadata.enrich` (Task 8), `dedup.find_existing` (Task 8),
  and Calibre-Web internals confirmed in Task 6:
  `editbooks.file_handling_on_upload`, `editbooks.create_book_on_upload`,
  `helper.update_dir_structure`, `editbooks.move_coverfile`, `calibre_db`, `config`.
- Produces: `flibusta` = `flask.Blueprint("flibusta", ...)` with endpoints
  `flibusta.index`, `flibusta.search`, `flibusta.cover`, `flibusta.add`.

- [ ] **Step 1: Write the failing test** — `tests/test_views.py`

```python
import types
import pytest
from flask import Flask


@pytest.fixture
def app(monkeypatch):
    # stub the cps modules the blueprint imports, before importing views
    import sys
    cps = types.ModuleType("cps")
    cps.calibre_db = types.SimpleNamespace()
    cps.config = types.SimpleNamespace(config_uploading=True)
    sys.modules["cps"] = cps
    sys.modules["cps.editbooks"] = types.ModuleType("cps.editbooks")
    sys.modules["cps.helper"] = types.ModuleType("cps.helper")

    # auth stubs
    ub = types.ModuleType("cps.usermanagement")
    def login_required(fn): return fn
    monkeypatch.setitem(sys.modules, "cps.usermanagement", ub)

    from cps_flibusta import views
    monkeypatch.setattr(views, "_require_permission", lambda: None)

    flask_app = Flask(__name__)
    flask_app.register_blueprint(views.flibusta)
    flask_app.config["TESTING"] = True
    return flask_app


def test_search_route_proxies_sidecar(app, monkeypatch):
    from cps_flibusta import views
    monkeypatch.setattr(views.sidecar, "search",
                        lambda q, page=0: {"results": [{"flibustaId": 1, "title": q,
                                                        "coverUrl": "/cover/1", "formats": []}],
                                           "page": 0, "hasNext": False})
    monkeypatch.setattr(views.dedup, "find_existing", lambda db, fid: None)
    client = app.test_client()
    resp = client.get("/flibusta/search?q=hello")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["results"][0]["title"] == "hello"
    assert data["results"][0]["coverUrl"] == "/flibusta/cover/1"
    assert data["results"][0]["alreadyInLibrary"] is False


def test_search_route_reports_sidecar_down(app, monkeypatch):
    from cps_flibusta import views
    def boom(*a, **k):
        raise views.sidecar.SidecarUnavailable("down")
    monkeypatch.setattr(views.sidecar, "search", boom)
    resp = app.test_client().get("/flibusta/search?q=x")
    assert resp.status_code == 503
    assert "error" in resp.get_json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd integration/calibre-web-flibusta && python -m pytest tests/test_views.py -q`
Expected: FAIL — `cps_flibusta.views` missing

- [ ] **Step 3: Write `cps_flibusta/views.py`**

```python
import os
import tempfile

from flask import Blueprint, jsonify, render_template, request, url_for, Response, abort
from flask_login import login_required, current_user

from cps import calibre_db, config
from cps.editbooks import file_handling_on_upload, create_book_on_upload, move_coverfile
from cps import helper

from . import sidecar
from . import metadata
from . import dedup

flibusta = Blueprint("flibusta", __name__, url_prefix="/flibusta",
                     template_folder="templates", static_folder="static")


def _require_permission():
    if not config.config_uploading or not current_user.role_upload():
        abort(403)


@flibusta.route("/")
@login_required
def index():
    _require_permission()
    return render_template("flibusta.html", title="Download Books", page="flibusta")


@flibusta.route("/search")
@login_required
def search():
    _require_permission()
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": [], "page": 0, "hasNext": False})
    try:
        data = sidecar.search(q, request.args.get("page", 0))
    except sidecar.SidecarUnavailable as exc:
        return jsonify({"error": "Flibusta service is unavailable", "detail": str(exc)}), 503
    except sidecar.UpstreamError as exc:
        return jsonify({"error": "Flibusta is unreachable, try again later",
                        "detail": str(exc)}), 502

    for item in data.get("results", []):
        fid = item.get("flibustaId")
        book_id = dedup.find_existing(calibre_db, fid) if fid else None
        item["alreadyInLibrary"] = book_id is not None
        item["bookId"] = book_id
        if item.get("coverUrl") and fid:
            item["coverUrl"] = url_for("flibusta.cover", flibusta_id=fid)
    return jsonify(data)


@flibusta.route("/cover/<int:flibusta_id>")
@login_required
def cover(flibusta_id):
    _require_permission()
    try:
        upstream = sidecar.cover_response(flibusta_id)
    except sidecar.SidecarError:
        abort(404)
    return Response(upstream.iter_content(chunk_size=8192),
                    content_type=upstream.headers.get("Content-Type", "image/jpeg"),
                    headers={"Cache-Control": "public, max-age=86400"})


@flibusta.route("/add", methods=["POST"])
@login_required
def add():
    _require_permission()
    payload = request.get_json(silent=True) or {}
    fid = payload.get("flibustaId")
    fmt = payload.get("format")
    opds_item = payload.get("item") or {}
    if not fid or not fmt:
        return jsonify({"error": "flibustaId and format are required"}), 400

    existing = dedup.find_existing(calibre_db, int(fid))
    if existing:
        return jsonify({"status": "already_exists", "bookId": existing,
                        "url": url_for("web.show_book", book_id=existing)})

    try:
        content, filename = sidecar.download(int(fid), fmt, title=opds_item.get("title", ""))
    except sidecar.BookNotFound:
        return jsonify({"error": "This book/format is no longer available"}), 404
    except sidecar.SidecarUnavailable:
        return jsonify({"error": "Flibusta service is unavailable"}), 503
    except sidecar.UpstreamError:
        return jsonify({"error": "Flibusta is unreachable, try again later"}), 502

    tmp_dir = tempfile.mkdtemp(prefix="flibusta_")
    tmp_path = os.path.join(tmp_dir, filename)
    with open(tmp_path, "wb") as fh:
        fh.write(content)

    try:
        book_id = _import_into_library(tmp_path, filename, opds_item, int(fid))
    except Exception as exc:  # rollback handled inside
        return jsonify({"error": "Import failed", "detail": str(exc)}), 500
    finally:
        _safe_rmtree(tmp_dir)

    return jsonify({"status": "added", "bookId": book_id,
                    "url": url_for("web.show_book", book_id=book_id)})


def _import_into_library(tmp_path, filename, opds_item, flibusta_id):
    """Mirror of cps.editbooks.upload()'s btn-upload branch, with a FileStorage
    built from our downloaded file and metadata enriched from OPDS."""
    from werkzeug.datastructures import FileStorage
    from markupsafe import Markup
    from cps.editbooks import edit_book_comments

    calibre_db.create_functions(config)
    modify_date = False

    with open(tmp_path, "rb") as fh:
        storage = FileStorage(stream=fh, filename=filename,
                              content_type="application/octet-stream")
        meta, error = file_handling_on_upload(storage)
        if error:
            raise RuntimeError("file rejected by Calibre-Web")

        meta = metadata.enrich(meta, opds_item, flibusta_id)

        db_book, input_authors, title_dir = create_book_on_upload(modify_date, meta)
        modify_date |= edit_book_comments(Markup(meta.description or "").unescape(), db_book)
        book_id = db_book.id

        dir_error = helper.update_dir_structure(
            book_id, config.get_book_path(), input_authors[0],
            meta.file_path, title_dir + meta.extension.lower())
        move_coverfile(meta, db_book)
        if modify_date:
            calibre_db.set_metadata_dirty(book_id)
        calibre_db.session.commit()
        helper.add_book_to_thumbnail_cache(book_id)
        if dir_error:
            # non-fatal; book row exists
            pass
    return book_id


def _safe_rmtree(path):
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass
```

Note: `_import_into_library` MUST be reconciled line-by-line with the `upload()` body
recorded in Task 6 for the pinned release. If `create_book_on_upload` or
`file_handling_on_upload` signatures differ, adjust here. On any exception the caller
returns 500; add explicit `calibre_db.session.rollback()` in an `except` inside
`_import_into_library` if the pinned release's helpers leave a partial transaction:

```python
    except Exception:
        calibre_db.session.rollback()
        raise
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd integration/calibre-web-flibusta && python -m pytest tests/test_views.py -q`
Expected: PASS

- [ ] **Step 5: Write `tests/test_add_flow.py`** (dedup + download error paths, sidecar mocked)

```python
import types, sys, pytest
from flask import Flask


@pytest.fixture
def app(monkeypatch):
    cps = types.ModuleType("cps")
    cps.calibre_db = types.SimpleNamespace()
    cps.config = types.SimpleNamespace(config_uploading=True)
    sys.modules["cps"] = cps
    sys.modules["cps.editbooks"] = types.ModuleType("cps.editbooks")
    sys.modules["cps.helper"] = types.ModuleType("cps.helper")
    from cps_flibusta import views
    monkeypatch.setattr(views, "_require_permission", lambda: None)
    a = Flask(__name__)
    a.register_blueprint(views.flibusta)
    a.config["TESTING"] = True

    # web.show_book endpoint stub for url_for
    a.add_url_rule("/book/<int:book_id>", endpoint="web.show_book", view_func=lambda book_id: "")
    return a


def test_add_returns_already_exists(app, monkeypatch):
    from cps_flibusta import views
    monkeypatch.setattr(views.dedup, "find_existing", lambda db, fid: 7)
    resp = app.test_client().post("/flibusta/add", json={"flibustaId": 1, "format": "fb2"})
    assert resp.get_json()["status"] == "already_exists"
    assert resp.get_json()["bookId"] == 7


def test_add_download_not_found(app, monkeypatch):
    from cps_flibusta import views
    monkeypatch.setattr(views.dedup, "find_existing", lambda db, fid: None)
    def boom(*a, **k):
        raise views.sidecar.BookNotFound("gone")
    monkeypatch.setattr(views.sidecar, "download", boom)
    resp = app.test_client().post("/flibusta/add", json={"flibustaId": 1, "format": "fb2"})
    assert resp.status_code == 404


def test_add_requires_fields(app):
    resp = app.test_client().post("/flibusta/add", json={"flibustaId": 1})
    assert resp.status_code == 400
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd integration/calibre-web-flibusta && python -m pytest tests/test_add_flow.py -q`
Expected: PASS

- [ ] **Step 7: Write `templates/flibusta.html`**

```jinja
{% extends "layout.html" %}
{% block body %}
<div class="col-sm-12">
  <h2>{{ _('Download Books from Flibusta') }}</h2>
  <div id="flibusta-error" class="alert alert-danger" style="display:none"></div>
  <form id="flibusta-search-form" class="form-inline">
    <input type="text" id="flibusta-q" class="form-control" style="width:60%"
           placeholder="{{ _('Search by title or author') }}" autocomplete="off">
    <button type="submit" class="btn btn-primary">{{ _('Search') }}</button>
  </form>
  <div id="flibusta-results" class="row"></div>
  <button id="flibusta-more" class="btn btn-default" style="display:none">{{ _('Load more') }}</button>
</div>
<link rel="stylesheet" href="{{ url_for('flibusta.static', filename='flibusta.css') }}">
<script>
  window.FLIBUSTA = {
    searchUrl: "{{ url_for('flibusta.search') }}",
    addUrl: "{{ url_for('flibusta.add') }}"
  };
</script>
<script src="{{ url_for('flibusta.static', filename='flibusta.js') }}"></script>
{% endblock %}
```

- [ ] **Step 8: Write `static/flibusta.js`**

```js
(function () {
  var q = document.getElementById('flibusta-q');
  var form = document.getElementById('flibusta-search-form');
  var box = document.getElementById('flibusta-results');
  var moreBtn = document.getElementById('flibusta-more');
  var errBox = document.getElementById('flibusta-error');
  var page = 0;
  var lastQuery = '';

  function showError(msg) { errBox.textContent = msg; errBox.style.display = msg ? 'block' : 'none'; }

  function card(item) {
    var el = document.createElement('div');
    el.className = 'col-sm-3 flibusta-card';
    var opts = item.formats.map(function (f) {
      return '<option value="' + f.format + '">' + f.format.toUpperCase() + '</option>';
    }).join('');
    el.innerHTML =
      (item.coverUrl ? '<img src="' + item.coverUrl + '" alt="">' : '') +
      '<div class="flibusta-title">' + item.title + '</div>' +
      '<div class="flibusta-author">' + item.authors.map(function (a) { return a.name; }).join(', ') + '</div>' +
      (item.series ? '<div class="flibusta-series">' + item.series + '</div>' : '') +
      (item.alreadyInLibrary
        ? '<a class="btn btn-success btn-sm" href="/book/' + item.bookId + '">In library</a>'
        : '<select class="form-control input-sm">' + opts + '</select>' +
          '<button class="btn btn-primary btn-sm flibusta-add">Add</button>');
    if (!item.alreadyInLibrary) {
      var btn = el.querySelector('.flibusta-add');
      var sel = el.querySelector('select');
      btn.addEventListener('click', function () { add(item, sel.value, btn); });
    }
    return el;
  }

  function add(item, format, btn) {
    btn.disabled = true; btn.textContent = 'Loading...';
    fetch(window.FLIBUSTA.addUrl, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ flibustaId: item.flibustaId, format: format, item: item })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (res.ok && (res.j.status === 'added' || res.j.status === 'already_exists')) {
          btn.outerHTML = '<a class="btn btn-success btn-sm" href="' + res.j.url + '">Open</a>';
        } else {
          btn.disabled = false; btn.textContent = 'Add';
          showError(res.j.error || 'Failed');
        }
      }).catch(function () { btn.disabled = false; btn.textContent = 'Add'; showError('Network error'); });
  }

  function run(reset) {
    if (reset) { page = 0; box.innerHTML = ''; lastQuery = q.value.trim(); }
    showError('');
    fetch(window.FLIBUSTA.searchUrl + '?q=' + encodeURIComponent(lastQuery) + '&page=' + page)
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok) { showError(res.j.error || 'Search failed'); return; }
        (res.j.results || []).forEach(function (item) { box.appendChild(card(item)); });
        moreBtn.style.display = res.j.hasNext ? 'inline-block' : 'none';
      }).catch(function () { showError('Network error'); });
  }

  form.addEventListener('submit', function (e) { e.preventDefault(); run(true); });
  moreBtn.addEventListener('click', function () { page += 1; run(false); });
})();
```

- [ ] **Step 9: Write `static/flibusta.css`**

```css
.flibusta-card { text-align: center; margin-bottom: 20px; }
.flibusta-card img { max-height: 180px; margin-bottom: 8px; }
.flibusta-title { font-weight: bold; }
.flibusta-author, .flibusta-series { font-size: 0.9em; color: #777; }
.flibusta-card select { margin-bottom: 6px; }
```

- [ ] **Step 10: Restore `__init__.py`**

```python
from .views import flibusta  # noqa: F401
```

- [ ] **Step 11: Run all blueprint tests**

Run: `cd integration/calibre-web-flibusta && python -m pytest tests -q`
Expected: PASS

- [ ] **Step 12: Commit**

```bash
git add integration/calibre-web-flibusta/cps_flibusta integration/calibre-web-flibusta/tests
git commit -m "feat(blueprint): search page, cover proxy, add-to-library flow"
```

---

### Task 10: Core patches + Docker image + compose

**Files:**
- Create: `integration/calibre-web-flibusta/patches/0001-register-flibusta-blueprint.patch`
- Create: `integration/calibre-web-flibusta/patches/0002-nav-menu-item.patch`
- Create: `integration/calibre-web-flibusta/Dockerfile`
- Create: `integration/calibre-web-flibusta/docker-compose.yml`
- Modify: `integration/calibre-web-flibusta/README.md` (usage section)

**Interfaces:**
- Consumes: pinned digest + app path from Task 6; the `cps_flibusta` package from Task 9;
  the sidecar image from Task 5.
- Produces: `docker compose up` serving Calibre-Web with a working "Download Books" menu item.

- [ ] **Step 1: Generate patch 0001** — register the blueprint in `cps/main.py`

Obtain the real `cps/main.py` from the pinned image, add these two lines in the blueprint
import block and the registration block respectively, and save a unified diff:

```
--- a/cps/main.py
+++ b/cps/main.py
@@
     from .remotelogin import remotelogin
+    from .flibusta import flibusta
@@
     app.register_blueprint(remotelogin)
+    app.register_blueprint(flibusta)
```

Verify context lines match the pinned release before finalizing.

- [ ] **Step 2: Generate patch 0002** — nav item in `cps/templates/layout.html`

Find the stock Upload `<li>` (search for `btn-upload` / `url_for('edit-book...` in the
navbar `<ul>`), add immediately after it:

```
--- a/cps/templates/layout.html
+++ b/cps/templates/layout.html
@@
         </li>
+        {% if g.user.role_upload() and config.config_uploading %}
+        <li id="nav_flibusta">
+          <a href="{{ url_for('flibusta.index') }}">
+            <span class="glyphicon glyphicon-download-alt"></span> {{ _('Download Books') }}
+          </a>
+        </li>
+        {% endif %}
```

- [ ] **Step 3: Write `Dockerfile`**

```dockerfile
# digest recorded in README.md (Task 6)
FROM lscr.io/linuxserver/calibre-web@sha256:REPLACE_WITH_PINNED_DIGEST

COPY cps_flibusta /app/calibre-web/cps/flibusta
COPY patches/ /tmp/flibusta-patches/

RUN cd /app/calibre-web \
 && for p in /tmp/flibusta-patches/*.patch; do patch -p1 < "$p"; done \
 && pip install --no-cache-dir --break-system-packages requests \
 && rm -rf /tmp/flibusta-patches
```

If the image's Python is managed by the s6/venv layout, replace the `pip install` with the
image's documented method (record in README from Task 6). `requests` may already be
present (Calibre-Web depends on it) — if so, drop the `pip install` line.

- [ ] **Step 4: Write `docker-compose.yml`**

```yaml
services:
  calibre-web:
    build:
      context: .
      dockerfile: Dockerfile
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Europe/Amsterdam
      - FLIBUSTA_SIDECAR_URL=http://flibusta-sidecar:8080
    volumes:
      - ./data/config:/config
      - ./data/books:/books
    ports:
      - "8083:8083"
    depends_on:
      - flibusta-sidecar
    restart: unless-stopped

  flibusta-sidecar:
    build:
      context: ./sidecar
      dockerfile: Dockerfile
    environment:
      - FLIBUSTA_BASE_URL=http://flibusta.is/
      - PORT=8080
    restart: unless-stopped
```

- [ ] **Step 5: Build and smoke-test the stack**

Run:
```bash
cd integration/calibre-web-flibusta
docker compose build
docker compose up -d
sleep 15
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8083/flibusta/    # 302 -> login is fine
docker compose exec flibusta-sidecar wget -qO- http://localhost:8080/health
docker compose logs calibre-web | grep -i "flibusta\|error" | head
```
Expected: sidecar `{"status":"ok"}`; Calibre-Web starts with no blueprint import error.

- [ ] **Step 6: Manual end-to-end check**

1. Open `http://localhost:8083`, complete first-run admin setup, enable uploads in
   Admin → Basic Configuration.
2. Confirm "Download Books" appears in the top nav.
3. Search "Богатый папа", pick `fb2`, click Add.
4. Verify the book appears in the library with author, series "Богатый папа", tags, and
   identifier `flibusta:416925` on the edit page.
5. Click Add again on the same result → "In library" / "already_exists".

Record the outcome in `README.md`.

- [ ] **Step 7: Commit**

```bash
git add integration/calibre-web-flibusta/patches integration/calibre-web-flibusta/Dockerfile integration/calibre-web-flibusta/docker-compose.yml integration/calibre-web-flibusta/README.md
git commit -m "build: Calibre-Web image with Flibusta blueprint + compose stack"
```

---

### Task 11: CI wiring + docs

**Files:**
- Create: `.github/workflows/calibre-web-flibusta.yml`
- Modify: `integration/calibre-web-flibusta/README.md` (full setup + update procedure)
- Modify: `README.md` (repo root — add a short pointer to the integration)

**Interfaces:**
- Consumes: sidecar test script (`npm test`), blueprint test suite (`pytest tests`).

- [ ] **Step 1: Write the workflow**

```yaml
name: calibre-web-flibusta
on:
  pull_request:
    paths: ["integration/calibre-web-flibusta/**"]
  push:
    branches: [master]
    paths: ["integration/calibre-web-flibusta/**"]
jobs:
  sidecar:
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: integration/calibre-web-flibusta/sidecar } }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "20" }
      - run: npm install
      - run: npm test
  blueprint:
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: integration/calibre-web-flibusta } }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install pytest responses requests flask flask-login markupsafe
      - run: python -m pytest tests -q
```

- [ ] **Step 2: Write `README.md`** — cover: architecture diagram (text), prerequisites,
  `docker compose up`, enabling uploads, the Flibusta identifier convention, and the
  **update procedure**: bump the digest in `Dockerfile`, re-extract `cps/main.py` +
  `layout.html`, regenerate both patches, re-run `pytest`, rebuild.

- [ ] **Step 3: Add root README pointer**

Add to the repo root `README.md` a short "Integrations" section linking
`integration/calibre-web-flibusta/README.md`.

- [ ] **Step 4: Run both suites locally one more time**

Run:
```bash
cd integration/calibre-web-flibusta/sidecar && npm test
cd .. && python -m pytest tests -q
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/calibre-web-flibusta.yml integration/calibre-web-flibusta/README.md README.md
git commit -m "ci+docs: test workflow and setup guide for the Flibusta integration"
```

---

## Self-Review

**1. Spec coverage**

| Spec item | Task |
|---|---|
| Node sidecar wrapping `flibusta-api` | 1–5 |
| Sidecar `/health`, `/search`, `/cover`, `/download` contract | 2, 4 |
| MIME → short-name format table | 1 (Global Constraints) |
| `fb2+zip` unpacking, epub pass-through | 3 |
| `Content-Disposition` transliteration | 4 |
| Python package mounted at `cps/flibusta/` | 7–9 |
| Blueprint routes `index/search/cover/add` + permission gate | 9 |
| Dedup via `flibusta:<id>` identifier | 8, 9 |
| Reuse `file_handling_on_upload` / `create_book_on_upload` / `update_dir_structure` / `move_coverfile` | 9 |
| OPDS metadata enrichment (fill-only-if-empty) | 8 |
| Rollback on import failure | 9 (`_import_into_library` + caller) |
| Frontend search page in Calibre-Web theme | 9 |
| Two pinned core patches (`main.py`, `layout.html`) | 10 |
| Custom Dockerfile on pinned digest + compose with sidecar | 10 |
| Error-handling table (sidecar down, blocked, missing format, corrupt zip, dup, 403) | 4, 7, 9 |
| Sidecar tests (mocha + nock) | 1–4 |
| Blueprint tests (pytest + responses) | 7–9 |
| Integration smoke | 10 |
| Release-pin + update procedure | 6, 11 |

No uncovered spec sections.

**2. Placeholder scan**

- `Dockerfile` contains `REPLACE_WITH_PINNED_DIGEST` — this is intentional and resolved in
  Task 10 Step 3 from the value recorded in Task 6 Step 1. It is a data value the executor
  fills from a prior recorded step, not an unresolved design decision.
- Task 6 explicitly exists because the exact upstream signatures cannot be known until the
  release is pinned; Tasks 8–10 reference "confirm in Task 6" for those specific values.
  All *code* is concrete; only version-specific context lines and the digest are deferred
  to the pin step, which is the correct place for them.
- No "TODO", "add error handling", or "write tests for the above" — every test step has
  runnable code.

**3. Type consistency**

- Sidecar `/search` result object shape (`flibustaId`, `title`, `authors[{name,flibustaId}]`,
  `series`, `categories`, `description`, `coverUrl`, `formats[{format,mime}]`) is identical
  in Task 2 (producer), Task 4 (passthrough), Task 9 (`views.search` rewrites `coverUrl`,
  adds `alreadyInLibrary`/`bookId`), and `flibusta.js` (consumer).
- `sidecar.download` returns `(bytes, filename)` in Task 7 and is unpacked as
  `content, filename` in Task 9. Consistent.
- `dedup.find_existing(calibre_db, flibusta_id) -> int | None` — same signature in Task 8
  definition and both call sites in Task 9.
- `metadata.enrich(meta, opds_item, flibusta_id) -> BookMeta` — Task 8 definition matches
  the Task 9 call `metadata.enrich(meta, opds_item, flibusta_id)`.
- Exception names (`SidecarUnavailable`, `BookNotFound`, `UpstreamError`, `SidecarError`)
  identical between Task 7 definitions and Task 9 handlers.
- `_require_permission` monkeypatched in Tasks 9 tests exactly as named in `views.py`.

No inconsistencies found.
