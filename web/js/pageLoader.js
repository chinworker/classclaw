// 页面模块偶发加载失败后，以新 URL 重试，避免继续复用失败的模块加载结果。
export function createPageLoader(moduleUrl, importModule = (url) => import(url)) {
  const original = new URL(moduleUrl);
  let current = original.href;
  let failed = false;
  let revision = 0;

  function freshUrl() {
    const url = new URL(original);
    url.searchParams.set("_module_retry", `${Date.now()}-${++revision}`);
    return url.href;
  }

  return async function load() {
    if (failed) current = freshUrl();
    try {
      const module = await importModule(current);
      failed = false;
      return module;
    } catch (error) {
      failed = true;
      // 仅对浏览器的模块网络加载错误自动重试；代码执行错误交给页面错误面板。
      if (!/importing a module script failed|failed to fetch dynamically imported module|error loading dynamically imported module|load failed/i.test(error.message)) {
        throw error;
      }
      current = freshUrl();
      const module = await importModule(current);
      failed = false;
      return module;
    }
  };
}
