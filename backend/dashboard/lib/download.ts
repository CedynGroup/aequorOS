/**
 * Trigger a browser download of in-memory text (CSV templates, example
 * scripts). Client-only — guards against SSR.
 */
export function downloadTextFile(
  filename: string,
  content: string,
  mime = 'text/plain;charset=utf-8',
): void {
  if (typeof window === 'undefined') return;
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
