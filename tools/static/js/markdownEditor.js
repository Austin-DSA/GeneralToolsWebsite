/*
 * markdownEditor.js - a tiny, dependency-free, progressive markdown editor.
 *
 * Enhances any <textarea data-markdown-editor> with a formatting toolbar and a
 * live preview. No third-party library, no CDN, no build step (matching the
 * repo's no-Node-pipeline ethos). The textarea stays the real form control, so
 * if this script fails to load or run the field degrades to a plain textarea
 * that posts plain markdown - the server needs no special handling either.
 *
 * The preview renderer mirrors the safe subset in tools/resolutionText.py
 * (escape-first, scheme-checked links). It is only an authoring aid; the read
 * view is rendered authoritatively (and safely) on the server.
 */
(function () {
  "use strict";

  function escapeHtml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function safeHref(url) {
    var u = url.trim();
    var lower = u.toLowerCase();
    var ok = lower.indexOf("http://") === 0 || lower.indexOf("https://") === 0 ||
             lower.indexOf("mailto:") === 0 || u.charAt(0) === "/" || u.charAt(0) === "#";
    if (!ok) return null;
    return u.replace(/"/g, "%22").replace(/'/g, "%27");
  }

  function renderInline(escaped) {
    escaped = escaped.replace(/`([^`]+)`/g, function (_, c) { return "<code>" + c + "</code>"; });
    escaped = escaped.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (_, text, url) {
      var href = safeHref(url);
      if (href === null) return text;
      return '<a href="' + href + '" target="_blank" rel="noopener">' + text + "</a>";
    });
    escaped = escaped.replace(/\*\*([^*]+)\*\*|__([^_]+)__/g, function (_, a, b) {
      return "<strong>" + (a || b) + "</strong>";
    });
    escaped = escaped.replace(/\*([^*]+)\*|_([^_]+)_/g, function (_, a, b) {
      return "<em>" + (a || b) + "</em>";
    });
    return escaped;
  }

  function renderMarkdown(text) {
    if (!text) return "";
    var lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    var out = [];
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      if (line.trim() === "") { i++; continue; }
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) { out.push("<hr>"); i++; continue; }
      var h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        var level = Math.min(h[1].length + 1, 6);
        out.push("<h" + level + ">" + renderInline(escapeHtml(h[2].trim())) + "</h" + level + ">");
        i++; continue;
      }
      if (/^[-*]\s+/.test(line)) {
        var ul = [];
        while (i < lines.length && /^[-*]\s+/.test(lines[i])) {
          ul.push("<li>" + renderInline(escapeHtml(lines[i].replace(/^[-*]\s+/, "").trim())) + "</li>");
          i++;
        }
        out.push("<ul>" + ul.join("") + "</ul>"); continue;
      }
      if (/^\d+\.\s+/.test(line)) {
        var ol = [];
        while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
          ol.push("<li>" + renderInline(escapeHtml(lines[i].replace(/^\d+\.\s+/, "").trim())) + "</li>");
          i++;
        }
        out.push("<ol>" + ol.join("") + "</ol>"); continue;
      }
      if (/^>\s?/.test(line)) {
        var bq = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          bq.push(renderInline(escapeHtml(lines[i].replace(/^>\s?/, "").trim())));
          i++;
        }
        out.push("<blockquote>" + bq.join("<br>") + "</blockquote>"); continue;
      }
      var para = [];
      while (i < lines.length && lines[i].trim() !== "" &&
             !/^(-{3,}|\*{3,}|_{3,})$/.test(lines[i].trim()) &&
             !/^#{1,6}\s+/.test(lines[i]) && !/^[-*]\s+/.test(lines[i]) &&
             !/^\d+\.\s+/.test(lines[i]) && !/^>\s?/.test(lines[i])) {
        para.push(renderInline(escapeHtml(lines[i].trim())));
        i++;
      }
      out.push("<p>" + para.join("<br>") + "</p>");
    }
    return out.join("");
  }

  function surround(textarea, before, after, placeholder) {
    var start = textarea.selectionStart;
    var end = textarea.selectionEnd;
    var selected = textarea.value.substring(start, end) || placeholder || "";
    var replacement = before + selected + after;
    textarea.setRangeText(replacement, start, end, "end");
    textarea.focus();
    textarea.dispatchEvent(new Event("input"));
  }

  function prefixLines(textarea, prefix) {
    var start = textarea.selectionStart;
    var end = textarea.selectionEnd;
    var value = textarea.value;
    var lineStart = value.lastIndexOf("\n", start - 1) + 1;
    var segment = value.substring(lineStart, end);
    var replaced = segment.split("\n").map(function (l) { return prefix + l; }).join("\n");
    textarea.setRangeText(replaced, lineStart, end, "end");
    textarea.focus();
    textarea.dispatchEvent(new Event("input"));
  }

  // Toolbar glyphs. B/I/H are styled letters; list/quote/link are inline SVGs.
  // All are developer-authored constants (no user input), so assigning them via
  // innerHTML below is safe.
  var ICON_LIST =
    '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">' +
    '<circle cx="2.4" cy="4" r="1.1" fill="currentColor"/>' +
    '<circle cx="2.4" cy="8" r="1.1" fill="currentColor"/>' +
    '<circle cx="2.4" cy="12" r="1.1" fill="currentColor"/>' +
    '<rect x="5.2" y="3.3" width="8.8" height="1.4" rx="0.7" fill="currentColor"/>' +
    '<rect x="5.2" y="7.3" width="8.8" height="1.4" rx="0.7" fill="currentColor"/>' +
    '<rect x="5.2" y="11.3" width="8.8" height="1.4" rx="0.7" fill="currentColor"/></svg>';
  var ICON_QUOTE =
    '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M3 4h3.5v3.6C6.5 9.7 5.3 11 3.4 11.6l-.5-1.2c1-.4 1.5-1 1.6-1.9H3V4zm6 0h3.5v3.6c0 2.1-1.2 3.4-3.1 4l-.5-1.2c1-.4 1.5-1 1.6-1.9H9V4z"/></svg>';
  var ICON_LINK =
    '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' +
    '<path d="M6.6 9.4l2.8-2.8"/>' +
    '<path d="M8.2 5l1-1a2.4 2.4 0 0 1 3.4 3.4l-1 1"/>' +
    '<path d="M7.8 11l-1 1A2.4 2.4 0 0 1 3.4 8.6l1-1"/></svg>';

  var BUTTONS = [
    { html: '<span class="mde-ico-b">B</span>', title: "Bold", run: function (t) { surround(t, "**", "**", "bold text"); } },
    { html: '<span class="mde-ico-i">I</span>', title: "Italic", run: function (t) { surround(t, "_", "_", "italic text"); } },
    { html: '<span class="mde-ico-h">H</span>', title: "Heading", run: function (t) { prefixLines(t, "## "); } },
    { html: ICON_LIST, title: "Bullet list", run: function (t) { prefixLines(t, "- "); } },
    { html: ICON_QUOTE, title: "Blockquote", run: function (t) { prefixLines(t, "> "); } },
    { html: ICON_LINK, title: "Link", run: function (t) { surround(t, "[", "](https://)", "link text"); } },
  ];

  function enhance(textarea) {
    if (textarea.dataset.mdeReady) return;
    textarea.dataset.mdeReady = "1";

    var wrap = document.createElement("div");
    wrap.className = "mde";
    textarea.parentNode.insertBefore(wrap, textarea);

    var toolbar = document.createElement("div");
    toolbar.className = "mde-toolbar";
    BUTTONS.forEach(function (b) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "mde-btn";
      btn.innerHTML = b.html;
      btn.title = b.title;
      btn.setAttribute("aria-label", b.title);
      btn.addEventListener("click", function () { b.run(textarea); });
      toolbar.appendChild(btn);
    });

    var previewBtn = document.createElement("button");
    previewBtn.type = "button";
    previewBtn.className = "mde-btn mde-preview-toggle";
    previewBtn.textContent = "Preview";
    previewBtn.setAttribute("aria-pressed", "false");
    toolbar.appendChild(previewBtn);

    var preview = document.createElement("div");
    preview.className = "mde-preview resolution-body";
    preview.hidden = true;

    wrap.appendChild(toolbar);
    wrap.appendChild(textarea);
    wrap.appendChild(preview);

    function refreshPreview() {
      if (!preview.hidden) preview.innerHTML = renderMarkdown(textarea.value);
    }
    previewBtn.addEventListener("click", function () {
      preview.hidden = !preview.hidden;
      previewBtn.setAttribute("aria-pressed", String(!preview.hidden));
      previewBtn.classList.toggle("is-active", !preview.hidden);
      refreshPreview();
    });
    textarea.addEventListener("input", refreshPreview);
  }

  function init() {
    var fields = document.querySelectorAll("textarea[data-markdown-editor]");
    Array.prototype.forEach.call(fields, enhance);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
