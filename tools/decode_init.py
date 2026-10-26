import json, pathlib

SRC = pathlib.Path(r"E:/file/workBuddy/2026-10-26-16-49-26/aeris/data/query_page.html")
OUT = pathlib.Path(r"E:/file/workBuddy/2026-10-26-16-49-26/aeris/data")

src = SRC.read_text(encoding="utf-8", errors="replace")

# Google 用 JS 字符串字面量承载配置，内部引号写成 \x22。
# Python 的 json 不认 \x，统一换成 \u00XX 后即可用 raw_decode 直接吃这个字面量。
src = src.replace("\\x", "\\u00")

marker = "goog.script.init("
i = src.index(marker) + len(marker)
dec = json.JSONDecoder()
text, _end = dec.raw_decode(src, i)      # text = 内层 JSON 文本
cfg = json.loads(text)

(OUT / "user_html.html").write_text(cfg["userHtml"], encoding="utf-8")
(OUT / "init_config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

print("functionNames:", cfg["functionNames"])
print("sandboxHost  :", cfg["sandboxHost"])
print("actionPrefix :", cfg["actionPrefix"])
print("userHtml     :", len(cfg["userHtml"]), "chars -> data/user_html.html")
