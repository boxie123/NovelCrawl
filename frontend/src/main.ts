import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import Sources from './pages/Sources.vue'
import SourceDetail from './pages/SourceDetail.vue'
import Tasks from './pages/Tasks.vue'
import Editions from './pages/Editions.vue'
import './style.css'
const router = createRouter({ history: createWebHashHistory(), scrollBehavior: () => ({top:0}), routes: [
  { path: '/', component: Sources },
  { path: '/sources/:id', component: SourceDetail },
  { path: '/tasks', component: Tasks },
  { path: '/editions', component: Editions },
  { path: '/editions/:id', component: Editions },
  { path: '/:pathMatch(.*)*', component: {template:'<section class="panel empty"><h1>页面不存在</h1><p>请从书库重新进入。</p><RouterLink to="/" class="button">返回书库</RouterLink></section>'} },
] })
createApp(App).use(router).mount('#app')
