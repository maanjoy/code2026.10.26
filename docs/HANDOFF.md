# IT Coding Assessment — 侦察结果交接给 CodeBuddy

> 本文件由 WorkBuddy 产出。**代码一律交给 CodeBuddy 写**，这里只有已验证的事实、可直接用的数据文件、以及踩过的坑。
> 所有结论都实测过，不是推测。
>
> ⚠️ **本文件随仓库提交**：凭证、账号号、partner 地址一律写成占位符，真实值只存在本机 `.env`（已被 `.gitignore` 排除）。

---

## 0. 一句话结论

题目里那句「Australia Post API 目前有权限问题」**已经过时**——HTML v2 给的凭证现在完全可用，运费和轨迹都能拿到真实数据。
所以运费估算和轨迹展示**都应该做出来**，不要走 `A$0.00` 的降级路线；降级逻辑仍然要写，但只在 API 真的挂掉时才触发。

---

## 1. 已产出的数据文件

全部在 `aeris/data/` 下，可直接被应用读取：

| 文件 | 内容 |
|---|---|
| `orders.json` | 两笔订单 + SKU 行项目 + 运单映射 + 起运地。**这是唯一输入源** |
| `sku_catalog.json` | SKU 全表 **698 条**，按 SKU 建索引，数值字段已解析 |
| `sku_order.json` | 本单涉及的 **9 条** SKU 明细 |
| `expected_totals.json` | 参考校验值（**仅供测试比对，禁止写进业务逻辑**） |
| `product_list_full.json` / `product_list.json` / `order_skus.json` | Apps Script 原始返回 |
| `auspost/*.json` | AusPost 各接口的真实响应样例（track / price） |
| `postman/ap.zip` | 官方 Postman 集合（端点清单的权威来源） |

字段单位已统一：`weight_g`(g)、`length_mm`/`width_mm`/`height_mm`(mm)、`volume_mm3`(mm³)、`volumetric_gross_weight_kg`(kg)、`rrp`(AUD，含 GST)。

---

## 2. SKU 数据源（Apps Script SQL-like 查询页）

**真实表名是 `product_list`，不是 `sku`。**
页面上方的 Examples 全部写 `FROM sku`，紧接着一行小字 `Note: table name is product_list`。这就是 FAQ 第 2 条警告的陷阱，照抄示例会查空。

- 全部列名（26 列）：`SKU, SPU, ProductName, Barcode, DosageType, ProductType, Size, Schedule, MaxStoragePerContainer, TGACategory, RRP, Status, Date, Volumetric_GrossWeight, length, width, height, volume, weight, RouteOfAdministration, PlantSpecies, Spectrum, StrainLineage, WarningThreshold, ETA, Description`
- 支持 `WHERE ... IN (...)` 和 `LIMIT n`。**不带 LIMIT 默认只返回 10 行**（`rowCount:10`），必须显式 `LIMIT 1000` 才能拿到全部 698 条。
- `?q=` / `?query=` / `?sql=` 这些 URL 参数**都不生效**，`doGet` 只会回 HTML。
- 没法用 curl 直接调：服务端函数走 `google.script.run` 的 postMessage RPC，外层再发 XHR，纯 HTTP 逆向不划算。
- **已写好的取数脚本：`tools/fetch_sku.js`**（Node + puppeteer-core 驱动本机 Chrome，已装好依赖）

```bash
node tools/fetch_sku.js "SELECT * FROM product_list LIMIT 1000" product_list_full.json
node tools/fetch_sku.js "SELECT * FROM product_list WHERE SKU IN ('TBAMET10','TBAMET28')" out.json
```

本项目按题目要求**把 JSON 落到本地文件**用即可，不必在运行时去查这个页面。

### 9 个 SKU 的关键数据（已实测）

