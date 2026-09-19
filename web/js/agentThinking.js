// Keep Gateway IDs and binary labels; this is a display filter, not a model capability table.
export function thinkingChoices(profile) {
  return ["off", "low", "medium", "high"].flatMap((id) => profile?.levels.filter((item) => item.id === id) || []);
}

export function thinkingSeconds(thinking, currentMs = Date.now()) {
  const elapsed = thinking.elapsedMs + (thinking.active ? Math.max(0, currentMs - thinking.startedAt) : 0);
  return Math.floor(elapsed / 1000);
}
