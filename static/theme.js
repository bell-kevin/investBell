// SPDX-License-Identifier: AGPL-3.0-or-later
// Color theme: "auto" follows the device setting; "light" and "dark" override it.
// Loaded as a blocking script in <head> so the page never flashes the wrong theme.
// Sets data-theme on <html> to the resolved theme and fires "themechange" for canvas redraws.
(() => {
  const key = 'investbell-theme', root = document.documentElement, prefersLight = matchMedia('(prefers-color-scheme: light)');
  const valid = value => value === 'light' || value === 'dark' ? value : 'auto';
  let choice = 'auto';
  try { choice = valid(localStorage.getItem(key)); } catch {}
  function apply() {
    root.dataset.theme = choice === 'auto' ? (prefersLight.matches ? 'light' : 'dark') : choice;
    for (const button of document.querySelectorAll('[data-theme-choice]')) button.setAttribute('aria-pressed', String(button.dataset.themeChoice === choice));
    document.dispatchEvent(new Event('themechange'));
  }
  apply();
  prefersLight.addEventListener('change', apply);
  // Keep other open tabs on the same choice.
  addEventListener('storage', event => { if (event.key === key) { choice = valid(event.newValue); apply(); } });
  document.addEventListener('DOMContentLoaded', () => {
    for (const button of document.querySelectorAll('[data-theme-choice]')) button.addEventListener('click', () => {
      choice = button.dataset.themeChoice;
      try { if (choice === 'auto') localStorage.removeItem(key); else localStorage.setItem(key, choice); } catch {}
      apply();
    });
    apply();
  });
})();
