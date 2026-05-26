<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  NButton,
  NCheckbox,
  NCollapse,
  NCollapseItem,
  NConfigProvider,
  NEmpty,
  NGlobalStyle,
  NIcon,
  NInputNumber,
  NProgress,
  NSelect,
  NSlider,
  NSpin,
  NSwitch,
  NTag,
  NUpload,
  createDiscreteApi,
  lightTheme
} from 'naive-ui'
import {
  CloudUploadOutline,
  ColorPaletteOutline,
  FilmOutline,
  FlashOutline,
  ImagesOutline,
  OpenOutline,
  OptionsOutline,
  PlayCircleOutline,
  RefreshOutline,
  ResizeOutline,
  SpeedometerOutline,
  StopCircleOutline
} from '@vicons/ionicons5'

const themeOverrides = {
  common: {
    primaryColor: '#007AFF',
    primaryColorHover: '#268BFF',
    primaryColorPressed: '#0066D6',
    primaryColorSuppl: '#007AFF',
    infoColor: '#007AFF',
    successColor: '#34C759',
    errorColor: '#FF3B30',
    borderRadius: '12px',
    bodyColor: '#F5F5F7',
    cardColor: '#FFFFFF',
    modalColor: '#FFFFFF',
    popoverColor: '#FFFFFF',
    textColorBase: '#1D1D1F'
  },
  Button: { borderRadiusMedium: '11px' },
  Input: { borderRadius: '10px' }
}

const { message } = createDiscreteApi(['message'], {
  configProviderProps: { theme: lightTheme, themeOverrides }
})

const formatCards = [
  { key: 'avif', label: 'AVIF', sub: '暂不可用', desc: '动态格式兼容性有限，暂不开放输出。', tone: 'disabled', disabled: true },
  { key: 'webp', label: 'WebP', sub: '均衡推荐', desc: '网页与分享场景兼容性和画质兼顾。', tone: 'blue' },
  { key: 'apng', label: 'APNG', sub: '高保真', desc: '适合透明与图形动画，视频体积偏大。', tone: 'purple' },
  { key: 'gif', label: 'GIF', sub: '兼容兜底', desc: '最通用，但颜色与流畅度牺牲明显。', tone: 'amber' }
]
const presetInfo = [
  { key: 'low', title: '低压缩', hint: '质量优先', desc: '尽量保留原尺寸与帧率' },
  { key: 'medium', title: '中压缩', hint: '日常均衡', desc: '1200px / 20fps 起步' },
  { key: 'high', title: '高压缩', hint: '体积优先', desc: '720px / 12fps 起步' }
]

const config = ref(null)
const videos = ref([])
const selectedSource = ref(null)
const uploadBusy = ref(false)
const loading = ref(true)
const form = reactive({
  formats: ['webp'],
  preset: 'low',
  targetEnabled: true,
  targetMb: 10,
  autoOptimize: true,
  maxWidth: 0,
  maxFps: 0,
  colors: 256,
  webpQuality: 90,
  avifCrf: 16,
  speed: 3
})
const job = ref(null)
const results = ref([])
const busy = computed(() => ['queued', 'running'].includes(job.value?.status))
const selectedVideo = computed(() => videos.value.find((item) => item.path === selectedSource.value))
const sourceOptions = computed(() =>
  videos.value.map((video) => ({
    label: `${video.name}  ·  ${video.width}x${video.height}  ·  ${video.sizeText}`,
    value: video.path
  }))
)
const logs = computed(() => job.value?.logs || [])
const previewUrl = computed(() => (job.value?.status === 'done' ? `/preview/${job.value.id}` : null))
const progress = computed(() => {
  if (!job.value) return 0
  if (job.value.status === 'done') return 100
  if (job.value.status === 'running') return Math.min(94, 14 + logs.value.length * 3)
  return job.value.status === 'queued' ? 6 : 0
})
const statusText = computed(() => {
  const states = {
    queued: '任务排队中',
    running: '正在逐轮寻找最佳输出',
    done: '输出完成',
    cancelled: '任务已取消',
    error: '转换失败'
  }
  return job.value ? states[job.value.status] : '等待开始'
})
let timer = null

function toggleFormat(key) {
  const item = formatCards.find((format) => format.key === key)
  if (item?.disabled) return
  const exists = form.formats.includes(key)
  if (exists && form.formats.length === 1) {
    message.warning('至少保留一种输出格式')
    return
  }
  form.formats = exists ? form.formats.filter((value) => value !== key) : [...form.formats, key]
}

