// 后端能力适配层。以下能力已由真实 API 提供。

import { api } from "./api.js";

/**
 * 班级彻底删除。
 * 目标契约：DELETE /api/v1/classes/{class_id}
 *   - 删除班级及其业务数据，释放该班主任账号的唯一班级名额
 *   - 成功后前端应清空班级缓存，重新请求 /auth/me 与 /classes，回到无班级欢迎页
 */
export async function deleteClass(classId) {
  return api(`/classes/${classId}`, { method: "DELETE" });
}

/**
 * 考试列表。
 * 目标契约：GET /api/v1/exams?class_id={class_id} → 考试数组（id/name/exam_date/status/subjects）
 */
export async function listExams(classId) {
  return api(`/exams?class_id=${encodeURIComponent(classId)}`);
}
