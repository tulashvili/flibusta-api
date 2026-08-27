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
