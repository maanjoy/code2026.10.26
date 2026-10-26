# Aeris 订单核算全栈项目（FastAPI + Vue 3 + 本地 JSON）

数据驱动的订单金额核算 + Australia Post / StarTrack **实时**运费报价与轨迹查询。

- 后端：Python 3.10 + FastAPI（`backend/`）
- 前端：Vue 3 + Vite（`frontend/`）
- 数据层：本地 JSON，**没有数据库**（`data/orders.json` + `data/sku_catalog.json`，698 条 SKU）
- partner API：AusPost / StarTrack **testbed**，真实调用

> 本文档中的接口端点、金额数字与降级行为，均来自本机实际调用。
>
> 章节导航：§1–§6 是"怎么跑、怎么算、怎么接"，§7 是已解决的困难，§9 是明确未做的部分，  
> **§10 是上线前必须与业务方 / 数据提供方 / 财务确认的开放问题清单**（字段定义、计算口径、  
> 错误提示、多语言、时区、数据冲突以哪个为准）。

---

## 0. 两个页面

| 页面             | 地址                                                       | 说明                                 |
| -------------- | -------------------------------------------------------- | ---------------------------------- |
| **订单页**（交付内容）  | `http://127.0.0.1:5173/` | 5 笔订单：题目给的 2 笔 + 自造的 3 笔测试订单（§2.2） |
| **边界用例 · 测试页** | `http://127.0.0.1:8000/tests.html`                       | 故意造坏的虚构数据，默认**关闭**，见下              |

两个端口渲染的是**同一份源码**，内容一致：

| 端口     | 谁在提供服务         | 内容来源                                | 改代码后                 |
| ------ | -------------- | ----------------------------------- | -------------------- |
| `5173` | Vite 开发服务器     | 直接编译 `frontend/src`                 | 即时生效（HMR）            |
| `8000` | FastAPI 托管静态文件 | `npm run build` 产出的 `frontend/dist` | 需要重新 `npm run build` |

演示 / 交付用单端口 `8000` 最省事（只需一个进程）；开发时用 `5173`。

订单页上没有任何通往测试页的按钮或链接——它是测试工具，不是产品功能。要看边界用例：

```bash
# .env 里加一行（后端按 mtime 自动重载，不需要重启）
EXPOSE_TESTCASES=true
```

然后打开 `tests.html`。生产保持 `EXPOSE_TESTCASES=false`（默认）：`/api/testcases` 返回 404，  
测试页会给出开启说明而不是半张空表。离线回归走 `pytest`（23 条断言，见 §1.4）。  
边界数据集与隔离方式详见 §2.5。

---

## 1. 怎么跑

### 1.1 依赖

| 组件      | 版本要求                |
| ------- | ------------------- |
| Python  | 3.10+（本机实测 3.10.11） |
| Node.js | 18+（本机实测 v24.21.0）  |

Python 包：`fastapi`、`uvicorn[standard]`、`httpx`、`pydantic`、可选 `python-dotenv`，测试用 `pytest` / `pytest-asyncio`。  
Node 包：`vue`、`@vitejs/plugin-vue`、`vite`。

### 1.2 一次性配置

```bash
cd aeris
cp .env.example .env        # 然后只在本机 .env 里填 AusPost 凭证
```

配置示例**只保留变量名，不写任何真实值**——这份仓库是要提交的，凭证、账号号、partner 地址  
一律不落在文档和代码里：

```ini
# AusPost / StarTrack testbed 凭证（HTTP Basic：username = Key，password = Password）
AUSPOST_API_KEY=
AUSPOST_API_PASSWORD=
AUSPOST_ACCOUNT_NUMBER=

# partner API 基地址
AUSPOST_BASE_URL=

# 起运地（留空则回退到 data/orders.json 的 origin 节点）
ORIGIN_POSTCODE=
ORIGIN_SUBURB=
ORIGIN_STATE=

# 业务参数
HTTP_TIMEOUT_SECONDS=
GST_RATE=
AUSPOST_CACHE_TTL_SECONDS=
AUSPOST_DEGRADED_CACHE_TTL_SECONDS=

# 数据集与测试开关
DATA_DIR=
ORDER_DATASET_PATTERN=
SKU_CATALOG_FILE=
TEST_DATASET_PATTERN=
EXPOSE_TESTCASES=
```

留空时会用到的默认值（都写在 `backend/app/config.py` 里）：

| 变量                                                                    | 作用                                                           | 默认值                                              |
| --------------------------------------------------------------------- | ------------------------------------------------------------ | ------------------------------------------------ |
| `AUSPOST_API_KEY` / `_PASSWORD` / `AUSPOST_ACCOUNT_NUMBER`            | testbed 凭证与必填的 `account-number` header（**向 AusPost 申请，不入库**） | 空 → `CREDENTIALS_MISSING` 降级（§7.7）               |
| `AUSPOST_BASE_URL`                                                    | partner 基地址                                                  | testbed 地址（见 `config.py`）                        |
| `ORIGIN_POSTCODE` / `_SUBURB` / `_STATE`                              | 起运地                                                          | `data/orders.json` 的 `origin` 节点（注意 §7.1）        |
| `HTTP_TIMEOUT_SECONDS`                                                | 超时（秒），超时进入降级态                                                | `8`                                              |
| `GST_RATE`                                                            | GST 税率                                                       | `0.10`                                           |
| `AUSPOST_CACHE_TTL_SECONDS` / `_DEGRADED_...`                         | 正常 / 降级结果缓存时长（秒）                                             | `900` / `30`                                     |
| `DATA_DIR` / `SKU_CATALOG_FILE`                                       | 数据目录与 SKU 目录文件名                                              | 仓库 `data/` / `sku_catalog.json`                  |
| `ORDER_DATASET_PATTERN` / `TEST_DATASET_PATTERN` / `EXPOSE_TESTCASES` | 数据集范围与测试接口开关（§2.5）                                           | `orders*.json` / `testcases_edge.json` / `false` |

`.env` 已被 `.gitignore` 排除；仓库里只有 `.env.example`（同样只有变量名，不含真实值）。  
凭证只在后端进程内使用，`GET /api/health` 只返回"是否已配置"的布尔值，响应体里查不到任何凭证串（有单测守卫）。

### 1.3 启动

```bash
# 终端 1：后端  http://127.0.0.1:8000
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# 终端 2：前端  http://127.0.0.1:5173
cd frontend
npm install
npm run dev
```

**单端口运行**（演示/讲解最省事）：

```bash
cd frontend && npm run build      # 产出 frontend/dist
cd ../backend && python -m uvicorn app.main:app --port 8000
# 打开 http://127.0.0.1:8000     FastAPI 直接托管构建好的 SPA
```

### 1.4 测试

```bash
cd backend
python -m pytest -q          # 23 passed
```

