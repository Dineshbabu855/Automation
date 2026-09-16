<template>
  <div class="ab-node-palette" :class="{ 'ab-node-palette--collapsed': collapsed }">
    <div class="ab-node-palette-header">
      <span v-if="!collapsed" class="ab-node-palette-title">Nodes</span>
      <button class="ab-node-palette-toggle" @click="collapsed = !collapsed" :title="collapsed ? 'Expand' : 'Collapse'">
        <svg v-if="collapsed" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="14" height="14"><path d="m9 18 6-6-6-6"/></svg>
        <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="14" height="14"><path d="m15 18-6-6 6-6"/></svg>
      </button>
    </div>
    <div v-if="!collapsed" class="ab-node-palette-items">
      <!-- Triggers section -->
      <div class="ab-node-palette-section">Triggers</div>
      <div
        class="ab-node-palette-item ab-node-palette-item--trigger"
        draggable="true"
        @dragstart="onDragStart($event, { key: 'trigger', label: 'Trigger', nodeCategory: 'trigger' })"
      >
        <span class="ab-node-palette-icon ab-node-palette-icon--trigger">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
        </span>
        <span class="ab-node-palette-label">Trigger</span>
      </div>

      <!-- Logic section -->
      <div class="ab-node-palette-section">Logic</div>
      <div
        v-for="item in logicItems"
        :key="item.key"
        class="ab-node-palette-item"
        draggable="true"
        @dragstart="onDragStart($event, item)"
      >
        <span class="ab-node-palette-icon" :class="item.iconClass">
          <svg v-if="item.key === 'if_condition'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h12l4 6-10 13L2 9Z"/></svg>
          <svg v-else-if="item.key === 'switch_case'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/></svg>
        </span>
        <span class="ab-node-palette-label">{{ item.label }}</span>
      </div>
      <div
        class="ab-node-palette-item"
        draggable="true"
        @dragstart="onDragStart($event, { key: 'condition', label: 'Condition', nodeCategory: 'condition' })"
      >
        <span class="ab-node-palette-icon ab-node-palette-icon--condition">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
        </span>
        <span class="ab-node-palette-label">Condition</span>
      </div>

      <!-- Actions section -->
      <div class="ab-node-palette-section">Actions</div>
      <div
        v-for="item in actionItems"
        :key="item.key"
        class="ab-node-palette-item"
        draggable="true"
        @dragstart="onDragStart($event, item)"
      >
        <span class="ab-node-palette-icon ab-node-palette-icon--action">
          <svg v-if="item.key === 'send_email'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
          <svg v-else-if="item.key === 'http_request'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
          <svg v-else-if="item.key === 'telegram'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>
          <svg v-else-if="item.key === 'update_field'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>
          <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M9 15h6"/><path d="M9 11h6"/></svg>
        </span>
        <span class="ab-node-palette-label">{{ item.label }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  actionTypes: { type: Array, default: () => [] },
})

const collapsed = ref(false)

const logicItems = computed(() => {
  return props.actionTypes
    .filter(at => at.node_category === 'logic')
    .map(at => ({
      key: at.key,
      label: at.label,
      nodeCategory: 'logic',
    }))
})

const actionItems = computed(() => {
  return props.actionTypes
    .filter(at => at.node_category !== 'logic')
    .map(at => ({
      key: at.key,
      label: at.label,
      nodeCategory: 'action',
    }))
})

function onDragStart(event, item) {
  let nodeType, actionType
  if (item.key === 'trigger' || item.nodeCategory === 'trigger') {
    nodeType = 'trigger'
    actionType = null
  } else if (item.key === 'condition') {
    nodeType = 'condition'
    actionType = null
  } else if (item.nodeCategory === 'logic' || item.key === 'if_condition' || item.key === 'switch_case') {
    nodeType = item.key === 'if_condition' ? 'if' : 'switch'
    actionType = null
  } else {
    nodeType = 'action'
    actionType = item.key
  }

  const payload = JSON.stringify({ nodeType, actionType })
  event.dataTransfer.setData('application/automation-builder-node', payload)
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', payload)
}
</script>
