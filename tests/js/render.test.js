const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const R = require('../../static/render.js');

test('escapeHtml escapes and tolerates null', () => {
  assert.equal(R.escapeHtml('<a href="x">&\'</a>'), '&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;');
  assert.equal(R.escapeHtml(null), '');
  assert.equal(R.escapeHtml(undefined), '');
  assert.equal(R.escapeHtml(5), '5');
});

test('safeUrl accepts only http(s) and trims punctuation', () => {
  assert.equal(R.safeUrl('https://njt.hu/x.'), 'https://njt.hu/x');
  assert.equal(R.safeUrl('javascript:alert(1)'), '');
  assert.equal(R.safeUrl('http://a.b/"onmouseover'), '');
  assert.equal(R.safeUrl(''), '');
  assert.equal(R.safeUrl(null), '');
});

test('inline renders code, links, bold, italics, dates and footnotes', () => {
  const html = R.inline('**Pay** *soon* by 2026-11-14 `a<b` [site](https://x.hu/a) [^1]');
  assert.match(html, /<strong>Pay<\/strong>/);
  assert.match(html, /<em>soon<\/em>/);
  assert.match(html, /<span class="date">2026-11-14<\/span>/);
  assert.match(html, /<code>a&lt;b<\/code>/);
  assert.match(html, /<a href="https:\/\/x.hu\/a" target="_blank" rel="noopener noreferrer">site<\/a>/);
  assert.match(html, /<sup><a href="#fn-1">1<\/a><\/sup>/);
});

