// Optional browser smoke test for the dashboard (needs Node + Playwright).
//
//   node tests/browser_smoke.js path/to/.reposage/dashboard.html [screenshots-folder]
//
// Opens the page in headless Chromium, walks through the main features
// (files map, selection, symbols map, search, table) and fails if the page
// throws an error or gets too slow. Pass a folder to also save screenshots.
const path = require("path");
let chromium;
try { ({ chromium } = require("playwright")); }
catch { ({ chromium } = require(path.join(require("child_process").execSync("npm root -g").toString().trim(), "playwright"))); }

const [, , file, shots] = process.argv;
const BUDGET = { firstMapMs: 3000, symbolsLayoutMs: 8000, frameMs: 16 };

(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("console", m => m.type() === "error" && errors.push(m.text()));
  const snap = async name => shots && page.screenshot({ path: path.join(shots, name + ".png") });
  const ready = view => page.waitForFunction(v => VIEWS[v].laidOut && !document.getElementById("busy").classList.contains("on"), view, { timeout: 60000 });

  let t = Date.now();
  await page.goto("file://" + path.resolve(file));
  await ready("files");
  const firstMapMs = Date.now() - t;
  await snap("1-files");

  // Select the most connected file: the detail panel must fill in.
  await page.evaluate(() => {
    const v = VIEWS.files; let best = 0;
    v.nodes.forEach(n => { if (v.degree[n.i] > v.degree[best]) best = n.i; });
    select({ type: "file", i: best });
  });
  await page.waitForSelector(".d-name");
  await snap("2-file-selected");

  t = Date.now();
  await page.keyboard.press("2");
  await ready("syms");
  const symbolsLayoutMs = Date.now() - t;
  await snap("3-symbols");
  const frameMs = await page.evaluate(() => { const t0 = performance.now(); for (let i = 0; i < 20; i++) draw(); return (performance.now() - t0) / 20; });

  // Search for the first symbol's name and open it with Enter.
  const name = await page.evaluate(() => syms.length ? syms[0].name : files[0].name);
  await page.click("#q"); await page.keyboard.type(name);
  await page.waitForSelector(".result");
  await snap("4-search");
  await page.keyboard.press("Enter");
  await page.waitForSelector(".d-name");
  await snap("5-selected");

  await page.click("#m-table");
  await page.waitForSelector("table tbody tr");
  await snap("6-table");

  const result = { firstMapMs, symbolsLayoutMs, frameMs: +frameMs.toFixed(1), errors };
  console.log(JSON.stringify(result));
  await browser.close();
  const slow = Object.keys(BUDGET).filter(k => result[k] > BUDGET[k]);
  if (errors.length || slow.length) {
    console.error("FAILED", { errors, tooSlow: slow });
    process.exit(1);
  }
})().catch(e => { console.error(e); process.exit(1); });
