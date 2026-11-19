(() => {
  const form = document.querySelector('[data-search-form]');
  if (!form) return;
  const button = form.querySelector('button[type="submit"]');
  const panel = form.querySelector('[data-search-status]');
  const elapsed = panel.querySelector('[data-elapsed]');
  let interval;
  let submitting = false;
  form.addEventListener('submit', (event) => {
    if (submitting) { event.preventDefault(); return; }
    submitting = true;
    form.setAttribute('aria-busy', 'true');
    button.disabled = true;
    button.textContent = 'Подождите…';
    panel.hidden = false;
    const start = Date.now();
    elapsed.textContent = '0 с';
    interval = setInterval(() => { elapsed.textContent = Math.floor((Date.now() - start) / 1000) + ' с'; }, 1000);
  });
  window.addEventListener('pageshow', () => {
    clearInterval(interval);
    submitting = false;
    form.removeAttribute('aria-busy');
    button.disabled = false;
    button.textContent = 'Найти →';
    panel.hidden = true;
  });
})();