测试覆盖：金额口径对齐 `data/expected_totals.json`、舍入顺序差异、SKU 匹配失败 / 非法数量 /  
缺收件地 / 查不到轨迹四类边界、降级态、"不要用 Python `round()`"的 AST 守卫、凭证不外泄，  
以及"生产接口不含边界用例"与"`/api/testcases` 默认关闭、开启后才返回边界数据"两条隔离断言。

边界用例**不在页面主流程里**（见 §2.5）：`pytest` 通过显式切换到 `testcases_edge.json`  
来跑同一套业务逻辑，页面上的边界用例测试页需要手动打开开关才可用。

### 1.5 接口一览

| 方法   | 路径                                      | 说明                                                 |
| ---- | --------------------------------------- | -------------------------------------------------- |
| GET  | `/api/orders`                           | 全部订单（含每个 SKU 行、金额、运费报价、轨迹）                         |
| GET  | `/api/orders/{order_no}`                | 单个订单详情                                             |
| GET  | `/api/trackings`                        | 全部运输分组的报价 + 轨迹                                     |
| GET  | `/api/tracking/{tracking_id}?refresh=1` | 按运单号单独查轨迹（可跳过缓存）                                   |
| GET  | `/api/health`                           | 数据目录、SKU/订单数量、凭证是否已配置                              |
| POST | `/api/cache/clear`                      | 清空 partner API 结果缓存                                |
| GET  | `/api/testcases`                        | **边界用例**（默认 404，需 `EXPOSE_TESTCASES=true`；不在生产链路上） |

交互式文档：`http://127.0.0.1:8000/docs`

---

## 2. 输入数据结构

### 2.1 `data/orders.json`（题目给出的订单）

```jsonc
{
  "origin": { "postcode": "2111", "suburb": "RYDE", "state": "NSW", "country": "AU" },
  "tracking": {
    "Track 1": { "number": "2FWZ50008569", "courier": "StarTrack / Australia Post" }
  },
  "orders": [
    {
      "order_no": "PO-20251130-00072",
      "order_date": "30/11/25",
      "status": "Completed",
      "company": "Harbour Wellness Dispensary",
      "customer": "...", "phone": "...", "email": "...",
      "address": "Unit 4, 88 Sample Street, South Yarra VIC 3141",
      "suburb": "SOUTH YARRA", "state": "VIC", "postcode": "3141", "country": "AU",
      "items": [{ "sku": "TBAMET10", "qty": 3, "tracking": "Track 1" }]
    }
  ]
}
```

字段名（不是字段值）由配置决定：`ORDER_NO_FIELD` / `ITEMS_FIELD` / `ITEM_SKU_FIELD` /  
`ITEM_QTY_FIELD` / `ITEM_TRACKING_FIELD`。订单头里的其它标量字段会被原样透传给前端渲染，  
所以真实订单多一列客户备注也不用改代码。

### 2.2 `data/orders_generated.json`（自造的测试订单，直接进首页）

题目要求「You may generate multiple test orders as reasonable assumptions」，因此另造了 3 笔  
**结构完全一致、数据合理合法**的测试订单，和上面两笔真实订单一起出现在首页（共 5 笔）：

| 订单                  | 场景设定                                                    |
| ------------------- | ------------------------------------------------------- |
| `PO-20251208-00118` | Sydney NSW 2000，单一运单 4 个 SKU                            |
| `PO-20251210-00127` | Brisbane QLD 4000，**两个运单**（Track 5 / Track 6），拆成两票货分别报价 |
| `PO-20251212-00134` | Perth WA 6000，TNT 承运单号                                  |

它们的作用是证明"换一批订单进来，代码零改动"这件事是真的——12 个 SKU 全部能在 698 条目录里命中、  
尺寸重量齐全、数量合法、收货地完整，于是 3 笔单都能拿到实时报价（实测  
`1144.70 / 1543.80 / 1612.41`），四行金额同样自洽。

所有公司、姓名、电话、地址均为虚构，邮箱统一用保留域名 `example.com.au`；运单号是占位值  
（testbed 对任意单号都返回同一份样例轨迹，见 §7.11）。

### 2.3 `data/sku_catalog.json`

按 SKU 索引的 698 条记录，单位已统一：`weight_g`(g)、`length_mm`/`width_mm`/`height_mm`(mm)、  
`rrp`(AUD，含 GST)。

### 2.4 `data/expected_totals.json`

**仅供测试比对**，业务代码从不读取它（测试会验证二者一致，但计算路径完全独立）。

### 2.5 `data/testcases_edge.json`（边界数据集，仅测试链路使用）

结构与 `orders.json` 完全一致，用来证明"换一份同结构的数据文件进来，代码一行都不用改"：

| 订单             | 覆盖场景                                                  |
| -------------- | ----------------------------------------------------- |
| `PO-EDGE-0001` | SKU 在目录中不存在、数量 = 0 / 负数 / 非数字字符串 / 数量写成 `"7"`、SKU 为空串 |
| `PO-EDGE-0002` | 运单号在 AusPost 侧查不到任何轨迹事件                               |
| `PO-EDGE-0003` | 收货地址缺 suburb / postcode，导致无法向 partner 报价              |

**它不属于交付界面的一部分**，因此做了三层隔离：

| 层    | 生产默认                                                                                                                     | 打开方式                                     |
| ---- | ------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------- |
| 数据加载 | `ORDER_DATASET_PATTERN=orders*.json` 命中 `orders.json` + `orders_generated.json`；边界文件改名为 `testcases_edge.json`，不在这个通配符范围内 | 用 `TEST_DATASET_PATTERN` 单独指定            |
| API  | `/api/orders` 只返回 5 笔正常订单；`/api/testcases` 返回 404                                                                        | `EXPOSE_TESTCASES=true`（`.env` 热重载，无需重启） |
| 页面   | 订单页只渲染正常订单，页面上没有通往测试页的按钮/链接                                                                                              | 直接访问 `tests.html`（入口见 §0）                |

理由很简单：把虚构的坏数据混在正常订单列表里，会让使用者分不清哪些是真订单、  
哪些是故意造出来验证容错的数据；金额看板上出现"数量 = two"的行，也谈不上是交付质量。  
注意区分两类自造数据：**§2.2 那 3 笔是"合理的假设"**（合法数据，直接进首页），  
**这一节的 3 笔是"故意的坏数据"**（只进测试链路）。  
边界用例的正确位置是测试——`pytest`（离线可跑）和可选的测试页，而不是生产页面。

**测试页**（入口见 §0）：打开 `EXPOSE_TESTCASES=true` 后按地址访问。页面用同一套后端逻辑跑边界数据集，  
并给出一张断言表（异常行被标记而非崩溃、问题行不计入金额、Subtotal/GST/Total 自洽、  
降级订单运费为 0.00 且原因可见、查不到轨迹走空态）与降级原因的机器码 → 中文对照。  
接口关闭时页面直接给出可操作的开启说明，不会显示半张空表。

### 2.6 数据驱动的具体含义

业务逻辑里**没有**任何：订单号、SKU 码、RRP、数量、折扣、计算结果。