| SKU | RRP(含GST) | 名称 | 重量 | 长/宽/高 (mm) |
|---|---|---|---|---|
| TBAMET10 | 99.00 | Amethyst THC 25% 10G | 56.0g | 67/67/104 |
| TBAMET28 | 199.00 | Amethyst THC 25% 28G | 93.0g | 86/86/113 |
| TBOPAL28 | 199.00 | Opal THC 23% 28G | 93.0g | 86/86/113 |
| AURPUR10 | 99.00 | Purple Raine THC18% 15g | 68.0g | 76/76/78 |
| HARNIG | 149.00 | Harbour LR Night Cartridge | 25.0g | 30/60/120 |
| LELCBD100 | 140.00 | CBD 100 Full Spectrum Organic | 89.0g | 45/34/104 |
| HALGEO15 | 110.00 | The Geoffrey CBG 12% 15g | 68.0g | 76/76/78 |
| MCMW10 | 129.00 | Maali Wind THC 25% 10g | 59.0g | 76/76/79 |
| MCBO30 | 99.00 | BOB 30 (Alien OG) THC 30% 10g | 59.0g | 76/76/79 |

---

## 3. AusPost / StarTrack testbed（实测全部可用）

Base：`<AUSPOST_BASE_URL>`（testbed 地址写在 `backend/app/config.py` 的默认值里）

**认证**：HTTP Basic，username = API Key，password = Password。
**额外必需 header**：`account-number: <你的账号号>`（少了会 400：`missing the mandatory header element with the name account-number`）。
`Content-Type: application/json`。

### 3.1 轨迹（必做项）

```
GET /shipping/v1/track?tracking_ids=2FWZ50008569
```

三个运单号**全部 200 返回真实事件流**（`2025-02` 的历史数据，状态 Delivered）：

- `2FWZ50008569` (Track 1) ✅
- `2FWZ50008645` (Track 2) ✅
- `305506914` (Track 3，题目标为 TNT) ✅ —— **在 AusPost 侧也能查到**，`product_type: Parcel Post`
- 附加测试号 `RBXZ50016112` ✅

响应结构：`tracking_results[].consignment{status, events[]}` 与 `tracking_results[].trackable_items[].{article_id, product_type, status, events[{location, description, date}]}`。
**注意两种结构都可能出现在不同单号上，解析时要都兼容。**

### 3.2 运费估算（bonus，建议做）

```
POST /shipping/v1/prices/shipments
```

请求体（这就是官方集合里的样例，`movement_type` 可省略，会自动填 `DESPATCH`）：

```json
{"shipments":[{"from":{"suburb":"GLADESVILLE","state":"NSW","postcode":"2111"},
               "to":{"suburb":"SOUTH YARRA","state":"VIC","postcode":"3141"},
               "items":[{"length":"76","height":"78","width":"76","weight":"0.5"}]}]}
```

实测响应 `shipment_summary`：`{"total_cost":71.04,"total_cost_ex_gst":64.58,"shipping_cost":54.04,"fuel_surcharge":10.54,"total_gst":6.46}`。

**坑：suburb 必须和 postcode 对得上，否则 400。** 用 `GET /shipping/v1/address?suburb=&state=&postcode=` 先校验（返回 `found: true/false` + 候选 suburb 列表）。只传 postcode 不传 suburb 也会 400。

**⚠️ 起运地有个真实矛盾**：题目写 `2111 (Ryde, NSW)`，但 AusPost testbed 里 `RYDE` 只配 `2112`：
- `suburb=RYDE & postcode=2111` → 400（address 查询 `found:false`，候选是 GLADESVILLE / HENLEY / HUNTLEYS POINT …）
- `suburb=RYDE & postcode=2112` → 200
- `suburb=GLADESVILLE & postcode=2111` → 200

**建议**：配置里保留题目给的 `2111` 作为 origin postcode，suburb 用 address 接口解析（或配一个已验证可用的值），并在 README 里明确记录这个不一致。这是个能加分的发现。

### 3.3 其它可用端点（官方 Postman 集合里的）

