// Fetching a bundle means asking the server for /data/<slug>.json. The ?city
// parameter and the data/index.json entries both deliver slugs, and the app
// controls neither input. A dot segment reaching the request would normalize
// away into a fetch of any same-origin path, so a legal slug has to look like a
// plain name. The name is non-empty and free of path separators, control
// characters and colons, and it differs from ".", ".." and "index", whose path
// lists every place. Colons go because drive-relative paths and
// alternate data stream names break files on the producing side, and Windows
// refuses them as filenames anyway. Characters below 0x20 go too, because
// browsers drop tab, LF and CR out of request paths silently, so a slug holding
// one would fetch something the exporter never wrote. The exporter also turns
// away what Windows cannot store, so no bundle file carries '<', '>', '"',
// '|', '?' or '*', or a stem that is a Windows device name before its first
// dot. This guard and its Python twin refuse the same set. Everything else
// works, Unicode included.
const WINDOWS_BANNED = /[<>"|?*]/;
const WINDOWS_DEVICE = /^(con|prn|aux|nul|conin\$|conout\$|(com|lpt)[0-9¹²³])$/i;

export function isValidSlug(slug) {
  return typeof slug === "string" && slug.length > 0
    && !slug.includes("/") && !slug.includes("\\")
    && !/[\x00-\x1f]/.test(slug)
    && !slug.includes(":")
    && !WINDOWS_BANNED.test(slug)
    && !WINDOWS_DEVICE.test(slug.split(".")[0])
    && slug !== "." && slug !== ".."
    && slug.toLowerCase() !== "index";
}

// Build the request path for a valid slug. A '#' or '%' in the name would
// otherwise reinterpret the URL as a fragment or a percent escape, so only
// those are encoded; percent goes first to avoid double-encoding escapes
// already in the name. The guard refuses '?', so no query can form.
// Everything else, Unicode included, stays raw so the built path names the
// exported file directly.
export function bundlePath(slug) {
  const encoded = slug
    .replaceAll("%", "%25")
    .replaceAll("#", "%23");
  return `/data/${encoded}.json`;
}
