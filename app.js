const $ = selector => document.querySelector(selector);
const KEY = 'daily-ai.interests.v1';
const params = new URLSearchParams(location.search);
let issue;
let selected = categories.map(category => category[0]);
let active = categories.some(category => category[0] === params.get('category'))
  ? params.get('category') : 'all';
let onboarded = false;

try {
  const stored = JSON.parse(localStorage.getItem(KEY));
  if (Array.isArray(stored)) {
    const valid = stored.filter(id => categories.some(category => category[0] === id));
    if (valid.length) {
      selected = [...new Set(valid)];
      onboarded = true;
    }
  }
} catch { /* Use default interests when storage is unavailable. */ }

function notify(text) {
  $('#toast').textContent = text;
  $('#toast').hidden = false;
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => { $('#toast').hidden = true; }, 2400);
}

function updateSelection() {
  const count = $('#category-options').querySelectorAll('input:checked').length;
  $('#selection-count').textContent = `已选择 ${count} 个栏目`;
  $('#form-error').hidden = true;
}

function showPreferences() {
  $('#dialog-title').textContent = onboarded ? '调整你的阅读栏目' : '选择你想读的栏目';
  $('#close-dialog').hidden = !onboarded;
  $('#save-preferences').innerHTML = (onboarded ? '保存兴趣' : '开始阅读') +
    ' <span aria-hidden="true">→</span>';
  $('#category-options').innerHTML = categories.map(([id, label, description]) => `
    <label class="category-choice">
      <input type="checkbox" name="interest" value="${id}" ${selected.includes(id) ? 'checked' : ''}>
      <span><span class="category-name">${label}</span>
      <span class="category-desc">${description}</span></span>
    </label>`).join('');
  updateSelection();
  $('#preference-dialog').showModal();
}

function savePreferences(event) {
  event.preventDefault();
  const choices = [...$('#category-options').querySelectorAll('input:checked')]
    .map(input => input.value);
  if (!choices.length) {
    $('#form-error').hidden = false;
    return;
  }
  selected = choices;
  onboarded = true;
  if (active !== 'all' && !selected.includes(active)) active = 'all';
  let saved = true;
  try { localStorage.setItem(KEY, JSON.stringify(selected)); }
  catch { saved = false; }
  $('#preference-dialog').close();
  render();
  notify(saved ? '兴趣已保存' : '当前浏览器无法保存偏好，本次选择仍然有效');
}

function card(article) {
  const source = article.sources[0];
  const site = platform(source.url);
  let author = article.author.replace(/：.*$/, '').replace(/·人工智能/, '');
  if (author.includes('Hacker News')) author = site.name;
  const date = article.publishedAt.slice(5, 10).replace('-', '/') +
    (article.publishedAt.includes('T') ? ' ' + article.publishedAt.slice(11, 16) : '');
  const image = article.image;
  return `
    <article class="news-card feed-story" id="card-${escape(article.id)}">
      <span class="story-kind">${escape(article.kind || name(article.category))}</span>
      <h3>${escape(article.title)}</h3>
      <div class="story-meta">
        <a class="story-source" href="${escape(source.url)}" target="_blank"
           rel="noopener noreferrer" aria-label="${escape(site.name)} 原文：${escape(article.title)}">
          <span class="platform-logo ${site.className}" aria-hidden="true">${site.logo}</span>
          <span>${escape(author)}</span><span aria-hidden="true">↗</span>
        </a>
        <span class="meta-dot">·</span>
        <time datetime="${escape(article.publishedAt)}">${date}</time>
      </div>
      ${image ? `<figure class="news-media">
        <img src="${escape(image.src)}" width="${image.width}" height="${image.height}"
             alt="${escape(image.alt)}" loading="lazy" decoding="async">
        ${image.caption ? `<figcaption>${escape(image.caption)}</figcaption>` : ''}
      </figure>` : ''}
      <div class="story-body">${article.paragraphs.map(p => `<p>${escape(p)}</p>`).join('')}</div>
      <p class="story-links">${article.sources.map(s => `<a href="${escape(s.url)}" target="_blank" rel="noopener noreferrer">${escape(s.label)} ↗</a>`).join(' · ')}</p>
    </article>`;
}

