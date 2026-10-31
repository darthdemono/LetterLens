(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.LLRender = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ESC[c]);
  }

  function safeUrl(value) {
    const url = String(value || '').trim().replace(/[.,;:!?]+$/, '');
    if (!/^https?:\/\/[^\s<>"'`]+$/i.test(url)) return '';
    return escapeHtml(url);
  }

  function link(url, text) {
    const href = safeUrl(url);
    if (!href) return escapeHtml(text);
    return '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' + text + '</a>';
  }

  function inline(source) {
    const stash = [];
    const keep = (html) => '\u0000' + (stash.push(html) - 1) + '\u0000';
    let s = String(source == null ? '' : source).replace(/\u0000/g, '');
    s = s.replace(/`([^`]+)`/g, (_, code) => keep('<code>' + escapeHtml(code) + '</code>'));
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, text, url) =>
      keep(link(url, escapeHtml(text))));
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<>)]+)/g, (_, lead, url) => {
      const clean = url.replace(/[.,;:!?]+$/, '');
      const tail = url.slice(clean.length);
      return lead + keep(link(clean, escapeHtml(clean))) + tail;
    });
    s = escapeHtml(s);
    s = s.replace(/\[\^(\d+)\]/g, '<sup><a href="#fn-$1">$1</a></sup>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    s = s.replace(/\b(\d{4}-\d{2}-\d{2})\b/g, '<span class="date">$1</span>');
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => stash[Number(i)]);
  }

  function cells(line) {
    return line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  }

  function isSeparator(line) {
    return cells(line).every((c) => /^:?-{2,}:?$/.test(c));
  }

  function urgency(value) {
    const v = String(value).toLowerCase();
    if (v.startsWith('urgent')) return 'urgent';
    if (v.startsWith('soon')) return 'soon';
    return 'none';
  }

  function glance(rows) {
    const out = ['<dl class="glance">'];
    rows.forEach(([key, value]) => {
      const text = key.toLowerCase() === 'urgency'
        ? '<span class="badge badge-' + urgency(value) + '">' + inline(value) + '</span>'
        : inline(value);
      out.push('<div><dt>' + inline(key) + '</dt><dd>' + text + '</dd></div>');
    });
    out.push('</dl>');
    return out.join('');
  }

  function parseGlance(md) {
    const lines = String(md || '').split('\n');
    const at = lines.findIndex((l) => /^#{2,3}\s+at a glance\s*$/i.test(l.trim()));
    const result = {};
    if (at < 0) return result;
    for (let i = at + 1; i < lines.length; i++) {
      const line = lines[i].trim();
      if (/^#{1,6}\s/.test(line)) break;
      if (line.startsWith('|') && !isSeparator(line)) {
        const [key, value] = cells(line);
        if (key && value) result[key.toLowerCase()] = value;
      }
    }
    return result;
  }

  function render(md) {
    const lines = String(md || '').split('\n');
    const out = [];
    const lists = [];
    let table = null;
    let section = '';
    let quote = [];

    function closeLists(depth) {
      while (lists.length > depth) out.push('</li></' + lists.pop().tag + '>');
    }

    function flushQuote() {
      if (quote.length) {
        out.push('<blockquote>' + quote.map(inline).join('<br>') + '</blockquote>');
        quote = [];
      }
    }

    function flushTable() {
      if (!table) return;
      if (section === 'at a glance' && table.rows.every((r) => r.length >= 2)) {
        const generic = ['', 'item', 'field', 'detail', 'details', 'what'];
        const keepHeader = !generic.includes(table.header[0].toLowerCase());
        out.push(glance(keepHeader ? [table.header, ...table.rows] : table.rows));
      } else {
        out.push('<div class="table-wrap"><table><thead><tr>' +
          table.header.map((h) => '<th>' + inline(h) + '</th>').join('') + '</tr></thead><tbody>' +
          table.rows.map((r) => '<tr>' + r.map((c) => '<td>' + inline(c) + '</td>').join('') +
            '</tr>').join('') + '</tbody></table></div>');
      }
      table = null;
    }

    lines.forEach((raw) => {
      const line = raw.replace(/\s+$/, '');
      const trimmed = line.trim();

      if (trimmed.startsWith('|') && trimmed.endsWith('|') && trimmed.length > 1) {
        closeLists(0);
        flushQuote();
        if (!table) table = { header: cells(trimmed), rows: [], sawSeparator: false };
        else if (!table.sawSeparator && isSeparator(trimmed)) table.sawSeparator = true;
        else table.rows.push(cells(trimmed));
        return;
      }
      flushTable();

      if (trimmed.startsWith('>')) {
        closeLists(0);
        quote.push(trimmed.replace(/^>\s?/, ''));
        return;
      }
      flushQuote();

      if (!trimmed) {
        closeLists(0);
        return;
      }

      const heading = trimmed.match(/^(#{1,3})\s+(.*)$/);
      if (heading) {
        closeLists(0);
        section = heading[2].trim().toLowerCase();
        const level = heading[1].length === 1 ? 2 : 3;
        out.push('<h' + level + '>' + inline(heading[2]) + '</h' + level + '>');
        return;
      }

      if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
        closeLists(0);
        out.push('<hr>');
        return;
      }

      const item = line.match(/^(\s*)([*+-]|\d+\.)\s+(.*)$/);
      if (item) {
        const indent = item[1].length;
        const tag = /\d/.test(item[2]) ? 'ol' : 'ul';
        const sources = tag === 'ol' && /sources|footnotes/.test(section);
        while (lists.length && lists[lists.length - 1].indent > indent) {
          out.push('</li></' + lists.pop().tag + '>');
        }
        let top = lists[lists.length - 1];
        if (top && top.indent === indent && top.tag !== tag) {
          out.push('</li></' + lists.pop().tag + '>');
          top = lists[lists.length - 1];
        }
        if (top && top.indent === indent) {
          out.push('</li><li>');
        } else {
          out.push('<' + tag + (sources ? ' class="sources"' : '') + '><li>');
          lists.push({ tag, indent });
        }
        if (sources) out.push('<span id="fn-' + /^\d+/.exec(item[2])[0] + '"></span>');
        out.push(inline(item[3]));
        return;
      }

      closeLists(0);
      const note = trimmed.match(/^\[\^(\d+)\]:\s*(.*)$/);
      if (note) {
        out.push('<p class="footnote" id="fn-' + note[1] + '">' + inline(note[2]) + '</p>');
      } else if (/^disclaimer:/i.test(trimmed)) {
        out.push('<p class="disclaimer">' + inline(trimmed) + '</p>');
      } else {
        out.push('<p>' + inline(trimmed) + '</p>');
      }
    });

    closeLists(0);
    flushQuote();
    flushTable();
    return out.join('');
  }

  function icsEscape(text) {
    return String(text).replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,')
      .replace(/\r?\n/g, '\\n');
  }

  function icsFor(title, isoDate, description) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(isoDate))) return '';
    const day = isoDate.replace(/-/g, '');
    const next = new Date(isoDate + 'T00:00:00Z');
    if (Number.isNaN(next.getTime())) return '';
    next.setUTCDate(next.getUTCDate() + 1);
    const end = next.toISOString().slice(0, 10).replace(/-/g, '');
    return [
      'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//LetterLens//EN', 'BEGIN:VEVENT',
      'UID:' + day + '-letterlens@local', 'DTSTAMP:' + day + 'T000000Z',
      'DTSTART;VALUE=DATE:' + day, 'DTEND;VALUE=DATE:' + end,
      'SUMMARY:' + icsEscape(title), 'DESCRIPTION:' + icsEscape(description || ''),
      'END:VEVENT', 'END:VCALENDAR', '',
    ].join('\r\n');
  }

  function duration(ms) {
    const value = Number(ms);
    if (!Number.isFinite(value) || value < 0) return '';
    if (value < 1000) return Math.round(value) + ' ms';
    if (value < 60000) return (value / 1000).toFixed(1) + ' s';
    const total = Math.round(value / 1000);
    return Math.floor(total / 60) + ' min ' + (total % 60) + ' s';
  }

  function tookText(total, first) {
    const all = duration(total);
    if (!all) return '';
    const early = first == null ? '' : duration(first);
    return 'Answered in ' + all + (early && first < total ? ' (first words after ' + early + ')' : '');
  }

  return { escapeHtml, safeUrl, inline, render, parseGlance, icsFor, urgency, duration, tookText };
});
