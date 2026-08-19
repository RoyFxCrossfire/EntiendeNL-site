document.addEventListener('DOMContentLoaded', function () {
  var toggle = document.querySelector('.menutoggle');
  var links = document.querySelector('.navlinks');
  if (toggle && links) {
    toggle.addEventListener('click', function () {
      links.style.display = links.style.display === 'flex' ? 'none' : 'flex';
      links.style.flexDirection = 'column';
      links.style.position = 'absolute';
      links.style.top = '64px';
      links.style.left = '0';
      links.style.right = '0';
      links.style.background = 'var(--paper)';
      links.style.padding = '16px 24px';
      links.style.borderBottom = '1px solid var(--line)';
      links.style.gap = '14px';
    });
  }
});

function categoryLabel(cat) {
  var map = { nederland: 'Países Bajos', europa: 'Europa', regulacion: 'Regulación', trabajo: 'Trabajo y derechos' };
  return map[cat] || cat;
}

function renderNewsItem(article, compact) {
  var div = document.createElement('div');
  div.className = compact ? 'card' : 'newsitem';
  var tagHtml = '<span class="tag' + (article.category === 'regulacion' ? ' stamped' : '') + '">' + categoryLabel(article.category) + '</span>';
  var titleTag = compact ? 'h3' : 'h3';
  div.innerHTML =
    tagHtml +
    '<' + titleTag + '>' + article.title + '</' + titleTag + '>' +
    '<p>' + article.summary + '</p>' +
    '<a class="source' + (compact ? ' readmore' : '') + '" href="' + article.source_url + '" target="_blank" rel="noopener">Leer en ' + article.source + ' \u2192</a>' +
    '<div class="news-meta">' + article.date + '</div>';
  return div;
}

function loadNews(targetId, opts) {
  opts = opts || {};
  var target = document.getElementById(targetId);
  if (!target) return;
  fetch('assets/news_data.json')
    .then(function (r) { return r.json(); })
    .then(function (data) {
      var articles = data.articles || [];
      if (opts.limit) articles = articles.slice(0, opts.limit);
      renderList(articles, target, opts.compact);
      if (opts.filterable) setupFilters(data.articles || [], target);
    })
    .catch(function () {
      target.innerHTML = '<p style="color:var(--ink-soft)">No se pudo cargar el resumen de noticias en este momento.</p>';
    });
}

function renderList(articles, target, compact) {
  target.innerHTML = '';
  var empty = document.getElementById('news-empty');
  if (!articles.length) {
    if (empty) empty.style.display = 'block';
    return;
  }
  if (empty) empty.style.display = 'none';
  articles.forEach(function (a) { target.appendChild(renderNewsItem(a, compact)); });
}

function setupFilters(allArticles, target) {
  var buttons = document.querySelectorAll('.filterbtn');
  buttons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      buttons.forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      var cat = btn.getAttribute('data-cat');
      var filtered = cat === 'todas' ? allArticles : allArticles.filter(function (a) { return a.category === cat; });
      renderList(filtered, target, false);
    });
  });
}
