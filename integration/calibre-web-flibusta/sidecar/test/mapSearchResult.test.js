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