test('inline links bare urls and leaves trailing punctuation outside', () => {
  const html = R.inline('See https://njt.hu/page, then (https://a.hu/b).');
  assert.match(html, /<a href="https:\/\/njt.hu\/page"[^>]*>https:\/\/njt.hu\/page<\/a>,/);
  assert.match(html, /\(<a href="https:\/\/a.hu\/b"/);
});

test('inline neutralises scripts, markdown links with bad schemes and null bytes', () => {
  assert.equal(R.inline('<script>alert(1)</script>'), '&lt;script&gt;alert(1)&lt;/script&gt;');
  assert.doesNotMatch(R.inline('[x](javascript:alert(1))'), /<a /);
  assert.equal(R.inline('a\u0000b'), 'ab');
  assert.equal(R.inline(null), '');
  assert.equal(R.inline('[x](https://a.hu/"onclick=1)'), 'x');
});

test('render handles headings, paragraphs, rules and the disclaimer', () => {
  const html = R.render('# Title\n## Sub\n### Deep\ntext\n---\nDisclaimer: not legal advice');
  assert.match(html, /<h2>Title<\/h2><h3>Sub<\/h3><h3>Deep<\/h3><p>text<\/p><hr>/);
  assert.match(html, /<p class="disclaimer">Disclaimer: not legal advice<\/p>/);
  assert.equal(R.render(null), '');
});

test('render builds nested and mixed lists', () => {
  const html = R.render('- one\n  - inner\n- two\n\n1. first\n2. second\n- back to bullets');
  assert.equal(
    html,
    '<ul><li>one<ul><li>inner</li></ul></li><li>two</li></ul>' +
    '<ol><li>first</li><li>second</li></ol><ul><li>back to bullets</li></ul>',
  );
});

test('render switches list type at the same indent', () => {
  const html = R.render('- a\n1. b');
  assert.equal(html, '<ul><li>a</li></ul><ol><li>b</li></ol>');
});

test('render outdents several levels at once', () => {
  const html = R.render('- a\n  - b\n    - c\n- d');
  assert.equal(
    html,
    '<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li><li>d</li></ul>',
  );
});

test('render numbers footnote anchors inside Sources lists', () => {
  const html = R.render('## Sources\n1. [NJT](https://njt.hu)\n2. https://x.hu/a');
  assert.match(html, /<ol class="sources"><li><span id="fn-1"><\/span><a /);
  assert.match(html, /<li><span id="fn-2"><\/span>/);
});

test('render handles footnote definitions and quotes', () => {
  const html = R.render('> line one\n> line two\n[^3]: note text\nplain');
  assert.match(html, /<blockquote>line one<br>line two<\/blockquote>/);
  assert.match(html, /<p class="footnote" id="fn-3">note text<\/p>/);
  assert.match(html, /<p>plain<\/p>/);
});

test('quotes close at the end of input and after lists', () => {
  assert.equal(R.render('- a\n> q'), '<ul><li>a</li></ul><blockquote>q</blockquote>');
  assert.equal(R.render('>quoted'), '<blockquote>quoted</blockquote>');
});

test('render builds generic tables', () => {
  const html = R.render('| A | B |\n|---|:--:|\n| 1 | **2** |\n');
  assert.match(html, /<table><thead><tr><th>A<\/th><th>B<\/th><\/tr><\/thead>/);
  assert.match(html, /<tbody><tr><td>1<\/td><td><strong>2<\/strong><\/td><\/tr><\/tbody>/);
  assert.match(html, /^<div class="table-wrap">/);
});

test('table closes lists and quotes and ends at end of input', () => {
  const html = R.render('- x\n> q\n| A |\n|---|\n| 1 |');
  assert.match(html, /<\/ul><blockquote>q<\/blockquote><div class="table-wrap">/);
});

const GLANCE = [
  '## At a glance',
  '| Item | Detail |',
  '|---|---|',
  '| Deadline | 2026-11-14 |',
  '| Urgency | Urgent: 14 days |',
  '| Cost | |',
  '',
  'After',
].join('\n');

test('At a glance becomes a card with an urgency badge', () => {
  const html = R.render(GLANCE);
  assert.match(html, /<dl class="glance">/);
  assert.match(html, /<dt>Deadline<\/dt><dd><span class="date">2026-11-14<\/span><\/dd>/);
  assert.match(html, /<span class="badge badge-urgent">Urgent: 14 days<\/span>/);
  assert.doesNotMatch(html, /<dt>Item<\/dt>/);
  assert.match(html, /<p>After<\/p>/);
});

test('At a glance keeps a meaningful header row', () => {
  const html = R.render('## At a glance\n| Sender | Landlord |\n|---|---|\n| Type | Notice |');
  assert.match(html, /<dt>Sender<\/dt><dd>Landlord<\/dd>/);
});

test('At a glance falls back to a table when rows have one column', () => {
  const html = R.render('## At a glance\n| A |\n|---|\n| 1 |');
  assert.match(html, /<table>/);
});

test('urgency classifies labels', () => {
  assert.equal(R.urgency('Urgent'), 'urgent');
  assert.equal(R.urgency('soon - 3 weeks'), 'soon');
  assert.equal(R.urgency('No rush'), 'none');
});

test('parseGlance reads the table under the heading', () => {
  const parsed = R.parseGlance(`${GLANCE}\n## Next\n| Deadline | 1999-01-01 |`);
  assert.equal(parsed.deadline, '2026-11-14');
  assert.equal(parsed.urgency, 'Urgent: 14 days');
  assert.equal(parsed.cost, undefined);
  assert.deepEqual(R.parseGlance('no table'), {});
  assert.deepEqual(R.parseGlance(null), {});
});

test('icsFor builds an all-day event', () => {
  const ics = R.icsFor('Pay; rent, now', '2026-12-31', 'Line\nTwo \\ end');
  assert.match(ics, /^BEGIN:VCALENDAR\r\n/);
  assert.match(ics, /DTSTART;VALUE=DATE:20261231\r\n/);
  assert.match(ics, /DTEND;VALUE=DATE:20270101\r\n/);
  assert.match(ics, /SUMMARY:Pay\\; rent\\, now\r\n/);
  assert.match(ics, /DESCRIPTION:Line\\nTwo \\\\ end\r\n/);
  assert.match(ics, /END:VCALENDAR\r\n$/);
});

test('icsFor rejects bad dates and tolerates a missing description', () => {
  assert.equal(R.icsFor('x', 'tomorrow'), '');
  assert.equal(R.icsFor('x', '2026-13-45'), '');
  assert.match(R.icsFor('x', '2026-01-02'), /DESCRIPTION:\r\n/);
});

test('loads as a browser global when there is no module system', () => {
  const file = path.join(__dirname, '../../static/render.js');
  const sandbox = {};
  sandbox.self = sandbox;
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), sandbox, { filename: file });
  assert.equal(typeof sandbox.LLRender.render, 'function');
});

test('duration formats milliseconds, seconds and minutes', () => {
  assert.equal(R.duration(420.4), '420 ms');
  assert.equal(R.duration(4249), '4.2 s');
  assert.equal(R.duration(75000), '1 min 15 s');
  assert.equal(R.duration(-1), '');
  assert.equal(R.duration('x'), '');
  assert.equal(R.duration(undefined), '');
});

test('tookText reports total and first-word time', () => {
  assert.equal(R.tookText(4200, 1100), 'Answered in 4.2 s (first words after 1.1 s)');
  assert.equal(R.tookText(900, null), 'Answered in 900 ms');
  assert.equal(R.tookText(900, 900), 'Answered in 900 ms');
  assert.equal(R.tookText(NaN, 5), '');
});
