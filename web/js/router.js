// Hash 路由器：#/path?query。支持角色守卫与“需要班级”守卫。

import { saveRoute } from "./state.js";

let routes = [];
let current = { path: "", query: {}, params: {} };
let hooks = { resolve: null };

export function defineRoutes(defs) { routes = defs; }

export function setRouteResolver(fn) { hooks.resolve = fn; }

export function parseHash(hash = location.hash) {
  const raw = hash.replace(/^#/, "") || "/";
  const [pathPart, queryPart] = raw.split("?");
  const query = {};
  if (queryPart) for (const [key, value] of new URLSearchParams(queryPart)) query[key] = value;
  return { path: pathPart, query };
}

export function navigate(path, query = null) {
  const qs = query && Object.keys(query).length ? `?${new URLSearchParams(query)}` : "";
  const target = `#${path}${qs}`;
  if (location.hash === target) dispatch();
  else location.hash = target;
}

export function currentRoute() { return current; }

function match(path) {
  for (const route of routes) {
    if (route.path.includes(":")) {
      const names = [];
      const pattern = new RegExp(`^${route.path.replace(/:[^/]+/g, (m) => { names.push(m.slice(1)); return "([^/]+)"; })}$`);
      const found = path.match(pattern);
      if (found) {
        const params = {};
        names.forEach((name, index) => { params[name] = decodeURIComponent(found[index + 1]); });
        return { route, params };
      }
    } else if (route.path === path) {
      return { route, params: {} };
    }
  }
  return null;
}

export async function dispatch() {
  const { path, query } = parseHash();
  const found = match(path);
  if (!found) { navigate("/dashboard"); return; }
  current = { path, query, params: found.params };
  saveRoute(location.hash);
  if (hooks.resolve) await hooks.resolve(found.route, current);
}

export function startRouter() {
  window.addEventListener("hashchange", () => { dispatch(); });
}
