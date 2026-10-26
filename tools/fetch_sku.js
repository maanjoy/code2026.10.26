// 用真实 Chrome 打开 Apps Script 查询页，执行 SQL-like 查询并取回 JSON。
// 用法: node fetch_sku.js "<SQL>" [输出文件名]
const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer-core");

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const APP =
  "https://script.google.com/macros/s/AKfycbz7CmUgos3WY8Vmn6IuL40mJlsGhCvJZWxubWq0LZu3d3TdHIytPs1C5XNcJPZgle7J/exec";
const OUT_DIR = path.resolve(__dirname, "..", "data");

const sql = process.argv[2] || "SELECT * FROM product_list";
const outName = process.argv[3] || "product_list.json";

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    userDataDir: path.resolve(__dirname, "..", ".chrome-profile"),
  });

  try {
    const page = await browser.newPage();
    await page.goto(APP, { waitUntil: "networkidle2", timeout: 90000 });

    // 找到真正承载用户页面的最内层 iframe（有 #queryBox 的那个）
    let target = null;
    for (let i = 0; i < 60 && !target; i++) {
      for (const f of page.frames()) {
        try {
          const has = await f.evaluate(() => !!document.getElementById("queryBox"));
          if (has) { target = f; break; }
        } catch (_) {}
      }
      if (!target) await new Promise((r) => setTimeout(r, 1000));
    }
    if (!target) throw new Error("找不到 #queryBox，页面结构可能变了");

    await target.evaluate((q) => {
      document.getElementById("queryBox").value = q;
      window.run();
    }, sql);

    // 等结果：成功是 <pre>，失败是 <p class="error">
    let text = "";
    for (let i = 0; i < 90; i++) {
      text = await target.evaluate(() => document.getElementById("result").innerText || "");
      if (!/Running\.\.\./.test(text) && text.trim()) break;
      await new Promise((r) => setTimeout(r, 1000));
    }

    console.log("QUERY :", sql);
    console.log("RAW   :", text.slice(0, 4000));

    let parsed = null;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      console.log("!! 结果不是合法 JSON:", e.message);
    }

    fs.mkdirSync(OUT_DIR, { recursive: true });
    if (parsed) {
      fs.writeFileSync(path.join(OUT_DIR, outName), JSON.stringify(parsed, null, 2), "utf-8");
      const n = Array.isArray(parsed) ? parsed.length : Object.keys(parsed).length;
      console.log(`OK    : 已写入 data/${outName}，共 ${n} 条/键`);
    } else {
      fs.writeFileSync(path.join(OUT_DIR, outName + ".txt"), text, "utf-8");
      console.log(`OK    : 原始文本已写入 data/${outName}.txt`);
    }
  } finally {
    await browser.close();
  }
})();
