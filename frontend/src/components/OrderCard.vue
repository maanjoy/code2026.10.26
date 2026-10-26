<script setup>
import { computed } from 'vue'
import SkuLine from './SkuLine.vue'
import SummaryPanel from './SummaryPanel.vue'
import TrackingPanel from './TrackingPanel.vue'

const props = defineProps({
  order: { type: Object, required: true }
})

const STATUS = {
  Completed: { cls: 'pill--ok', label: '已完成' },
  Delivered: { cls: 'pill--ok', label: '已送达' },
  'In Transit': { cls: 'pill--info', label: '运输中' },
  Draft: { cls: 'pill--warn', label: '草稿' },
  'Awaiting Tracking': { cls: 'pill--warn', label: '待分配运单' },
  'Missing Destination': { cls: 'pill--danger', label: '收货地缺失' }
}

const status = computed(
  () => STATUS[props.order.status] || { cls: 'pill--ghost', label: props.order.status || '未知状态' }
)

const party = computed(() => props.order.party || {})
const delivery = computed(() => props.order.delivery || {})
// 只有「故意造坏」的边界数据集才打这个标记（后端按 TEST_DATASET_PATTERN 判定）
const isTest = computed(() => Boolean(props.order.is_test_case))
</script>

<template>
  <article class="order">
    <header class="order-bar">
      <div>
        <h2>订单 {{ order.order_no || '（缺少订单号）' }}</h2>
        <p class="sub">
          {{ order.order_date || '日期未提供' }}
          <template v-if="party.company"> · {{ party.company }}</template>
          <template v-if="party.customer"> · {{ party.customer }}</template>
          <template v-if="isTest"> · 边界测试数据（虚构）</template>
        </p>
      </div>
      <span class="pill pill--light">{{ status.label }}</span>
    </header>

    <div class="order-body">
      <!-- 左栏：条目 + 汇总。右栏：地址 + 追踪（顺序对齐题目第 10 节 mock-up） -->
      <div class="col">
        <section class="panel">
          <h3>订单条目</h3>
          <SkuLine v-for="(item, index) in order.items" :key="`${item.sku}-${index}`" :item="item" :index="index" />

          <p v-if="!order.items?.length" class="sum-note">该订单没有行项目。</p>

          <SummaryPanel
            :money="order.money"
            :shipment-mode="order.shipment_mode"
            :shipment-issues="order.shipment_issues"
          />
        </section>
      </div>

      <!-- 右栏：送货地址 + 按 Track 分组的追踪 -->
      <div class="col">
        <section class="panel">
          <h3>送货地址</h3>
          <div class="addr-name">{{ party.customer || '（无联系人）' }}</div>
          <div v-if="party.company" class="addr-line">{{ party.company }}</div>
          <div class="addr-line">{{ delivery.address || '（无详细地址）' }}</div>
          <div class="addr-line">
            {{ [delivery.suburb, delivery.state, delivery.postcode].filter(Boolean).join(' ') || '（缺少 suburb / state / postcode）' }}
            {{ delivery.country }}
          </div>
          <div v-if="party.phone || party.email" class="addr-line muted" style="margin-top: 6px">
            {{ party.phone }}<template v-if="party.phone && party.email"> · </template>{{ party.email }}
          </div>
        </section>

        <TrackingPanel v-for="shipment in order.shipments" :key="shipment.track_label" :shipment="shipment" />
        <section v-if="!order.shipments?.length" class="panel">
          <h3>物流追踪</h3>
          <div class="note warn">该订单未分配到任何运单分组。</div>
        </section>
      </div>
    </div>

  </article>
</template>
