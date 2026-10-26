<script setup>
import { computed } from 'vue'
import { eventDescription, eventTimestamp, formatDateTime, reasonText, trackLabel, trackingStatusLabel } from '../format.js'

const props = defineProps({
  shipment: { type: Object, required: true }
})

const tracking = computed(() => props.shipment.tracking || {})
const quote = computed(() => props.shipment.quote || {})

// AusPost 返回的事件大体是最新在前，但偶有并列时段顺序错乱，这里按事件自身时间统一降序
const events = computed(() => {
  if (tracking.value.mode !== 'live') return []
  return [...(tracking.value.events || [])].sort((a, b) => eventTimestamp(b.date) - eventTimestamp(a.date))
})
const visible = computed(() => events.value.slice(0, 5))
const rest = computed(() => events.value.slice(5))

const state = computed(() => {
  if (tracking.value.mode === 'live') {
    if (!tracking.value.event_count) return { cls: 'pill--info', label: '无轨迹事件' }
    return { cls: 'pill--ok', label: trackingStatusLabel(tracking.value.status) || '有轨迹更新' }
  }
  if (tracking.value.reason_code === 'NO_TRACKING_NUMBER') return { cls: 'pill--ghost', label: '未分配运单号' }
  return { cls: 'pill--danger', label: '轨迹不可用' }
})

const title = computed(() => {
  const courier = props.shipment.courier || ''
  if (/TNT/i.test(courier)) return 'TNT 追踪'
  if (/australia\s*post|startrack/i.test(courier)) return '澳大利亚邮政追踪'
  return '物流追踪'
})

const dest = computed(() => props.shipment.destination || {})

const place = computed(() => {
  const parts = [dest.value.suburb, dest.value.state, dest.value.postcode].filter(Boolean)
  return parts.length ? parts.join(' ') : '收货地信息缺失'
})
</script>

<template>
  <section class="panel">
    <div class="tk-head">
      <div>
        <h3 class="tk-title">{{ title }}</h3>
        <p class="tk-sub">
          运单号 {{ shipment.tracking_id || '—' }} · {{ trackLabel(shipment.track_label) }}
        </p>
      </div>
      <span class="pill" :class="state.cls">{{ state.label }}</span>
    </div>

    <!-- 轨迹正常 -->
    <template v-if="events.length">
      <div class="timeline">
        <div v-for="(event, index) in visible" :key="`${event.date}-${event.description}`" class="tl-item" :class="{ 'is-latest': index === 0 }">
          <div class="tl-title" :title="event.description">{{ eventDescription(event.description) }}</div>
          <div class="tl-meta">
            {{ formatDateTime(event.date) }}<template v-if="event.location"> · {{ event.location }}</template>
          </div>
        </div>
      </div>
      <details v-if="rest.length" class="tl-more">
        <summary>展开其余 {{ rest.length }} 条轨迹</summary>
        <div class="timeline" style="margin-top: 10px">
          <div v-for="event in rest" :key="`${event.date}-${event.description}`" class="tl-item">
            <div class="tl-title" :title="event.description">{{ eventDescription(event.description) }}</div>
            <div class="tl-meta">
              {{ formatDateTime(event.date) }}<template v-if="event.location"> · {{ event.location }}</template>
            </div>
          </div>
        </div>
      </details>
    </template>

    <!-- 轨迹不可用：明确状态，不编造 -->
    <div v-else class="note danger">
      <b>{{ reasonText(tracking.reason_code) || '轨迹不可用' }}</b>
      <template v-if="tracking.http_status"> · HTTP {{ tracking.http_status }}</template>
      <template v-if="tracking.message"> · {{ tracking.message }}</template>
      <template v-else> · 未取得该运单的轨迹信息</template>
    </div>

    <p class="sum-note" style="margin-top: 10px">
      收货地 {{ place }} · {{ shipment.item_count }} 件 · 关联订单 {{ (shipment.order_numbers || []).join('、') || '—' }}
      <template v-if="quote.mode === 'live'">
        <!-- 与汇总行的「运费」保持同一口径（含 GST），避免同页出现两个运价 -->
        · 本票运费 {{ quote.total_cost ? `A$${quote.total_cost}` : '—' }}（含 GST）
      </template>
    </p>

    <div v-if="quote.mode !== 'live'" class="note warn">
      <b>本票运费未计入：</b>{{ reasonText(quote.reason_code) || '未取得报价' }}
      <template v-if="quote.message"> · {{ quote.message }}</template>
    </div>
  </section>
</template>
