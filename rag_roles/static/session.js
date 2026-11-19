(() => {
  const sessionInfo = document.querySelector('[data-session-limit]');
  if (!sessionInfo) return;
  const csrf = sessionInfo.dataset.csrf;
  let deadline = Date.now() + Number(sessionInfo.dataset.sessionLimit) * 1000;
  let timer;
  let lastActivitySent = 0;
  let activityPending = false;
  let checking = false;
  const leave = () => window.location.replace('/login?expired=1');
  const apply = (remaining) => {
    deadline = Date.now() + remaining * 1000;
    clearTimeout(timer);
    timer = setTimeout(check, Math.max(50, remaining * 1000));
  };
  async function check() {
    if (checking) return;
    checking = true;
    try {
      const response = await fetch('/api/session-state', {cache: 'no-store', signal: AbortSignal.timeout(5000)});
      if (response.status === 401 || response.redirected) { leave(); return; }
      if (!response.ok) throw new Error('Session check failed');
      const result = await response.json();
      if (result.remaining <= 0) { leave(); return; }
      apply(result.remaining);
    } catch (error) {
      if (Date.now() >= deadline) leave();
      else { clearTimeout(timer); timer = setTimeout(check, Math.min(5000, deadline - Date.now())); }
    } finally { checking = false; }
  }
  async function activity(event) {
    if (!event.isTrusted || document.hidden || activityPending || Date.now() - lastActivitySent < 15000) return;
    lastActivitySent = Date.now();
    activityPending = true;
    try {
      const response = await fetch('/api/activity', {
        method: 'POST', headers: {'X-CSRF-Token': csrf}, signal: AbortSignal.timeout(5000)
      });
      if (response.status === 401 || response.redirected) { leave(); return; }
      if (response.ok) apply((await response.json()).remaining);
    } catch (error) { /* The server deadline still applies while offline. */ }
    finally { activityPending = false; }
  }
  for (const type of ['pointerdown', 'pointermove', 'keydown', 'scroll', 'input']) {
    document.addEventListener(type, activity, {passive: true, capture: true});
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
  window.addEventListener('pageshow', check);
  setInterval(check, 30000);
  apply(Number(sessionInfo.dataset.sessionLimit));
  check();
})();
