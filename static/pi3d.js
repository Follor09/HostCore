/**
 * HostCore Dashboard - live 3D Raspberry Pi 5 (CSS 3D, no library)
 * The board turns slowly and can be dragged around. It shows the live values:
 * the cooler glows from blue to red with the CPU temperature, the fan spins with the
 * real fan level, the green activity LED blinks faster with CPU load and the Ethernet
 * LEDs flicker with network traffic. During a day replay it shows the replayed values.
 */
(function () {
  'use strict';

  const card = document.getElementById('pi3d-card');
  if (!card) return;
  const t = s => (window.I18N && window.I18N.t ? window.I18N.t(s) : s);
  const LOCALE = (window.I18N && window.I18N.locale) || 'de-DE';
  const S = 2.6;                         // px per mm
  const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const stage = document.getElementById('pi3d-stage');
  const scene = document.createElement('div');
  scene.className = 'pi3d-scene';
  stage.appendChild(scene);

  function div(cls, parent) {
    const d = document.createElement('div');
    d.className = cls;
    if (parent) parent.appendChild(d);
    return d;
  }

  // A box in board millimetres: x/y on the board, z above it, w x d footprint, h height
  function box(cls, x, y, z, w, d, h, parent = board) {
    const b = div(`pi3d-box ${cls}`, parent);
    b.style.cssText = `left:${x * S}px;top:${y * S}px;width:${w * S}px;height:${d * S}px;transform:translateZ(${z * S}px)`;
    const face = (name, fw, fh, transform) => {
      const f = div(`pi3d-face f-${name}`, b);
      f.style.cssText = `width:${fw}px;height:${fh}px;transform:${transform}`;
      return f;
    };
    const W = w * S, D = d * S, H = h * S;
    face('n', W, H, 'rotateX(90deg)');
    face('s', W, H, `translateY(${D}px) rotateX(90deg)`);
    face('w', H, D, 'rotateY(-90deg)');
    face('e', H, D, `translateX(${W}px) rotateY(-90deg)`);
    b.top = face('t', W, D, `translateZ(${H}px)`);
    return b;
  }

  // ---------------------------------------------------------------------------
  // The board (Pi 5 layout, 85 x 56 mm)
  // ---------------------------------------------------------------------------
  const board = box('pi3d-board', 0, 0, 0, 85, 56, 1.6, scene);
  const top = board.top;
  [[3.5, 3.5], [61.5, 3.5], [3.5, 52.5], [61.5, 52.5]].forEach(([x, y]) => {
    const h = div('pi3d-hole', top);
    h.style.cssText = `left:${(x - 1.4) * S}px;top:${(y - 1.4) * S}px;width:${2.8 * S}px;height:${2.8 * S}px`;
  });
  const heat = div('pi3d-heat', top);          // glow on the board under the cooler
  heat.style.cssText = `left:${6 * S}px;top:${6 * S}px;width:${46 * S}px;height:${44 * S}px`;
  const label = div('pi3d-silk', top);
  label.textContent = 'Raspberry Pi 5';
  label.setAttribute('data-no-i18n', '');
  label.style.cssText = `left:${44 * S}px;top:${43 * S}px`;

  box('pi3d-header', 7, 1, 1.6, 51, 5, 6);                       // GPIO 2 x 20
  box('pi3d-chip', 21, 20, 1.6, 15, 15, 1.2);                    // BCM2712 (under the cooler)
  box('pi3d-chip dark', 47, 19, 1.6, 9, 13, 1);                   // RAM
  box('pi3d-chip dark', 53, 37, 1.6, 8, 8, 1);                    // RP1
  box('pi3d-usb blue', 69, 2.5, 1.6, 17.5, 13.5, 16);              // USB 3
  box('pi3d-usb', 69, 20, 1.6, 17.5, 13.5, 16);                   // USB 2
  const eth = box('pi3d-eth', 66, 38.5, 1.6, 21, 16, 13.5);       // Ethernet
  const ethLink = div('pi3d-led eth-link', eth.querySelector('.f-e'));
  const ethAct = div('pi3d-led eth-act', eth.querySelector('.f-e'));
  box('pi3d-port', 6, 50.5, 1.6, 9, 7.5, 3.2);                    // USB-C
  box('pi3d-port', 21, 50.5, 1.6, 7.5, 7.5, 3.5);                 // micro HDMI 0
  box('pi3d-port', 34, 50.5, 1.6, 7.5, 7.5, 3.5);                 // micro HDMI 1
  box('pi3d-ffc', 0.5, 18, 1.6, 3, 19, 2.5);                      // PCIe connector
  box('pi3d-ffc', 45, 45.5, 1.6, 2.5, 7, 2.5);                    // camera / display
  box('pi3d-ffc', 49, 45.5, 1.6, 2.5, 7, 2.5);
  const ledPower = box('pi3d-led-box power', 1, 44, 1.6, 2, 2, 0.8);
  const ledAct = box('pi3d-led-box act', 1, 47.5, 1.6, 2, 2, 0.8);

  // Active Cooler: heatsink with fins and a fan on top
  const cooler = box('pi3d-cooler', 13, 11, 1.6 + 1.2, 33, 31, 6);
  const fan = div('pi3d-fan', cooler.top);
  const rotor = div('pi3d-rotor', fan);
  for (let i = 0; i < 7; i++) {
    const blade = div('pi3d-blade', rotor);
    blade.style.transform = `rotate(${i * (360 / 7)}deg)`;
  }
  div('pi3d-hub', fan);

  // ---------------------------------------------------------------------------
  // View: drag to turn, slow auto-turn after a few seconds
  // ---------------------------------------------------------------------------
  const view = { rx: 58, rz: -28, dragging: false, lastUser: 0, visible: true, raf: 0, last: 0 };

  function applyView() {
    scene.style.transform = `translate(-50%, -50%) rotateX(${view.rx}deg) rotateZ(${view.rz}deg) translate(${-42.5 * S}px, ${-28 * S}px)`;
  }

  function loop(now) {
    view.raf = 0;
    if (!view.visible || document.hidden || reduceMotion) return;
    const dt = Math.min(0.1, (now - (view.last || now)) / 1000);
    view.last = now;
    if (!view.dragging && Date.now() - view.lastUser > 4000) {
      view.rz += dt * 8;
      applyView();
    }
    view.raf = requestAnimationFrame(loop);
  }
  const start = () => { if (!view.raf) { view.last = 0; view.raf = requestAnimationFrame(loop); } };

  stage.addEventListener('pointerdown', e => {
    view.dragging = true;
    view.px = e.clientX;
    view.py = e.clientY;
    stage.setPointerCapture(e.pointerId);
    stage.classList.add('dragging');
  });
  stage.addEventListener('pointermove', e => {
    if (!view.dragging) return;
    view.rz += (e.clientX - view.px) * 0.5;
    view.rx = Math.max(0, Math.min(85, view.rx - (e.clientY - view.py) * 0.4));
    view.px = e.clientX;
    view.py = e.clientY;
    applyView();
  });
  const endDrag = () => { view.dragging = false; view.lastUser = Date.now(); stage.classList.remove('dragging'); };
  stage.addEventListener('pointerup', endDrag);
  stage.addEventListener('pointercancel', endDrag);
  stage.addEventListener('dblclick', () => { view.rx = 58; view.rz = -28; view.lastUser = Date.now(); applyView(); });
  stage.addEventListener('keydown', e => {
    const step = { ArrowLeft: [0, -10], ArrowRight: [0, 10], ArrowUp: [-5, 0], ArrowDown: [5, 0] }[e.key];
    if (!step) return;
    e.preventDefault();
    view.rx = Math.max(0, Math.min(85, view.rx + step[0]));
    view.rz += step[1];
    view.lastUser = Date.now();
    applyView();
  });

  new IntersectionObserver(entries => {
    view.visible = entries.some(e => e.isIntersecting);
    if (view.visible) start();
  }).observe(card);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) start(); });

  // Fit the board to the card width
  new ResizeObserver(() => {
    const scale = Math.max(0.6, Math.min(1.5, stage.clientWidth / 300));
    stage.style.setProperty('--pi3d-scale', scale.toFixed(3));
  }).observe(stage);

  // ---------------------------------------------------------------------------
  // Live values
  // ---------------------------------------------------------------------------
  const $ = id => document.getElementById(id);

  function heatColor(c) {
    // 35 °C blue → 55 °C green/yellow → 80 °C red
    const k = Math.max(0, Math.min(1, (c - 35) / 45));
    return `hsl(${Math.round(205 - 205 * k)}, 90%, 55%)`;
  }

  function update(sys) {
    card.classList.toggle('replay-shown', !!sys.replay);
    if (sys.temperature && sys.temperature.celsius != null) {
      const c = sys.temperature.celsius;
      const k = Math.max(0, Math.min(1, (c - 35) / 45));
      card.style.setProperty('--heat', heatColor(c));
      card.style.setProperty('--heat-strength', (0.25 + 0.75 * k).toFixed(2));
      $('pi3d-temp').textContent = `${Math.round(c)}°C`;
    }
    if (sys.fan) {
      cooler.classList.toggle('no-fan', !sys.fan.available);
      if (sys.fan.available) {
        const { level, max_level: max } = sys.fan;
        rotor.classList.toggle('spinning', level > 0);
        if (level > 0) rotor.style.setProperty('--spin', `${(1.6 - 1.4 * (level / (max || 4))).toFixed(2)}s`);
        $('pi3d-fan').textContent = sys.fan.rpm != null ? `${sys.fan.rpm.toLocaleString(LOCALE)} U/min` : `${t('Lüfter')} ${level}/${max}`;
      } else {
        $('pi3d-fan').textContent = t('Kein Lüfter');
      }
    }
    if (sys.cpu && sys.cpu.percent != null) {
      const p = sys.cpu.percent;
      ledAct.style.setProperty('--blink', `${Math.max(0.12, 1.6 - p / 70).toFixed(2)}s`);
      ledAct.classList.toggle('busy', p >= 3);
      $('pi3d-cpu').textContent = `CPU ${Math.round(p)}%`;
    }
    if (sys.network) {
      const rate = (sys.network.download_kbps || 0) + (sys.network.upload_kbps || 0);
      ethLink.classList.toggle('on', true);
      ethAct.classList.toggle('busy', rate > 2);
      ethAct.style.setProperty('--blink', `${Math.max(0.08, 0.9 - Math.log10(rate + 1) / 5).toFixed(2)}s`);
    }
    if (sys.power) {
      ledPower.classList.toggle('warn', !!sys.power.undervoltage_now);
      $('pi3d-watt').hidden = !sys.power.available;
      if (sys.power.available && sys.power.board_watt != null) $('pi3d-watt').textContent = `${sys.power.board_watt.toFixed(1)} W`;
    }
  }

  window.addEventListener('pi:system', e => update(e.detail));
  applyView();
  start();
})();