function presetValue(key) {
  return config.value?.presets?.[key]
}

function applyPreset(key) {
  const preset = presetValue(key)
  if (!preset) return
  form.preset = key
  form.maxWidth = preset.width
  form.maxFps = preset.fps
  form.colors = preset.colors
  form.webpQuality = preset.webp_quality
  form.avifCrf = preset.avif_crf
  form.speed = preset.speed
}

async function fetchConfig() {
  loading.value = true
  try {
    const response = await fetch('/api/config')
    if (!response.ok) throw new Error('无法读取转换器设置')
    config.value = await response.json()
    videos.value = config.value.videos
    if (!selectedSource.value && videos.value.length) selectedSource.value = videos.value[0].path
    applyPreset(form.preset)
  } catch (error) {
    message.error(error.message)
  } finally {
    loading.value = false
  }
}

async function refreshVideos() {
  const response = await fetch('/api/videos')
  const data = await response.json()
  videos.value = data.videos
}

async function uploadVideo({ file, onFinish, onError }) {
  uploadBusy.value = true
  try {
    const response = await fetch('/api/upload', {
      method: 'POST',
      headers: { 'X-File-Name': encodeURIComponent(file.file.name) },
      body: file.file
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || '上传失败')
    await refreshVideos()
    selectedSource.value = data.video.path
    message.success('视频已导入，可以开始转换')
    onFinish()
  } catch (error) {
    message.error(error.message)
    onError()
  } finally {
    uploadBusy.value = false
  }
}

function requestPayload() {
  return {
    source: selectedSource.value,
    formats: form.formats,
    targetMb: form.targetEnabled ? form.targetMb : null,
    autoOptimize: form.autoOptimize,
    maxWidth: form.maxWidth,
    maxFps: form.maxFps,
    colors: form.colors,
    webpQuality: form.webpQuality,
    avifCrf: form.avifCrf,
    speed: form.speed
  }
}

async function startConversion() {
  if (!selectedSource.value) {
    message.warning('请先选择或导入一个视频')
    return
  }
  if (form.targetEnabled && (!form.targetMb || form.targetMb <= 0)) {
    message.warning('请填写有效的目标大小')
    return
  }
  results.value = []
  const response = await fetch('/api/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestPayload())
  })
  const data = await response.json()
  if (!response.ok) {
    message.error(data.error || '无法启动转换')
    return
  }
  job.value = data
  clearInterval(timer)
  timer = setInterval(pollJob, 900)
  await pollJob()
}

async function pollJob() {
  if (!job.value?.id) return
  try {
    const response = await fetch(`/api/jobs/${job.value.id}`)
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || '无法读取任务状态')
    job.value = data
    if (data.status === 'done') {
      results.value = data.results
      clearInterval(timer)
      message.success('转换完成，结果已就绪')
    } else if (data.status === 'error') {
      clearInterval(timer)
      message.error(data.error || '转换失败')
    } else if (data.status === 'cancelled') {
      clearInterval(timer)
      message.info('任务已取消')
    }
  } catch (error) {
    clearInterval(timer)
    message.error(error.message)
  }
}

async function cancelConversion() {
  if (!job.value?.id) return
  await fetch(`/api/jobs/${job.value.id}/cancel`, { method: 'POST' })
  message.info('正在停止当前编码')
}

function qualityDetail(item) {
  if (item.format === 'webp') return `质量 ${item.webpQuality}`
  if (item.format === 'avif') return `CRF ${item.avifCrf}`
  return `${item.colors} 色`
}

