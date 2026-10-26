/** 前端只跟自己的后端对话：partner API 的凭证永远不出现在这里。 */

const API_BASE = '/api'

async function get(path, params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== null)
  ).toString()
  const url = `${API_BASE}${path}${query ? `?${query}` : ''}`
  const response = await fetch(url)
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body.detail || detail
    } catch (error) {
      /* 保持默认状态码文案 */
    }
    throw new Error(`请求 ${url} 失败（${response.status}）：${detail}`)
  }
  return response.json()
}

export function fetchOrders() {
  return get('/orders')
}

/** 边界用例（测试页专用）。后端 EXPOSE_TESTCASES=false 时会返回 404。 */
export function fetchTestCases() {
  return get('/testcases')
}
