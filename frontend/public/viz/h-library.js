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
      vectorField() {
        throw new Error('H.plot.vectorField: not yet implemented');
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
      springMass() { throw new Error('H.phys.springMass: not yet implemented'); },
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
