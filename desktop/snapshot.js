(async function noteagentSnapshot() {
  const MAX_HTML_BYTES = 2000000;
  const MAX_ASSETS = 40;
  const MAX_ASSET_BYTES = 5000000;

  function blobToDataUrl(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(reader.error || new Error("read failed"));
      reader.readAsDataURL(blob);
    });
  }

  async function inlineSrc(src) {
    const value = String(src || "").trim();
    if (!value || value.startsWith("data:")) return value;
    const res = await fetch(value, { credentials: "include" });
    if (!res.ok) throw new Error(String(res.status));
    const blob = await res.blob();
    if (!blob.size || blob.size > MAX_ASSET_BYTES) {
      throw new Error("asset too large");
    }
    return blobToDataUrl(blob);
  }

  const images = Array.from(document.querySelectorAll("img"));
  let seen = 0;
  for (const img of images) {
    const src = img.currentSrc || img.getAttribute("src") || "";
    if (!src || src.startsWith("data:")) continue;
    seen += 1;
    if (seen > MAX_ASSETS) break;
    try {
      const dataUrl = await inlineSrc(src);
      if (dataUrl) {
        img.setAttribute("src", dataUrl);
        img.removeAttribute("srcset");
        img.removeAttribute("data-src");
      }
    } catch {
      /* leave the original src; Python may still try an anonymous fetch */
    }
  }

  const html = "<!DOCTYPE html>\n" + document.documentElement.outerHTML;
  const bytes = new TextEncoder().encode(html).length;
  if (bytes > MAX_HTML_BYTES) {
    throw new Error("html exceeds " + MAX_HTML_BYTES + " bytes");
  }
  return { url: location.href, html };
})();
