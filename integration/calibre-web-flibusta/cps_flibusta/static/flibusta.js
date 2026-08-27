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

  // Escapes for BOTH text and double-quoted attribute contexts. textContent/innerHTML
  // alone does not escape quotes, so a value like `" onerror="alert(1)` would break
  // out of an attribute. Flibusta data is attacker-controllable — escape quotes too.
  function esc(s) {
    return (s == null ? '' : String(s))
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function bookUrl(id) {
    return (cfg.bookUrlTemplate || '/book/0').replace(/0$/, String(id));
  }

  function card(item) {
    var el = document.createElement('div');
    el.className = 'col-sm-3 flibusta-card';
    var formats = (item.formats || []).map(function (f) {
      return String(f.format || f);
    });
    var boxes = formats.map(function (name) {
      return '<label class="flibusta-fmt"><input type="checkbox" value="' + esc(name) +
        '"> ' + esc(name.toUpperCase()) +
        '<span class="flibusta-primary-badge" style="display:none"> ' +
        esc(t.primary || 'primary') + '</span></label>';
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
        : '<div class="flibusta-formats">' + boxes + '</div>' +
          '<div class="flibusta-result" style="display:none"></div>' +
          '<button class="btn btn-primary btn-sm flibusta-add" disabled>' +
          esc(t.add || 'Add') + '</button>');

    if (!item.alreadyInLibrary) {
      var btn = el.querySelector('.flibusta-add');
      var inputs = [].slice.call(el.querySelectorAll('.flibusta-formats input'));
      // Selection order is the contract: the FIRST checked format is the one
      // that creates/locates the book, the rest are attached to it.
      var chosen = [];

      function sync() {
        inputs.forEach(function (input) {
          var badge = input.parentNode.querySelector('.flibusta-primary-badge');
          badge.style.display = (chosen[0] === input.value) ? 'inline' : 'none';
        });
        btn.disabled = chosen.length === 0;
      }

      inputs.forEach(function (input) {
        input.addEventListener('change', function () {
          var at = chosen.indexOf(input.value);
          if (input.checked) {
            if (at === -1) { chosen.push(input.value); }
          } else if (at !== -1) {
            chosen.splice(at, 1);
          }
          sync();
        });
      });
      sync();
      btn.addEventListener('click', function () {
        if (chosen.length) { add(item, chosen.slice(), btn, el); }
      });
    }
    return el;
  }

  function formatReport(res) {
    var f = res.formats || {};
    var parts = [];
    function chunk(list, cls, suffix) {
      (list || []).forEach(function (name) {
        parts.push('<span class="' + cls + '">' + esc(String(name).toUpperCase()) +
          (suffix ? ' ' + esc(suffix) : '') + '</span>');
      });
    }
    chunk(f.added, 'flibusta-ok', '');
    chunk(f.skipped, 'flibusta-skip', t.alreadyThere || 'already present');
    chunk(f.failed, 'flibusta-fail', t.notAdded || 'failed');
    return parts.join(' ');
  }

  function jsonResponse(r) {
    return r.json().then(function (j) { return { ok: r.ok, j: j || {} }; },
                         function () { return { ok: r.ok, j: {} }; });
  }

  function add(item, formats, btn, el) {
    btn.disabled = true;
    btn.textContent = t.loading || 'Loading...';
    var headers = { 'Content-Type': 'application/json' };
    if (cfg.csrfToken) { headers['X-CSRFToken'] = cfg.csrfToken; }
    fetch(cfg.addUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: headers,
      body: JSON.stringify({ flibustaId: item.flibustaId, formats: formats, item: item })
    }).then(jsonResponse)
      .then(function (res) {
        if (res.ok && (res.j.status === 'added' || res.j.status === 'already_exists')) {
          var report = el && el.querySelector('.flibusta-result');
          if (report) {
            report.innerHTML = formatReport(res.j);
            report.style.display = 'block';
          }
          // The button is gone; leaving the checkboxes live would let the user
          // re-tick formats with nothing to submit them.
          if (el) {
            [].slice.call(el.querySelectorAll('.flibusta-formats input'))
              .forEach(function (input) { input.disabled = true; });
          }
          var got = ((res.j.formats || {}).added || []).concat(
            (res.j.formats || {}).skipped || []);
          var label = (t.open || 'Open') + (got.length ? ' — ' + got.join(', ') : '');
          btn.outerHTML = '<a class="btn btn-success btn-sm" href="' + esc(res.j.url) + '">' +
            esc(label) + '</a>';
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
