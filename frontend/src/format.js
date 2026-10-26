/** 展示层格式化工具（金额一律用后端给的字符串，前端不再做二次计算）。 */

const FIELD_LABELS = {
  order_no: '订单号',
  order_date: '订单日期',
  status: '状态',
  company: '公司',
  customer: '客户',
  contact: '联系人',
  phone: '电话',
  email: '邮箱',
  address: '收货地址',
  suburb: 'Suburb',
  state: 'State',
  postcode: 'Postcode',
  country: 'Country'
}

export function labelOf(key) {
  return FIELD_LABELS[key] || key
}

export function moneyText(value) {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(2) : '—'
}

export function moneyCell(value) {
  const text = moneyText(value)
  return text === '—' ? '—' : `A$${text}`
}

const ISSUE_LABELS = {
  SKU_NOT_FOUND: 'SKU 未在目录中匹配',
  QTY_INVALID: '数量非法，未计入金额',
  QTY_MISSING: '缺少数量',
  RRP_MISSING: '缺少 RRP',
  DESTINATION_INCOMPLETE: '收货地信息不完整'
}

export function issueText(issue) {
  if (typeof issue !== 'string') return String(issue)
  if (ISSUE_LABELS[issue]) return ISSUE_LABELS[issue]
  // 带参数的问题码先处理（否则后面按 token 替换会把冒号后的值也弄乱）
  const withArgs = issue.replace(
    /SKIPPED_WITHOUT_DIMENSIONS:(\S+)/g,
    (_, list) => `缺少尺寸/重量、未参与报价：${list}`
  )
  // 后端有时会把问题码拼在业务键后面（如 "TBAMET10：QTY_INVALID"），逐个替换成人话
  return Object.entries(ISSUE_LABELS).reduce(
    (text, [code, label]) => text.replaceAll(code, label),
    withArgs
  )
}

/**
 * 后端返回的 reason_code 是稳定的机器码（便于测试断言），展示层统一翻成人话。
 * 未收录的码原样透出，避免臆造含义。
 */
const REASON_LABELS = {
  OK: '正常',
  DESTINATION_INCOMPLETE: '收货地信息不完整',
  NO_TRACKING_NUMBER: '未分配运单号',
  NO_PRICEABLE_ITEMS: '没有可计价的商品行',
  NO_RESULT_FOR_ID: '该运单号查不到轨迹',
  CREDENTIALS_MISSING: '缺少 AusPost 凭证',
  TIMEOUT: '承运商接口超时',
  HTTP_ERROR: '承运商返回 HTTP 错误',
  API_RETURNED_ERRORS: '承运商返回业务错误',
  BAD_JSON_RESPONSE: '承运商响应无法解析',
  TRANSPORT_ERROR: '网络不可达',
  QUOTE_FAILED: '运费报价失败',
  UNEXPECTED_ERROR: '未预期的错误',
  UNKNOWN: '原因未知',
  UNASSIGNED: '未分配'
}

export function reasonText(code) {
  if (!code) return ''
  return REASON_LABELS[code] || String(code)
}

/** 后端拼的降级说明形如「Track C：DESTINATION_INCOMPLETE - 订单缺少 suburb…」，把码换成中文。 */
export function shipmentIssueText(text) {
  if (!text) return ''
  return String(text)
    .replace(/\bTrack\s+([A-Z0-9]+)\b/g, '运单 $1')
    .replace(/\b[A-Z][A-Z0-9_]{3,}\b/g, (code) => REASON_LABELS[code] || code)
}

/** "Track A" -> "运单 A"（避免中文语境里被读成「追踪 A」）。 */
export function trackLabel(label) {
  return String(label || '').replace(/^Track\s+/i, '运单 ')
}

/** 从一句降级说明里取出 reason_code，供上层决定提示措辞。 */
export function reasonCodeOf(text) {
  const match = String(text || '').match(/\b([A-Z][A-Z0-9_]{3,})\b/)
  return match ? match[1] : ''
}

/**
 * AusPost 返回的是带时区偏移的 ISO 字符串（如 2025-02-13T11:49:59+11:00）。
 * 这里**不做时区换算**，直接取事件所在地的本地时间，避免浏览器时区把时间改掉。
 */
export function formatDateTime(value) {
  if (!value) return '—'
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/)
  if (!match) return String(value)
  const [, y, mo, d, h, mi] = match
  return `${Number(y)}年${Number(mo)}月${Number(d)}日 ${h}:${mi}`
}

/** 事件排序用的时间戳（同样按事件自身时区比较，只用于排序先后）。 */
export function eventTimestamp(value) {
  const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):?(\d{2})?([+-]\d{2}:\d{2}|Z)?/)
  if (!match) return 0
  const [, y, mo, d, h, mi, s = '00', offset = 'Z'] = match
  const parsed = Date.parse(`${y}-${mo}-${d}T${h}:${mi}:${s}${offset}`)
  return Number.isNaN(parsed) ? 0 : parsed
}

const TRACKING_STATUS = [
  [/delivered/i, '已送达'],
  [/onboard|out for delivery/i, '派送中'],
  [/in transit|transit/i, '运输中'],
  [/awaiting|pending|received/i, '待揽收'],
  [/returned/i, '退货中'],
  [/cancelled/i, '已取消']
]

export function trackingStatusLabel(status) {
  if (!status) return ''
  const hit = TRACKING_STATUS.find(([pattern]) => pattern.test(status))
  return hit ? hit[1] : status
}

// AusPost 的事件描述是英文自由文本，常见几种给中文，其余原样保留（不臆造翻译）
const EVENT_DESCRIPTIONS = [
  [/shipping information (received|approved)/i, '已收到装运信息'],
  [/awaiting collection at (.+)/i, (m) => `待自提（${m[1]}）`],
  [/awaiting collection/i, '待自提'],
  [/onboard for delivery/i, '派送中'],
  [/item processed at sorting facility/i, '分拣中心处理完成'],
  [/processed at facility/i, '设施处理完成'],
  [/arrived at facility/i, '到达设施'],
  [/departed/i, '已发出'],
  [/received by/i, '已揽收'],
  [/in transit to next facility/i, '转运至下一设施'],
  [/transit to/i, '转运中'],
  [/item delivered|^delivered$/i, '已妥投'],
  [/delivery attempted/i, '派送未成功'],
  [/carded/i, '已留取件卡'],
  [/returned to sender/i, '退回寄件人'],
  [/delayed/i, '运输延误']
]

export function eventDescription(text) {
  if (!text) return '（无描述）'
  for (const [pattern, out] of EVENT_DESCRIPTIONS) {
    const match = String(text).match(pattern)
    if (match) return typeof out === 'function' ? out(match) : out
  }
  return String(text)
}
