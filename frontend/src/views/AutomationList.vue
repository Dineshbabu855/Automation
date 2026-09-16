<template>
  <div class="ab-list">
    <div class="ab-list-header">
      <h1>Automations</h1>
      <div style="display: flex; gap: 8px;">
        <button class="ab-btn ab-btn-ghost ab-btn-sm" @click="openTemplates">Email Templates</button>
        <button class="ab-btn ab-btn-primary ab-btn-sm" @click="createNew">New Automation</button>
      </div>
    </div>

    <div v-if="loading" class="ab-loading">Loading...</div>

    <div v-else-if="automations.length === 0" class="ab-empty">
      <p>No automations yet. Create your first one to get started.</p>
    </div>

    <div v-else>
      <div v-for="auto in automations" :key="auto.name" class="ab-list-row" @click="openBuilder(auto.name)">
        <div class="ab-list-row-left">
          <div class="ab-list-row-subject">
            <span class="ab-list-row-title">{{ auto.automation_name }}</span>
          </div>
          <div class="ab-list-row-meta">{{ auto.trigger_doctype }} → {{ auto.trigger_event }}</div>
          <div>
            <span class="ab-indicator" :class="auto.status === 'Published' ? 'ab-indicator-green' : 'ab-indicator-gray'">
              {{ auto.status || 'Draft' }}
            </span>
            <span class="ab-indicator" :class="auto.enabled ? 'ab-indicator-blue' : 'ab-indicator-gray'" style="margin-left: 4px;">
              {{ auto.enabled ? 'Enabled' : 'Disabled' }}
            </span>
          </div>
        </div>
        <div class="ab-list-row-right">
          <div class="ab-row-actions">
            <button class="ab-row-action-btn" @click.stop="openBuilder(auto.name)" title="Edit">Edit</button>
            <button class="ab-row-action-btn" @click.stop="openRuns(auto.name)" title="Run History">Runs</button>
            <button
              class="ab-row-action-btn"
              :class="auto.enabled ? 'ab-row-action-btn--danger' : ''"
              @click.stop="toggleEnabled(auto)"
              :title="auto.enabled ? 'Disable' : 'Enable'"
            >
              {{ auto.enabled ? 'Disable' : 'Enable' }}
            </button>
            <button class="ab-row-action-btn ab-row-action-btn--danger" @click.stop="deleteAutomation(auto)" title="Delete">
              Delete
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { listAutomations, saveAutomation } from '../composables/api.js'

const router = useRouter()
const automations = ref([])
const loading = ref(true)

async function load() {
  try {
    automations.value = await listAutomations()
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

function createNew() {
  router.push({ name: 'builder' })
}

function openTemplates() {
  router.push({ name: 'templates' })
}

function openBuilder(name) {
  router.push({ name: 'builder', params: { name } })
}

function openRuns(name) {
  router.push({ name: 'runs', params: { name } })
}

async function toggleEnabled(auto) {
  try {
    // Minimal payload — triggers and graph are preserved server-side when
    // omitted; sending stale/undefined values would corrupt the automation
    await saveAutomation({
      name: auto.name,
      automation_name: auto.automation_name,
      status: auto.status,
      enabled: auto.enabled ? 0 : 1,
    })
    auto.enabled = auto.enabled ? 0 : 1
    frappe.show_alert({
      message: `Automation ${auto.enabled ? 'enabled' : 'disabled'}`,
      indicator: auto.enabled ? 'green' : 'gray',
    })
  } catch (e) {
    frappe.msgprint('Failed to toggle: ' + (e.message || e))
  }
}

async function deleteAutomation(auto) {
  if (!confirm(`Delete automation "${auto.automation_name}"? This cannot be undone.`)) return
  try {
    await frappe.call({
      method: 'frappe.client.delete',
      args: { doctype: 'Automation', name: auto.name },
    })
    frappe.show_alert({ message: 'Automation deleted', indicator: 'green' })
    await load()
  } catch (e) {
    frappe.msgprint('Delete failed: ' + (e.message || e))
  }
}

onMounted(load)
</script>
