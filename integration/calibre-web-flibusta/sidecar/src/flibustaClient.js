const FlibustaAPI = require('flibusta').default || require('flibusta');
const { mapSearchResult } = require('./mapSearchResult');

class NotFound extends Error {}
class UpstreamError extends Error {}

const BASE_URL = process.env.FLIBUSTA_BASE_URL || 'https://flibusta.is/';
const TIMEOUT = Number(process.env.REQUEST_TIMEOUT_MS || 20000);
const RETRIES = Number(process.env.RETRY_COUNT || 1);
const UA = process.env.USER_AGENT || 'flibusta-sidecar/0.1';
// Hard ceiling on downloaded book/cover bytes. Flibusta is untrusted upstream:
// without this a hostile/broken response could buffer unbounded into memory.
// Exceeding it makes axios reject -> UpstreamError -> HTTP 502 to the caller.
const MAX_BYTES = Number(process.env.MAX_DOWNLOAD_BYTES || 64 * 1024 * 1024);

// Optional egress proxy, honored only when FLIBUSTA_PROXY is set. axios takes a
// {protocol, host, port} object; `false` disables proxying entirely (the
// previous unconditional behavior).
function proxyConfig() {
  const raw = process.env.FLIBUSTA_PROXY;
  if (!raw) return false;
  try {
    const u = new URL(raw);
    const cfg = {
      protocol: u.protocol.replace(':', ''),
      host: u.hostname,
      port: Number(u.port) || (u.protocol === 'https:' ? 443 : 80),
    };
    if (u.username) cfg.auth = { username: decodeURIComponent(u.username), password: decodeURIComponent(u.password || '') };
    return cfg;
  } catch (err) {
    return false;
  }
}

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
  const pageNum = Number(page) || 0;
  const raw = await withRetry(() => api.getBooksByNameFromOpdsPaginated(q, pageNum, 20));
  // getBooksByNameFromOpdsPaginated returns:
  //   { items, currentPage, totalCountItems, hasNextPage, hasPreviousPage, totalPages }
  const items = Array.isArray(raw) ? raw : (raw && raw.items) || [];
  const results = items.map(mapSearchResult).filter((r) => r.flibustaId);
  const hasNext = Boolean(raw && raw.hasNextPage);
  return { results, page: pageNum, hasNext };
}

// direct HTTP for binary endpoints; flibusta does not expose file download
async function rawGet(pathname, responseType) {
  const axios = require('axios');
  const res = await withRetry(() => axios.get(new URL(pathname, BASE_URL).toString(), {
    responseType,
    timeout: TIMEOUT,
    headers: { 'User-Agent': UA },
    proxy: proxyConfig(),
    maxRedirects: 5,
    maxContentLength: MAX_BYTES,
    maxBodyLength: MAX_BYTES,
    validateStatus: (s) => s < 500,
  }));
  if (res.status === 404) throw new NotFound(pathname);
  if (res.status >= 400) throw new UpstreamError(`status ${res.status}`);
  return res;
}

// Flibusta stores cover images under /i/<bucket>/<id>/cover.<ext> where the
// bucket is the book id modulo 100, zero-padded to two digits
// (e.g. 416925 -> /i/25/416925/cover.jpg). Verified against real Flibusta.
function coverBucket(flibustaId) {
  return String(Number(flibustaId) % 100).padStart(2, '0');
}

async function fetchCover(flibustaId) {
  const bucket = coverBucket(flibustaId);
  const res = await rawGet(`/i/${bucket}/${flibustaId}/cover.jpg`, 'stream')
    .catch((err) => {
      if (err instanceof NotFound) return rawGet(`/i/${bucket}/${flibustaId}/cover.png`, 'stream');
      throw err;
    });
  return { stream: res.data, contentType: res.headers['content-type'] || 'image/jpeg' };
}

async function fetchBook(flibustaId, format) {
  const res = await rawGet(`/b/${flibustaId}/${format}`, 'arraybuffer');
  return Buffer.from(res.data);
}

module.exports = { search, fetchCover, fetchBook, NotFound, UpstreamError };
