// Placeholder Clawback orb, styled after the Pact Core sphere (HCK/Pact/frontend/src/PactCoreSphere.tsx).
// PROVENANCE: geometry, vertex gradient, states and wave animation adapted from Pact.
// C's frontend/components/orb/ replaces this; keep the window.Orb.{set,state} API.
(function () {
  function mount(host, label, opts) {
    opts = opts || {};
  const VIS = {
    idle: { core: "#1d4ed8", halo: "#38bdf8", speed: 2.2, amp: 0.14, freq: 3.4, wire: 0.92, label: "idle" },
    listening: { core: "#22d3ee", halo: "#22d3ee", speed: 4.4, amp: 0.24, freq: 4.6, wire: 1, label: "listening" },
    speaking: { core: "#3b82f6", halo: "#2563eb", speed: 5.2, amp: 0.32, freq: 5.2, wire: 1, label: "speaking" },
    ask: { core: "#f59e0b", halo: "#fbbf24", speed: 2.2, amp: 0.14, freq: 3.4, wire: 1, label: "needs your OK" },
    done: { core: "#10b981", halo: "#34d399", speed: 2.2, amp: 0.14, freq: 3.4, wire: 1, label: "done" },
    stopped: { core: "#38bdf8", halo: "#7dd3fc", speed: 3, amp: 0.18, freq: 3.8, wire: 0.95, label: "stopped by your rules" },
  };
  let state = "idle";
  const api = { set(s) { if (VIS[s]) { state = s; if (label) label.textContent = VIS[s].label; host.style.setProperty("--halo", VIS[s].halo); } } };
  if (!host || !window.THREE) { if (host) host.innerHTML = '<span class="fallback"></span>'; return api; }
  let renderer;
  try { renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true }); } catch { host.innerHTML = '<span class="fallback"></span>'; return api; }
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2)); renderer.setClearColor(0, 0);
  host.appendChild(renderer.domElement);
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1000); camera.position.z = 4.8;
  const group = new THREE.Group(); scene.add(group);
  const R = 1.45, geo = new THREE.SphereGeometry(R, opts.detail || 44, opts.detail || 44), pos = geo.attributes.position, orig = new Float32Array(pos.array);
  const violet = new THREE.Color("#10b981"), magenta = new THREE.Color("#2563eb"), cyan = new THREE.Color("#38bdf8"), mix = new THREE.Color();
  const cols = new Float32Array(pos.count * 3);
  for (let i = 0; i < pos.count; i++) {
    const y = (orig[i * 3 + 1] / R + 1) / 2, x = (orig[i * 3] / R + 1) / 2;
    mix.lerpColors(violet, magenta, Math.min(1, Math.max(0, ((x + y) * 0.8 - 0.3)))).multiplyScalar(0.72);
    cols.set([mix.r, mix.g, mix.b], i * 3);
  }
  geo.setAttribute("color", new THREE.BufferAttribute(cols, 3));
  const add = THREE.AdditiveBlending;
  const wireMat = new THREE.MeshBasicMaterial({ vertexColors: true, wireframe: true, transparent: true, opacity: 0.8, blending: add, depthWrite: false });
  group.add(new THREE.Mesh(geo, wireMat));
  const coreMat = new THREE.MeshBasicMaterial({ color: VIS.idle.core, transparent: true, opacity: opts.solid ? 0.9 : 0.38, blending: add, depthWrite: false });
  group.add(new THREE.Mesh(new THREE.SphereGeometry(1.26, 32, 32), coreMat));
  const nebMat = new THREE.MeshBasicMaterial({ color: VIS.idle.halo, transparent: true, opacity: 0.055, blending: add, depthWrite: false, side: THREE.BackSide });
  group.add(new THREE.Mesh(new THREE.SphereGeometry(1.38, 24, 24), nebMat));
  const N = opts.particles ?? 300, pg = new THREE.BufferGeometry(), pp = new Float32Array(N * 3), pc = new Float32Array(N * 3);
  let seed = 0x5eedc0de; const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
  for (let i = 0; i < N; i++) {
    const r = 1.62 + rnd() * 0.52, t = rnd() * 6.2832, p = Math.acos(rnd() * 2 - 1), c = rnd() > 0.6 ? violet : rnd() > 0.3 ? magenta : cyan;
    pp.set([r * Math.sin(p) * Math.cos(t), r * Math.sin(p) * Math.sin(t), r * Math.cos(p)], i * 3); pc.set([c.r, c.g, c.b], i * 3);
  }
  pg.setAttribute("position", new THREE.BufferAttribute(pp, 3)); pg.setAttribute("color", new THREE.BufferAttribute(pc, 3));
  const pts = new THREE.Points(pg, new THREE.PointsMaterial({ size: 0.042, vertexColors: true, transparent: true, opacity: 0.88, blending: add, depthWrite: false }));
  group.add(pts);
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const resize = () => { const w = host.clientWidth || 300, h = host.clientHeight || 250; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); };
  new ResizeObserver(resize).observe(host); resize();
  let px = 0, py = 0;
  host.addEventListener("pointermove", (e) => { const r = host.getBoundingClientRect(); px = ((e.clientX - r.left) / r.width - 0.5) * 0.45; py = ((e.clientY - r.top) / r.height - 0.5) * 0.45; });
  host.addEventListener("pointerleave", () => { px = py = 0; });
  const clock = new THREE.Clock();
  (function frame() {
    const v = VIS[state], t = clock.getElapsedTime();
    coreMat.color.set(opts.solid ? "#1d6fd8" : v.core); nebMat.color.set(v.halo); wireMat.opacity = v.wire;
    if (!reduce) {
      const a = pos.array;
      for (let i = 0; i < pos.count; i++) {
        const o = i * 3, x = orig[o], y = orig[o + 1], z = orig[o + 2];
        const w = Math.sin(x * v.freq + t * v.speed) * Math.cos(y * v.freq + t * v.speed * 0.85) * Math.sin(z * v.freq + t * 1.25);
        const n = Math.hypot(x, y, z) || 1, d = 1 + w * v.amp;
        a[o] = (x / n) * R * d; a[o + 1] = (y / n) * R * d; a[o + 2] = (z / n) * R * d;
      }
      pos.needsUpdate = true;
      group.rotation.y = t * 0.22 + px; group.rotation.x = Math.sin(t * 0.16) * 0.18 + py; pts.rotation.y = -t * 0.14;
    }
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  })();
  return api;
  }
  window.Orb = mount(document.getElementById("orb"), document.getElementById("orb-state"));
})();