- 税率：`GST_RATE` 环境变量（默认 0.10），以 `PricingRules` 注入 `pricing.py` 的纯函数；
- 起运地：优先取数据文件的 `origin` 节点，环境变量只作为"纠偏覆盖"；
- 订单号 / 行项目的字段名：全部来自配置；
- `expected_totals.json` 只出现在测试里；
- 换真实订单只需替换 `data/orders.json`（或改 `ORDER_DATASET_PATTERN`），代码改动量为 0。

### 2.7 订单状态与轨迹状态是**两个来源**，故意不合并

页面顶部深色条上的徽章来自订单数据（`orders.json` 的 `status` 字段），  
右侧追踪卡片上的徽章来自 AusPost 响应。两者可能不一致，而且**原始数据本身就不一致**：

| 订单                | `orders.json` 的 status | 轨迹实际状态                           |
| ----------------- | ---------------------- | -------------------------------- |
| PO-20251130-00072 | `Completed` → 已完成      | 已送达（一致）                          |
| PO-20251203-00046 | `In Transit` → 运输中     | Track 2 / Track 3 都是**已送达**（不一致） |

**没有擅自用轨迹去覆盖订单状态**，理由：

1. 订单状态是业务系统里的状态（可能还没同步、可能含退货/结算等业务语义），  
   轨迹状态是承运商的状态，二者不是同一个东西；
2. 数据驱动的前提是**忠实呈现输入**。代码里替用户"修正"数据，  
   等于把上游的数据问题藏起来——真正该做的是让它显形，然后由人去查；
3. 保留矛盾、注明来源，比悄悄对齐两个字段更诚实——后者会让人以为数据本来就是一致的。

`data/testcases_edge.json` 里的虚构订单同样保留原始 status，不做任何对齐。

---

## 3. 金额口径与舍入

```
Ex-GST 单价   = RRP / (1 + GST 税率)          ← 先舍入到分
行小计        = Ex-GST 单价 × 数量            ← 再乘数量，然后舍入到分
Subtotal      = Σ 行小计
GST           = Subtotal × 10%                ← 只对商品计税，严格照题目第 4 节
Shipment Fee  = 承运商报价总额（含 GST）
Total         = Subtotal + GST + Shipment Fee
```

- 全程 `decimal.Decimal` + `ROUND_HALF_UP`，`Decimal(str(x))` 而非 `Decimal(x)`。
- **禁用 Python 内置 `round()`**（银行家舍入：`round(2.5) == 2`）。`tests/test_pricing.py` 会用 AST  
  扫描 `backend/app/**/*.py`，一旦有人写 `round(` 测试直接失败。
- 舍入顺序不是学究问题，是**真的差钱**（下表为本机实测）：

| 订单                | A：单价先舍入到分（采用） | B：行合计后舍入 | 差        |
| ----------------- | ------------- | -------- | -------- |
| PO-20251130-00072 | 1937.24       | 1937.28  | **0.04** |
| PO-20251203-00046 | 1504.54       | 1504.55  | **0.01** |

单测同时断言这两种口径确实存在上述差额。

**与参考基准的一致性**：用 `data/orders.json` + `sku_catalog.json` 算出的 Subtotal / GST /  
每个 SKU 的 Ex-GST 单价与行小计，与 `data/expected_totals.json` 逐项相等（1937.24 / 193.72，  
1504.54 / 150.45）。

### 运费里的 GST 怎么处理

AusPost 返回的 `shipment_summary` 同时有 `total_cost`（含 GST）和 `total_cost_ex_gst`。  
**两份口径都能自洽，但只能选一个，否则 GST 行会偏离题目给的公式。** 本项目选择：

- **GST 行 = `Subtotal × 10%`**，一分钱都不掺运费，与题目第 4 节的公式严格一致，可直接验算；
- **Shipment Fee 行 = `total_cost`（含 GST 全额）**，即"运费实际要付多少钱"直接摆出来；
- `Subtotal + GST + ShipmentFee = Total` 数学上严格成立，且不重复计税；
- 同时保留 `shipment_fee_ex_gst` / `shipment_gst` / `shipment_total_cost_incl_gst` 三个字段  
  在 API 响应里供对账；**界面上不展示**——页面上同时出现 11.44 和 10.40 两个数，  
  只会让"运费到底是多少"产生歧义。
- 若 partner 没给 Ex-GST 字段，则按"报价含 GST"反算，并在 `quote.gst_treatment` 里标注  
  （取值：`ex_gst_field` / `already_ex_gst` / `assumed_gst_inclusive`），可直接看出走了哪种口径。

> 另一种写法（Shipment Fee 取 Ex-GST、运费 GST 并入 GST 行）总额一样，但 GST 行会变成  
> `193.72 + 1.12 = 194.84`，与题目"GST = Order Subtotal × 10%"的表述不符。**已明确放弃这种写法**，  
> 因为那样 GST 行无法直接按题目公式验算。

---

## 4. AusPost 集成：真实调用 + 明确降级

### 4.1 端点（全部实测通过）

| 用途   | 请求                                                  | 备注                                   |
| ---- | --------------------------------------------------- | ------------------------------------ |
| 轨迹   | `GET /shipping/v1/track?tracking_ids=...`           | HTTP Basic + `account-number` header |
| 运费   | `POST /shipping/v1/prices/shipments`                | 请求体假设见 §6                            |
| 地址校验 | `GET /shipping/v1/address?suburb=&state=&postcode=` | 用于自动修正起运地（见 §7.1）                    |

认证：HTTP Basic（username = API Key，password = Password），额外 header `account-number`。  
**缺少 `account-number` 会让 `prices/shipments` 直接 400**——这个 header 在官方样例里没出现，  
只能从 `accounts/{id}` 的可用性反推出来。


### 4.2 默认走真实 API，**不预设它会失败**

任务书里那句"由于测试 API 可能存在权限问题，必须优雅降级"与实测不符：
HTML v2 那套凭证实测完全可用（PDF 那套已吊销、返回 `API_001 Unauthenticated`）。
因此本项目的策略是：

```
正常路径 = 真实调用
降级路径 = 只在 超时 / 非 2xx / 传输异常 / 响应体里有 errors / 缺凭证 时触发
```

### 4.3 降级态必须是"看得见"的

降级时**不会**悄悄显示 `A$0.00` 假装成功，而是：

- `shipment_mode`：`live` / `partial` / `degraded`；
- `quote.reason_code` / `http_status` / `message` / `errors[]`：**原样透出 partner 返回的错误**；
- `order.shipment_issues[]`：给前端展示的一句人话；
- 此时 `shipment_fee = 0.00`，UI 在汇总下方打黄色提示条，**说明的是具体原因而不是笼统的"API 不可用"**：

  | reason_code | UI 措辞 |
  |---|---|
  | `DESTINATION_INCOMPLETE` | 收货地信息不完整，无法向 AusPost 报价，已按 A$0.00 计 |
  | `NO_PRICEABLE_ITEMS` | 没有可计价的商品行，已按 A$0.00 计 |
  | `CREDENTIALS_MISSING` | 未配置 AusPost 凭证，已按 A$0.00 计 |
  | `TIMEOUT` / `TRANSPORT_ERROR` | 承运商接口超时 / 网络不可达，已按 A$0.00 计 |
  | 其他 | 承运商接口不可用，已按 A$0.00 计 |

  这样区分是因为**地址缺失是数据问题、超时是接口问题**，使用者下一步该做什么完全不同。