function renderOverview() {
  const overview = issue.overview;
  const topics = overview.topics || [];
  $('#daily-summary').innerHTML = `
    <header class="summary-heading"><h2>${issue.date === latestIssue?.date ? "AI 今日总结" : "AI 本期总结"}</h2><span class="ai-label">AI 整理</span></header>
    <div class="overview-text">${overview.paragraphs.map(p => `<p>${escape(p)}</p>`).join('')}</div>
    ${topics.length ? `<details class="summary-details">
      <summary>查看全部栏目看点 <span aria-hidden="true">＋</span></summary>
      <dl>${topics.map(topic => `<div><dt>${name(topic.category)}</dt>
        <dd>${escape(topic.text)}</dd></div>`).join('')}</dl>
    </details>` : ''}
    <p class="summary-scope">根据本期 ${issue.articles.length} 条动态整理。</p>`;
}

function render() {
  if (!issue) return;
  const available = issue.articles.filter(article => selected.includes(article.category));
  $('#interest-count').textContent = selected.length + ' 个';
  $('#preference-summary').textContent = `本期 ${available.length} 条 · 按收录时间排列`;
  const navigation = [['all', '全部订阅'], ...categories.filter(category => selected.includes(category[0]))];
  $('#category-nav').innerHTML = navigation.map(([id, label]) => {
    const count = id === 'all' ? available.length : available.filter(article => article.category === id).length;
    return `<button class="category-tab ${active === id ? 'active' : ''}"
      data-category="${id}" aria-pressed="${active === id}"><span>${label}</span>
      <span class="tab-count">${count}</span></button>`;
  }).join('');
  const shown = active === 'all' ? available : available.filter(article => article.category === active);
  $('#feed-title').textContent = active === 'all' ? '全部订阅' : name(active);
  $('#feed-count').textContent = shown.length + ' 条新闻';
  const groups = categories.filter(([id]) => selected.includes(id) && (active === 'all' || active === id));
  $('#news-feed').innerHTML = groups.map(([id, label]) => {
    const articles = shown.filter(article => article.category === id)
      .sort((a, b) => (b.observedAt || b.publishedAt).localeCompare(a.observedAt || a.publishedAt));
    return `<section class="news-section" aria-label="${label}" id="section-${id}">
      <header class="section-header"><h2>${label}</h2><span class="section-count">${articles.length} 条</span>
        <span class="section-time">收录时间 ↓</span></header>
      ${articles.length ? `<div class="news-list">${articles.map(card).join('')}</div>` :
        '<div class="empty-state"><p>本期暂无这个方向的消息，下一期有内容时再更新。</p></div>'}
    </section>`;
  }).join('');
}

$('#category-options').addEventListener('change', updateSelection);
$('#select-all').onclick = () => {
  $('#category-options').querySelectorAll('input').forEach(input => { input.checked = true; });
  updateSelection();
};
$('#preferences').onclick = showPreferences;
$('#close-dialog').onclick = () => $('#preference-dialog').close();
$('#preference-dialog').addEventListener('cancel', event => { if (!onboarded) event.preventDefault(); });
$('#preference-form').onsubmit = savePreferences;
$('#category-nav').addEventListener('click', event => {
  const button = event.target.closest('[data-category]');
  if (!button) return;
  active = button.dataset.category;
  render();
  if (matchMedia('(max-width:720px)').matches) {
    $('#news-feed').scrollIntoView({
      behavior: matchMedia('(prefers-reduced-motion:reduce)').matches ? 'instant' : 'smooth', block: 'start'
    });
  }
});

let latestIssue;
let historyIndex;
let historyDays = 7;
let loadVersion = 0;

async function readJSON(url) {
  const response = await fetch(url, {cache:'no-store'});
  if (!response.ok) throw Error('载入失败');
  return response.json();
}

function showEdition(data, updateURL = true) {
  if (data.schemaVersion !== 1 || !data.articles?.length) throw Error('内容格式不正确');
  issue = data;
  const historical = data.date !== latestIssue.date;
  const date = new Date(data.date + 'T12:00:00+08:00');
  $('#edition-date').textContent = new Intl.DateTimeFormat('zh-CN', {
    year:'numeric', month:'long', day:'numeric', weekday:'long', timeZone:'Asia/Shanghai'
  }).format(date);
  $('h1').textContent = historical ? '这一天的 AI 动态' : '今日 AI 动态';
  $('.edition-intro').textContent = historical ? '回看这一天的消息与值得留意的变化。' : '先看今日总结，再读你感兴趣的消息。';
  $('.preview-dot').textContent = historical ? '历史日报' : '每日更新';
  $('#edition-info').textContent = `${data.articles.length} 条动态 · ${historical ? data.date : '每日更新'}`;
  document.title = `每日 AI · ${data.date}`;
  $('#back-latest').hidden = !historical;
  const position = historyIndex?.issues.findIndex(e => e.date === data.date) ?? -1;
  $('#previous-issue').hidden = position < 0 || position >= historyIndex.issues.length - 1;
  $('#next-issue').hidden = position <= 0;
  renderOverview();
  if (active !== 'all' && !selected.includes(active)) active = 'all';
  render();
  if (updateURL) {
    const url = new URL(location.href);
    if (historical) url.searchParams.set('date', data.date); else url.searchParams.delete('date');
    history.pushState({}, '', url);
  }
}