`GET /shipping/v1/accounts/{account}`（已验证 200，返回可用 product 列表）、
`POST /shipping/v1/shipments`、`POST /shipping/v1/labels`、`PUT /shipping/v1/orders`、
`GET /shipping/v1/address`、`POST /shipping/v1/estimated_time_arrival`。
完整清单见 `data/postman/ap.zip`。

### 3.4 TNT

TNT 是 XML RTT，属于 bonus。好消息：题目给的那个 TNT 单号 `305506914` **在 AusPost 轨迹接口里能查到真实数据**，所以「Track 3 显示 unavailable」这条路其实不必走。
但题目第 6/7 节仍要求说明 TNT 的处理方式——建议在 README 里写清：TNT 原生 RTT 未实现（bonus 未做），该单号改用 AusPost 轨迹接口兜底，并说明依据。

---

## 4. 凭证（安全！）

**两套凭证，只有一套能用。**

| 来源 | Key / Password | 实测 |
|---|---|---|
| HTML v2（新） | `<可用的一套，真值见本机 .env>` | ✅ 200 |
| PDF（旧） | `<已吊销的一套>` | ❌ 401 `API_001 Unauthenticated` |

PDF 里那套已失效——这正是题目所说「permission issues」的来源。README 里值得写一句「我们测了两套凭证，旧的已吊销」。

**强制要求（题目第 9 节明确写了）**：
- `.env` 存放，`AUSPOST_API_KEY` / `AUSPOST_API_PASSWORD` / `AUSPOST_ACCOUNT_NUMBER` / `ORIGIN_POSTCODE`
- `.gitignore` 排除 `.env`，提供只有变量名的 `.env.example`
- 凭证**只能出现在后端**，绝不进前端源码
- 另外建议仓库设 private：brief 里有真实客户姓名、邮箱、手机号，还有真实 API 凭证，公开仓库等于泄露

---

## 5. 计算规则与舍入陷阱

规则（HTML v2 第 4 节，已确认）：

```
Ex-GST 单价   = RRP / 1.10            （RRP 即 SKU Price，含 GST）
行小计        = Ex-GST 单价 × 数量
订单 Subtotal = Σ 行小计
GST          = Subtotal × 10%
Order Total  = Subtotal + GST + Shipment Fee
```

**⚠️ 舍入顺序会真的差钱，必须写进 README。** 实测两种算法：

| 订单 | A：单价先舍入到分 | B：行合计后舍入 | 差 |
|---|---|---|---|
| PO-20251130-00072 | 1937.24 | 1937.28 | **0.04** |
| PO-20251203-00046 | 1504.54 | 1504.55 | **0.01** |

**建议采用 A，并全程用整数分 + `Decimal` ROUND_HALF_UP**（Python `round()` 是银行家舍入，会在 `.5` 上出错；JS 用 `Math.round` 也要注意负数与浮点）。`expected_totals.json` 就是用 A 算的：

- PO-20251130-00072：Subtotal 1937.24 / GST 193.72 / Total 2130.96
- PO-20251203-00046：Subtotal 1504.54 / GST 150.45 / Total 1654.99

（以上 Total 的 Shipment Fee 按 0.00 计；接了运费 API 后会变。）

---

## 6. 订单数据的两处不一致（以 HTML v2 为准）

| 项 | PDF（旧） | HTML v2（新，用这个） |
|---|---|---|
| 订单 2 编号 | `PO-20251202-00046` | **`PO-20251203-00046`** |
| 后端语言 | Python / Java / Go | **Python / Java / Go / Node.js**（多了 Node） |

另外注意：订单 2 的 4 个 SKU 分属 Track 2（AURPUR10）和 Track 3（HALGEO15 / MCMW10 / MCBO30）——**运费和轨迹都按 Track 分组，不是按订单**。订单 1 全部 5 个 SKU 都在 Track 1。

---

## 7. 交付清单（照题目第 9 节）

**应用**：前端 Vue/React/Angular + 后端 Python/Java/Go/Node.js（全栈 Node 或 Python 二选一最省事，10 分钟讲解也好讲）。
前后端都要有，控制台/JSON 输出不能替代前端 UI。

