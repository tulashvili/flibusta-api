(function () {
  var cfg = window.FLIBUSTA || {};
  var t = cfg.i18n || {};
  var q = document.getElementById('flibusta-q');
  var form = document.getElementById('flibusta-search-form');
  var box = document.getElementById('flibusta-results');
  var moreBtn = document.getElementById('flibusta-more');
  var errBox = document.getElementById('flibusta-error');
  var page = 0;
  var lastQuery = '';

  function showError(msg) {
    errBox.textContent = msg || '';
    errBox.style.display = msg ? 'block' : 'none';
  }

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function bookUrl(id) {
    return (cfg.bookUrlTemplate || '/book/0').replace(/0$/, String(id));
  }

  function card(item) {
    var el = document.createElement('div');
    el.className = 'col-sm-3 flibusta-card';
    var formats = item.formats || [];
    var opts = formats.map(function (f) {
      var name = f.format || f;
      return '<option value="' + esc(name) + '">' + esc(String(name).toUpperCase()) + '</option>';
    }).join('');
    var authors = (item.authors || []).map(function (a) {
      return a && a.name ? a.name : a;
    }).join(', ');

    el.innerHTML =
      (item.coverUrl ? '<img src="' + esc(item.coverUrl) + '" alt="">' : '') +
      '<div class="flibusta-title">' + esc(item.title) + '</div>' +
      '<div class="flibusta-author">' + esc(authors) + '</div>' +
      (item.series ? '<div class="flibusta-series">' + esc(item.series) + '</div>' : '') +
      (item.alreadyInLibrary
        ? '<a class="btn btn-success btn-sm" href="' + esc(bookUrl(item.bookId)) + '">' +
          esc(t.inLibrary || 'In library') + '</a>'
        : '<select class="form-control input-sm">' + opts + '</select>' +
          '<button class="btn btn-primary btn-sm flibusta-add">' + esc(t.add || 'Add') + '</button>');

    if (!item.alreadyInLibrary) {
      var btn = el.querySelector('.flibusta-add');
      var sel = el.querySelector('select');
      btn.addEventListener('click', function () { add(item, sel.value, btn); });
    }
    return el;
  }

  function jsonResponse(r) {
    return r.json().then(function (j) { return { ok: r.ok, j: j || {} }; },
                         function () { return { ok: r.ok, j: {} }; });
  }

  function add(item, format, btn) {
    btn.disabled = true;
    btn.textContent = t.loading || 'Loading...';
    var headers = { 'Content-Type': 'application/json' };
    if (cfg.csrfToken) { headers['X-CSRFToken'] = cfg.csrfToken; }
    fetch(cfg.addUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: headers,
      body: JSON.stringify({ flibustaId: item.flibustaId, format: format, item: item })
    }).then(jsonResponse)
      .then(function (res) {
        if (res.ok && (res.j.status === 'added' || res.j.status === 'already_exists')) {
          btn.outerHTML = '<a class="btn btn-success btn-sm" href="' + esc(res.j.url) + '">' +
            esc(t.open || 'Open') + '</a>';
        } else {
          btn.disabled = false;
          btn.textContent = t.add || 'Add';
          showError(res.j.error || t.failed || 'Failed');
        }
      }).catch(function () {
        btn.disabled = false;
        btn.textContent = t.add || 'Add';
        showError(t.networkError || 'Network error');
      });
  }

  function run(reset) {
    if (reset) { page = 0; box.innerHTML = ''; lastQuery = q.value.trim(); }
    if (!lastQuery) { return; }
    showError('');
    fetch(cfg.searchUrl + '?q=' + encodeURIComponent(lastQuery) + '&page=' + page,
          { credentials: 'same-origin' })
      .then(jsonResponse)
      .then(function (res) {
        if (!res.ok) { showError(res.j.error || t.searchFailed || 'Search failed'); return; }
        (res.j.results || []).forEach(function (item) { box.appendChild(card(item)); });
        moreBtn.style.display = res.j.hasNext ? 'inline-block' : 'none';
      }).catch(function () { showError(t.networkError || 'Network error'); });
  }

  form.addEventListener('submit', function (e) { e.preventDefault(); run(true); });
  moreBtn.addEventListener('click', function () { page += 1; run(false); });
})();
