// 截图工具：node shot.js <url> <输出png> [宽] [高] [--full]
const puppeteer = require("puppeteer-core");
const path = require("path");

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const url = process.argv[2] || "http://localhost:5173/";
const out = path.resolve(process.argv[3] || "shot.png");
const width = parseInt(process.argv[4] || "1280", 10);
const height = parseInt(process.argv[5] || "900", 10);
const full = process.argv.includes("--full");

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--force-device-scale-factor=1"],
    userDataDir: path.resolve(__dirname, "..", ".chrome-shot"),
    defaultViewport: { width, height },
  });
  try {
    const page = await browser.newPage();
    await page.goto(url, { waitUntil: "networkidle2", timeout: 60000 });
    await new Promise((r) => setTimeout(r, 2500));
    await page.screenshot({ path: out, fullPage: full });
    const title = await page.title();
    const h = await page.evaluate(() => document.body.scrollHeight);
    console.log(`OK ${out}  title=${title}  bodyHeight=${h}`);
  } finally {
    await browser.close();
  }
})();