**前端最少要显示**：订单头和收货信息、每个 SKU 的编码/名称/数量/RRP/Ex-GST 单价/Ex-GST 行小计、图片占位区、轨迹信息与明确的 API 不可用态、每单的 Subtotal / GST / Shipment Fee / Total。

**README 必须用中文**，内容包含：怎么跑、依赖、输入数据结构、所有假设、遇到了什么困难怎么解决的、API 失败与降级行为、占位图说明、运费估算假设。

**数据驱动是硬要求**（题目强调了三遍）：订单号、SKU 码、价格、计算结果**都不能写进业务逻辑**，要能吃同样结构的真实订单。
建议额外造几条测试订单覆盖：缺 SKU 匹配、数量非法、轨迹查不到。

---

## 8. 技术栈已锁定：Python FastAPI + Vue 3

（这一节是针对「Gemini 版任务书」的补充，前面的事实部分不变。）

### 8.1 栈选型确认

- **后端 Python / FastAPI**：题目允许（Python / Java / Go / Node.js）。
- **前端 Vue 3 + Vite**：题目允许（Vue / React / Angular）。
- **数据层建议用本地 JSON，不要上 SQLite**。`data/orders.json` + `data/sku_catalog.json` 已经能完全满足「数据驱动」的要求，而且 10 分钟讲解时打开一个 JSON 就能说明输入结构，比让人去查数据库直观得多。SQLite 只会增加依赖和讲解负担，不产生额外分数。

### 8.2 Python 专属坑：别用 `round()`

Gemini 那版任务书只给了公式，没提舍入——**这是本项目最容易被扣分的地方**。

Python 的 `round()` 是**银行家舍入**（`round(2.5) == 2`，不是 3），而且浮点本身不精确。必须：

```python
from decimal import Decimal, ROUND_HALF_UP
CENT = Decimal("0.01")

def money(x) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)

unit_ex = money(Decimal(str(rrp)) / Decimal("1.1"))   # 单价先舍入到分
line_ex = money(unit_ex * qty)                        # 再乘数量
```

`Decimal(str(x))` 而不是 `Decimal(x)`——后者会把 float 的二进制误差带进来。
实测两种舍入顺序的差（见第 5 节）：订单 1 差 **0.04**，订单 2 差 **0.01**。

### 8.3 建议目录结构

```
aeris/
├─ backend/
│  ├─ app/
│  │  ├─ main.py              FastAPI 入口
│  │  ├─ config.py            读 .env（pydantic-settings 或 os.getenv）
│  │  ├─ schemas.py           Pydantic 模型
│  │  ├─ services/
│  │  │  ├─ pricing.py        GST 计算（纯函数，可单测）
│  │  │  ├─ sku.py            SKU 匹配（SKU -> product_list 记录）
│  │  │  ├─ auspost.py        AusPost 客户端（track / prices）
│  │  │  └─ shipment.py       运费聚合与降级
│  │  └─ routers/
│  │     ├─ orders.py         GET /api/orders, /api/orders/{no}
│  │     └─ tracking.py       GET /api/tracking/{id}
│  ├─ requirements.txt
│  └─ tests/
├─ frontend/
│  ├─ index.html
│  ├─ src/
│  │  ├─ main.js  App.vue
│  │  ├─ api.js               只打后端，绝不直接碰 AusPost
│  │  └─ components/
│  │     ├─ OrderCard.vue     订单头 + 收货地址 + 状态
│  │     ├─ SkuLine.vue       SKU 行 + 图片占位
│  │     ├─ SummaryPanel.vue  Subtotal / GST / Shipment / Total
│  │     └─ TrackingPanel.vue 轨迹 + API 不可用态
│  └─ vite.config.js          代理 /api -> localhost:8000
├─ data/                      已产出，见第 1 节
├─ .env.example               只有变量名
├─ .gitignore                 含 .env
└─ README.md                  中文
```

