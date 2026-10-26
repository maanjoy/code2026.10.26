<script setup>
import { computed } from 'vue'
import { issueText, moneyCell } from '../format.js'

const props = defineProps({
  item: { type: Object, required: true },
  index: { type: Number, default: 0 }
})

// 占位图：题目第 5 节允许用中性药瓶图标；四款配色按行序轮换，视觉上不至于全一样
const thumb = computed(() => `/img/bottle-${(props.index % 4) + 1}.svg`)
</script>

<template>
  <div class="p-item">
    <div class="p-thumb">
      <img :src="thumb" :alt="`${item.name || item.sku} 的产品占位图`" loading="lazy" />
    </div>

    <div>
      <div class="p-name">{{ item.name || '未匹配到产品名称' }}</div>
      <span class="p-sku">{{ item.sku || '（空 SKU）' }}</span>

      <div class="p-meta">
        <div>RRP <b>{{ moneyCell(item.rrp_incl_gst) }}</b>（含 GST）</div>
        <div>
          不含税单价 <b>{{ moneyCell(item.unit_price_ex_gst) }}</b> × {{ item.qty ?? '—' }}
        </div>
        <div>行小计 <b>{{ moneyCell(item.line_total_ex_gst) }}</b>（不含 GST）</div>
      </div>

      <div v-if="item.issues?.length" class="p-warn">
        未计入金额：<span v-for="(issue, i) in item.issues" :key="issue">{{ i ? '、' : '' }}{{ issueText(issue) }}</span>
      </div>
    </div>
  </div>
</template>