- 轨迹同理：`TrackingPanel` 显示中文原因（未分配运单号 / 该运单号查不到轨迹 / 承运商接口超时）+ HTTP 状态。
- 机器码只留在 JSON 里供测试断言，前端统一经 `reasonText()` 翻译，界面上不出现 `DESTINATION_INCOMPLETE`
  这类裸常量。

降级结果只缓存 30 秒（`AUSPOST_DEGRADED_CACHE_TTL_SECONDS`），正常结果缓存 15 分钟，
partner 一恢复就能自动回到真实数据，不用重启服务。想立刻生效可调 `POST /api/cache/clear`。

**配置本身也支持热重载**：`config.py` 用 `.env` 的 `(mtime_ns, size)` 做指纹，
文件一变就自动重读——把 `.env` 改名后下一次请求立刻走 `CREDENTIALS_MISSING` 降级，
改回来立刻恢复 `live`。实测（不重启服务）：

| 操作 | `credentials_configured` | `shipment_mode` | 运费 | Total |
|---|---|---|---|---|
| 正常 | `true` | `live` | 11.44 | 2142.40 |
| `mv .env .env.hidden` | `false` | `degraded` | 0.00 | 2130.96 |
| `mv .env.hidden .env` | `true` | `live` | 11.44 | 2142.40 |

这一点是刻意做的：如果配置在进程启动时就被缓存死，**"降级"这条路径只能靠嘴说、无法现场验证**，
而它是题目第 11 节明确要求的行为。

> 复现上表时，每次改名后要先调 `POST /api/cache/clear`——partner 报价正常缓存 15 分钟，
> 不清缓存会拿到上一次的结果，看起来像"没生效"。

### 4.4 实测结果（本机真实调用，不是样例回放）

| 项目 | 结果 |
|---|---|
| 官方 Postman 基准样例（GLADESVILLE 2111 → SOUTH YARRA 3141，76/78/76，0.5kg） | `total_cost 71.04 / ex_gst 64.58 / gst 6.46`，与官方样例一致 |
| Track 1 `2FWZ50008569` | 200，6 条事件，`Delivered in Full`，2 个 EXP 包裹 |
| Track 2 `2FWZ50008645` | 200，真实事件流 |
| Track 3 `305506914`（题目标 TNT） | 200，`Parcel Post`，**Delivered**（不是 unavailable） |
| 运费 PO-20251130-00072 | Track 1 报价 11.44（含 GST）/ 10.40（Ex-GST）→ Total **2142.40** |
| 运费 PO-20251203-00046 | Track 2 + Track 3 合计 22.22（含 GST）/ 20.20（Ex-GST）→ Total **1677.21** |

> 运费数值取决于装箱假设（§6）。改装箱会改运费，但**不会**改 `Subtotal` 和 `GST`——
> 后两者只由 RRP 和数量决定，与 API 是否可用无关。

---

## 5. 前端展示内容

页面只放订单业务字段，不放调试信息。订单页渲染 5 笔订单（题目给的 2 笔 + 自造的 3 笔，见 §2.2），
故意造坏的边界用例不在这一页——它们的入口是 `pytest` 与独立的测试页 `tests.html`（见 §0 与 §2.5）。

- 订单头与收货信息：订单号、日期、状态、公司、客户、电话、邮箱、完整地址（含郊区/州/邮编/国家）。
- SKU 明细：**SKU 编码、名称、数量、RRP（含 GST）、Ex-GST 单价、Ex-GST 行小计**、图片占位区。
- 轨迹信息 + 明确的不可用态（状态胶囊 + 中文原因 + HTTP 状态）。
- 每单 **Subtotal / GST / Shipment Fee / Total** 四行。

装箱尺寸、起运地、报价策略、HTTP 状态、被跳过的 SKU 这些**只出现在 `/api/orders` 响应里**，
页面上不渲染——它们回答的是"这个数字怎么来的"，属于排障信息，
放在订单详情页只会让使用者困惑（同一个运费出现两个口径尤其如此）。

前端**只**调 `/api/*`，代码里没有任何 partner 凭证或 partner 域名。

### 对照题目第 10 节 mock-up 逐项核对

| mock-up 区块 | mock-up 里的内容 | 本项目实现 |
|---|---|---|
| 订单条 | 订单号 + 日期 + 说明 + 状态徽章 | 深色条：订单号 / 日期 / 公司 / 客户 / 状态徽章 |
| Order Items | 产品名、RRP incl. GST、Ex-GST × 数量、行小计 | 同上，另加 SKU 码、药瓶占位图、坏行的红字原因 |
| — | （mock-up 无） | 额外显示行级问题：SKU 未匹配 / 数量非法时标红且**不计入金额** |
| Summary | Subtotal / GST (10%) / Shipment Fee / Total | 同四行，另加一行小字说明运费来源（实时报价 / 降级原因） |
| Shipping Address | 姓名 / 公司 / 街道 / 城市州邮编 / 备注 | 姓名 / 公司 / 街道 / suburb state postcode / 国家 / 电话 · 邮箱 |
| Australia Post Tracking | 运单号 + 状态 + 事件时间线（含地点） | 同左，另支持多票货并列（订单 2 有 Track 2 / Track 3 两票）、超出 5 条可展开 |
| 布局 | 单栏顺序流 | 两栏：左=条目+汇总，右=地址+追踪（阅读顺序与 mock-up 一致） |

题目写明 mock-up "is not a prescribed design"，因此没有逐像素照抄，但六个区块一个不缺，
且字段比 mock-up 更全（mock-up 没有 SKU 码、没有国家、没有行级错误提示）。

### 占位图说明

SKU 数据源（`product_list` 全表 26 列）**没有图片字段**，也拿不到图床 URL。因此：

- 后端在每行返回 `image_url: null`，语义明确表示"数据源里没有图片"；
- 前端按 SKU 序号轮换四款**本地产 SVG 药瓶图**（`frontend/public/img/bottle-1..4.svg`），
  是同一造型的不同配色（青 / 绿 / 紫 / 琥珀），不使用外部图床、不发任何网络请求；
- 药品瓶造型刻意不同于真实产品照：没有品牌、没有标签文字（只有 `Rx` 标记），
  不会被误认成真实商品图，也满足题目"中性的医药瓶图标"要求；
- 后续只要在 `sku_catalog.json` 里补一个图片字段、前端优先读 `item.image_url` 即可替换，
  不需要改组件结构。

