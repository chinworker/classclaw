// 认证：登录、恢复会话、退出、修改密码。

import { api, ApiError } from "./api.js";
import { state, saveSession, refreshIdentity, clearSession, loadToken } from "./state.js";

export async function login(username, password) {
  const data = await api("/auth/login", { method: "POST", body: { username, password } });
  saveSession(data.access_token);
  await refreshIdentity();
  return state.user;
}

export async function restoreSession() {
  const token = loadToken();
  if (!token) return null;
  saveSession(token);
  try {
    return await refreshIdentity();
  } catch (error) {
    clearSession();
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export async function logout() {
  try { await api("/auth/logout", { method: "POST", body: {} }); } catch { /* 即使失败也本地登出 */ }
  clearSession();
}

export async function changePassword(currentPassword, newPassword) {
  const data = await api("/auth/change-password", { method: "POST", body: { current_password: currentPassword, new_password: newPassword } });
  if (state.user) state.user.must_change_password = data?.must_change_password ?? false;
  return data;
}
