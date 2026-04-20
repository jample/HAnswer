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

  function easingOf(name) {
    switch (name) {
      case 'easeInOutSine':
        return function (t) { return -(Math.cos(Math.PI * t) - 1) / 2; };
      case 'linear':
      default:
        return function (t) { return t; };
    }
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
      loop(options) {
        const cfg = Object.assign({
          durationMs: 2000,
          onFrame: null,
          easing: 'linear',
          yoyo: false,
          repeat: true,
        }, options || {});
        const ease = easingOf(cfg.easing);
        let handle = 0;
        let start = null;
        let cycle = 0;
        let stopped = false;

        function fail(error) {
          try {
            if (typeof H._handleRuntimeError === 'function') {
              H._handleRuntimeError(error);
            }
          } catch (_) {}
          stop();
        }

        function step(now) {
          if (stopped) return;
          if (start == null) start = now;
          const raw = cfg.durationMs <= 0 ? 1 : Math.min(1, (now - start) / cfg.durationMs);
          const base = ease(raw);
          const progress = cfg.yoyo && cycle % 2 === 1 ? 1 - base : base;
          try {
            if (typeof cfg.onFrame === 'function') cfg.onFrame(progress, now - start, cycle);
          } catch (error) {
            fail(error);
            return;
          }
          try {
            if (H._currentBoard && typeof H._currentBoard.update === 'function') {
              H._currentBoard.update();
            }
          } catch (error) {
            fail(error);
            return;
          }
          if (raw >= 1) {
            if (!cfg.repeat) {
              stopped = true;
              H._activeStops.delete(stop);
              return;
            }
            cycle += 1;
            start = now;
          }
          handle = requestAnimationFrame(step);
        }

        handle = requestAnimationFrame(step);

        function stop() {
          stopped = true;
          if (handle) cancelAnimationFrame(handle);
          H._activeStops.delete(stop);
        }

        H._activeStops.add(stop);
        return stop;
      },
      oscillate(options) {
        const cfg = Object.assign({
          from: 0,
          to: 1,
          durationMs: 2000,
          onValue: null,
          easing: 'easeInOutSine',
          yoyo: true,
          repeat: true,
        }, options || {});
        return H.anim.loop({
          durationMs: cfg.durationMs,
          easing: cfg.easing,
          yoyo: cfg.yoyo,
          repeat: cfg.repeat,
          onFrame: function (progress, elapsedMs, cycle) {
            const value = cfg.from + (cfg.to - cfg.from) * progress;
            try {
              if (typeof cfg.onValue === 'function') cfg.onValue(value, progress, elapsedMs, cycle);
            } catch (_) {}
          },
        });
      },
      animate(paramName, from, to, durationMs, onUpdate) {
        return H.anim.loop({
          durationMs: durationMs,
          easing: 'linear',
          repeat: false,
          onFrame: function (progress) {
            const value = from + (to - from) * progress;
            try { onUpdate && onUpdate(paramName, value); } catch (_) {}
          },
        });
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
    _activeStops: new Set(),
    _handleRuntimeError: null,
    _setBoard(b) {
      H._currentBoard = b;
      if (!b) {
        for (const stop of Array.from(H._activeStops)) {
          try { stop(); } catch (_) {}
        }
        H._activeStops.clear();
      }
    },
    _setRuntimeGuards(guards) {
      H._handleRuntimeError = guards && typeof guards.onError === 'function'
        ? guards.onError
        : null;
    },
  };

  window.H = H;
})();
