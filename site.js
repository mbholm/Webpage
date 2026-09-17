// ---------------------------------------------------------------
// 1. Assemble the mailto link at runtime (keeps the address out of the raw HTML).
// ---------------------------------------------------------------
document.querySelectorAll('a[data-email]').forEach(function (a) {
  var addr = a.dataset.email + '@' + a.dataset.domain;
  a.href = 'mailto:' + addr;
  var span = a.querySelector('span');
  if (span) span.textContent = addr;
});

// ---------------------------------------------------------------
// 2. Home page "Recent ..." lists.
//    They are generated from research.html and op-eds.html, so those two
//    pages are the only place you edit. The static items already in
//    index.html are a fallback shown only if the fetch fails (e.g. when
//    the file is opened directly from disk rather than over http).
//
//    <ul class="recent" data-recent="papers"       data-limit="4" data-sections="working-papers,rr">
//    <ul class="recent" data-recent="publications" data-limit="4" data-sections="published">
//    <ul class="recent" data-recent="opeds"        data-limit="3">
// ---------------------------------------------------------------
(function () {
  var lists = document.querySelectorAll('ul[data-recent]');
  if (!lists.length) return;

  var MONTHS = { january: 1, february: 2, march: 3, april: 4, may: 5, june: 6, july: 7,
                 august: 8, september: 9, october: 10, november: 11, december: 12 };

  // "May 2026" -> 202605 ; "2026" -> 202612 (so a bare year sorts after any month in that year)
  function dateKey(text) {
    var m = /([A-Za-z]+)\s+(\d{4})/.exec(text);
    if (m && MONTHS[m[1].toLowerCase()]) return +m[2] * 100 + MONTHS[m[1].toLowerCase()];
    var y = /(\d{4})/.exec(text);
    return y ? +y[1] * 100 + 12 : 0;
  }

  // Date of a paper: "This version: ..." wins, then "First version: ...", then the year in .meta
  function paperKey(article) {
    var meta = article.querySelector(':scope > .meta');
    var t = meta ? meta.textContent : '';
    var m = /This version:\s*([A-Za-z]+\s+\d{4})/.exec(t) || /First version:\s*([A-Za-z]+\s+\d{4})/.exec(t);
    return dateKey(m ? m[1] : t);
  }

  function monthYear(key) {
    if (!key) return '';
    var y = Math.floor(key / 100), mo = key % 100;
    var name = Object.keys(MONTHS).filter(function (k) { return MONTHS[k] === mo; })[0] || '';
    return name ? name.charAt(0).toUpperCase() + name.slice(1) + ' ' + y : String(y);
  }

  function li(href, title, side) {
    var el = document.createElement('li');
    var t = document.createElement('span'); t.className = 't';
    var a = document.createElement('a'); a.href = href; a.textContent = title; t.appendChild(a);
    var s = document.createElement('span'); s.className = 's'; s.textContent = side;
    el.appendChild(t); el.appendChild(s);
    return el;
  }

  // Collect .paper articles that sit under the given h2 ids in research.html
  function papersInSections(doc, ids) {
    var out = [], current = null;
    Array.prototype.forEach.call(doc.querySelectorAll('main > *'), function (node) {
      if (node.tagName === 'H2') current = node.id;
      else if (node.classList && node.classList.contains('paper') && ids.indexOf(current) !== -1)
        out.push({ node: node, section: current });
    });
    return out;
  }

  function render(list, items) {
    if (!items.length) return;
    list.innerHTML = '';
    items.forEach(function (it) { list.appendChild(it); });
  }

  function fetchDoc(url) {
    return fetch(url, { cache: 'no-cache' }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.text();
    }).then(function (html) { return new DOMParser().parseFromString(html, 'text/html'); });
  }

  var needResearch = false, needOpeds = false;
  lists.forEach(function (l) {
    if (l.dataset.recent === 'opeds') needOpeds = true; else needResearch = true;
  });

  if (needResearch) {
    fetchDoc('research.html').then(function (doc) {
      lists.forEach(function (list) {
        var kind = list.dataset.recent;
        if (kind !== 'papers' && kind !== 'publications') return;
        var limit = +list.dataset.limit || 4;
        var sections = (list.dataset.sections || (kind === 'papers' ? 'working-papers,rr' : 'published')).split(',');
        var entries = papersInSections(doc, sections).map(function (p) {
          var a = p.node;
          var titleEl = a.querySelector('.title');
          var link = titleEl && titleEl.querySelector('a');
          var title = titleEl ? (link ? link.textContent : titleEl.firstChild.textContent).trim() : '';
          var href = link ? link.getAttribute('href') : 'research.html#' + p.section;
          var key = paperKey(a);
          var side;
          if (kind === 'papers') {
            var authors = a.querySelector('.authors');
            side = (authors ? authors.textContent.trim().replace(/\s+/g, ' ') + ' · ' : '') + monthYear(key);
          } else {
            var venue = a.querySelector('.venue');
            side = (venue ? venue.textContent.trim() + ' · ' : '') + Math.floor(key / 100);
          }
          return { key: key, el: li(href, title, side) };
        });
        entries.sort(function (x, y) { return y.key - x.key; });
        render(list, entries.slice(0, limit).map(function (e) { return e.el; }));
      });
    }).catch(function () { /* keep fallback */ });
  }

  if (needOpeds) {
    fetchDoc('op-eds.html').then(function (doc) {
      lists.forEach(function (list) {
        if (list.dataset.recent !== 'opeds') return;
        var limit = +list.dataset.limit || 3;
        var entries = Array.prototype.map.call(doc.querySelectorAll('.oped'), function (o) {
          var a = o.querySelector('.t a');
          var date = o.querySelector('.date');
          var outlet = o.querySelector('.o');
          var dateText = date ? date.textContent.trim() : '';
          return { key: dateKey(dateText),
                   el: li(a ? a.getAttribute('href') : 'op-eds.html', a ? a.textContent.trim() : '',
                          (outlet ? outlet.textContent.trim() + ' · ' : '') + dateText) };
        });
        entries.sort(function (x, y) { return y.key - x.key; });  // newest first
        render(list, entries.slice(0, limit).map(function (e) { return e.el; }));
      });
    }).catch(function () { /* keep fallback */ });
  }
})();
