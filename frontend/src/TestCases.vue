<script setup>
/**
 * 边界用例测试页（/tests.html）。
 *
 * 这里展示的数据**不在生产链路上**：后端默认只加载 orders.json，
 * 本页读的是 TEST_DATASET_PATTERN 指向的边界数据集，且接口默认关闭。
 * 页面只做「取数 + 断言展示」，所有金额与判定都由后端给的结果算出，不另写一套规则。
 */
import { computed, onMounted, ref } from 'vue'
import { fetchTestCases } from './api.js'
import OrderCard from './components/OrderCard.vue'
import { moneyCell, reasonText, shipmentIssueText, trackLabel } from './format.js'

const orders = ref([])
const meta = ref(null)
const loading = ref(true)
const error = ref('')
const disabled = ref(false)

const MODE_LABELS = { live: '实时报价', degraded: '降级', partial: '部分成功' }

async function load() {
  loading.value = true
  error.value = ''
  disabled.value = false
  try {
    const payload = await fetchTestCases()
    orders.value = payload.orders
    meta.value = payload.meta
  } catch (err) {
    const text = String(err.message || err)
    // 404 = 后端没开 EXPOSE_TESTCASES，这是生产默认状态，要给出可操作的说明
    disabled.value = text.includes('404')
    error.value = text
  } finally {
    loading.value = false
  }
}

function amount(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number : 0
}

const rows = computed(() => orders.value.flatMap((order) => order.items || []))

const checks = computed(() => {
  const list = []
  const flagged = rows.value.filter((row) => (row.issues || []).length > 0)
  const priced = rows.value.filter((row) => row.priced)

  list.push({
    name: '异常数据被标记，而不是让流程崩溃',
    expected: '存在被标记的问题行，页面照常渲染',
    actual: flagged.length ? `${flagged.length} 行被标记（${flagged.map((r) => r.sku || '<空 SKU>').join('、')}）` : '没有命中任何问题行',
    pass: flagged.length > 0
  })

  const badPriced = flagged.filter((row) => row.priced)
  list.push({
    name: '有问题行不计入金额',
    expected: '被标记的行没有 Ex-GST 行小计',
    actual: badPriced.length ? `${badPriced.length} 行被错误计价` : '全部未计价',
    pass: badPriced.length === 0
  })

  const subtotalOk = orders.value.every((order) => {
    const sum = priced
      .filter((row) => (order.items || []).includes(row))
      .reduce((acc, row) => acc + amount(row.line_total_ex_gst), 0)
    return Math.abs(sum - amount(order.money.subtotal_ex_gst)) < 0.005
  })
  list.push({
    name: '小计 = 可计价行之和',
    expected: '每行 Ex-GST 小计相加等于订单 Subtotal',
    actual: subtotalOk ? '全部订单一致' : '存在对不上的订单',
    pass: subtotalOk
  })

  const gstOk = orders.value.every((order) => {
    const rate = Number(order.money.gst_rate || 0.1)
    const expected = amount(order.money.subtotal_ex_gst) * rate
    return Math.abs(expected - amount(order.money.gst)) < 0.005
  })
  list.push({
    name: 'GST = Subtotal × 税率（不对运费计税）',
    expected: 'GST 行严格等于商品小计 × GST_RATE',
    actual: gstOk ? '全部订单吻合' : '存在偏差',
    pass: gstOk
  })

  const degraded = orders.value.filter((order) => order.shipment_mode !== 'live')
  const degradedOk = degraded.every(
    (order) => amount(order.money.shipment_fee) === 0 && (order.shipment_issues || []).length > 0
  )
  list.push({
    name: '降级订单：运费不为假值，且原因可见',
    expected: '运费计 0.00，同时给出可读的降级原因',
    actual: degraded.length ? `${degraded.length} 单降级，原因均可见` : '本次没有降级订单',
    pass: degradedOk
  })

  const trackings = orders.value.flatMap((order) => order.shipments || [])
  const emptyTracks = trackings.filter((shipment) => (shipment.tracking?.event_count ?? 0) === 0)
  list.push({
    name: '查不到轨迹的运单是空态而不是报错',
    expected: 'event_count = 0 时接口仍返回该运单，并附可读说明',
    actual: emptyTracks.length
      ? `${emptyTracks.length} 个运单走空态分支：${emptyTracks
          .map((s) => `${trackLabel(s.track_label)}${s.tracking_id ? ` ${s.tracking_id}` : '，未分配单号'}`)
          .join('；')}`
      : '所有运单都有事件',
    pass: emptyTracks.every((shipment) => Boolean(shipment.tracking?.message))
  })

  const totalOk = orders.value.every((order) => {
    const expected =
      amount(order.money.subtotal_ex_gst) + amount(order.money.gst) + amount(order.money.shipment_fee)
    return Math.abs(expected - amount(order.money.total)) < 0.005
  })
  list.push({
    name: 'Total = Subtotal + GST + 运费',
    expected: '四行金额自洽',
    actual: totalOk ? '全部订单自洽' : '存在对不上的订单',
    pass: totalOk
  })

  return list
})

