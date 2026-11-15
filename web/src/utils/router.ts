// 路由工具

export function navigate(path: string) {
  window.history.pushState({}, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

export function getCurrentPath(): string {
  return window.location.pathname;
}
