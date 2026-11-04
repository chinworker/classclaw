// 内存状态；只有登录令牌写入 sessionStorage。
// 业务数据一律不落地，刷新后从 API 重新获取。

import { api, getToken, setToken } from "./api.js";
import { agentChatStore } from "./agentChatStore.js";
import { compareStudents } from "./studentOrder.js";

const TOKEN_KEY = "classclaw.session.token";

export const state = {
  user: null,          // /auth/me 返回的用户对象
  classId: null,       // 当前班主任班级；管理员通常为 null
  classInfo: null,     // GET /classes/{id} 的缓存（内存）
  students: [],        // 当前班学生列表缓存（内存，供座位/课表/查询复用）
  subjects: [],        // 当前班科目列表（由课表自动归纳）
};

export function loadToken() { return sessionStorage.getItem(TOKEN_KEY) || null; }

export function saveSession(token) {
  // Cancel with the old credential before changing identity; never leak another
  // account's in-memory conversation list into the new login.
  if (getToken() !== (token || null)) agentChatStore.clear();
  setToken(token);
  if (token) sessionStorage.setItem(TOKEN_KEY, token); else sessionStorage.removeItem(TOKEN_KEY);
}


export async function refreshIdentity() {
  const me = await api("/auth/me");
  state.user = me;
  state.classId = me.class_id || null;
  state.classInfo = null;
  state.students = [];
  state.subjects = [];
  return me;
}

export async function refreshClassInfo() {
  if (!state.classId) { state.classInfo = null; return null; }
  state.classInfo = await api(`/classes/${state.classId}`);
  return state.classInfo;
}

export async function refreshStudents({ force = false } = {}) {
  if (!state.classId) { state.students = []; return []; }
  if (!force && state.students.length) return state.students;
  const data = await api(`/students?class_id=${state.classId}&page_size=100`);
  state.students = (data.items || []).sort(compareStudents);
  return state.students;
}

export async function refreshSubjects({ force = false } = {}) {
  if (!state.classId) { state.subjects = []; return []; }
  if (!force && state.subjects.length) return state.subjects;
  state.subjects = await api(`/classes/${state.classId}/subjects`);
  return state.subjects;
}

export function refreshOpenclaw({ force = false } = {}) {
  return api(`/openclaw/status${force ? "?refresh=true" : ""}`);
}

function clearClassContext() {
  state.classId = null;
  state.classInfo = null;
  state.students = [];
  state.subjects = [];
}

export function clearSession() {
  saveSession(null);
  state.user = null;
  clearClassContext();
}
