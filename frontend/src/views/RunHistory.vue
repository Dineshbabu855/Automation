<template>
  <div class="ab-runs">
    <div class="ab-runs-header">
      <h1>Run History — {{ automationName }}</h1>
      <button class="ab-btn ab-btn-ghost ab-btn-sm" @click="goBack">← Back to Builder</button>
    </div>

    <div v-if="loading" class="ab-loading">Loading...</div>

    <div v-else-if="runs.length === 0" class="ab-empty">
      <p>No runs yet.</p>
    </div>

    <div v-else>
      <div v-for="run in runs" :key="run.name">
        <div class="ab-run-row">
          <div class="ab-run-col" style="flex: 0 0 120px;">
            <span class="ab-badge" :class="{
              'ab-badge-success': run.status === 'Success',
              'ab-badge-failed': run.status === 'Failed',
              'ab-badge-skipped': run.status === 'Skipped',
            }">{{ run.status }}</span>
          </div>
          <div class="ab-run-col" style="flex: 2;">{{ run.reference_doctype }} {{ run.reference_name }}</div>
          <div class="ab-run-col" style="flex: 2;">{{ formatDate(run.started_at) }}</div>
          <div class="ab-run-col" style="flex: 1;">{{ duration(run.started_at, run.ended_at) }}</div>
          <div class="ab-run-col" style="flex: 0 0 80px; text-align: right;">
            <span class="ab-log-toggle" @click="toggleLog(run.name)">
              {{ expandedRun === run.name ? 'Hide' : 'Show' }}
            </span>
          </div>
        </div>
        <div v-if="expandedRun === run.name" class="ab-log-content">
          <div v-if="parsedLog(run.log).length" class="ab-steps">
            <div v-for="(step, idx) in parsedLog(run.log)" :key="idx" class="ab-step">
              <span class="ab-step-badge" :class="{
                'ab-step-success': step.status === 'Success',
                'ab-step-failed': step.status === 'Failed',
              }">{{ step.status === 'Success' ? '\u2713' : '\u2717' }}</span>
              <div class="ab-step-body">
                <div class="ab-step-title">{{ stepLabel(step.step_type) }}</div>
                <div class="ab-step-detail">{{ step.output || step.error || 'No details' }}</div>
              </div>
            </div>
          </div>
          <div v-else class="ab-step-detail">{{ run.log || 'No log' }}</div>
          <div v-if="run.error" class="ab-error-content">{{ run.error }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { listRuns, getAutomation } from '../composables/api.js'

const route = useRoute()
const router = useRouter()
const automationName = ref('')
const runs = ref([])
const loading = ref(true)
const expandedRun = ref(null)

function formatDate(dt) {
  if (!dt) return '-'
  return new Date(dt).toLocaleString()
}

function duration(start, end) {
  if (!start || !end) return '-'
  const ms = new Date(end) - new Date(start)
  if (ms < 1000) return ms + 'ms'
  return (ms / 1000).toFixed(1) + 's'
}

function toggleLog(name) {
  expandedRun.value = expandedRun.value === name ? null : name
}

const STEP_LABELS = {
  create_document: 'Create Document',
  send_email: 'Send Email',
  update_field: 'Update Field',
  http_request: 'HTTP Request',
  telegram: 'Telegram',
  if_condition: 'IF',
  switch_case: 'Switch',
  condition: 'Condition',
  webhook: 'Webhook',
  schedule: 'Schedule',
  manual: 'Manual',
  trigger: 'Trigger',
}

function stepLabel(stepType) {
  return STEP_LABELS[stepType] || stepType || 'Step'
}

function parsedLog(log) {
  if (!log) return []
  try {
    const parsed = JSON.parse(log)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function goBack() {
  router.push({ name: 'builder', params: { name: route.params.name } })
}

onMounted(async () => {
  try {
    const auto = await getAutomation(route.params.name)
    automationName.value = auto.automation_name
    runs.value = await listRuns(route.params.name)
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
})
</script>
