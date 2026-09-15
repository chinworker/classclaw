// Resource ownership for admin pages and embedded panels. Chat requests use their own store.
import { api } from "./api.js";
import { openModal } from "./components.js";
import { el } from "./util.js";
import { setNavigationGuard } from "./router.js";

export function adminView() {
  const controller = new AbortController();
  const cleanups = new Set();
  const timers = new Set();
  const dirtyChecks = new Set();
  const view = {
    get active() { return !controller.signal.aborted; },
    signal: controller.signal,
    isDirty: () => [...dirtyChecks].some((check) => check()),
    trackDirty(check) { dirtyChecks.add(check); return () => dirtyChecks.delete(check); },
    async api(path, options = {}) {
      const result = await api(path, { ...options, signal: options.signal || controller.signal });
      if (!view.active) throw new DOMException("Page disposed", "AbortError");
      return result;
    },
    own(cleanup) { cleanups.add(cleanup); return cleanup; },
    overlay(dialog) { if (dialog) view.own(() => dialog.close()); return dialog; },
    delay(fn, ms) {
      const timer = window.setTimeout(() => { timers.delete(timer); if (view.active) fn(); }, ms);
      timers.add(timer); return timer;
    },
    cancelTimer(timer) { window.clearTimeout(timer); timers.delete(timer); },
    guard(isDirty) {
      const dirty = () => isDirty() || view.isDirty();
      let pending = null;
      const unload = (event) => { if (dirty()) { event.preventDefault(); event.returnValue = ""; } };
      window.addEventListener("beforeunload", unload);
      view.own(() => window.removeEventListener("beforeunload", unload));
      view.own(setNavigationGuard(() => !dirty() || (pending ||= confirmDiscard(view).finally(() => { pending = null; }))));
    },
    dispose() {
      if (!view.active) return;
      controller.abort();
      for (const timer of timers) window.clearTimeout(timer);
      timers.clear();
      for (const cleanup of cleanups) cleanup();
      cleanups.clear();
      dirtyChecks.clear();
    },
  };
  return view;
}

export function confirmDiscard(view) {
  return new Promise((resolve) => {
    const dialog = openModal({ title: "有尚未保存的修改", body: el("p", {}, "离开将放弃当前修改。"),
      onClose: () => resolve(false), actions: [{ label: "继续编辑" }, {
        label: "放弃并离开", kind: "primary", onClick: ({ close }) => { resolve(true); close(); },
      }],
    });
    view.overlay(dialog);
  });
}
