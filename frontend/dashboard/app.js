/**
 * EcoLink Operations Dashboard Main Application Logic
 */
document.addEventListener('DOMContentLoaded', () => {
  const api = new ApiClient({ mockMode: true });

  // DOM Elements
  const loginScreen = document.getElementById('login-screen');
  const loginForm = document.getElementById('login-form');
  const loginEmail = document.getElementById('login-email');
  const loginPassword = document.getElementById('login-password');

  const app = document.getElementById('app');
  const userBadge = document.getElementById('user-badge');
  const btnLogout = document.getElementById('btn-logout');
  const btnDemoReset = document.getElementById('btn-demo-reset');
  const btnRefresh = document.getElementById('btn-refresh');

  const viewTitle = document.getElementById('view-title');
  const navItems = document.querySelectorAll('.nav-item');
  const tabPanes = document.querySelectorAll('.tab-pane');

  const tableBody = document.getElementById('pickups-table-body');
  const statusFilterChips = document.querySelectorAll('#status-filters .chip');
  const detailDrawer = document.getElementById('detail-drawer');
  const detailContent = document.getElementById('detail-content');
  const closeDrawer = document.getElementById('close-drawer');

  // Stats & filter counts elements
  const statTotal = document.getElementById('stat-total');
  const statRequested = document.getElementById('stat-requested');
  const statOffered = document.getElementById('stat-offered');
  const statConfirmed = document.getElementById('stat-confirmed');
  const statCompleted = document.getElementById('stat-completed');

  const countAll = document.getElementById('count-all');
  const countRequested = document.getElementById('count-requested');
  const countOffered = document.getElementById('count-offered');
  const countConfirmed = document.getElementById('count-confirmed');
  const countCompleted = document.getElementById('count-completed');
  const countDeclined = document.getElementById('count-declined');

  const pickupSearch = document.getElementById('pickup-search');
  const btnMapFit = document.getElementById('btn-map-fit');
  const btnMapCenter = document.getElementById('btn-map-center');

  // State
  let currentFilter = 'all';
  let searchQuery = '';
  let pickups = [];
  let recyclers = [];
  let selectedPickupId = null;
  let leafletMap = null;
  let mapMarkers = [];

  // --- Auth Handler ---
  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const email = loginEmail.value.trim();
      const password = loginPassword.value.trim();
      const auth = await api.login(email, password);

      userBadge.textContent = auth.group === 'admins' ? 'Admin' : 'Viewer';
      userBadge.style.background = auth.group === 'admins' ? '#fef3c7' : '#e0e7ff';
      userBadge.style.color = auth.group === 'admins' ? '#b45309' : '#3730a3';

      const sidebarUserDisplay = document.getElementById('sidebar-user-display');
      if (sidebarUserDisplay) {
        sidebarUserDisplay.textContent = email;
      }

      // Trigger brief authenticating transition (350ms)
      const transitionEl = document.getElementById('auth-transition');
      if (transitionEl) {
        transitionEl.classList.add('active');
        await new Promise(res => setTimeout(res, 350));
        transitionEl.classList.remove('active');
      }

      loginScreen.classList.add('hidden');
      app.classList.remove('hidden');

      await loadDashboardData();
    } catch (err) {
      alert(`Login error: ${err.message}`);
    }
  });

  btnLogout.addEventListener('click', () => {
    api.clearToken();
    app.classList.add('hidden');
    loginScreen.classList.remove('hidden');
    if (leafletMap) {
      leafletMap.remove();
      leafletMap = null;
    }
  });

  // --- Navigation & Tabs ---
  navItems.forEach(btn => {
    btn.addEventListener('click', () => {
      navItems.forEach(n => n.classList.remove('active'));
      tabPanes.forEach(p => p.classList.add('hidden'));

      btn.classList.add('active');
      const targetTab = btn.getAttribute('data-tab');
      document.getElementById(targetTab).classList.remove('hidden');

      const viewSubtitle = document.getElementById('view-subtitle');
      if (targetTab === 'map-view') {
        viewTitle.textContent = 'The network is moving.';
        if (viewSubtitle) viewSubtitle.textContent = 'Live recycler and pickup activity across the operating cluster.';
        setTimeout(initOrUpdateMap, 200);
      } else {
        viewTitle.textContent = 'Pickup network';
        if (viewSubtitle) viewSubtitle.textContent = 'Every request, offer, and confirmed pickup in one place.';
      }
    });
  });

  // --- Filters & Search ---
  statusFilterChips.forEach(chip => {
    chip.addEventListener('click', () => {
      statusFilterChips.forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      currentFilter = chip.getAttribute('data-filter');
      renderPickupsTable();
    });
  });

  if (pickupSearch) {
    pickupSearch.addEventListener('input', (e) => {
      searchQuery = e.target.value.toLowerCase().trim();
      renderPickupsTable();
    });
  }

  // --- Drawer ---
  closeDrawer.addEventListener('click', () => {
    detailDrawer.classList.add('hidden');
    selectedPickupId = null;
    document.querySelectorAll('.data-table tr').forEach(r => r.classList.remove('selected'));
  });

  // --- Refresh & Demo Reset ---
  btnRefresh.addEventListener('click', async () => {
    btnRefresh.textContent = '↻ Syncing...';
    await loadDashboardData();
    setTimeout(() => { btnRefresh.textContent = '↻ Refresh'; }, 400);
  });

  btnDemoReset.addEventListener('click', async () => {
    if (confirm('Reset demo state to default test pickups and recyclers?')) {
      await api.resetDemoData();
      selectedPickupId = null;
      detailDrawer.classList.add('hidden');
      await loadDashboardData();
      alert('Demo data has been reset successfully!');
    }
  });

  // --- Map Controls ---
  if (btnMapFit) {
    btnMapFit.addEventListener('click', () => {
      if (!leafletMap || mapMarkers.length === 0) return;
      const group = new L.featureGroup(mapMarkers);
      leafletMap.fitBounds(group.getBounds().pad(0.15));
    });
  }

  if (btnMapCenter) {
    btnMapCenter.addEventListener('click', () => {
      if (!leafletMap) return;
      leafletMap.setView([12.9716, 77.5946], 12);
    });
  }

  // --- Data Loading ---
  async function loadDashboardData() {
    try {
      pickups = await api.getPickups();
      recyclers = await api.getRecyclers();

      updateStats();
      renderPickupsTable();

      if (selectedPickupId) {
        showPickupDetail(selectedPickupId);
      }
      if (leafletMap) {
        updateMapMarkers();
      }
    } catch (err) {
      console.error('Error fetching dashboard data:', err);
    }
  }

  function updateStats() {
    const totalCount = pickups.length;
    const reqCount = pickups.filter(p => p.status === 'requested').length;
    const offCount = pickups.filter(p => p.status === 'offered').length;
    const confCount = pickups.filter(p => p.status === 'confirmed').length;
    const compCount = pickups.filter(p => p.status === 'completed').length;
    const declCount = pickups.filter(p => p.status === 'declined').length;

    statTotal.textContent = totalCount;
    statRequested.textContent = reqCount;
    statOffered.textContent = offCount;
    statConfirmed.textContent = confCount;
    statCompleted.textContent = compCount;

    if (countAll) countAll.textContent = totalCount;
    if (countRequested) countRequested.textContent = reqCount;
    if (countOffered) countOffered.textContent = offCount;
    if (countConfirmed) countConfirmed.textContent = confCount;
    if (countCompleted) countCompleted.textContent = compCount;
    if (countDeclined) countDeclined.textContent = declCount;
  }

  function renderPickupsTable() {
    tableBody.innerHTML = '';
    
    let filtered = currentFilter === 'all'
      ? pickups
      : pickups.filter(p => p.status === currentFilter);

    if (searchQuery) {
      filtered = filtered.filter(p => {
        const idMatch = p.pickup_id.toLowerCase().includes(searchQuery);
        const userMatch = (p.user_id || '').toLowerCase().includes(searchQuery);
        const recyclerMatch = (p.recycler_name || '').toLowerCase().includes(searchQuery);
        const itemMatch = (p.items || []).some(it => it.label.toLowerCase().includes(searchQuery));
        const statusMatch = p.status.toLowerCase().includes(searchQuery);
        return idMatch || userMatch || recyclerMatch || itemMatch || statusMatch;
      });
    }

    if (filtered.length === 0) {
      tableBody.innerHTML = `
        <tr>
          <td colspan="7" style="text-align:center; padding:3.5rem 1rem; color:var(--text-muted);">
            <div style="font-size:1.6rem; margin-bottom:0.5rem;">🔍</div>
            <p style="font-weight:700; color:var(--deep-green); margin-bottom:0.25rem;">No matching pickups found</p>
            <p style="font-size:0.8rem; color:var(--text-faint);">Try adjusting your filter or search query</p>
          </td>
        </tr>
      `;
      return;
    }

    filtered.forEach(p => {
      const tr = document.createElement('tr');
      if (p.pickup_id === selectedPickupId) tr.classList.add('selected');

      const itemsText = p.items.map(it => 
        `${it.label}${it.weight_kg ? ` (${it.weight_kg}kg)` : ''}`
      ).join(', ');

      const priceStr = p.estimated_value ? `₹${p.estimated_value.low} – ₹${p.estimated_value.high}` : '—';
      const slotFormatted = new Date(p.slot).toLocaleString('en-IN', {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
      });

      const pulseIndicator = (p.status === 'offered' || p.status === 'confirmed')
        ? '<span style="display:inline-block; width:6px; height:6px; border-radius:50%; background:currentColor; margin-right:4px; vertical-align:middle; animation:pulseDot 1.6s infinite;"></span>'
        : '';

      const userDisplay = p.user_id.includes('protected')
        ? `<span style="font-size:0.78rem; color:var(--text-faint); font-style:italic;">🔒 Protected</span>`
        : `<span style="font-family:var(--font-mono); font-size:0.8rem; color:var(--text-main); font-weight:600;">${p.user_id}</span>`;

      tr.innerHTML = `
        <td style="font-family:var(--font-mono); font-weight:700; font-size:0.82rem; color:var(--deep-green);">
          ${p.pickup_id}
        </td>
        <td>${userDisplay}</td>
        <td style="color:var(--text-main); font-size:0.84rem; font-weight:500;">${itemsText}</td>
        <td style="font-weight:700; color:var(--text-main); font-size:0.86rem;">${priceStr}</td>
        <td style="font-size:0.8rem; color:var(--text-muted); font-weight:500;">
          📅 ${slotFormatted}
        </td>
        <td>
          <span class="badge badge-${p.status}">${pulseIndicator}${p.status}</span>
        </td>
        <td>
          <span class="action-view-text">View Details →</span>
        </td>
      `;

      tr.addEventListener('click', () => {
        document.querySelectorAll('.data-table tr').forEach(r => r.classList.remove('selected'));
        tr.classList.add('selected');
        showPickupDetail(p.pickup_id);
      });

      tableBody.appendChild(tr);
    });
  }

  async function showPickupDetail(pickupId) {
    selectedPickupId = pickupId;
    const p = await api.getPickupDetail(pickupId);
    if (!p) return;

    detailDrawer.classList.remove('hidden');

    let stepNum = 1;
    if (p.status === 'offered') stepNum = 2;
    else if (p.status === 'confirmed') stepNum = 3;
    else if (p.status === 'completed') stepNum = 4;
    else if (p.status === 'declined') stepNum = 2;

    const stepperHtml = `
      <div class="drawer-stepper">
        <div class="stepper-step ${stepNum >= 1 ? 'completed' : ''} ${stepNum === 1 ? 'current' : ''}">
          <span class="stepper-circle">1</span>
          <span class="stepper-label">Requested</span>
        </div>
        <div class="stepper-step ${stepNum >= 2 ? 'completed' : ''} ${stepNum === 2 ? 'current' : ''}">
          <span class="stepper-circle">2</span>
          <span class="stepper-label">Offered</span>
        </div>
        <div class="stepper-step ${stepNum >= 3 ? 'completed' : ''} ${stepNum === 3 ? 'current' : ''}">
          <span class="stepper-circle">3</span>
          <span class="stepper-label">Confirmed</span>
        </div>
        <div class="stepper-step ${stepNum >= 4 ? 'completed' : ''} ${stepNum === 4 ? 'current' : ''}">
          <span class="stepper-circle">4</span>
          <span class="stepper-label">Completed</span>
        </div>
      </div>
    `;

    const itemsHtml = p.items.map(it => {
      const isHazard = it.label === 'battery' || it.label === 'ewaste';
      return `<li style="margin-bottom:0.35rem; display:flex; justify-content:space-between; align-items:center;">
        <span><strong>${it.label}</strong> ${isHazard ? '<span style="color:#B91C1C;font-size:0.75rem;">(Hazardous / Safe drop-off only)</span>' : ''}</span>
        <span style="font-family:var(--font-mono); font-size:0.8rem; font-weight:700;">${it.weight_kg || '1.0'} kg</span>
      </li>`;
    }).join('');

    const logHtml = (p.action_log || []).map(entry => `
      <li class="timeline-item">
        <span class="timeline-time">${entry.time}</span>
        <strong>${entry.event}</strong>: ${entry.detail}
      </li>
    `).join('');

    const estStr = p.estimated_value ? `₹${p.estimated_value.low} – ₹${p.estimated_value.high}` : 'N/A';

    const isAddressUnlocked = (p.status === 'confirmed' || p.status === 'completed');
    const privacyCardHtml = isAddressUnlocked
      ? `<div class="privacy-card unlocked">
          <div style="font-weight:700; margin-bottom:2px;">✓ Verified Recycler Access</div>
          <p style="margin:4px 0;">📍 ${p.address_text}</p>
          <small style="opacity:0.85;">Address released following explicit acceptance by recycler.</small>
        </div>`
      : `<div class="privacy-card locked">
          <div style="font-weight:700; margin-bottom:2px;">🔒 Household Privacy Shield Active</div>
          <p style="margin:4px 0;">📍 ${p.address_text}</p>
          <small style="opacity:0.85;">Household address is strictly withheld until partner accepts the dispatch offer.</small>
        </div>`;

    const ivrBannerHtml = p.status === 'offered'
      ? `<div class="ivr-call-banner" style="margin-top:0.75rem;">
          <div style="font-weight:700; display:flex; align-items:center; gap:6px;">
            <span style="animation:pulseDot 1.5s infinite;">📞</span>
            <span>Automated IVR Call in Progress</span>
          </div>
          <p style="margin:4px 0; font-size:0.78rem;">Voice provider calling recycler. Waiting for DTMF response (Key 1: Accept, Key 2: Decline).</p>
        </div>`
      : '';

    detailContent.innerHTML = `
      ${stepperHtml}

      <div class="detail-section">
        <h4>📋 Overview</h4>
        <div class="detail-box">
          <p><strong>Pickup ID:</strong> <span style="font-family:var(--font-mono); font-weight:700; color:var(--deep-green);">${p.pickup_id}</span></p>
          <p><strong>Status:</strong> <span class="badge badge-${p.status}">${p.status}</span></p>
          <p><strong>Customer:</strong> <span style="font-family:var(--font-mono); font-weight:600;">${p.user_id}</span></p>
          <p><strong>Slot:</strong> <span>${new Date(p.slot).toLocaleString('en-IN')}</span></p>
        </div>
      </div>

      <div class="detail-section">
        <h4>📦 Items & Valuation</h4>
        <div class="detail-box">
          <ul style="list-style:none; padding:0; margin-bottom:0.75rem;">${itemsHtml}</ul>
          <div style="border-top:1px solid var(--border-light); padding-top:0.5rem; display:flex; justify-content:space-between; align-items:center;">
            <span style="font-size:0.78rem; font-weight:700; color:var(--text-muted);">🏷️ ESTIMATE:</span>
            <span style="font-weight:700; color:var(--text-main); font-size:0.86rem;">${estStr}</span>
          </div>
        </div>
      </div>

      <div class="detail-section">
        <h4>🛡️ Address & Privacy Compliance</h4>
        ${privacyCardHtml}
      </div>

      <div class="detail-section">
        <h4>🚛 Recycler Dispatch</h4>
        <div class="detail-box">
          <p><strong>Assigned Partner:</strong> <span>${p.recycler_name || p.recycler_id || 'Awaiting partner match'}</span></p>
          ${ivrBannerHtml}
        </div>
      </div>

      <div class="detail-section">
        <h4>⚡ Bedrock Agent Action Log</h4>
        <ul class="timeline">${logHtml}</ul>
      </div>

      <div class="detail-section">
        <h4>⚙️ Manual Ops Transition</h4>
        <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
          <button class="btn btn-secondary btn-sm" onclick="window.overrideStatus('${p.pickup_id}', 'confirmed')">Force Confirm</button>
          <button class="btn btn-secondary btn-sm" onclick="window.overrideStatus('${p.pickup_id}', 'completed')">Mark Complete</button>
          <button class="btn btn-outline btn-sm" onclick="window.overrideStatus('${p.pickup_id}', 'cancelled')">Cancel</button>
        </div>
      </div>
    `;
  }

  window.overrideStatus = async (id, status) => {
    try {
      await api.updatePickupStatus(id, status);
      if (status === 'completed') {
        selectedPickupId = null;
        detailDrawer.classList.add('hidden');
        document.querySelectorAll('.data-table tr').forEach(r => r.classList.remove('selected'));
      }
      await loadDashboardData();
    } catch (err) {
      alert(`Could not update: ${err.message}`);
    }
  };

  // --- Map Integration (Leaflet / OpenStreetMap / Amazon Location) ---
  function initOrUpdateMap() {
    const canvas = document.getElementById('map-canvas');
    if (!leafletMap) {
      leafletMap = L.map('map-canvas').setView([12.9716, 77.5946], 12);

      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '© OpenStreetMap contributors | EcoLink Logistics'
      }).addTo(leafletMap);
    }
    updateMapMarkers();
  }

  function updateMapMarkers() {
    if (!leafletMap) return;

    mapMarkers.forEach(m => leafletMap.removeLayer(m));
    mapMarkers = [];

    // Recyclers (green/purple)
    recyclers.forEach(r => {
      const isDropoff = r.accepts && (r.accepts.includes('battery') || r.accepts.includes('ewaste'));
      const color = isDropoff ? '#8b5cf6' : '#10b981';

      const circle = L.circleMarker([r.lat, r.lng], {
        radius: 8,
        fillColor: color,
        color: '#ffffff',
        weight: 2,
        opacity: 1,
        fillOpacity: 0.85
      }).addTo(leafletMap);

      circle.bindPopup(`
        <div style="font-family:var(--font-sans);">
          <strong style="font-size:0.95rem; color:var(--deep-green);">${r.name}</strong><br/>
          <span style="font-size:0.75rem; color:var(--text-muted);">${isDropoff ? 'Authorised Drop-off Center' : 'Kabadiwala Partner'}</span><br/>
          <div style="margin:4px 0; font-size:0.78rem;"><strong>Accepts:</strong> ${r.accepts.join(', ')}</div>
          <div style="font-size:0.75rem;">Status: <strong style="color:#059669;">${r.available ? 'Available' : 'Busy'}</strong></div>
        </div>
      `);

      mapMarkers.push(circle);
    });

    // Pickups (blue)
    pickups.forEach(p => {
      if (!p.lat || !p.lng) return;
      const marker = L.circleMarker([p.lat, p.lng], {
        radius: 9,
        fillColor: '#3b82f6',
        color: '#ffffff',
        weight: 2,
        opacity: 1,
        fillOpacity: 0.9
      }).addTo(leafletMap);

      marker.bindPopup(`
        <div style="font-family:var(--font-sans);">
          <strong style="font-size:0.95rem; color:var(--deep-green);">Pickup ${p.pickup_id}</strong><br/>
          <div style="margin:3px 0;"><span class="badge badge-${p.status}">${p.status}</span></div>
          <div style="font-size:0.78rem; margin-bottom:6px;"><strong>Items:</strong> ${p.items.map(it => it.label).join(', ')}</div>
          <button class="btn btn-secondary btn-sm" style="width:100%; font-size:0.75rem; padding:3px 8px;" onclick="window.selectFromMap('${p.pickup_id}')">Open in Drawer →</button>
        </div>
      `);

      mapMarkers.push(marker);
    });
  }

  window.selectFromMap = (id) => {
    navItems[0].click();
    showPickupDetail(id);
  };
});

