export function workspaceHash() {
  return decodeURIComponent(window.location.hash.slice(1));
}

export function focusWorkspaceTarget(targetId: string) {
  const target = document.getElementById(targetId);
  if (!target) return false;
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  target.focus({ preventScroll: true });
  return true;
}
