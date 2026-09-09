// Small DOM test double for interaction tests; no browser/dependency downloads.
// This exercises real page handlers, but does not claim browser layout coverage.
class TestNode extends EventTarget {
  constructor(tag, text = "") {
    super();
    this.tagName = tag;
    this.nodeType = tag === "#text" ? 3 : 1;
    this._text = text;
    this.children = [];
    this.parentNode = null;
    this.className = "";
    this.attributes = {};
    this.style = {};
    this.dataset = {};
    this.value = "";
    this.scrollHeight = 100;
    this.classList = {
      contains: (name) => this.className.split(/\s+/).includes(name),
      toggle: (name, force) => {
        const classes = new Set(this.className.split(/\s+/).filter(Boolean));
        const include = force ?? !classes.has(name);
        if (include) classes.add(name); else classes.delete(name);
        this.className = [...classes].join(" ");
        return include;
      },
      add: (name) => this.classList.toggle(name, true),
      remove: (name) => this.classList.toggle(name, false),
    };
  }
  get textContent() { return this.nodeType === 3 ? this._text : this.children.map((child) => child.textContent).join(""); }
  set textContent(value) { this.replaceChildren(new TestNode("#text", String(value))); }
  get isConnected() { return this === globalThis.document.body || Boolean(this.parentNode?.isConnected); }
  get parentElement() { return this.parentNode; }
  append(...children) {
    for (let child of children) {
      if (!(child instanceof TestNode)) child = new TestNode("#text", String(child));
      child.remove();
      child.parentNode = this;
      this.children.push(child);
    }
  }
  replaceChildren(...children) {
    for (const child of this.children) child.parentNode = null;
    this.children = [];
    this.append(...children);
  }
  remove() {
    if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    this.parentNode = null;
  }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  matches(selector) { return selector.startsWith(".") ? this.classList.contains(selector.slice(1)) : this.tagName === selector; }
  querySelectorAll(selector) {
    return this.children.flatMap((child) => [
      ...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector),
    ]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  focus() { if (this.isConnected) document.activeElement = this; }
  click() { if (!this.disabled) this.dispatchEvent(new Event("click")); }
}

export function installDom() {
  globalThis.Node = TestNode;
  globalThis.document = {
    body: new TestNode("body"), activeElement: null,
    createElement: (tag) => new TestNode(tag), createTextNode: (text) => new TestNode("#text", text),
  };
  const values = new Map();
  globalThis.sessionStorage = { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
  globalThis.window = Object.assign(new EventTarget(), { setTimeout, clearTimeout });
  globalThis.requestAnimationFrame = (callback) => { callback(); return 1; };
  Object.defineProperty(globalThis, "navigator", { value: {}, configurable: true });
  return document;
}

export const tick = () => new Promise((resolve) => setImmediate(resolve));
export const response = (data, status = 200) => new Response(JSON.stringify({ success: status < 400, data }), { status });
