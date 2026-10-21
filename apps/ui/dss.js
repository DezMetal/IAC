/* GENERATED COPY — do not edit. Source: /test/DSS. Run `python sync.py`. */
/**
 * DSS — D-Net Signature Stylesheet
 * v8.2.0 · theme + preset controller
 * D-Net Lab · From Mind To Matter
 *
 * Zero dependencies. Safe to load in <head> or at end of <body>.
 *
 * Two orthogonal axes:
 *   THEME  — dark | light. Surfaces and ink. Dark is the house default
 *            regardless of OS preference; light is opt-in.
 *   PRESET — signal | aero | softclub | eink | terminal. Accent family,
 *            halos and glow. Any preset works in either theme.
 *
 * Markup hooks (no wiring required):
 *   <button data-dss-theme-toggle></button>
 *   <button data-dss-preset-cycle></button>
 *   <select data-dss-preset-select></select>     (auto-populated)
 *   <button data-dss-preset="aero">Aero</button>
 *
 * API:
 *   DSS.theme() / DSS.setTheme('light') / DSS.toggleTheme()
 *   DSS.preset() / DSS.setPreset('aero') / DSS.cyclePreset()
 *   DSS.presets  ->  [{id, label}, ...]
 *
 * Events:
 *   window.addEventListener('dss:themechange',  e => e.detail.theme)
 *   window.addEventListener('dss:presetchange', e => e.detail.preset)
 */
