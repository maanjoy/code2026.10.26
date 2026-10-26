<script setup>
import { onMounted, ref } from 'vue'
import { fetchOrders } from './api.js'
import OrderCard from './components/OrderCard.vue'

const orders = ref([])
const loading = ref(true)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const orderPayload = await fetchOrders()
    orders.value = orderPayload.orders
  } catch (err) {
    error.value = err.message || String(err)
  } finally {
    loading.value = false
  }
}

onMounted(() => load())
</script>

<template>
  <div class="app">
    <header class="page-head">
      <h1>订单详情 · 物流追踪 · 运费估算</h1>
    </header>

    <div v-if="error" class="state-box error">后端请求失败：{{ error }}</div>
    <div v-else-if="loading" class="state-box">正在从后端加载订单与实时运费…</div>

    <template v-else>
      <OrderCard v-for="order in orders" :key="order.order_no" :order="order" />
    </template>
  </div>
</template>
