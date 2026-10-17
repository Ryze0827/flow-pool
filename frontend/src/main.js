import { createApp } from 'vue'
import Antd from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import App from './App.vue'
import './style.css'

// 在挂载和异步加载数据前关闭恢复，避免移动端刷新后跳回旧位置。
if (window.location.pathname === '/usage') {
  window.history.scrollRestoration = 'manual'
  window.scrollTo({ top: 0, left: 0, behavior: 'instant' })
}

createApp(App).use(Antd).mount('#app')
