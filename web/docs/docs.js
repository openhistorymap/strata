// Theme toggle (shared choice with the viewer) and the contents highlight.

const btn = document.getElementById("theme");
const dark = matchMedia("(prefers-color-scheme: dark)");
const MODES = ["auto", "light", "dark"];
const LABEL = { auto: "◐ Auto", light: "☀ Light", dark: "☾ Dark" };

function apply() {
  const mode = localStorage.getItem("strata-theme") || "auto";
  document.documentElement.dataset.theme = mode === "auto" ? (dark.matches ? "dark" : "light") : mode;
  btn.textContent = LABEL[mode];
  btn.title = `Theme: ${mode}. Click to change.`;
}
btn.addEventListener("click", () => {
  const mode = localStorage.getItem("strata-theme") || "auto";
  localStorage.setItem("strata-theme", MODES[(MODES.indexOf(mode) + 1) % MODES.length]);
  apply();
});
dark.addEventListener("change", apply);
apply();

const links = new Map([...document.querySelectorAll(".toc a")].map((a) => [a.getAttribute("href").slice(1), a]));
const seen = new Set();
const observer = new IntersectionObserver((entries) => {
  for (const e of entries) (e.isIntersecting ? seen.add(e.target.id) : seen.delete(e.target.id));
  const first = [...links.keys()].find((id) => seen.has(id));
  if (first) for (const [id, a] of links) a.setAttribute("aria-current", String(id === first));
}, { rootMargin: "-70px 0px -60% 0px" });
for (const id of links.keys()) {
  const el = document.getElementById(id);
  if (el) observer.observe(el);
}
