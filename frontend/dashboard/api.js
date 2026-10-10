/**
 * EcoLink Operations Dashboard - API Client
 * Supports mock mode and real Cognito / API Gateway backend
 */
class ApiClient {
  constructor(options = {}) {
    this.baseUrl = options.baseUrl || 'https://api.ecolink.example.com';
    this.mockMode = options.mockMode !== undefined ? options.mockMode : true;
    this.token = null;
    this.user = null;
    this.userGroup = 'viewers'; // 'admins' | 'viewers'
    this.initMockData();
  }

  setToken(token, user = null, group = 'viewers') {
    this.token = token;
    this.user = user;
    this.userGroup = group;
  }

  clearToken() {
    this.token = null;
    this.user = null;
    this.userGroup = 'viewers';
  }

  initMockData() {
    this.mockPickups = [
      {
        pickup_id: 'pk_3f9a1c2b7d10',
        user_id: '+919876543210',
        items: [
          { label: 'paper', weight_kg: 4.5 },
          { label: 'cardboard', weight_kg: 2.0 },
          { label: 'plastic', weight_kg: 1.5 }
        ],
        estimated_value: { low: 110, high: 185, currency: 'INR' },
        address_text: 'Flat 4B, Lakeview Apartments, 12th Main, Indiranagar, Bengaluru 560038',
        lat: 12.9784,
        lng: 77.6408,
        slot: '2026-10-09T10:00:00+05:30',
        status: 'confirmed',
        recycler_id: 'R001',
        recycler_name: 'Sharma Kabadi Works',
        created_at: '2026-10-07T14:32:00+05:30',
        action_log: [
          { time: '14:32:00', event: 'agent_received_photo', detail: 'Classified 3 items (paper, cardboard, plastic)' },
          { time: '14:32:45', event: 'price_quoted', detail: 'Quoted estimated Rs 110-185' },
          { time: '14:33:15', event: 'pickup_confirmed_by_user', detail: 'User replied YES to slot 09 Oct 10:00' },
          { time: '14:33:20', event: 'voice_call_placed', detail: 'Outbound call to Sharma Kabadi Works (R001)' },
          { time: '14:33:38', event: 'voice_call_answered', detail: 'Recycler pressed 1 (ACCEPT)' },
          { time: '14:33:40', event: 'status_transition', detail: 'Status updated to CONFIRMED. Address shared with recycler.' }
        ]
      },
      {
        pickup_id: 'pk_8c1e4f9b2a33',
        user_id: '+919811223344',
        items: [
          { label: 'metal', weight_kg: 6.0 },
          { label: 'glass', weight_kg: 3.0 }
        ],
        estimated_value: { low: 180, high: 360, currency: 'INR' },
        address_text: 'No 15, 3rd Cross, Victoria Layout, Bengaluru 560047',
        lat: 12.9642,
        lng: 77.6150,
        slot: '2026-10-08T16:00:00+05:30',
        status: 'offered',
        recycler_id: 'R003',
        recycler_name: 'Metro Metal Mart',
        created_at: '2026-10-07T16:10:00+05:30',
        action_log: [
          { time: '16:10:00', event: 'agent_classified', detail: 'Items detected: metal (6kg), glass (3kg)' },
          { time: '16:11:05', event: 'user_confirmed', detail: 'Slot requested for 08 Oct 16:00' },
          { time: '16:11:12', event: 'voice_call_placed', detail: 'Calling Metro Metal Mart (R003)... awaiting DTMF keypad response' }
        ]
      },
      {
        pickup_id: 'pk_a72f05d9e112',
        user_id: '+919845012345',
        items: [
          { label: 'plastic', weight_kg: 5.0 }
        ],
        estimated_value: { low: 40, high: 90, currency: 'INR' },
        address_text: '102, Green Glen Layout, Bellandur, Bengaluru 560103',
        lat: 12.9280,
        lng: 77.6740,
        slot: '2026-10-08T09:00:00+05:30',
        status: 'requested',
        recycler_id: null,
        created_at: '2026-10-07T16:45:00+05:30',
        action_log: [
          { time: '16:45:00', event: 'agent_classified', detail: 'Items: plastic 5kg' },
          { time: '16:46:10', event: 'pickup_created', detail: 'Status set to requested. Queueing for recycler match.' }
        ]
      },
      {
        pickup_id: 'pk_41b89ef01a99',
        user_id: '+919877001122',
        items: [
          { label: 'paper', weight_kg: 12.0 }
        ],
        estimated_value: { low: 120, high: 168, currency: 'INR' },
        address_text: '45, 8th Main, Malleshwaram, Bengaluru 560003',
        lat: 12.9980,
        lng: 77.5710,
        slot: '2026-10-07T11:00:00+05:30',
        status: 'completed',
        recycler_id: 'R002',
        recycler_name: 'Gupta Scrap Traders',
        created_at: '2026-10-07T09:15:00+05:30',
        action_log: [
          { time: '09:15:00', event: 'created', detail: 'Paper 12kg booked' },
          { time: '09:16:00', event: 'voice_accepted', detail: 'Gupta Scrap Traders accepted via key 1' },
          { time: '11:45:00', event: 'weighed_and_paid', detail: 'Recycler marked complete. Paid Rs 150 to household.' }
        ]
      },
      {
        pickup_id: 'pk_99d123bc44ef',
        user_id: '+919822334455',
        items: [
          { label: 'cardboard', weight_kg: 3.0 }
        ],
        estimated_value: { low: 18, high: 27, currency: 'INR' },
        address_text: '78, 5th Block, Koramangala, Bengaluru 560095',
        lat: 12.9352,
        lng: 77.6245,
        slot: '2026-10-07T15:00:00+05:30',
        status: 'declined',
        recycler_id: 'R006',
        recycler_name: 'Khan Glass & Bottle',
        created_at: '2026-10-07T13:20:00+05:30',
        action_log: [
          { time: '13:20:00', event: 'created', detail: 'Cardboard pickup created' },
          { time: '13:21:00', event: 'voice_declined', detail: 'Recycler pressed 2 (DECLINE). Looking for next partner.' }
        ]
      }
    ];

    this.mockRecyclers = [
      { recycler_id: 'R001', name: 'Sharma Kabadi Works', lat: 12.9826, lng: 77.5986, accepts: ['paper', 'cardboard', 'plastic', 'metal', 'glass'], available: true, phone: '+919800000001', lang: 'hi' },
      { recycler_id: 'R002', name: 'Gupta Scrap Traders', lat: 12.9936, lng: 77.5846, accepts: ['paper', 'cardboard', 'plastic'], available: true, phone: '+919800000002', lang: 'hi' },
      { recycler_id: 'R003', name: 'Metro Metal Mart', lat: 12.9536, lng: 77.6096, accepts: ['metal'], available: true, phone: '+919800000003', lang: 'en' },
      { recycler_id: 'R004', name: 'Green Loop Recyclers', lat: 13.0016, lng: 77.6146, accepts: ['paper', 'cardboard', 'plastic', 'glass', 'metal'], available: true, phone: '+919800000004', lang: 'en' },
      { recycler_id: 'R005', name: 'City E-Waste Hub (authorised)', lat: 12.9466, lng: 77.5826, accepts: ['ewaste', 'battery'], available: true, phone: '+919800000005', lang: 'en' },
      { recycler_id: 'R006', name: 'Khan Glass & Bottle', lat: 12.9796, lng: 77.5726, accepts: ['glass', 'plastic'], available: true, phone: '+919800000006', lang: 'hi' },
      { recycler_id: 'R007', name: 'Eco Drop Point (authorised)', lat: 13.0126, lng: 77.5646, accepts: ['ewaste', 'battery'], available: true, phone: '+919800000007', lang: 'en' }
    ];
  }

