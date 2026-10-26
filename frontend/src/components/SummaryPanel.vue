<script setup>
import { computed } from 'vue'
import { moneyCell, reasonCodeOf, shipmentIssueText } from '../format.js'

const props = defineProps({
  money: { type: Object, required: true },
  shipmentMode: { type: String, default: 'degraded' },
  shipmentIssues: { type: Array, default: () => [] }
})

// 四行口径严格按题目第 4 节：GST 只对商品小计计征，运费按承运商报价单列
const modeBadge = computed(() => {
  if (props.shipmentMode === 'live') return { cls: 'pill--ok', label: '运费已实时报价' }
  if (props.shipmentMode === 'partial') return { cls: 'pill--warn', label: '部分运费已报价' }
  return { cls: 'pill--danger', label: '运费不可用' }
})

const issuesText = computed(() => (props.shipmentIssues || []).map(shipmentIssueText))

const firstReason = computed(() => reasonCodeOf((props.shipmentIssues || [])[0]))

// 降级提示要说出**具体**原因，不能一律归成「API 不可用」——
// 地址缺失是数据问题，超时才是接口问题，两种情况对使用者的下一步动作完全不同。
const feeHint = computed(() => {
  if (props.shipmentMode === 'live') return 'AusPost 实时报价（含 GST）'
  const tail = '已按 A$0.00 计'
  if (props.shipmentMode === 'partial') return `部分票件未取得报价，${tail}`
  const byReason = {
    DESTINATION_INCOMPLETE: `收货地信息不完整，无法向 AusPost 报价，${tail}`,
    NO_PRICEABLE_ITEMS: `没有可计价的商品行，${tail}`,
    CREDENTIALS_MISSING: `未配置 AusPost 凭证，${tail}`,
    TIMEOUT: `承运商接口超时，${tail}`,
    TRANSPORT_ERROR: `承运商接口网络不可达，${tail}`
  }
  return byReason[firstReason.value] || `承运商接口不可用，${tail}`
})
</script>

<template>
  <div class="sum">
    <div class="sum-row">
      <span class="k">小计（不含 GST）</span>
      <span class="v">{{ moneyCell(money.subtotal_ex_gst) }}</span>
    </div>
    <div class="sum-row">
      <span class="k">GST（10%）</span>
      <span class="v">{{ moneyCell(money.gst) }}</span>
    </div>
    <div class="sum-row">
      <span class="k">运费</span>
      <span class="v">{{ moneyCell(money.shipment_fee) }}</span>
    </div>
    <div class="sum-row total">
      <span class="k">总计</span>
      <span class="v">{{ moneyCell(money.total) }} 澳元</span>
    </div>

    <p class="sum-note">{{ feeHint }}</p>

    <p v-if="issuesText.length" class="note warn" style="margin-top: 8px">
      <b>运费降级原因：</b>{{ issuesText.join('；') }}
    </p>
  </div>
</template>