---

## 6. 运费估算：调用方式与装箱假设

运费**全部来自 `POST /prices/shipments` 的真实响应**，没有"按重量 × 单价"之类的本地公式。
但"把一票货描述成几个包裹"这件事必须由我们决定，假设如下：

1. **分组粒度**：按 `(Track 标签, 收货地址)` 聚合成一票货。原始数据的运单号是挂在 Track 上、
   而不是挂在订单上，所以按 Track 分组；订单 2 的 4 个 SKU 因此分成 Track 2 / Track 3 两票。
2. **策略 A `single_carton`（默认）**：整票合成一个标准纸箱，用**体积法**推算箱体：

   - 底面固定 `30 × 20 cm`（`CARTON_BASE_*`），若任一 SKU 的长或宽超过则取最大值；
   - 高度 = `Σ(单件体积 × 数量) / 底面积`，再与"最高单件"和最小高度 `3 cm` 取大，
     上限 `100 cm`（`CARTON_MAX_SIDE_MM`）；
   - 重量取实际重量之和（kg，三位小数），**不用立方重量**——立方重由 AusPost 自己的计费规则处理，
     我们再折算一次会重复放大运费。

   > **为什么要改成体积法**：最初的实现是"高度 = Σ(单件高度 × 数量)"，5 个 SKU 会算出
   > `9×9×165 cm` 的药箱。testbed 不校验尺寸上限所以照样返回 200，但这个高度已经超过
   > 澳大利亚邮政单件包裹的常规上限，物理上也不像一箱药品。
   > 体积法算出来是 `30×20×12 cm / 0.988 kg`，接近真实发货箱型。

3. **策略 B `per_item_sum`（兜底）**：若整箱报价被 partner 拒绝（非 2xx），改为逐件报价后求和，
   **仍然是真实调用**。`quote.strategy` 记录实际用的是哪种，`quote.attempts[]` 记录每次尝试的
   suburb / 策略 / HTTP 状态 / 原因。
4. **两种策略都失败**才判为降级（此时 UI 明确标注运费未计入）。
5. **起运地候选链**：`ORIGIN_*` 环境变量 → 数据文件 `origin` → 逐候选调 `/address` 校验，
   取第一个校验通过的组合（详见 §7.1）。`quote.origin_used` 记录最终用的是哪个。
6. 按官方样例请求体建模：一票货一次报价，`items` 里一项包裹描述。
7. `quote.carton` 把最终箱型（长/宽/高/重）一并返回（`/api/orders` 响应里可见），
   讲解时能直接说清"这个运费是为多大的箱子报的"。

---

## 7. 遇到的困难与解决

### 7.1 起运地 `2111 (Ryde)` 在 AusPost testbed 里根本配不上

**现象**：题目写起运地是 `2111 (Ryde, NSW)`，但 `suburb=RYDE & postcode=2111` 报价直接 400；
`GET /address` 校验返回 `found: false`，候选 suburb 是 GLADESVILLE / HENLEY / HUNTLEYS POINT…；
`suburb=RYDE & postcode=2112` 和 `suburb=GLADESVILLE & postcode=2111` 才 200。

**解决**（`services/shipment.py::origin_candidates`）：

1. 先按"环境变量覆盖 → 数据文件 origin"排出候选 suburb；
2. 调 `GET /address` 校验，若 `found: false`，把 partner 返回的候选 suburb 插到队列前面重试，
   最多试 3 个（`MAX_ORIGIN_ATTEMPTS`），第一次成功即停；
3. `quote.origin_used` 回显最终用的起运地，`quote.note` 说明发生过什么，UI 上可见。

这样既保留了 `ORIGIN_POSTCODE=2111`（忠于题目），又不会因为这条数据矛盾让运费直接挂掉；
配置里把 `ORIGIN_SUBURB` 固定成 GLADESVILLE（已验证可用的写法），只是把答案固化下来。
题目数据与 testbed 之间的这条矛盾已如实记录在本节，未做任何掩盖。

### 7.2 舍入顺序会真的差钱（0.04 / 0.01）

两种都"看起来合理"的算法（单价先舍入 vs 行合计后舍入）算出来差 4 分和 1 分。
**解决**：确定采用"单价先舍入到分"，并把两种口径都写进测试——一条断言结果与
`expected_totals.json` 一致，另一条断言"另一种口径确实差 0.04 / 0.01"。
讲解时可以一句话点破：这不是风格之争，是对不上账。

### 7.3 Python 的 `round()` 是银行家舍入

`round(2.5) == 2`，且 float 本身不精确（`Decimal(0.1)` 会带上二进制误差）。
**解决**：统一 `Decimal(str(x))` + `quantize(Decimal("0.01"), ROUND_HALF_UP)`，
并加了一条 AST 扫描测试：凡是有人直接调用 `round()` 就红灯（该测试当前为通过状态）。

### 7.4 轨迹响应有两种结构

`tracking_results[]` 里有时是 `consignment{status, events[]}`（多件 StarTrack 包裹），
有时是顶层 `status` + `trackable_items[].{article_id, product_type, status, events[]}`
（TNT 单号 `305506914` 打到 AusPost 就是这种）。

**解决**：`parse_tracking()` 同时兼容两种结构，事件跨 `consignment` 与所有 `trackable_items` 合并，
按 `(date, location, description)` 去重后按时间倒序；状态按
`consignment.status → 顶层 status → 首个 trackable item status` 的优先级取值。
单测用 `data/auspost/*.json` 的**真实响应**覆盖了两种形态。

### 7.5 Track 3 的 TNT 单号不要显示 unavailable

任务书要求说明 TNT 的处理方式。原生 TNT 是 XML RTT（属于 bonus，**未实现**），
但题目给的单号 `305506914` 在 AusPost 轨迹接口里能查到真实数据（`product_type: Parcel Post`，Delivered）。
**解决**：该单号走 AusPost 轨迹接口兜底，实测能出真实事件流；README 中同时写明
"TNT 原生 RTT 未实现（bonus 未做）"以及这样做出的依据。

### 7.6 凭证有两套，只有一套能用

HTML v2 那套 200，PDF 那套 401 `API_001 Unauthenticated`——任务书说的"permission issues"
其实就是旧凭证已被吊销。
**解决**：只用 HTML v2 那套；凭证进 `.env`（`.gitignore` 排除）、只出现在后端进程内；
`/api/health` 只回布尔值；仓库另给 `.env.example`（只有变量名）。
单测会把一串假 secret 塞进环境变量，再断言所有接口的响应体里搜不到它。

### 7.7 缺凭证 / API 挂掉时不能假装成功

**解决**：见 §4.3。缺凭证时 `reason_code = CREDENTIALS_MISSING`，金额退化到 Shipment Fee = 0 口径，
但页面上必须有醒目的原因提示（而不是无声的 `A$0.00`），
且措辞要区分"没配凭证""接口超时""地址不全"——这三件事的处理方式完全不同。