onMounted(fetchConfig)
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <n-config-provider :theme="lightTheme" :theme-overrides="themeOverrides">
    <n-global-style />
    <div class="app-shell">
      <header class="hero">
        <div class="brand"><span class="brand-mark"></span> MotionMint</div>
        <div class="hero-copy">
          <h1>视频转动图 <span>更小，也更清晰</span></h1>
          <p>三种常用格式一站转换，设定容量上限后自动寻找更好的输出。</p>
        </div>
        <div class="hero-stat">
          <span>3 种可用格式</span>
          <span>严格限容</span>
          <span>自动优化</span>
        </div>
      </header>

      <main v-if="!loading" class="workspace">
        <section class="panel source-panel">
          <div class="section-title">
            <div>
              <p class="eyebrow">SOURCE</p>
              <h2>输入视频</h2>
            </div>
            <n-button quaternary circle @click="refreshVideos">
              <template #icon><n-icon><refresh-outline /></n-icon></template>
            </n-button>
          </div>
          <div class="source-tools">
            <n-select v-model:value="selectedSource" :options="sourceOptions" placeholder="选择工作区中的视频" />
            <n-upload :show-file-list="false" accept="video/*" :custom-request="uploadVideo">
              <n-button :loading="uploadBusy" class="upload-button">
                <template #icon><n-icon><cloud-upload-outline /></n-icon></template>
                导入
              </n-button>
            </n-upload>
          </div>
          <div class="source-meta" :class="{ empty: !selectedVideo }">
            <div class="video-frame">
              <video v-if="selectedVideo" :src="selectedVideo.url" controls muted preload="metadata" />
              <div v-else class="video-placeholder">
                <n-icon><film-outline /></n-icon>
                <strong>等待导入视频</strong>
                <span>导入视频后将在这里预览源画面</span>
              </div>
            </div>
            <div v-if="selectedVideo" class="metadata">
              <span>{{ selectedVideo.width }} x {{ selectedVideo.height }}</span>
              <span>{{ selectedVideo.fps }} fps</span>
              <span>{{ selectedVideo.duration }} 秒</span>
              <span>{{ selectedVideo.sizeText }}</span>
            </div>
            <div v-else class="metadata metadata-placeholder" aria-hidden="true">
              <span>分辨率</span>
              <span>帧率</span>
              <span>时长</span>
              <span>体积</span>
            </div>
          </div>
          <div class="section-title format-heading-title">
            <div>
              <p class="eyebrow">FORMAT</p>
              <h2>输出格式</h2>
            </div>
            <span class="micro-copy">可多选</span>
          </div>
          <div class="format-grid">
            <button
              v-for="item in formatCards"
              :key="item.key"
              class="format-card"
              :class="[item.tone, { selected: form.formats.includes(item.key), disabled: item.disabled }]"
              type="button"
              :disabled="item.disabled"
              @click="toggleFormat(item.key)"
            >
              <n-checkbox
                :checked="form.formats.includes(item.key)"
                :disabled="item.disabled"
                @click.stop
                @update:checked="toggleFormat(item.key)"
              />
              <div class="format-heading">
                <strong>{{ item.label }}</strong>
                <span>{{ item.sub }}</span>
              </div>
              <p>{{ item.desc }}</p>
            </button>
          </div>
        </section>

        <section class="panel tuning-panel">
          <div class="section-title">
            <div>
              <p class="eyebrow">QUALITY CONTROL</p>
              <h2>压缩设置</h2>
            </div>
            <n-icon class="large-icon"><options-outline /></n-icon>
          </div>

          <div class="preset-grid">
            <button
              v-for="preset in presetInfo"
              :key="preset.key"
              class="preset"
              :class="{ active: form.preset === preset.key }"
              type="button"
              @click="applyPreset(preset.key)"
            >
              <span>{{ preset.hint }}</span>
              <b>{{ preset.title }}</b>
              <small>{{ preset.desc }}</small>
            </button>
          </div>

          <div class="target-row">
            <div class="target-switch">
              <n-switch v-model:value="form.targetEnabled" />
              <div>
                <strong>限制文件大小</strong>
                <small>严格按十进制 MB 计算</small>
              </div>
            </div>
            <n-input-number v-model:value="form.targetMb" :min="0.1" :disabled="!form.targetEnabled">
              <template #suffix>MB</template>
            </n-input-number>
          </div>
          <div class="auto-row">
            <n-checkbox v-model:checked="form.autoOptimize" :disabled="!form.targetEnabled">
              自动试压，输出容量内质量最佳版本
            </n-checkbox>
          </div>

          <n-collapse class="advanced" :default-expanded-names="['advanced']">
            <n-collapse-item title="高级参数" name="advanced">
              <div class="setting-list">
                <div class="setting">
                  <div class="label"><n-icon><resize-outline /></n-icon><b>最大宽度</b><span>0 为原尺寸</span></div>
                  <div class="slider"><n-slider v-model:value="form.maxWidth" :min="0" :max="2400" :step="40" /></div>
                  <n-input-number v-model:value="form.maxWidth" :min="0" :step="40"><template #suffix>px</template></n-input-number>
                </div>
                <div class="setting">
                  <div class="label"><n-icon><film-outline /></n-icon><b>最大帧率</b><span>越高越流畅</span></div>
                  <div class="slider"><n-slider v-model:value="form.maxFps" :min="0" :max="60" :step="1" /></div>
                  <n-input-number v-model:value="form.maxFps" :min="0" :max="60"><template #suffix>fps</template></n-input-number>
                </div>
                <div class="setting">
                  <div class="label"><n-icon><color-palette-outline /></n-icon><b>GIF/APNG 色数</b><span>越多渐变更自然</span></div>
                  <div class="slider"><n-slider v-model:value="form.colors" :min="16" :max="256" :step="16" /></div>
                  <n-input-number v-model:value="form.colors" :min="16" :max="256" :step="16" />
                </div>
                <div class="setting">
                  <div class="label"><n-icon><images-outline /></n-icon><b>WebP 质量</b><span>越高细节越好</span></div>
                  <div class="slider"><n-slider v-model:value="form.webpQuality" :min="0" :max="100" /></div>
                  <n-input-number v-model:value="form.webpQuality" :min="0" :max="100" />
                </div>
                <div class="setting">
                  <div class="label disabled-label"><n-icon><flash-outline /></n-icon><b>AVIF CRF</b><span>暂不开放</span></div>
                  <div class="slider"><n-slider v-model:value="form.avifCrf" :min="0" :max="63" disabled /></div>
                  <n-input-number v-model:value="form.avifCrf" :min="0" :max="63" disabled />
                </div>
                <div class="setting">
                  <div class="label"><n-icon><speedometer-outline /></n-icon><b>编码速度</b><span>越低压缩更精细</span></div>
                  <div class="slider"><n-slider v-model:value="form.speed" :min="0" :max="8" /></div>
                  <n-input-number v-model:value="form.speed" :min="0" :max="8" />
                </div>
              </div>
            </n-collapse-item>
          </n-collapse>

          <div class="actions">
            <n-button type="primary" size="large" :disabled="busy" @click="startConversion">
              <template #icon><n-icon><play-circle-outline /></n-icon></template>
              开始转换
            </n-button>
            <n-button v-if="busy" size="large" secondary type="error" @click="cancelConversion">
              <template #icon><n-icon><stop-circle-outline /></n-icon></template>
              取消
            </n-button>
          </div>
        </section>
      </main>

      <n-spin v-else class="loading" size="large" description="正在载入工作台..." />

      <section v-if="job" class="process panel">
        <div class="process-head">
          <h2>{{ statusText }}</h2>
          <n-tag :type="job.status === 'done' ? 'success' : job.status === 'error' ? 'error' : 'info'" round>
            {{ job.status.toUpperCase() }}
          </n-tag>
        </div>
        <n-progress
          type="line"
          :percentage="progress"
          :processing="busy"
          :show-indicator="false"
          color="#007aff"
          rail-color="#e7e8ed"
        />
        <n-collapse class="job-log">
          <n-collapse-item title="查看转换日志" name="log">
            <div class="terminal">
              <div v-if="!logs.length" class="terminal-empty">任务准备中...</div>
              <div v-for="(entry, index) in logs" :key="index" class="log-line">{{ entry || '\u00a0' }}</div>
            </div>
          </n-collapse-item>
        </n-collapse>
      </section>

      <section class="gallery panel">
        <div class="gallery-title">
          <div>
            <p class="eyebrow">OUTPUT GALLERY</p>
            <h2>效果对比</h2>
          </div>
          <div class="gallery-action">
            <p class="gallery-message">
              {{ results.length ? '新页面同屏播放，比较细节与流畅度' : '完成转换后可打开同屏预览页' }}
            </p>
            <n-button
              v-if="previewUrl"
              tag="a"
              :href="previewUrl"
              target="_blank"
              rel="noopener"
              type="primary"
            >
              <template #icon><n-icon><open-outline /></n-icon></template>
              新页查看对比
            </n-button>
            <n-button v-else disabled>
              <template #icon><n-icon><open-outline /></n-icon></template>
              新页查看对比
            </n-button>
          </div>
        </div>
        <div v-if="results.length" class="comparison-files">
          <article v-for="item in results" :key="item.format" class="comparison-file">
            <div>
              <strong>{{ item.label }}</strong>
              <span>{{ item.sizeText }}</span>
              <small>{{ item.width }} x {{ item.height }} · {{ item.fps }} fps · {{ qualityDetail(item) }}</small>
            </div>
            <a :href="item.url" :download="item.name">下载</a>
          </article>
        </div>
        <n-empty v-else class="empty-results" description="完成转换后，打开新页面查看同屏效果" />
      </section>
    </div>
  </n-config-provider>
</template>
