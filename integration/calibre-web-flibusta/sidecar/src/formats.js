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