async function loadEdition(day, updateURL = true) {
  const version = ++loadVersion;
  $('#news-feed').setAttribute('aria-busy','true');
  try {
    if (day && !historyIndex?.issues.some(e => e.date === day)) throw Error('超出保留范围');
    const data = !day || day === latestIssue.date ? latestIssue : await readJSON(`data/issues/${day}.json`);
    if (version !== loadVersion) return;
    showEdition(data, updateURL);
    if ($('#history-dialog').open) $('#history-dialog').close();
    window.scrollTo({top:0, behavior:'instant'});
  } catch {
    if (version !== loadVersion) return;
    notify('这期暂时无法载入，请重试或选择其他日期');
    if (!issue) showEdition(latestIssue, false);
  } finally {
    if (version === loadVersion) $('#news-feed').removeAttribute('aria-busy');
  }
}

function renderHistory() {
  if (!historyIndex) return;
  const start = new Date(latestIssue.date + 'T00:00:00Z');
  start.setUTCDate(start.getUTCDate() - historyDays + 1);
  const entries = historyIndex.issues.filter(e => e.date >= start.toISOString().slice(0,10));
  $('#history-count').textContent = `${entries.length} 期 · ${entries.reduce((n,e)=>n+e.count,0)} 条`;
  $('#history-list').innerHTML = entries.map(e => `<button class="history-entry" data-date="${escape(e.date)}" ${issue?.date === e.date ? 'aria-current="date"' : ''}>
    <span class="history-date">${escape(e.date.slice(5).replace('-',' / '))}<small>${e.count} 条${e.date === latestIssue.date ? ' · 最新' : ''}</small></span>
    <span class="history-titles">${e.titles.map(t=>`<span>${escape(t)}</span>`).join('')}</span><span class="history-arrow" aria-hidden="true">↗</span></button>`).join('') || '<p class="load-note">这段时间暂无日报，试试近 30 天。</p>';
}

$('#history-open').onclick = async () => {
  $('#history-dialog').showModal();
  if (!historyIndex) {
    try { historyIndex = await readJSON('data/history.json'); }
    catch { $('#history-list').innerHTML = '<p class="load-note">历史暂时无法载入，请关闭后重试。</p>'; return; }
  }
  renderHistory();
};
$('#history-close').onclick = () => $('#history-dialog').close();
$('#history-dialog').addEventListener('click', event => {
  const range = event.target.closest('[data-days]');
  if (range) {
    historyDays = Number(range.dataset.days);
    document.querySelectorAll('[data-days]').forEach(b=>b.setAttribute('aria-pressed',String(b===range)));
    renderHistory();
  }
  const entry = event.target.closest('[data-date]');
  if (entry) loadEdition(entry.dataset.date);
});
$('#back-latest').onclick = () => loadEdition(latestIssue.date);
for (const [id, offset] of [['previous-issue',1],['next-issue',-1]]) {
  $('#' + id).onclick = () => {
    const at = historyIndex.issues.findIndex(e=>e.date===issue.date);
    const target = historyIndex.issues[at + offset];
    if (target) loadEdition(target.date);
  };
}
window.addEventListener('popstate',()=>loadEdition(new URLSearchParams(location.search).get('date'),false));

(async () => {
  try {
    latestIssue = await readJSON('data/web-preview.json');
    try { historyIndex = await readJSON('data/history.json'); } catch { /* Latest still works offline. */ }
    await loadEdition(params.get('date'), false);
    if (!onboarded) showPreferences();
  } catch {
    $('#edition-info').textContent = '本期内容暂时无法载入';
    $('#news-feed').innerHTML = '<div class="empty-state"><h2>内容暂时无法载入</h2><p>请稍后刷新重试。</p><button class="primary-button" id="retry">重新载入</button></div>';
    $('#retry').onclick = () => location.reload();
  }
})();