### 7.8 缺 SKU 匹配 / 非法数量 / 缺收件地（题目第 8 节点名要覆盖）

这些不是"抛异常就算完了"，要把能算的继续算出来：

- 行级 issues：`SKU_NOT_FOUND`、`QTY_INVALID`、`QTY_MISSING`、`RRP_MISSING`、`DESTINATION_INCOMPLETE`；
- 未匹配 / 数量非法的行**不计入金额**，但依然渲染并打红色标记，同时显示原始值（如 `"two"`）；
- 缺尺寸/重量的 SKU 会被排除出 partner 报价请求（不能向 partner 谎报尺寸），并在
  `shipment.issues` 里记一条 `SKIPPED_WITHOUT_DIMENSIONS:...`；只要分组里还有可报价的行就继续报价；
- 收件地不完整 → 该票货标 `DESTINATION_INCOMPLETE` 降级，其余订单不受影响。

### 7.9 `account-number` header 缺失会 400

官方 Postman 集合的样例请求里没有这个 header，照着发会直接 400。
已在 `AusPostClient` 里固定为默认 header（见 `.env.example` 的 `AUSPOST_ACCOUNT_NUMBER`）。

### 7.10 重复调用会把 testbed 打爆

列表页 + 详情页 + 反复刷新会重复请求同样的报价。
**解决**：`TTLCache`，按请求体 MD5 做 key；正常结果 15 分钟、降级结果 30 秒。
需要立刻取新结果时调 `POST /api/cache/clear`（界面上不放清缓存按钮——它属于运维操作，
不是订单页面该有的东西）。

### 7.11 testbed 对**任何**单号都返回同一份样例轨迹

这是最容易自欺欺人的一个坑：`track?tracking_ids=INVALID123`、`123456`、`000000000000`
拿回来的都是同一份"已妥投"事件流。也就是说：

- **没法用假单号验证"查不到轨迹"的降级分支**——接口永远是 200；
- 如果把假单号放进演示数据，页面会显示一个并不存在的"已送达"结果。

**解决**：降级分支改用**单元测试 + 构造响应**来覆盖（`reason_code = NO_RESULT_FOR_ID`），
不依赖真实接口行为；演示数据只用题目给的真实单号，不自造单号。

### 7.12 界面不应出现机器码

早期版本把 `DESTINATION_INCOMPLETE`、`NO_TRACKING_NUMBER`、`Track C` 直接渲染进页面，
出现「运费降级原因：Track C：DESTINATION_INCOMPLETE - …」这种句子——机器码混中文，
既难读，`Track C` 还会被误读成"追踪 C"。

**解决**：机器码只留在 JSON 里供测试断言，展示层统一过 `reasonText()` / `shipmentIssueText()` /
`trackLabel()` 三个函数翻译；降级提示按 reason 分类给不同措辞（见 §4.3），
不再一律说"API 不可用"。同时给 AusPost 的英文事件描述建了一张映射表
（`Delivered`→已妥投、`Onboard for delivery`→派送中、`Item processed at sorting facility`→分拣中心处理完成…），
未收录的原文透出、不臆造翻译。

### 7.13 降级做了，但演示不出来

初版实现的 `get_settings()` 上有 `@lru_cache(maxsize=1)`，`.env` 在**模块导入时**
就被解析进 `_DOTENV_VALUES`。也就是说——把 `.env` 改名、页面刷新，**什么都不会变**，
必须重启后端。而"降级"是题目第 11 节明确要求的行为，这条路径却只能靠嘴说。

**解决**：`config.py` 改成 `.env` 指纹感知（`(mtime_ns, size)`，见 §4.3 的实测表）：

- `_refresh_dotenv()` 在每次 `env()` 调用前比对指纹，变了就重读并清空旧值；
- `get_settings()` 从 `lru_cache(maxsize=1)` 拆成 `_build_settings(dotenv_signature)`，
  指纹一变 key 就变，自动重建；
- 保留 `get_settings.cache_clear()` 这个既有接口（测试在用），
  但**故意不重置指纹**——否则测试 `monkeypatch` 成空的 `_DOTENV_VALUES` 会被文件内容重新灌回来。

改完之后：`mv .env .env.hidden` → 清缓存 → 刷新 → 页面变降级 → `mv` 回来 → 清缓存 → 刷新 → 恢复。
全程不重启服务。

---

## 8. 目录结构

```
aeris/
├─ backend/
│  ├─ app/
│  │  ├─ main.py                FastAPI 入口（CORS / 静态托管 / health）
│  │  ├─ config.py              .env 读取（无第三方依赖，凭证只在这里出现）
│  │  ├─ deps.py                依赖注入单例
│  │  ├─ data_loader.py         JSON 数据层（mtime 感知缓存）+ SKU 目录
│  │  ├─ services/
│  │  │  ├─ money.py            Decimal + ROUND_HALF_UP（禁用 round()）
│  │  │  ├─ pricing.py          纯金额计算（不认识 SKU / 订单号）
│  │  │  ├─ auspost.py          partner 客户端：track / prices / address + 解析器
│  │  │  ├─ shipment.py         起运地解析、分组、报价、轨迹
│  │  │  ├─ orders.py           组装订单视图
│  │  │  └─ cache.py            TTL 缓存
│  │  └─ routers/orders.py      GET /api/orders、/api/testcases（默认关闭）等
│  ├─ tests/                    23 条单测
│  ├─ pytest.ini
│  └─ requirements.txt
├─ frontend/
│  ├─ index.html                订单页（生产入口）
│  ├─ tests.html                边界用例测试页（接口开启后才有数据）
│  ├─ vite.config.js            proxy /api -> 127.0.0.1:8000 + 双入口构建
│  └─ src/
│     ├─ api.js                 只打本地后端
│     ├─ format.js
│     ├─ App.vue                订单页
│     ├─ TestCases.vue          测试页：断言表 + 降级原因对照 + 边界订单
│     └─ components/
│        ├─ OrderCard.vue       订单头 + 收货信息 + SKU 表 + 汇总 + 轨迹
│        ├─ SkuLine.vue         SKU 行 + 图片占位区
│        ├─ SummaryPanel.vue    Subtotal / GST / Shipment Fee / Total
│        └─ TrackingPanel.vue   轨迹 + 明确的 API 不可用态
├─ data/
│  ├─ orders.json               题目给出的订单（输入）
│  ├─ orders_generated.json     自造的 3 笔测试订单（直接进首页）
│  ├─ testcases_edge.json       故意造坏的边界数据集（只进测试链路）
│  └─ sku_catalog.json 等       698 条 SKU 目录与取数过程的派生数据
├─ docs/
│  ├─ HANDOFF.md                取数实测记录与实现交接（接口细节、字段名、坑）
│  └─ INTERVIEW.md              10 分钟讲解稿与追问应答
├─ .env.example                 只有变量名，无值
├─ .gitignore                   含 .env
└─ README.md
```



---

## 9. 已明确未做（含原因）