const passedCount = computed(() => checks.value.filter((check) => check.pass).length)

/**
 * 后端拼的说明形如「Track C：DESTINATION_INCOMPLETE - 订单缺少 suburb/state/postcode…」。
 * 用**原始文案**拆解（先翻译会把机器码换掉，就没法再拿码查表了），再逐段翻成中文。
 */
const reasons = computed(() => {
  const seen = new Map()
  for (const order of orders.value) {
    for (const raw of order.shipment_issues || []) {
      const match = String(raw).match(/^(Track\s+\S+?)：([A-Z][A-Z0-9_]{3,})(?:\s*-\s*(.*))?$/)
      const track = match ? trackLabel(match[1]) : ''
      const code = match ? match[2] : String(raw).match(/\b([A-Z][A-Z0-9_]{3,})\b/)?.[1] || ''
      const detail = match ? match[3] || '' : shipmentIssueText(raw)
      const key = `${track}-${code}-${detail}`
      if (!seen.has(key)) seen.set(key, { track, code, labelText: reasonText(code), detail })
    }
  }
  return [...seen.values()]
})

onMounted(() => load())
</script>

<template>
  <div class="app">
    <header class="page-head">
      <div>
        <h1>边界用例 · 测试页</h1>
        <p class="lede">
          非生产数据。这一页读的是边界数据集（{{ meta?.datasets?.join('、') || 'testcases_edge.json' }}），
          主页面与 <code>/api/orders</code> 都不包含它。
        </p>
        <p class="lede">
          数据源：SKU 目录 {{ meta?.sku_count ?? '—' }} 条 · GST {{ meta?.gst_rate ?? '0.10' }} · 币种
          {{ meta?.currency ?? 'AUD' }} · 运费与轨迹来自 AusPost testbed 实时接口
        </p>
      </div>
      <a class="back-link" href="/">← 回到订单页</a>
    </header>

    <div v-if="loading" class="state-box">正在加载边界用例…</div>

    <div v-else-if="disabled" class="state-box warn">
      <p><strong>边界用例接口当前未开启（生产默认关闭）。</strong></p>
      <p class="muted">
        在 <code>.env</code> 里设置 <code>EXPOSE_TESTCASES=true</code>，后端按 mtime 自动重载，无需重启。
        生产部署保持 <code>false</code>：<code>/api/testcases</code> 返回 404，订单页也不显示这一页的入口。
      </p>
      <p class="muted">离线回归同样覆盖这些用例：<code>pytest</code>（见 <code>backend/tests/</code>）。</p>
    </div>

    <div v-else-if="error" class="state-box error">请求失败：{{ error }}</div>

    <template v-else>
      <section class="panel">
        <h3>
          断言检查
          <span class="pill pill--light">{{ passedCount }} / {{ checks.length }} 通过</span>
        </h3>
        <table class="checks">
          <thead>
            <tr><th>检查项</th><th>预期</th><th>实际</th><th>结果</th></tr>
          </thead>
          <tbody>
            <tr v-for="check in checks" :key="check.name">
              <td>{{ check.name }}</td>
              <td class="muted">{{ check.expected }}</td>
              <td>{{ check.actual }}</td>
              <td>
                <span class="pill" :class="check.pass ? 'pill--ok' : 'pill--danger'">
                  {{ check.pass ? '通过' : '未通过' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="reasons.length" class="panel">
        <h3>降级原因（机器码 → 中文）</h3>
        <ul class="reason-list">
          <li v-for="reason in reasons" :key="`${reason.track}-${reason.code}-${reason.detail}`">
            <span v-if="reason.track">{{ reason.track }}：</span>
            <code>{{ reason.code }}</code> → {{ reason.labelText }}
            <span v-if="reason.detail" class="muted">· {{ reason.detail }}</span>
          </li>
        </ul>
      </section>

      <section class="panel">
        <h3>用例概览</h3>
        <table class="checks">
          <thead>
            <tr><th>订单号</th><th>数据问题</th><th>运费模式</th><th>Subtotal</th><th>GST</th><th>运费</th><th>Total</th></tr>
          </thead>
          <tbody>
            <tr v-for="order in orders" :key="order.order_no">
              <td>{{ order.order_no }}</td>
              <td>{{ (order.data_issues || []).length }} 条</td>
              <td>{{ MODE_LABELS[order.shipment_mode] || order.shipment_mode }}</td>
              <td>{{ moneyCell(order.money.subtotal_ex_gst) }}</td>
              <td>{{ moneyCell(order.money.gst) }}</td>
              <td>{{ moneyCell(order.money.shipment_fee) }}</td>
              <td>{{ moneyCell(order.money.total) }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <OrderCard v-for="order in orders" :key="order.order_no" :order="order" />
    </template>
  </div>
</template>
