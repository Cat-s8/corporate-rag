document.querySelectorAll('form[data-auto-group]').forEach((form) => {
  const select = form.querySelector('select[name="group"]');
  const status = form.querySelector('[role="status"]');
  let saved = select.value;
  select.addEventListener('change', async () => {
    const data = new FormData(form);
    const chosen = select.value;
    select.disabled = true;
    status.textContent = 'Сохранение…';
    try {
      const response = await fetch(form.action, {
        method: 'POST', body: data, headers: { Accept: 'application/json' }
      });
      if (!response.ok || response.redirected) throw new Error('Save failed');
      const result = await response.json();
      if (!result.saved) throw new Error('Save failed');
      saved = chosen;
      status.textContent = 'Сохранено';
    } catch (error) {
      select.value = saved;
      status.textContent = 'Не удалось сохранить. Обновите страницу и попробуйте снова.';
    } finally {
      select.disabled = false;
    }
  });
});
document.querySelectorAll('[data-cancel-delete]').forEach((button) => {
  button.addEventListener('click', () => {
    const details = button.closest('details');
    details.querySelector('form').reset();
    details.open = false;
    details.querySelector('summary').focus();
  });
});
document.querySelectorAll('.delete-document').forEach((details) => {
  details.addEventListener('toggle', () => {
    if (!details.open) details.querySelector('form').reset();
  });
});