| 项 | 状态 |
|---|---|
| TNT 原生 RTT（XML） | 未实现（bonus）。该单号改用 AusPost 轨迹接口兜底，实测可用 |
| 真实装箱算法 | 未做，用 §6 的保守假设并在 UI 里标注实际策略 |
| 下单 / 打面单（`POST /shipments`、`POST /labels`） | 未做，题目不要求；端点清单见 `data/postman/ap.zip` |
| SQLite / 任何数据库 | 未引入，数据层就是 JSON |
| 商品图片 | 数据源无图片字段，统一占位图 |

## 10. 待确认清单：上线前必须先问清的问题

§7 记的是已经解决的问题。这一节是**另一类问题**——歧义来自业务规则或上游数据本身，
代码再怎么写也只是"选一种假设跑起来"。每条都写三件事：歧义在哪、本项目当前的假设、由谁拍板。
这些问题的共同特点是：**改了口径代码不会报错，只是算出来的钱不一样**，所以必须落在书面上。

### 10.1 字段定义：同一个名字，两边理解不一样

| 字段 | 歧义 | 本项目当前假设 | 该问谁 |
|---|---|---|---|
| `rrp` | 含不含 GST？澳洲零售标价惯例含税，但源表没注明 | **含 GST**，Ex-GST 单价 = `rrp / 1.1`（§3） | 财务 / 商品部 |
| `size`（如 `"10"`） | 单位不明：克数、毫升，还是包装规格？ | 仅作旁证，不参与计算 | 商品部 |
| `weight_g` | 是**单件**还是**整箱** | 单件；运费按 `weight_g × qty` 累加 | 商品部 / 仓储 |
| `qty` | 单位是瓶 / 盒 / 箱？目录尺寸对应哪一层包装 | 一件 = 目录一行 | 仓储 |
| SKU 大小写 | `TBAMET10` 与 `tbamet10` 是同一个 SKU 吗 | 统一 `upper() + strip()`，**视为同一个** | 数据提供方 |
| `status` | 枚举还是自由文本（现见 `Completed` / `Processing`） | 原样展示，不做状态机 | 运营 |
| `address` vs `suburb/state/postcode` | 两者同时存在，冲突时听谁的 | 报价只用分列字段，`address` 仅展示 | 数据提供方 |
| `tracking` | 层级：订单级字典 + 行级引用 | 一个行一个单号，一个订单可多票货 | 运营 |

补充一条流程约定：新增字段不用改代码（数据驱动，§2.6），但**改字段含义**必须走一次变更评审——
因为含义变了程序不会报错，只是静静地算错。同理，`weight_g` 若实际记的是整箱重量，运费会被放大 N 倍，
而所有单测依然会通过。

### 10.2 需求边界：题目没写、真上线一定会问

- **运费是"估算"还是"结算价"**：当前直接展示 partner 报价。真上线需要标注"以承运商账单为准"，
  并明确报价与实际扣费出现差价时谁承担。
- **缺 SKU 的行怎么处置**：整单作废，还是跳过该行？当前选后者——该行标红不计价，其余行照算（§7.8）。
  这是业务策略，不是技术选择。
- **一单多票货**：按运单分组分别报价再求和（当前），还是合并成一票？合并通常更便宜，金额会变。
- **界面字段与顺序**：题目 mock-up 只给大致布局。客户姓名 / 电话 / 邮箱是否展示（见 §11）、
  是否显示承运商原始状态码，都需要产品定。
- **量级与 SLA**：当前同步调用 + 15 分钟 TTL 缓存，够展示用。若要支撑每天几千单或批量导出，
  需要任务队列与持久化（§9 明确未做）。
- **是否需要写操作**：现在只读。下单 / 打面单 / 取消运单是否在同一期？

### 10.3 计算方式：口径要写死，不能靠"看起来对"

- **舍入顺序**（§3 实测差 0.04 / 0.01）：单价先舍入 / 行合计后舍入 / 全单最后舍入，三种都自洽但结果不同。
  必须由财务指定一种并写进文档。
- **GST 基数**：只对商品（当前，严格照题目公式）还是商品 + 运费。§3 给出了另一种写法及放弃理由，
  若财务要求运费税单列，页面就要多一行。
- **成交价 vs 目录价**：目录只有 `rrp`，没有合同价 / 批发价 / 促销价。真实业务一旦引入，
  需要新增订单行价格字段，并明确优先级（订单行价优先，目录价兜底）与缺失时的处理。
- **运费附加项**：包装皮重、燃油附加费、保险、偏远地区附加费——当前都没算。
  要确认 partner 返回的 `total_cost` 已含哪些（testbed 的响应里没有逐项拆解）。
- **装箱**：当前是保守的体积法近似（30×20×H，§6），不是真实装箱。实际承运商有单边上限与体积重换算，
  换成真实算法会影响每一单的运费。
- **负数与退货**：当前负数量判为非法并标红。若存在退货 / 冲销单，负数就是合法的，需要独立的状态与规则。
- **税率与币种**：`GST_RATE` 与币种已做成配置项，但业务规则未定义——出口订单 GST 为 0 怎么判定、
  跨币种结算按哪天的汇率，都需要明确。

### 10.4 错误提示：给谁看、看到什么程度

- **受众是谁**：运营（要能自己修数据）、客服（要能答复客户）、客户（只该看到"物流信息暂未更新"）。
  当前页面是运营视角，原因写得具体（"缺少 suburb/state/postcode"）；给客户的版本必须降级措辞。
- **机器码 vs 人话**：已分层——JSON / API 保留 `reason_code`（`DESTINATION_INCOMPLETE`）供排查与断言，
  展示层统一翻译（§7.12）。这条要作为规范固定下来，避免哪天图省事把机器码直接渲染出去。
- **partner 原始错误不外泄**：HTTP 状态与响应体只进后端日志（`auspost.py` 里的 `logger.warning`），
  响应里只保留 `reason_code` 与翻译后的中文提示，页面只说"暂时不可用"。
  凭证、内部 URL、上游堆栈一律不透出。
- **错误分级**：行级（某 SKU 缺匹配 → 该行不计价，其余照算）/ 单级（缺收货地 → 整单运费 0 并标注）/
  系统级（API 全挂 → 全局降级）。三级的视觉权重、以及是否阻断后续流程，需要产品定义。
- **可操作性**：当前提示只描述原因（"缺邮编"），没说下一步。正式文案建议补上动作（去哪里补、找谁）。
- **可观测性**：错误目前只体现在页面上。生产需要结构化日志与告警（如连续 N 次降级触发通知），当前未做。

### 10.5 多语言：不只是"翻译一遍"

- **当前状态**：界面中文；AusPost 的事件描述是英文（`Delivered`、`Onboard for delivery`），
  经映射表转中文；**未收录的原文直接透出，不臆造翻译**（§7.12）。
- **目标语言要确认**：业务在澳洲、运营可能用英文，中文界面是交付要求还是真实需求？
  两种都要的话文案必须外置成 i18n 资源，当前是硬编码在 `format.js` 里。
