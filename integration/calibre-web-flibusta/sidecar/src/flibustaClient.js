const FlibustaAPI = require('flibusta').default || require('flibusta');
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
    proxy: false,
    maxRedirects: 5,
    validateStatus: (s) => s < 500,
  }));
  if (res.status === 404) throw new NotFound(pathname);
  if (res.status >= 400) throw new UpstreamError(`status ${res.status}`);
  return res;
}

async function fetchCover(flibustaId) {
  const res = await rawGet(`/i/${String(flibustaId).slice(0, 2)}/${flibustaId}/cover.jpg`, 'stream')
    .catch((err) => {
      if (err instanceof NotFound) return rawGet(`/b/${flibustaId}/cover`, 'stream');
      throw err;
    });
  return { stream: res.data, contentType: res.headers['content-type'] || 'image/jpeg' };
}

async function fetchBook(flibustaId, format) {
  const res = await rawGet(`/b/${flibustaId}/${format}`, 'arraybuffer');
  return Buffer.from(res.data);
}

module.exports = { search, fetchCover, fetchBook, NotFound, UpstreamError };
