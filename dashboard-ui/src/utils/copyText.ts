/**
 * Put text on the clipboard, and say whether it worked.
 *
 * `navigator.clipboard` is only defined in a secure context. localhost counts,
 * so the usual `dashboard-ui.sh start` is fine — but the moment the UI is
 * served to another machine over plain http (`--host`, a tunnel, an IP in the
 * address bar) the whole API is simply absent. The old code reached for it
 * with `?.` and a swallowed rejection, so both of those failures did nothing
 * at all and looked exactly like success.
 *
 * So: try the real API, fall back to the legacy selection trick, and return a
 * boolean the caller is expected to show. Never fail silently.
 */

/**
 * Pre-`navigator.clipboard` copy: put the text in an offscreen textarea,
 * select it, and let the document copy its own selection. Deprecated, but it
 * is the only thing that works in a non-secure context, and it needs the same
 * user gesture the caller already has.
 */
function legacyCopy(text: string): boolean {
  const ta = document.createElement('textarea');
  ta.value = text;
  // Offscreen rather than hidden: `display:none` cannot be selected.
  ta.style.cssText = 'position:fixed;top:-1000px;left:-1000px;opacity:0';
  ta.setAttribute('readonly', '');
  document.body.appendChild(ta);
  try {
    ta.select();
    ta.setSelectionRange(0, text.length);
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    document.body.removeChild(ta);
  }
}

export async function copyText(text: string): Promise<boolean> {
  // Empty would *clear* the clipboard — silently destroying whatever the user
  // had. A caller with nothing to copy has a bug; refuse rather than obey.
  if (!text) return false;

  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Rejects when the document is not focused, or when the permission was
      // denied. Both are recoverable by the legacy path.
    }
  }
  return legacyCopy(text);
}
