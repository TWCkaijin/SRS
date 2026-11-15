// Apply before first paint, including when browser storage is unavailable.
(function () {
  var theme = 'system';
  try { theme = localStorage.getItem('srs-theme') || 'system'; } catch (_) {}
  var resolved = theme === 'dark' || (theme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  document.documentElement.dataset.theme = resolved;
  document.documentElement.style.colorScheme = resolved;
})();
