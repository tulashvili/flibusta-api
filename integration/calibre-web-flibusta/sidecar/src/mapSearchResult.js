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
