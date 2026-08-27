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