(function (root) {
  'use strict';

  var THEME_KEY  = 'dss-theme';
  // Set once the visitor picks a theme themselves. Until then a preset is
  // free to open in its own natural mode.
  var THEME_CHOSEN_KEY = 'dss-theme-chosen';
  var PRESET_KEY = 'dss-preset';
  var DEFAULT_THEME = 'dark';
  var DEFAULT_PRESET = 'signal';

  var PRESETS = [
    { id: 'signal',   label: 'Signal Glass' },
    { id: 'aero',     label: 'Aero' },
    { id: 'softclub', label: 'Softclub' },
    { id: 'eink',     label: 'E-Ink' },
    { id: 'terminal', label: 'Terminal' }
  ];

  var el = document.documentElement;

  function read(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, value); } catch (e) {}
  }
  function emit(name, detail) {
    root.dispatchEvent(new CustomEvent(name, { detail: detail }));
  }

  /* ── Theme ───────────────────────────────────────────────── */

  function theme() {
    return el.getAttribute('data-theme') || DEFAULT_THEME;
  }

  /** A preset's natural mode, declared in CSS as --dss-prefers.
      Paper wants light, a console wants dark, Frutiger Aero wants a sunlit
      day. Read from the cascade so presets stay pure CSS. */
  function prefers(id) {
    var probe = el.getAttribute('data-dss-preset');
    if (id === DEFAULT_PRESET) el.removeAttribute('data-dss-preset');
    else el.setAttribute('data-dss-preset', id);
    var want = getComputedStyle(el).getPropertyValue('--dss-prefers').trim();
    if (probe === null) el.removeAttribute('data-dss-preset');
    else el.setAttribute('data-dss-preset', probe);
    return want === 'light' || want === 'dark' ? want : DEFAULT_THEME;
  }

  function applyTheme(next) {
    el.setAttribute('data-theme', next);
    write(THEME_KEY, next);
    paint();
    emit('dss:themechange', { theme: next });
  }

  function setTheme(next) {
    if (next !== 'dark' && next !== 'light') return;
    write(THEME_CHOSEN_KEY, '1');      // an explicit choice, remembered
    applyTheme(next);
  }

  function toggleTheme() { setTheme(theme() === 'dark' ? 'light' : 'dark'); }

  /* ── Preset ──────────────────────────────────────────────── */

  function isPreset(id) {
    for (var i = 0; i < PRESETS.length; i++) if (PRESETS[i].id === id) return true;
    return false;
  }

  function preset() {
    return el.getAttribute('data-dss-preset') || DEFAULT_PRESET;
  }

  function setPreset(next) {
    if (!isPreset(next)) return;
    // "signal" is the built-in identity; it carries no attribute.
    if (next === DEFAULT_PRESET) el.removeAttribute('data-dss-preset');
    else el.setAttribute('data-dss-preset', next);
    write(PRESET_KEY, next);
    // Follow the new preset home, unless the visitor has stated a preference.
    if (!read(THEME_CHOSEN_KEY)) {
      var want = prefers(next);
      if (want !== theme()) { el.setAttribute('data-theme', want); write(THEME_KEY, want); }
    }
    paint();
    emit('dss:presetchange', { preset: next });
  }

  function cyclePreset() {
    var current = preset(), i = 0;
    for (var n = 0; n < PRESETS.length; n++) if (PRESETS[n].id === current) i = n;
    setPreset(PRESETS[(i + 1) % PRESETS.length].id);
  }

  function labelFor(id) {
    for (var i = 0; i < PRESETS.length; i++) if (PRESETS[i].id === id) return PRESETS[i].label;
    return id;
  }

  /* ── Reflect state into the UI ───────────────────────────── */

  function paint() {
    var dark = theme() === 'dark';
    var current = preset();

    document.querySelectorAll('[data-dss-theme-toggle]').forEach(function (node) {
      if (!node.hasAttribute('data-dss-no-glyph')) node.textContent = dark ? '☀' : '☾';
      node.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
      node.setAttribute('aria-pressed', String(!dark));
    });

    document.querySelectorAll('[data-dss-preset-cycle]').forEach(function (node) {
      if (!node.hasAttribute('data-dss-no-glyph')) node.textContent = labelFor(current);
      node.setAttribute('aria-label', 'Change theme preset (current: ' + labelFor(current) + ')');
    });

    document.querySelectorAll('[data-dss-preset-select]').forEach(function (node) {
      if (!node.options.length) {
        PRESETS.forEach(function (p) {
          var opt = document.createElement('option');
          opt.value = p.id;
          opt.textContent = p.label;
          node.appendChild(opt);
        });
      }
      node.value = current;
    });

    document.querySelectorAll('[data-dss-preset]').forEach(function (node) {
      if (node === el) return;
      node.setAttribute('aria-pressed', String(node.getAttribute('data-dss-preset') === current));
    });
  }

  /* ── Boot ────────────────────────────────────────────────── */

  function init() {
    var savedPreset = read(PRESET_KEY);
    if (savedPreset && isPreset(savedPreset) && savedPreset !== DEFAULT_PRESET) {
      el.setAttribute('data-dss-preset', savedPreset);
    }

    var savedTheme = read(THEME_KEY);
    var chosen = read(THEME_CHOSEN_KEY);
    if (chosen && (savedTheme === 'dark' || savedTheme === 'light')) {
      el.setAttribute('data-theme', savedTheme);          // the visitor decided
    } else if (!el.hasAttribute('data-theme')) {
      el.setAttribute('data-theme', prefers(preset()));   // the preset decides
    }

    paint();

    document.addEventListener('click', function (e) {
      if (!e.target.closest) return;

      if (e.target.closest('[data-dss-theme-toggle]')) { e.preventDefault(); toggleTheme(); return; }
      if (e.target.closest('[data-dss-preset-cycle]')) { e.preventDefault(); cyclePreset(); return; }

      var pick = e.target.closest('[data-dss-preset]');
      if (pick && pick !== el) { e.preventDefault(); setPreset(pick.getAttribute('data-dss-preset')); }
    });

    document.addEventListener('change', function (e) {
      if (e.target.matches && e.target.matches('[data-dss-preset-select]')) setPreset(e.target.value);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  root.DSS = {
    version: '8.2.0',
    presets: PRESETS.slice(),
    theme: theme, setTheme: setTheme, toggleTheme: toggleTheme,
    preset: preset, setPreset: setPreset, cyclePreset: cyclePreset,
    /** A preset's natural light/dark polarity. */
    prefers: prefers,
    /** Forget the visitor's explicit theme choice, so presets open in their
        own mode again. */
    clearThemeChoice: function () {
      try { localStorage.removeItem(THEME_CHOSEN_KEY); } catch (e) {}
      applyTheme(prefers(preset()));
    }
  };
})(window);
