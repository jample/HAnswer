#!/usr/bin/env node
/**
 * HAnswer viz code AST validator (§3.3.3).
 *
 * Reads LLM-emitted JSXGraph code from stdin. Parses with acorn, applies
 * the allow/forbidden-list policy from HAnswerR.md §3.3.3, and prints a
 * JSON report to stdout:
 *
 *   {"ok": true, "node_count": 42}                -- valid
 *   {"ok": false, "violations": [{kind, message}]} -- rejected
 *
 * The LLM code is expected to be a FUNCTION BODY; we wrap it in
 * `function(board, JXG, H, params){ ... }` before parsing so `return`
 * statements are legal.
 */

import * as acorn from 'acorn';
import * as walk from 'acorn-walk';

const ALLOWED_GLOBALS = new Set([
  'board', 'JXG', 'H', 'params',
  'Math', 'Number', 'Array', 'Object', 'Boolean', 'String', 'JSON',
  'console', 'requestAnimationFrame', 'cancelAnimationFrame',
  // locals legitimately created inside the function body
  'undefined', 'NaN', 'Infinity', 'arguments',
]);

const FORBIDDEN_NEW = new Set(['Function', 'WebSocket', 'Worker', 'XMLHttpRequest']);
const FORBIDDEN_COMPUTED_PROPS = new Set(['eval', 'Function', 'constructor']);

const MAX_NODES  = 2000;
const MAX_BYTES  = 32 * 1024;

function readStdin() {
  return new Promise((resolve) => {
    let buf = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (d) => (buf += d));
    process.stdin.on('end', () => resolve(buf));
  });
}

function collectLocalBindings(ast) {
  // Identifiers declared locally (var / let / const / function / parameter).
  const locals = new Set();
  walk.ancestor(ast, {
    VariableDeclarator(node) {
      if (node.id && node.id.type === 'Identifier') locals.add(node.id.name);
    },
    FunctionDeclaration(node) {
      if (node.id) locals.add(node.id.name);
      for (const p of node.params) if (p.type === 'Identifier') locals.add(p.name);
    },
    FunctionExpression(node) {
      for (const p of node.params) if (p.type === 'Identifier') locals.add(p.name);
    },
    ArrowFunctionExpression(node) {
      for (const p of node.params) if (p.type === 'Identifier') locals.add(p.name);
    },
    CatchClause(node) {
      if (node.param && node.param.type === 'Identifier') locals.add(node.param.name);
    },
  });
  return locals;
}

function main() {
  readStdin().then((code) => {
    const violations = [];
    const push = (kind, message) => violations.push({ kind, message });

    if (code.length > MAX_BYTES) {
      return finish([{ kind: 'size', message: `source ${code.length}B > ${MAX_BYTES}B` }]);
    }

    const wrapped = `(function(board, JXG, H, params){\n${code}\n});`;
    let ast;
    try {
      ast = acorn.parse(wrapped, { ecmaVersion: 2022, sourceType: 'script' });
    } catch (e) {
      return finish([{ kind: 'syntax', message: String(e.message || e) }]);
    }

    // Node-count DoS guard.
    let nodeCount = 0;
    walk.full(ast, () => { nodeCount++; });
    if (nodeCount > MAX_NODES) {
      return finish([{ kind: 'size', message: `${nodeCount} AST nodes > ${MAX_NODES}` }]);
    }

    const locals = collectLocalBindings(ast);

    walk.ancestor(ast, {
      Identifier(node, _state, ancestors) {
        const name = node.name;
        const parent = ancestors[ancestors.length - 2];
        if (!parent) return;
        // Skip property keys / member property names.
        if (parent.type === 'MemberExpression' && parent.property === node && !parent.computed) return;
        if (parent.type === 'Property' && parent.key === node && !parent.computed) return;
        if (parent.type === 'MethodDefinition' && parent.key === node && !parent.computed) return;
        // Skip declarations (we already collected them).
        if (parent.type === 'VariableDeclarator' && parent.id === node) return;
        if (
          (parent.type === 'FunctionDeclaration' ||
           parent.type === 'FunctionExpression' ||
           parent.type === 'ArrowFunctionExpression') &&
          (parent.id === node || parent.params.includes(node))
        ) return;
        if (parent.type === 'CatchClause' && parent.param === node) return;
        if (parent.type === 'LabeledStatement' && parent.label === node) return;

        if (locals.has(name)) return;
        if (!ALLOWED_GLOBALS.has(name)) {
          push('forbidden-global', `identifier '${name}' is not on the allow-list`);
        }
      },

      MemberExpression(node) {
        if (node.computed && node.property.type === 'Literal') {
          const val = node.property.value;
          if (typeof val === 'string' && FORBIDDEN_COMPUTED_PROPS.has(val)) {
            push('computed-access', `computed access to '${val}' is forbidden`);
          }
        }
      },

      NewExpression(node) {
        if (node.callee.type === 'Identifier' && FORBIDDEN_NEW.has(node.callee.name)) {
          push('forbidden-new', `new ${node.callee.name}(...) is forbidden`);
        }
      },

      CallExpression(node) {
        const { callee } = node;
        const name =
          callee.type === 'Identifier' ? callee.name :
          (callee.type === 'MemberExpression' && callee.property && !callee.computed)
            ? callee.property.name : null;
        if ((name === 'setTimeout' || name === 'setInterval') &&
            node.arguments.length > 0 && node.arguments[0].type === 'Literal' &&
            typeof node.arguments[0].value === 'string') {
          push('string-timer', `${name}(string, ...) is forbidden`);
        }
        if (callee.type === 'Identifier' &&
            (callee.name === 'eval' || callee.name === 'Function' || callee.name === 'require' || callee.name === 'importScripts')) {
          push('forbidden-call', `call to '${callee.name}' is forbidden`);
        }
      },

      ImportExpression() { push('import', 'dynamic import() is forbidden'); },
      ImportDeclaration() { push('import', 'import statements are forbidden'); },
      WithStatement()     { push('with', '`with` statements are forbidden'); },
    });

    finish(violations, nodeCount);
  });
}

function finish(violations, nodeCount) {
  if (violations.length === 0) {
    process.stdout.write(JSON.stringify({ ok: true, node_count: nodeCount }) + '\n');
  } else {
    process.stdout.write(JSON.stringify({ ok: false, violations }) + '\n');
  }
  process.exit(0);
}

main();