**CORS / 代理**：Vite dev server 配 `proxy: {'/api': 'http://localhost:8000'}`，生产用 FastAPI 挂静态文件，双端口时开 `CORSMiddleware`。凭证只允许出现在 `backend/` 里。

### 8.4 `.env` 变量清单

```ini
# 凭证向 AusPost 申请，真值只写在本机 .env（不入库）
AUSPOST_API_KEY=
AUSPOST_API_PASSWORD=
AUSPOST_ACCOUNT_NUMBER=
# 留空则用 config.py 里的默认值
AUSPOST_BASE_URL=
ORIGIN_POSTCODE=2111
ORIGIN_SUBURB=                  # 填已验证可用的 suburb；RYDE 配 2111 会 400，见第 3.2 节
ORIGIN_STATE=NSW
HTTP_TIMEOUT_SECONDS=8
```

`AUSPOST_ACCOUNT_NUMBER` 是 Gemini 那版**漏掉**的——少了它 `prices/shipments` 直接 400。
`.env.example` 里这些值全部留空或写占位符，不要带真实值。

### 8.5 对 Gemini 版任务书的三处修正

1. **不要预设 API 会失败。** Gemini 写了「由于测试 API 可能存在权限或网络限制…必须优雅降级」，实测那套凭证是通的。正确做法：默认走真实 API，降级逻辑保留但只在真的超时/非 2xx 时触发。把 fallback 当主路径会白白丢掉 bonus 分。
2. **Track 3（TNT 单号 `305506914`）不要显示 unavailable。** 实测它在 AusPost 轨迹接口里能查到真实数据。README 里写：TNT 原生 RTT（XML）未实现，该单号改用 AusPost 兜底。
3. **运费要真的算。** 用 `POST /prices/shipments`，别只写个「基于重量的公式」然后显示 0。实测 `total_cost 71.04`，是个能拿 bonus 的点。

### 8.6 Gemini 版还漏了这些题目明确要求的东西

- **SKU 匹配失败的分支**：题目第 8 节点名要考虑 missing SKU matches、invalid quantities、unavailable tracking。至少要有一条测试订单覆盖。
- **额外测试订单**：题目第 8 节允许且鼓励自己造，用来证明「数据驱动」。
- **README 要写「遇到的困难」**：题目第 9 节原话是 "Explain what you built, the difficulties you identified in the assessment, and how you addressed them"。光写怎么跑不够。
- **10 分钟中文面试讲解**：题目说可能会有 follow-up，README 结尾可以附一页讲解要点。
- **前端要显示 RRP**：题目第 9 节「Minimum front-end content」明确列了 RRP，不能只显示 Ex-GST 单价。
- **PII**：brief 里有真实客户姓名/邮箱/手机号。仓库建议 private，README 或示例数据里可考虑脱敏。

---

## 9. 建议给 CodeBuddy 的开工指令

```
在 E:/file/workBuddy/2026-10-26-16-49-26/aeris/ 下从零搭一个全栈项目：
后端 Python FastAPI，前端 Vue 3 + Vite，数据层用本地 JSON（不要 SQLite）。
先读 HANDOFF.md（尤其第 3、5、8 节），再读 data/orders.json、data/sku_catalog.json。
所有外部事实以 HANDOFF.md 为准，不要重新猜测 API，也不要预设 API 会失败。

要求：
- 数据驱动：订单号/SKU/价格/计算结果一律不进业务逻辑
- 金额用 Decimal + ROUND_HALF_UP，单价先舍入到分（禁用 Python round()）
- 凭证走 .env，只在后端使用；.gitignore 排除 .env；提供 .env.example
- AusPost 真实调用 + 超时/非 2xx 时明确降级态
- 运费走 POST /prices/shipments，不是写死公式
- 最后写一份中文 README，含「遇到的困难与解决」一节
```

**不要做**：把 `expected_totals.json` 的值硬编码进计算逻辑（它只是测试基准）；把凭证或真实客户信息提交进仓库。
