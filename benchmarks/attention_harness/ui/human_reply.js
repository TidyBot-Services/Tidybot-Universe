(function (global) {
  'use strict';
  // The shared structure of panel 04 (Help & replies): one block per question the person has.
  //   A  What happened       B  The robot asks       C  Your answer       D  What happens next       E  History
  // live.js supplies the content of each block; this file only lays them out and owns the optional
  // pieces of C (problem type, skip) that a record sink can store. The same layout serves a live run
  // and a replay, so a reply is given in the same place either way.
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

  const CATEGORIES = [['position', 'Position or direction'], ['speed', 'Speed'], ['object', 'Wrong object'], ['reach', 'Out of reach'], ['other', 'Something else']];

  function block(letter, title, html, extra = '') {
    if (!html) return '';
    return `<div class="hr-block" data-hr-block="${letter}"${extra}><h5><span aria-hidden="true">${letter}</span>${esc(title)}</h5><div class="hr-body">${html}</div></div>`;
  }

  // One optional, single-choice "what kind of problem was it" row. Empty means no answer.
  function categoryChips(name = 'hr-category') {
    return `<fieldset class="hr-category" data-hr-category><legend>What kind of problem was it? <small>(optional)</small></legend>${CATEGORIES.map(([value, label]) =>
      `<label><input type="radio" name="${esc(name)}" value="${value}"><span>${esc(label)}</span></label>`).join('')}</fieldset>`;
  }

  function skipButton(label = 'I\'m not sure — skip') {
    return `<button type="button" class="ab-button-quiet" data-hr-skip>${esc(label)}</button>`;
  }

  // parts: {a, b, c, d, e} are HTML strings (an empty one drops its block); titles may be overridden.
  function blocks(parts = {}, titles = {}) {
    const t = {a: 'What happened', b: 'The robot asks', c: 'Your answer', d: 'What happens next', e: 'History', ...titles};
    return `<div class="hr-blocks">${block('A', t.a, parts.a)}${block('B', t.b, parts.b)}${block('C', t.c, parts.c)}${block('D', t.d, parts.d)}${block('E', t.e, parts.e, ' data-hr-history')}</div>`;
  }

  global.HumanReply = {CATEGORIES, blocks, block, categoryChips, skipButton, esc};
})(window);
