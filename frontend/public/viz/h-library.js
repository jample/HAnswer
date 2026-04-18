// HAnswer `H` helper library v1 (§3.3.5).
// Runs inside the viz sandbox. Keep in sync with VizCoderPrompt's H_CHEATSHEET.
// No network / storage access anywhere in here.

(function () {
  'use strict';
  if (typeof JXG === 'undefined') {
    console.warn('H: JSXGraph not loaded; H will be unavailable.');
    return;
  }

  function boardOf(x) {
    return x && x.board ? x.board : null;
  }

  const H = {
    shapes: {
      circle(cx, cy, r, attrs) {
        return this._board().create('circle', [[cx, cy], r], attrs || {});
      },
      triangle(A, B, C, attrs) {
        return this._board().create('polygon', [A, B, C], attrs || {});
      },
      polygon(points, attrs) {
        return this._board().create('polygon', points, attrs || {});
      },
      segmentWithLabel(P, Q, label, attrs) {
        const seg = this._board().create('segment', [P, Q], attrs || {});
        if (label) seg.setLabel(label);
        return seg;
      },
      _board() { return H._currentBoard; },
    },
    plot: {
      functionGraph(fn, domain, attrs) {
        return H._currentBoard.create('functiongraph',
          [fn, domain[0], domain[1]], attrs || {});
      },
      parametric(spec, tRange, attrs) {
        return H._currentBoard.create('curve',
          [spec.x, spec.y, tRange[0], tRange[1]], attrs || {});
      },
      vectorField(fn, grid, attrs) {
        const cfg = Object.assign({
          xMin: -4, xMax: 4, yMin: -4, yMax: 4, step: 1, scale: 0.35,
        }, grid || {});
        const arrows = [];
        for (let x = cfg.xMin; x <= cfg.xMax; x += cfg.step) {
          for (let y = cfg.yMin; y <= cfg.yMax; y += cfg.step) {
            const out = fn(x, y);
            const vx = Array.isArray(out) ? out[0] : out.x;
            const vy = Array.isArray(out) ? out[1] : out.y;
            arrows.push(H._currentBoard.create('arrow', [
              [x, y],
              [x + vx * cfg.scale, y + vy * cfg.scale],
            ], attrs || {}));
          }
        }
        return arrows;
      },
    },
    phys: {
      projectile(opts, attrs) {
        const { v0, angle, g } = opts;
        const rad = angle * Math.PI / 180;
        const vx = v0 * Math.cos(rad), vy = v0 * Math.sin(rad);
        const tMax = 2 * vy / g;
        return H._currentBoard.create('curve', [
          (t) => vx * t,
          (t) => vy * t - 0.5 * g * t * t,
          0, tMax,
        ], attrs || {});
      },
      springMass(opts, attrs) {
        const { k, m, x0 } = opts;
        const omega = Math.sqrt(Math.max(k, 0.0001) / Math.max(m, 0.0001));
        const amp = x0 == null ? 1 : x0;
        return H._currentBoard.create('curve', [
          (t) => t,
          (t) => amp * Math.cos(omega * t),
          0,
          Math.PI * 4,
        ], attrs || {});
      },
    },
    anim: {
      animate(paramName, from, to, durationMs, onUpdate) {
        const start = performance.now();
        let handle;
        function frame(now) {
          const t = Math.min(1, (now - start) / durationMs);
          const v = from + (to - from) * t;
          try { onUpdate && onUpdate(paramName, v); } catch (_) {}
          if (t < 1) handle = requestAnimationFrame(frame);
        }
        handle = requestAnimationFrame(frame);
        return () => cancelAnimationFrame(handle);
      },
    },
    geom: {
      midpoint(P, Q, attrs) {
        return H._currentBoard.create('midpoint', [P, Q], attrs || {});
      },
      reflect(P, line, attrs) {
        return H._currentBoard.create('reflection', [P, line], attrs || {});
      },
      rotate(P, center, angleDeg, attrs) {
        const rot = H._currentBoard.create('transform',
          [angleDeg * Math.PI / 180, center], { type: 'rotate' });
        const pt = H._currentBoard.create('point', [0, 0], attrs || {});
        pt.addTransform(P, rot);
        return pt;
      },
      intersectionPoint(a, b, attrs) {
        return H._currentBoard.create('intersection', [a, b, 0], attrs || {});
      },
    },
    // Set by sandbox.html before invoking user code.
    _currentBoard: null,
    _setBoard(b) { H._currentBoard = b; },
  };

  window.H = H;
})();
