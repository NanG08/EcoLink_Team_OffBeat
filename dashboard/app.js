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

  // Stats elements
  const statTotal = document.getElementById('stat-total');
  const statRequested = document.getElementById('stat-requested');
  const statOffered = document.getElementById('stat-offered');
  const statConfirmed = document.getElementById('stat-confirmed');
  const statCompleted = document.getElementById('stat-completed');

  // State
  let currentFilter = 'all';
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

      if (targetTab === 'map-view') {
        viewTitle.textContent = 'Live Dispatch & Recycler Network';
        setTimeout(initOrUpdateMap, 200);
      } else {
        viewTitle.textContent = 'Pickups & Household Requests';
      }
    });
  });

  // --- Filters ---
  statusFilterChips.forEach(chip => {
    chip.addEventListener('click', () => {
      statusFilterChips.forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      currentFilter = chip.getAttribute('data-filter');
      renderPickupsTable();
    });
  });

  // --- Drawer ---
  closeDrawer.addEventListener('click', () => {
    detailDrawer.classList.add('hidden');
    selectedPickupId = null;
    document.querySelectorAll('.data-table tr').forEach(r => r.classList.remove('selected'));
  });

  // --- Refresh & Demo Reset ---
  btnRefresh.addEventListener('click', loadDashboardData);

  btnDemoReset.addEventListener('click', async () => {
    if (confirm('Reset demo state to default test pickups and recyclers?')) {
      await api.resetDemoData();
      selectedPickupId = null;
      detailDrawer.classList.add('hidden');
      await loadDashboardData();
      alert('Demo data has been reset successfully!');
    }
  });

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
    statTotal.textContent = pickups.length;
    statRequested.textContent = pickups.filter(p => p.status === 'requested').length;
    statOffered.textContent = pickups.filter(p => p.status === 'offered').length;
    statConfirmed.textContent = pickups.filter(p => p.status === 'confirmed').length;
    statCompleted.textContent = pickups.filter(p => p.status === 'completed').length;
  }

  function renderPickupsTable() {
    tableBody.innerHTML = '';
    const filtered = currentFilter === 'all'
      ? pickups
      : pickups.filter(p => p.status === currentFilter);

    if (filtered.length === 0) {
      tableBody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:#64748b;padding:2rem;">No pickups matching filter "${currentFilter}"</td></tr>`;
      return;
    }

    filtered.forEach(p => {
      const tr = document.createElement('tr');
      if (p.pickup_id === selectedPickupId) tr.classList.add('selected');

      const itemsStr = p.items.map(it => `${it.label}${it.weight_kg ? ` (${it.weight_kg}kg)` : ''}`).join(', ');
      const priceStr = p.estimated_value ? `Rs ${p.estimated_value.low}-${p.estimated_value.high}` : '—';
      const slotFormatted = new Date(p.slot).toLocaleString('en-IN', {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
      });

      tr.innerHTML = `
        <td><strong>${p.pickup_id}</strong></td>
        <td>${itemsStr}</td>
        <td><span class="estimate-badge">${priceStr}</span></td>
        <td>${slotFormatted}</td>
        <td><span class="badge badge-${p.status}">${p.status}</span></td>
        <td><button class="btn btn-secondary btn-sm btn-view">View Details</button></td>
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

    const itemsHtml = p.items.map(it =>
      `<li><strong>${it.label}</strong>: ${it.weight_kg || '1.0'} kg</li>`
    ).join('');

    const logHtml = (p.action_log || []).map(entry => `
      <li class="timeline-item">
        <span class="timeline-time">${entry.time}</span>
        <strong>${entry.event}</strong>: ${entry.detail}
      </li>
    `).join('');

    const estStr = p.estimated_value ? `estimated Rs ${p.estimated_value.low}-${p.estimated_value.high}` : 'N/A';

    detailContent.innerHTML = `
      <div class="detail-section">
        <h4>Overview</h4>
        <div class="detail-box">
          <p><strong>Pickup ID:</strong> ${p.pickup_id}</p>
          <p><strong>Status:</strong> <span class="badge badge-${p.status}">${p.status}</span></p>
          <p><strong>Customer:</strong> ${p.user_id}</p>
          <p><strong>Slot:</strong> ${new Date(p.slot).toLocaleString('en-IN')}</p>
        </div>
      </div>

      <div class="detail-section">
        <h4>Items & Price Estimate</h4>
        <div class="detail-box">
          <ul style="padding-left:1.25rem;margin-bottom:0.5rem;">${itemsHtml}</ul>
          <p>🏷️ <strong>ESTIMATE:</strong> <span class="estimate-badge">${estStr}</span></p>
        </div>
      </div>

      <div class="detail-section">
        <h4>Household Address (Protected)</h4>
        <div class="detail-box">
          <p>📍 ${p.address_text}</p>
          ${p.status === 'confirmed' || p.status === 'completed'
            ? '<small style="color:#059669;">✓ Confirmed: Address shared with recycler.</small>'
            : '<small style="color:#d97706;">🔒 Privacy Rule: Hidden from recycler until acceptance.</small>'}
        </div>
      </div>

      <div class="detail-section">
        <h4>Assigned Recycler</h4>
        <div class="detail-box">
          <p><strong>Name:</strong> ${p.recycler_name || p.recycler_id || 'None matched yet'}</p>
        </div>
      </div>

      <div class="detail-section">
        <h4>Agent Action Log</h4>
        <ul class="timeline">${logHtml}</ul>
      </div>

      <div class="detail-section">
        <h4>Manual Ops Transition</h4>
        <div style="display:flex;gap:0.5rem;flex-wrap:wrap;">
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
      await loadDashboardData();
    } catch (err) {
      alert(`Could not update: ${err.message}`);
    }
  };

  // --- Map Integration (Leaflet / OpenStreetMap / Amazon Location) ---
  function initOrUpdateMap() {
    const canvas = document.getElementById('map-canvas');
    if (!leafletMap) {
      // Default center: Bengaluru central
      leafletMap = L.map('map-canvas').setView([12.9716, 77.5946], 12);

      // OpenStreetMap Tiles (standard web fallback for Amazon Location Service)
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '© OpenStreetMap contributors | EcoLink Logistics'
      }).addTo(leafletMap);
    }
    updateMapMarkers();
  }

  function updateMapMarkers() {
    if (!leafletMap) return;

    // Clear existing markers
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
        <strong>${r.name}</strong><br/>
        Type: ${isDropoff ? 'Authorised E-Waste Drop-off' : 'Kabadiwala Partner'}<br/>
        Accepts: ${r.accepts.join(', ')}<br/>
        Available: ${r.available ? 'Yes' : 'No'}
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
        <strong>Pickup ${p.pickup_id}</strong><br/>
        Status: <span class="badge badge-${p.status}">${p.status}</span><br/>
        Items: ${p.items.map(it => it.label).join(', ')}<br/>
        <button style="margin-top:6px;padding:3px 8px;font-size:11px;cursor:pointer;" onclick="window.selectFromMap('${p.pickup_id}')">View Details</button>
      `);

      mapMarkers.push(marker);
    });
  }

  window.selectFromMap = (id) => {
    // Switch to pickups tab and open detail
    navItems[0].click();
    showPickupDetail(id);
  };
});

