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
  if (requestedFormat === 'epub') {
    return { buffer, ext: 'epub', contentType: 'application/epub+zip' };
  }
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
