async function loadAlerts() {
  const alertsEl = document.getElementById('alerts');
  const updatedEl = document.getElementById('updated');

  try {
    const response = await fetch('./data/alerts.json', { cache: 'no-store' });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const alerts = await response.json();

    if (!Array.isArray(alerts) || alerts.length === 0) {
      alertsEl.innerHTML = '<div class="empty">No alerts found yet.</div>';
      updatedEl.textContent = 'No data available';
      return;
    }

    alertsEl.innerHTML = alerts
      .slice()
      .reverse()
      .map((item) => {
        const title = escapeHtml(item.title || 'Unknown Tender');
        const source = escapeHtml(item.source || 'Unknown source');
        const date = escapeHtml(item.date || item.added_at || 'N/A');
        const url = escapeAttribute(item.url || '#');

        return `
          <div class="card">
            <div class="title">${title}</div>
            <div class="meta">Source: ${source}</div>
            <div class="meta">Date: ${date}</div>
            <a href="${url}" target="_blank" rel="noopener noreferrer">Open tender</a>
          </div>
        `;
      })
      .join('');

    updatedEl.textContent = new Date().toLocaleString();
  } catch (error) {
    console.error(error);
    alertsEl.innerHTML = '<div class="empty">Unable to load alerts data.</div>';
    updatedEl.textContent = 'Error loading data';
  }
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function escapeAttribute(value) {
  return escapeHtml(value);
}

const refreshBtn = document.getElementById('refresh');
refreshBtn.addEventListener('click', loadAlerts);

loadAlerts();
setInterval(loadAlerts, 300000);