  async login(email, password) {
    if (this.mockMode) {
      await new Promise(r => setTimeout(r, 400));
      const group = email.includes('admin') ? 'admins' : 'viewers';
      this.setToken('mock-jwt-token-xyz', { email, name: email.split('@')[0] }, group);
      return { token: this.token, user: this.user, group: this.userGroup };
    }
    // Real AWS Cognito Auth integration placeholder (Cognito SRP/USER_PASSWORD_AUTH)
    const resp = await fetch(`${this.baseUrl}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    if (!resp.ok) throw new Error('Cognito authentication failed');
    const data = await resp.json();
    this.setToken(data.id_token, data.user, data.group || 'viewers');
    return data;
  }

  async getPickups(params = {}) {
    if (this.mockMode) {
      await new Promise(r => setTimeout(r, 200));
      let list = [...this.mockPickups];
      if (params.status && params.status !== 'all') {
        list = list.filter(p => p.status === params.status);
      }
      // Obey Address Rule for viewers group: hide address if not confirmed/completed
      return list.map(p => {
        const canViewAddress = this.userGroup === 'admins' || p.status === 'confirmed' || p.status === 'completed';
        return {
          ...p,
          address_text: canViewAddress ? p.address_text : '[Hidden until confirmed]',
          user_id: canViewAddress ? p.user_id : 'User (protected)'
        };
      });
    }

    const qs = new URLSearchParams(params).toString();
    const res = await fetch(`${this.baseUrl}/pickups?${qs}`, {
      headers: { Authorization: `Bearer ${this.token}` }
    });
    if (!res.ok) throw new Error(`Failed to fetch pickups: ${res.statusText}`);
    const data = await res.json();
    return data.pickups;
  }

  async getRecyclers() {
    if (this.mockMode) {
      await new Promise(r => setTimeout(r, 100));
      return [...this.mockRecyclers];
    }
    const res = await fetch(`${this.baseUrl}/recyclers/match?lat=12.9716&lng=77.5946&labels=paper,metal,plastic,ewaste`, {
      headers: { Authorization: `Bearer ${this.token}` }
    });
    if (!res.ok) throw new Error('Failed to load recyclers');
    return await res.json();
  }

  async getPickupDetail(id) {
    if (this.mockMode) {
      const p = this.mockPickups.find(x => x.pickup_id === id);
      if (!p) throw new Error('Pickup not found');
      const canViewAddress = this.userGroup === 'admins' || p.status === 'confirmed' || p.status === 'completed';
      return {
        ...p,
        address_text: canViewAddress ? p.address_text : '[Hidden until confirmed]',
        user_id: canViewAddress ? p.user_id : 'User (protected)'
      };
    }
    const res = await fetch(`${this.baseUrl}/pickups/${id}`, {
      headers: { Authorization: `Bearer ${this.token}` }
    });
    if (!res.ok) throw new Error('Failed to load pickup detail');
    return await res.json();
  }

  async updatePickupStatus(id, newStatus) {
    if (this.mockMode) {
      const p = this.mockPickups.find(x => x.pickup_id === id);
      if (!p) throw new Error('Not found');
      p.status = newStatus;
      p.action_log.push({
        time: new Date().toLocaleTimeString(),
        event: 'dashboard_override',
        detail: `Operator manually moved status to ${newStatus}`
      });
      return { pickup_id: id, status: newStatus };
    }
    const res = await fetch(`${this.baseUrl}/pickups/${id}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${this.token}`
      },
      body: JSON.stringify({ status: newStatus })
    });
    if (!res.ok) throw new Error('Failed to update pickup status');
    return await res.json();
  }

  async resetDemoData() {
    if (this.mockMode) {
      await new Promise(r => setTimeout(r, 300));
      this.initMockData();
      return { success: true, message: 'Demo data re-seeded to initial state' };
    }
    const res = await fetch(`${this.baseUrl}/demo/reset`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${this.token}` }
    });
    if (!res.ok) throw new Error('Failed to reset demo data');
    return await res.json();
  }
}

window.ApiClient = ApiClient;