- **术语表**：Ex-GST（不含税）、Consignment（运单）、Article（包裹）、Subtotal（小计）需要一份中英对照，
  同一术语不能一处叫"运单"一处叫"快递单"（§7.12 已修过 `Track C` 被读成"追踪 C"的问题）。
- **本地化格式**：金额符号 `A$` 还是 `$`（澳洲本地用 `$`，与其他澳元场景混淆时才加前缀）、千分位、
  日期格式（澳洲 `DD/MM/YYYY`，数据里的 `order_date` 正是这个格式）。
- **维护流程**：承运商会改事件文案，映射表由谁维护？出现未收录文案是告警还是静默透出？

### 10.6 时区：最容易被忽略，出错又最难查

数据里**两种时间混在一起**：

| 来源 | 例子 | 时区信息 |
|---|---|---|
| 订单 `order_date` | `30/11/25` | **无**（澳洲 `DD/MM/YY` 格式） |
| 轨迹事件 `date` | `2025-02-13T11:49:59+11:00` | **带偏移**（AEDT 夏令时） |

- **当前做法：原样保留字符串，不做任何转换**（`formatDateTime` 保留原始时区）。这是有意的——
  规则没定之前，任何"顺手转一下"都是把错误藏进数据里。
- **需要确认**：
  - 显示基准：客户所在地 / 公司所在地（NSW）/ UTC？澳洲本土就有 AEST(+10)、AEDT(+11)、
    ACST(+9:30)、AWST(+8)，Perth 与 Sydney 的订单摆在同一页，不转换会直接误导。
  - 夏令时：同一地点一年里偏移会变；看历史订单时用**当时**的偏移还是现在的？
  - 前端陷阱：`new Date("30/11/25")` 按浏览器本地时区解析——同一份数据在中国和澳洲的机器上
    会显示成不同时刻。要显示就必须带时区，或统一用 UTC 存储。
  - 业务判定依赖时区：SLA（"24 小时未揽收"）、"今日订单"、"当日发货截止时间"都必须先定基准时区，
    差一小时就会误判。
- **建议（未实施）**：存储统一 UTC（ISO 8601 带 `Z`），展示层按用户时区渲染；
  纯日期字段（订单日期）与时刻字段（事件时间）在模型里分开，不要都叫 `date`。

### 10.7 数据冲突：以哪个为准

| 冲突场景 | 当前取舍 | 待确认 |
|---|---|---|
| 订单收货地址 vs 轨迹里的地点 | 以订单为准（合同依据），轨迹地点只展示，差异不提示 | 要不要提示差异 |
| 目录价格 / 重量 vs 订单行自带价格 | 订单只有 `sku`+`qty`，只能取目录 | 将来若订单带成交价：**建议以订单行价为准，目录作兜底与校验** |
| 订单 `status=Completed` vs 轨迹仍在途 | 两者**故意不合并**，都展示（§2.7） | 自动化动作以哪个为准（如"已完成的单不再查轨迹"） |
| `total_cost` vs `total_cost_ex_gst × 1.1` 对不上 | 以 `total_cost`（承运商实际收取）为准，差额不调整（§3） | 分位差额如何入账 |
| `origin` 写 `RYDE 2111` 但 partner 校验不过 | **自动改写**成能通过的 `GLADESVILLE 2111`（§7.1），响应里保留实际起运地 | 这种"自动修正"是否被允许、要不要提示 |
| 同一运单号出现在多笔订单 | 每笔独立报价，可能重复计费 | 是否跨单去重 |
| 缓存值 vs partner 实时值 | 15 分钟 TTL，期间以缓存为准（§4，故意避免打爆 testbed） | 可接受的陈旧度；轨迹与报价可能不同（轨迹要新鲜，报价可旧） |
| testbed 对任意单号都返回样例轨迹（§7.11） | 不把 testbed 返回值当生产真值 | testbed 与生产的行为差异清单 |

**建议写进团队规范的四条原则**：

1. **合同性字段**（SKU、数量、价格、收货地址）以**订单**为准；
2. **事实性字段**（轨迹、报价、地址校验）以 **partner 实时返回**为准；
3. **派生字段**（Ex-GST 单价、行小计、GST、Total）**永不落库**，只由规则现算，
   否则迟早出现两份互相矛盾的数字；
4. 冲突要**记录且可见**，不要静默覆盖——静默覆盖的那天，就是账对不上的那天。

以上任何一条有了答案，改动量都很小（口径集中在 `pricing.py` / `money.py` / `shipment.py`，
参数集中在 `.env`）；难的是把口径本身定下来。

---

## 11. 提交到 GitHub 之前的自检

### 11.1 秘密（credential）

仓库里**不应该出现**的东西：AusPost API Key / Password、账号号、partner 基地址。
这几项在本仓库的状态：

| 位置 | 状态 |
|---|---|
| `.env`（本机真实值） | 已被 `.gitignore` 排除，**不入库** |
| `.env.example` | 只保留变量名，值全部留空 |
| README / `docs/` | 只写变量名与占位符（`<...>`），不写真值 |
| `backend/app/config.py` | `AUSPOST_BASE_URL` 有代码默认值（AusPost 公开的 testbed 地址），凭证本身无默认值 |
| `frontend/` | 不接触任何凭证，只打本地 `/api` |

提交前跑一遍自检（两条都应无输出）：

```bash
# 1) 任何"变量名 = 非空值"（.env 之外的文件里都不该有）
grep -rnE "AUSPOST_(API_KEY|API_PASSWORD|ACCOUNT_NUMBER)=\s*\S" \
  . --exclude-dir=node_modules --exclude-dir=dist --exclude-dir=.git --exclude-dir=.chrome-shot --exclude=.env

# 2) partner 基地址：仓库里只允许 config.py 的默认值这一处
grep -rn "digitalapi.auspost" backend frontend data tools .env.example \
  --exclude-dir=node_modules --exclude-dir=__pycache__ --exclude=config.py
```

### 11.2 个人信息（PII）

题目给的 `data/orders.json` 原本含真实客户姓名 / 邮箱 / 手机号。**已做脱敏**，仓库里不再有真实个人信息：

| 字段 | 处理 |
|---|---|
| `customer` / `phone` / `email` | 换成虚构值（邮箱用保留域名 `example.com.au`） |
| `company` 与 `address` 的街道部分 | 一并换成虚构公司名与街道——公司 + 地址同样可定位到具体客户 |
| `suburb` / `state` / `postcode` / `country` | **保留原值**：运费报价依赖目的地，且是区域级信息，不是个人标识 |
| `order_no` / `items` / `tracking` | 原样保留，金额口径不受影响（脱敏后 23 条单测仍全过） |

`orders_generated.json` 与 `testcases_edge.json` 本来就是虚构数据。
脱敏只换值、不动字段名与结构（数据驱动，§2.6），程序照跑。
