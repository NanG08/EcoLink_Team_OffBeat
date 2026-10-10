/**
 * EcoLink SaaS Experience Interactions
 * Smooth scroll-linked effects, interactive step pipeline,
 * and IVR phone call simulation.
 */

document.addEventListener('DOMContentLoaded', () => {
  // 1. Sticky Nav state
  const navBar = document.querySelector('.nav-bar');
  window.addEventListener('scroll', () => {
    if (window.scrollY > 40) {
      navBar?.classList.add('scrolled');
    } else {
      navBar?.classList.remove('scrolled');
    }
  }, { passive: true });

  // 2. Hero 3D Parallax & Scroll Movement
  const heroWrap = document.querySelector('.hero-composition-wrap');
  const panelWa = document.querySelector('.panel-whatsapp');
  const panelAi = document.querySelector('.panel-ai');
  const panelDispatch = document.querySelector('.panel-dispatch');

  let mouseX = 0;
  let mouseY = 0;
  let currentX = 0;
  let currentY = 0;

  window.addEventListener('mousemove', (e) => {
    const { innerWidth, innerHeight } = window;
    mouseX = (e.clientX - innerWidth / 2) / (innerWidth / 2);
    mouseY = (e.clientY - innerHeight / 2) / (innerHeight / 2);
  }, { passive: true });

  function renderHeroParallax() {
    currentX += (mouseX - currentX) * 0.05;
    currentY += (mouseY - currentY) * 0.05;

    const scrollY = window.scrollY;

    if (window.innerWidth > 1024 && heroWrap) {
      heroWrap.style.transform = `rotateY(${currentX * 4}deg) rotateX(${-currentY * 4}deg)`;

      // Subtle scroll translation: WA moves left, AI pops, Dispatch slides in
      if (panelWa && panelAi && panelDispatch) {
        const factor = Math.min(scrollY * 0.15, 60);
        panelWa.style.transform = `translate3d(${-factor * 1.2}px, 0, 40px) rotateY(-4deg)`;
        panelAi.style.transform = `translate3d(${-factor * 0.4}px, ${-factor * 0.3}px, -20px) rotateY(-8deg)`;
        panelDispatch.style.transform = `translate3d(${factor * 0.8}px, 0, -10px) rotateY(6deg)`;
      }
    }

    requestAnimationFrame(renderHeroParallax);
  }
  renderHeroParallax();

  // 3. Flow Path Progress on Scroll
  const flowSection = document.querySelector('.section-flow');
  const flowProgress = document.getElementById('flow-progress-bar');
  const flowNodes = document.querySelectorAll('.flow-step-node');

  function checkFlowProgress() {
    if (!flowSection || !flowProgress) return;
    const rect = flowSection.getBoundingClientRect();
    const windowH = window.innerHeight;

    if (rect.top < windowH * 0.6 && rect.bottom > 0) {
      const totalH = rect.height;
      const visible = (windowH * 0.6 - rect.top);
      const ratio = Math.min(Math.max(visible / (totalH * 0.7), 0), 1);

      flowProgress.style.width = `${ratio * 90}%`;

      flowNodes.forEach((node, index) => {
        const threshold = (index) / (flowNodes.length - 1);
        if (ratio >= threshold * 0.8) {
          node.classList.add('active');
        } else {
          node.classList.remove('active');
        }
      });
    }
  }
  window.addEventListener('scroll', checkFlowProgress, { passive: true });
  checkFlowProgress();

  // 4. Interactive Phone Call Simulation (Keypad DTMF "1" animation)
  const keyAccept = document.getElementById('btn-key-accept');
  const callStatus = document.getElementById('call-status-label');
  const callBanner = document.getElementById('call-result-banner');

  if (keyAccept && callStatus && callBanner) {
    let callState = 'ringing';

    // Auto trigger cycle every 6s if not clicked
    setInterval(() => {
      if (callState === 'ringing') {
        keyAccept.classList.add('active-accept');
        callStatus.textContent = 'Accepted by Partner';
        callBanner.textContent = 'Recycler confirmed · Address securely dispatched';
        callBanner.style.background = 'rgba(16, 185, 129, 0.25)';
        callState = 'accepted';
      } else {
        keyAccept.classList.remove('active-accept');
        callStatus.textContent = 'Inbound IVR Dispatch';
        callBanner.textContent = 'Awaiting Recycler Keypad (1: Accept, 2: Decline)';
        callBanner.style.background = 'rgba(255, 255, 255, 0.08)';
        callState = 'ringing';
      }
    }, 4500);

    keyAccept.addEventListener('click', () => {
      keyAccept.classList.add('active-accept');
      callStatus.textContent = 'Accepted by Partner';
      callBanner.textContent = 'Recycler confirmed · Address securely dispatched';
      callBanner.style.background = 'rgba(16, 185, 129, 0.25)';
      callState = 'accepted';
    });
  }

  // 5. Landing -> Ops Console Smooth Transition (450ms)
  const opsLinks = document.querySelectorAll('.ops-transition-link');
  const transitionLayer = document.getElementById('landing-ops-transition');

  opsLinks.forEach(link => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const targetHref = link.getAttribute('href');
      if (transitionLayer) {
        transitionLayer.classList.add('active');
        setTimeout(() => {
          window.location.href = targetHref;
        }, 450);
      } else {
        window.location.href = targetHref;
      }
    });
  });
});

