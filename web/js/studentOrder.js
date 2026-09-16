// Match the backend's natural student-number ordering without changing stored IDs.
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;

export function compareStudentNumbers(left, right) {
  const a = String(left ?? "").normalize("NFKC").trim().toLowerCase();
  const b = String(right ?? "").normalize("NFKC").trim().toLowerCase();
  if (!a || !b) return compare(!a, !b);
  const aParts = a.match(/[0-9]+|[^0-9]+/g);
  const bParts = b.match(/[0-9]+|[^0-9]+/g);
  for (let i = 0; i < Math.min(aParts.length, bParts.length); i += 1) {
    const x = aParts[i], y = bParts[i];
    const xDigit = /^[0-9]+$/.test(x), yDigit = /^[0-9]+$/.test(y);
    const order = xDigit !== yDigit ? compare(!xDigit, !yDigit) : compare(xDigit ? BigInt(x) : x, yDigit ? BigInt(y) : y);
    if (order) return order;
  }
  return compare(aParts.length, bParts.length) || compare(a.length, b.length) || compare(a, b) || compare(String(left), String(right));
}

export function compareStudents(a, b) {
  return compareStudentNumbers(a.student_no, b.student_no) || compare(a.class_id || "", b.class_id || "") || compare(a.id || "", b.id || "");
}
