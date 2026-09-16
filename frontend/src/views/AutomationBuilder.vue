<template>
  <div class="ab-builder">
    <!-- Left sidebar: node palette -->
    <NodePalette :actionTypes="actionTypes" />

    <div class="ab-canvas-area">
      <div class="ab-topbar">
        <button class="ab-btn ab-btn-ghost ab-btn-sm" @click="goBack">← Back</button>
        <input type="text" v-model="automationName" placeholder="Automation Name" />
        <label>
          <input type="checkbox" v-model="enabled" />
          Enabled
        </label>
        <div class="ab-status-toggle" :class="{ 'ab-status-published': status === 'Published' }">
          <button 
            class="ab-status-btn" 
            :class="{ 'ab-status-btn-active': status === 'Draft' }"
            @click="status = 'Draft'"
          >
            Draft
          </button>
          <button 
            class="ab-status-btn" 
            :class="{ 'ab-status-btn-active': status === 'Published' }"
            @click="publish"
            :disabled="!canPublish"
          >
            Published
          </button>
        </div>
        <div class="ab-topbar-actions">
          <button
            v-if="hasManualTrigger && automationId"
            class="ab-btn ab-btn-ghost ab-btn-sm"
            @click="openRunNowModal"
          >Run Now</button>
          <button class="ab-btn ab-btn-ghost ab-btn-sm" @click="showRuns" v-if="automationId">Run History</button>
          <button class="ab-btn ab-btn-primary ab-btn-sm" @click="save" :disabled="saving">
            {{ saving ? 'Saving...' : 'Save' }}
          </button>
        </div>
      </div>

      <div
        class="ab-canvas-wrapper"
        @drop="onDrop"
        @dragover="onDragOver"
      >
        <VueFlow
          v-model:nodes="nodes"
          v-model:edges="edges"
          :fit-view-on-init="true"
          :default-edge-options="{ type: 'smoothstep', animated: false }"
          :snap-to-grid="true"
          :snap-grid="[15, 15]"
          :connectable="true"
          :connection-line-style="{ stroke: 'var(--blue-500)', strokeWidth: 2 }"
          :is-valid-connection="isValidConnection"
          @connect="onConnect"
          @connect-start="onConnectStart"
          @connect-end="onConnectEnd"
        >
          <template #node-trigger="nodeProps">
            <div class="ab-node ab-node-trigger" :class="{ 'ab-node-selected': selectedNodeId === nodeProps.id }" @click="selectNode('trigger', nodeProps.data, nodeProps.id)">
              <Handle type="source" :position="Position.Bottom" :id="nodeProps.id + '-out'" />
              <Handle type="source" :position="Position.Right" :id="nodeProps.id + '-out-right'" />
              <div class="ab-node-header">
                <div class="ab-node-icon-wrap ab-node-icon-wrap--trigger">
                  <svg class="ab-node-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                </div>
                <span class="ab-node-title">Trigger</span>
              </div>
              <div class="ab-node-divider"></div>
              <div class="ab-node-body">
                <div>{{ nodeProps.data.trigger_doctype || 'Select DocType' }}</div>
                <div class="ab-node-summary">{{ nodeProps.data.trigger_event || 'Select Event' }}</div>
                <div v-if="nodeProps.data.conditions && nodeProps.data.conditions.length" class="ab-node-conditions-summary">
                  <span class="ab-node-condition-badge">{{ nodeProps.data.condition_logic === 'Any must match (OR)' ? 'OR' : 'AND' }}</span>
                  {{ nodeProps.data.conditions.length }} condition{{ nodeProps.data.conditions.length > 1 ? 's' : '' }}
                </div>
              </div>
            </div>
          </template>

          <template #node-condition="nodeProps">
            <div class="ab-node ab-node-condition" :class="{ 'ab-node-selected': selectedNodeId === nodeProps.id }" @click="selectNode('condition', nodeProps.data, nodeProps.id)">
              <Handle type="target" :position="Position.Top" :id="nodeProps.id + '-in'" />
              <Handle type="target" :position="Position.Left" :id="nodeProps.id + '-in-left'" />
              <Handle type="source" :position="Position.Bottom" :id="nodeProps.id + '-out'" />
              <Handle type="source" :position="Position.Right" :id="nodeProps.id + '-out-right'" />
              <div class="ab-node-header">
                <div class="ab-node-icon-wrap ab-node-icon-wrap--condition">
                  <svg class="ab-node-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
                </div>
                <span class="ab-node-title">Condition</span>
              </div>
              <div class="ab-node-divider"></div>
              <div class="ab-node-body">
                <span v-if="!nodeProps.data.condition_field" class="ab-node-placeholder">Click to configure</span>
                <template v-else>
                  <div>{{ nodeProps.data.condition_field }}</div>
                  <div class="ab-node-summary">{{ nodeProps.data.condition_operator }} {{ nodeProps.data.condition_value }}</div>
                </template>
              </div>
            </div>
          </template>

          <template #node-action="nodeProps">
            <div class="ab-node ab-node-action" :class="{ 'ab-node-selected': selectedNodeId === nodeProps.id }" @click="selectNode('action', nodeProps.data, nodeProps.id)">
              <Handle type="target" :position="Position.Top" :id="nodeProps.id + '-in'" />
              <Handle type="target" :position="Position.Left" :id="nodeProps.id + '-in-left'" />
              <Handle type="source" :position="Position.Bottom" :id="nodeProps.id + '-out'" />
              <Handle type="source" :position="Position.Right" :id="nodeProps.id + '-out-right'" />
              <div class="ab-node-header">
                <div class="ab-node-icon-wrap ab-node-icon-wrap--action">
                  <svg v-if="nodeProps.data.action_type === 'send_email'" class="ab-node-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
                  <svg v-else-if="nodeProps.data.action_type === 'http_request'" class="ab-node-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
                  <svg v-else-if="nodeProps.data.action_type === 'telegram'" class="ab-node-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>
                  <svg v-else-if="nodeProps.data.action_type === 'update_field'" class="ab-node-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>
                  <svg v-else class="ab-node-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M9 15h6"/><path d="M9 11h6"/></svg>
                </div>
                <span class="ab-node-title">Action</span>
              </div>
              <div class="ab-node-divider"></div>
              <div class="ab-node-body">
                <span v-if="!nodeProps.data.action_type" class="ab-node-placeholder">Click to configure</span>
                <template v-else>
                  <div>{{ actionLabel(nodeProps.data) }}</div>
                  <div class="ab-node-summary">{{ actionSummary(nodeProps.data) }}</div>
                </template>
              </div>
            </div>
          </template>

          <template #node-if="nodeProps">
            <div class="ab-node ab-node-if" :class="{ 'ab-node-selected': selectedNodeId === nodeProps.id }" @click="selectNode('if', nodeProps.data, nodeProps.id)">
              <Handle type="target" :position="Position.Top" :id="nodeProps.id + '-in'" />
              <div class="ab-node-header">
                <div class="ab-node-icon-wrap ab-node-icon-wrap--if">
                  <svg class="ab-node-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h12l4 6-10 13L2 9Z"/></svg>
                </div>
                <span class="ab-node-title">IF</span>
              </div>
              <div class="ab-node-divider"></div>
              <div class="ab-node-body">
                <span v-if="!nodeProps.data.field_to_check" class="ab-node-placeholder">Click to configure</span>
                <template v-else>
                  <div>{{ nodeProps.data.field_to_check }} {{ nodeProps.data.operator }} {{ nodeProps.data.value }}</div>
                  <div class="ab-node-summary">Evaluates condition</div>
                </template>
              </div>
              <div class="ab-node-outputs">
                <div class="ab-node-output-label ab-node-output-label--true">True</div>
                <Handle type="source" :position="Position.Right" :id="nodeProps.id + '-true'" :style="{ top: '30%' }" />
                <div class="ab-node-output-label ab-node-output-label--false">False</div>
                <Handle type="source" :position="Position.Right" :id="nodeProps.id + '-false'" :style="{ top: '70%' }" />
              </div>
            </div>
          </template>

          <template #node-switch="nodeProps">
            <div class="ab-node ab-node-switch" :class="{ 'ab-node-selected': selectedNodeId === nodeProps.id }" @click="selectNode('switch', nodeProps.data, nodeProps.id)">
              <Handle type="target" :position="Position.Top" :id="nodeProps.id + '-in'" />
              <div class="ab-node-header">
                <div class="ab-node-icon-wrap ab-node-icon-wrap--switch">
                  <svg class="ab-node-icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/></svg>
                </div>
                <span class="ab-node-title">Switch</span>
              </div>
              <div class="ab-node-divider"></div>
              <div class="ab-node-body">
                <span v-if="!nodeProps.data.field_to_check" class="ab-node-placeholder">Click to configure</span>
                <template v-else>
                  <div>{{ nodeProps.data.field_to_check }}</div>
                  <div class="ab-node-summary">{{ (nodeProps.data.cases || []).length }} cases + default</div>
                </template>
              </div>
              <div class="ab-node-outputs">
                <template v-for="(caseItem, idx) in (nodeProps.data.cases || [])" :key="'case-'+idx">
                  <div class="ab-node-output-label" :style="{ top: (20 + idx * (60 / Math.max((nodeProps.data.cases || []).length + 1, 2))) + '%' }">
                    {{ caseItem.case_value || 'Case ' + (idx + 1) }}
                  </div>
                  <Handle type="source" :position="Position.Right" :id="nodeProps.id + '-case-'+idx" :style="{ top: (20 + idx * (60 / Math.max((nodeProps.data.cases || []).length + 1, 2))) + '%' }" />
                </template>
                <div class="ab-node-output-label ab-node-output-label--default">Default</div>
                <Handle type="source" :position="Position.Right" :id="nodeProps.id + '-default'" :style="{ top: '85%' }" />
              </div>
            </div>
          </template>

          <template #node-add-trigger="nodeProps">
            <div class="ab-node-add-wrapper">
              <Handle type="target" :position="Position.Top" id="add-trigger-in" />
              <div class="ab-node-add-line"></div>
              <button class="ab-add-node-btn" @click.stop="toggleAddMenu(nodeProps.id)">+</button>
              <div v-if="showAddMenu === nodeProps.id" class="ab-add-node-menu">
                <div class="ab-add-node-menu-section">Logic</div>
                <button
                  v-for="lt in logicTypes"
                  :key="lt.key"
                  class="ab-add-node-menu-item ab-add-node-menu-item--logic"
                  @click.stop="addNewLogicNode(lt.key === 'if_condition' ? 'if' : 'switch')"
                >
                  <span class="ab-add-node-menu-item-icon ab-add-node-menu-item-icon--logic">
                    <svg v-if="lt.key === 'if_condition'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h12l4 6-10 13L2 9Z"/></svg>
                    <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/></svg>
                  </span>
                  {{ lt.label }}
                </button>
                <div class="ab-add-node-menu-section">Actions</div>
                <button
                  v-for="at in actionTypes"
                  :key="at.key"
                  class="ab-add-node-menu-item"
                  @click.stop="addNewAction(at.key)"
                >
                  <span class="ab-add-node-menu-item-icon ab-add-node-menu-item-icon--action">⚙</span>
                  {{ at.label }}
                </button>
              </div>
            </div>
          </template>

          <Background :gap="15" :size="2" :pattern-color="'#d4d4d4'" />
          <Controls />
        </VueFlow>

        <!-- Node Type Picker (shown on drag-to-empty-canvas) -->
        <div
          v-if="pickerVisible"
          class="ab-type-picker"
          :style="{ left: pickerPosition.x + 'px', top: pickerPosition.y + 'px' }"
        >
          <button
            v-for="item in pickerItems"
            :key="item.key"
            class="ab-type-picker-item"
            @click="onPickerSelect(item)"
          >
            <span class="ab-type-picker-icon" :class="item.iconClass">
              <svg v-if="item.key === 'trigger'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/></svg>
              <svg v-else-if="item.key === 'condition'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
              <svg v-else-if="item.key === 'if_condition'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h12l4 6-10 13L2 9Z"/></svg>
              <svg v-else-if="item.key === 'switch_case'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/></svg>
              <svg v-else-if="item.key === 'send_email'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
              <svg v-else-if="item.key === 'http_request'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
              <svg v-else-if="item.key === 'telegram'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>
              <svg v-else-if="item.key === 'update_field'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>
              <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M9 15h6"/><path d="M9 11h6"/></svg>
            </span>
            <span class="ab-type-picker-label">{{ item.label }}</span>
          </button>
        </div>
      </div>
    </div>

    <div class="ab-sidebar" v-if="selectedNode">
      <ConfigPanel
        :node-type="selectedNodeType"
        :node-data="selectedNodeData"
        :node-id="selectedNodeId"
        :trigger-doctype="selectedNodeTriggerDoctype"
        :trigger-doctypes="triggerDoctypes"
        :automation-name="automationId"
        :trigger-index="selectedTriggerIndex"
        @update="updateNodeData"
        @close="selectedNode = null"
        @add-action="addActionNode"
        @remove-action="removeActionNode"
      />
    </div>

    <!-- Run Now Modal (Manual Trigger) -->
    <div v-if="showRunNowModal" class="ab-modal-overlay" @click.self="closeRunNowModal">
      <div class="ab-modal">
        <h3>Run Now — {{ runNowDoctype }}</h3>
        <div class="ab-modal-body">
          <div class="ab-config-group">
            <label>Search Documents</label>
            <input
              v-model="runNowQuery"
              type="text"
              placeholder="Type to search..."
              @input="searchRunNowDocs"
            />
          </div>
          <div v-if="runNowDocs.length" class="ab-runnow-list">
            <div
              v-for="doc in runNowDocs"
              :key="doc.name"
              class="ab-runnow-item"
              :class="{ 'ab-runnow-item-selected': runNowSelectedDoc === doc.name }"
              @click="runNowSelectedDoc = doc.name"
            >
              {{ doc.label }}
              <span class="ab-runnow-item-id">{{ doc.name }}</span>
            </div>
          </div>
          <div v-else-if="runNowQuery" class="ab-runnow-empty">No documents found</div>
        </div>
        <div class="ab-modal-footer">
          <button class="ab-btn ab-btn-ghost" @click="closeRunNowModal">Cancel</button>
          <button
            class="ab-btn ab-btn-primary"
            :disabled="!runNowSelectedDoc || runNowLoading"
            @click="executeRunNow"
          >
            {{ runNowLoading ? 'Running...' : 'Execute' }}
          </button>
        </div>
        <div v-if="runNowResult" class="ab-runnow-result">
          <span :class="'ab-runnow-status-' + runNowResult.status">
            {{ runNowResult.status }}
          </span>
          <span v-if="runNowResult.error">{{ runNowResult.error }}</span>
          <span v-else-if="runNowResult.output">{{ runNowResult.output }}</span>
        </div>
      </div>

    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { VueFlow, Handle, Position, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import ConfigPanel from '../components/ConfigPanel.vue'
import NodePalette from '../components/NodePalette.vue'
import { getAutomation, saveAutomation, getActionTypes, canPublish as checkCanPublish, runAutomationManually, searchDocuments } from '../composables/api.js'

import '@vue-flow/controls/dist/style.css'

// screenToFlowCoordinate and setCenter are called at runtime after VueFlow mounts,
// so we get them from the injected instance (available in onMounted)
let screenToFlowCoordinate = (pos) => pos
let setCenterFn = null

const route = useRoute()
const router = useRouter()
const automationId = ref(route.params.name || null)
const automationName = ref('')
const enabled = ref(true)
const status = ref('Draft')
const saving = ref(false)
const showAddMenu = ref(null)
const actionTypes = ref([])
const logicTypes = computed(() => {
  return actionTypes.value.filter(at => at.node_category === 'logic')
})

// Run Now (Manual Trigger) state
const showRunNowModal = ref(false)
const runNowDoctype = ref('')
const runNowQuery = ref('')
const runNowDocs = ref([])
const runNowSelectedDoc = ref('')
const runNowResult = ref(null)
const runNowLoading = ref(false)

const hasManualTrigger = computed(() => {
  return nodes.value.some(n => n.type === 'trigger' && n.data?.trigger_type === 'Manual')
})

// Type picker state (for drag-to-empty-canvas)
const pickerVisible = ref(false)
const pickerPosition = ref({ x: 0, y: 0 })
const pickerSourceNodeId = ref('')
const pickerSourceHandleId = ref('')

const nodes = ref([
  {
    id: 'trigger',
    type: 'trigger',
    position: { x: 250, y: 50 },
    data: {
      trigger_type: 'DocType Event',
      trigger_doctype: '',
      trigger_event: 'On Update',
      schedule_frequency: 'Hourly',
    },
  },
  {
    id: 'condition',
    type: 'condition',
    position: { x: 250, y: 250 },
    data: {
      condition_field: '',
      condition_operator: '=',
      condition_value: '',
    },
  },
  {
    id: 'action-1',
    type: 'action',
    position: { x: 250, y: 450 },
    data: {
      action_type: 'create_document',
      target_doctype: '',
      field_mapping: [{ target_field: '', source_value: '' }],
    },
  },
  {
    id: 'action-2',
    type: 'action',
    position: { x: 250, y: 620 },
    data: {
      action_type: 'send_email',
      recipient: '',
      subject: '',
      body: '',
    },
  },
  {
    id: 'add-trigger',
    type: 'add-trigger',
    position: { x: 250, y: 790 },
    data: {},
  },
])

const edges = ref([
  {
    id: 'e-trigger-condition',
    source: 'trigger',
    target: 'condition',
    sourceHandle: 'trigger-out',
    targetHandle: 'condition-in',
    type: 'smoothstep',
    markerEnd: { type: 'arrowclosed', color: 'var(--gray-500)' },
  },
  {
    id: 'e-condition-action1',
    source: 'condition',
    target: 'action-1',
    sourceHandle: 'condition-out',
    targetHandle: 'action-1-in',
    type: 'smoothstep',
    markerEnd: { type: 'arrowclosed', color: 'var(--gray-500)' },
  },
  {
    id: 'e-action1-action2',
    source: 'action-1',
    target: 'action-2',
    sourceHandle: 'action-1-out',
    targetHandle: 'action-2-in',
    type: 'smoothstep',
    markerEnd: { type: 'arrowclosed', color: 'var(--gray-500)' },
  },
  {
    id: 'e-action2-add',
    source: 'action-2',
    target: 'add-trigger',
    sourceHandle: 'action-2-out',
    targetHandle: 'add-trigger-in',
    type: 'smoothstep',
    markerEnd: { type: 'arrowclosed', color: 'var(--gray-500)' },
  },
])

const selectedNode = ref(null)
const selectedNodeType = ref('')
const selectedNodeData = ref({})
const selectedNodeId = ref('')

const triggerDoctype = computed(() => {
  const trigger = nodes.value.find(n => n.id === 'trigger')
  return trigger?.data?.trigger_doctype || ''
})

const triggerDoctypes = computed(() => {
  return nodes.value
    .filter(n => n.type === 'trigger' && n.data?.trigger_doctype)
    .map(n => n.data.trigger_doctype)
})

function findUpstreamTriggerDoctype(nodeId) {
  const visited = new Set()
  const queue = [nodeId]
  while (queue.length) {
    const current = queue.shift()
    if (visited.has(current)) continue
    visited.add(current)
    const node = nodes.value.find(n => n.id === current)
    if (node && node.type === 'trigger') {
      return node.data?.trigger_doctype || ''
    }
    for (const e of edges.value) {
      if (e.target === current && !visited.has(e.source)) {
        queue.push(e.source)
      }
    }
  }
  return ''
}

const selectedNodeTriggerDoctype = computed(() => {
  if (!selectedNodeId.value) return triggerDoctype.value
  return findUpstreamTriggerDoctype(selectedNodeId.value) || triggerDoctype.value
})

// Index of the selected node among canvas trigger nodes — matches the DB
// trigger row order, since save() persists triggers in canvas order
const selectedTriggerIndex = computed(() => {
  const triggerNodes = nodes.value.filter(n => n.type === 'trigger')
  return Math.max(triggerNodes.findIndex(n => n.id === selectedNodeId.value), 0)
})

const canPublish = ref(false)

function publish() {
  if (canPublish.value) {
    status.value = 'Published'
  }
}

// Which source handles already have an outgoing edge (linear-only enforcement)
const connectedSourceHandles = computed(() => {
  const connected = new Set()
  for (const e of edges.value) {
    if (e.sourceHandle) {
      connected.add(`${e.source}:${e.sourceHandle}`)
    }
  }
  return connected
})

// Picker items depend on which node we're dragging from
const pickerItems = computed(() => {
  const sourceNode = nodes.value.find(n => n.id === pickerSourceNodeId.value)
  if (!sourceNode) return []

  if (sourceNode.type === 'trigger') {
    return [
      { key: 'trigger', label: 'Another Trigger', iconClass: 'ab-type-picker-icon--trigger', nodeType: 'trigger' },
      { key: 'condition', label: 'Condition', iconClass: 'ab-type-picker-icon--condition', nodeType: 'condition' },
      { key: 'if_condition', label: 'IF', iconClass: 'ab-type-picker-icon--logic', nodeType: 'if' },
      { key: 'switch_case', label: 'Switch', iconClass: 'ab-type-picker-icon--logic', nodeType: 'switch' },
    ]
  }

  // Condition, IF, Switch, or Action: show all action types + logic types
  const items = []
  for (const at of actionTypes.value) {
    items.push({
      key: at.key,
      label: at.label,
      iconClass: at.node_category === 'logic' ? 'ab-type-picker-icon--logic' : 'ab-type-picker-icon--action',
      nodeType: at.node_category === 'logic' ? (at.key === 'if_condition' ? 'if' : 'switch') : 'action',
      actionType: at.node_category !== 'logic' ? at.key : null,
    })
  }
  return items
})

function actionLabel(data) {
  const at = actionTypes.value.find(a => a.key === data.action_type)
  return at ? at.label : data.action_type || 'Unknown'
}

function actionSummary(data) {
  if (data.action_type === 'create_document') {
    return data.target_doctype || 'No target DocType'
  }
  if (data.action_type === 'send_email') {
    return `To: ${data.recipient || '...'}`
  }
  if (data.action_type === 'http_request') {
    return data.url || 'No URL'
  }
  if (data.action_type === 'telegram') {
    return data.chat_id ? `Chat: ${data.chat_id}` : 'No chat ID'
  }
  if (data.action_type === 'update_field') {
    return data.target || 'Select target'
  }
  return 'Click to configure'
}

function selectNode(type, data, id) {
  selectedNodeType.value = type
  selectedNodeData.value = { ...data }
  selectedNodeId.value = id || type
  selectedNode.value = true
}

function updateNodeData(newData) {
  const nodeId = selectedNodeId.value
  nodes.value = nodes.value.map(n =>
    n.id === nodeId ? { ...n, data: { ...n.data, ...newData } } : n
  )
  selectedNode.value = null
}

function toggleAddMenu(nodeId) {
  showAddMenu.value = showAddMenu.value === nodeId ? null : nodeId
}

// Run Now (Manual Trigger) functions
function openRunNowModal() {
  const triggerNode = nodes.value.find(n => n.type === 'trigger' && n.data?.trigger_type === 'Manual')
  if (!triggerNode) return
  runNowDoctype.value = triggerNode.data.trigger_doctype
  runNowQuery.value = ''
  runNowDocs.value = []
  runNowSelectedDoc.value = ''
  runNowResult.value = null
  showRunNowModal.value = true
}

async function searchRunNowDocs() {
  if (!runNowDoctype.value) return
  try {
    runNowDocs.value = await searchDocuments(runNowDoctype.value, runNowQuery.value)
  } catch (e) {
    console.error(e)
    runNowDocs.value = []
  }
}

async function executeRunNow() {
  if (!runNowSelectedDoc.value) return
  runNowLoading.value = true
  runNowResult.value = null
  try {
    const result = await runAutomationManually({
      automation_name: automationId.value,
      reference_doctype: runNowDoctype.value,
      reference_name: runNowSelectedDoc.value,
    })
    runNowResult.value = result
  } catch (e) {
    console.error(e)
    runNowResult.value = { status: 'Failed', error: e.message || 'Unknown error' }
  } finally {
    runNowLoading.value = false
  }
}

function closeRunNowModal() {
  showRunNowModal.value = false
  runNowResult.value = null
}

function isValidConnection(params) {
  if (params.id) return true
  const { source, sourceHandle, target, targetHandle } = params
  if (source === target) return false
  // Block connections to any trigger node (initial or dynamically created)
  const targetNode = nodes.value.find(n => n.id === target)
  if (targetNode && targetNode.type === 'trigger') return false
  // Block connections to add-trigger node (it's a UI button, not a real node)
  if (target === 'add-trigger') return false
  if (source === 'add-trigger') return false

  // Linear-only for non-trigger nodes: reject if source handle already has an outgoing edge
  const sourceNode = nodes.value.find(n => n.id === source)
  if (sourceNode && sourceNode.type !== 'trigger') {
    if (sourceHandle && connectedSourceHandles.value.has(`${source}:${sourceHandle}`)) {
      return false
    }
  }

  return true
}

function onConnect(params) {
  if (!isValidConnection(params)) return

  // Auto-detect available input handle for convergence:
  // If the target already has an incoming edge on "-in", use "-in-left" instead.
  let targetHandle = params.targetHandle
  const existingIncoming = edges.value.find(e => e.target === params.target && e.targetHandle === targetHandle)
  if (existingIncoming && targetHandle.endsWith('-in')) {
    targetHandle = targetHandle.replace(/-in$/, '-in-left')
  }

  const newEdge = {
    id: `e-${params.source}-${params.target}-${Date.now()}`,
    source: params.source,
    target: params.target,
    sourceHandle: params.sourceHandle,
    targetHandle: targetHandle,
    type: 'smoothstep',
    markerEnd: { type: 'arrowclosed', color: 'var(--gray-400)' },
  }
  edges.value.push(newEdge)
}

/**
 * Unified node creation + edge connection.
 * Single source of truth for adding nodes to the canvas.
 * When sourceNodeId is null (sidebar drop), creates a free-floating node at dropPosition.
 */
function createNodeAndConnect(nodeType, actionType, sourceNodeId, sourceHandleId, dropPosition) {
  let newNodeId, newNodeData, newNodePosition

  if (nodeType === 'condition') {
    newNodeId = nodes.value.some(n => n.id === 'condition') ? `condition-${Date.now()}` : 'condition'
    newNodeData = { condition_field: '', condition_operator: '=', condition_value: '' }
  } else if (nodeType === 'trigger') {
    newNodeId = `trigger-${Date.now()}`
    newNodeData = {
      trigger_doctype: '',
      trigger_event: 'On Update',
      condition_logic: 'All must match',
      conditions: [],
    }
  } else if (nodeType === 'if') {
    newNodeId = `if-${Date.now()}`
    newNodeData = { field_to_check: '', operator: '=', value: '' }
  } else if (nodeType === 'switch') {
    newNodeId = `switch-${Date.now()}`
    newNodeData = { field_to_check: '', cases: [{ case_value: '' }] }
  } else {
    newNodeId = `action-${Date.now()}`
    const at = actionTypes.value.find(a => a.key === actionType)
    newNodeData = { action_type: actionType }
    if (at && at.config_schema) {
      for (const field of at.config_schema) {
        if (field.type === 'field_mapping_table') {
          newNodeData[field.name] = [{ target_field: '', source_value: '' }]
        } else {
          newNodeData[field.name] = field.default !== undefined ? field.default : ''
        }
      }
    }
  }

  if (sourceNodeId) {
    // Connected node: position below the source node
    const sourceNode = nodes.value.find(n => n.id === sourceNodeId)
    newNodePosition = dropPosition || (sourceNode ? { x: sourceNode.position.x, y: sourceNode.position.y + 170 } : { x: 250, y: 250 })
  } else {
    // Free-floating node from sidebar drop: use drop position
    newNodePosition = dropPosition || { x: 250, y: 250 }
  }

  nodes.value.push({
    id: newNodeId,
    type: nodeType,
    position: newNodePosition,
    data: newNodeData,
  })

  // If we have a source node, connect to it
  if (sourceNodeId) {
    const sourceNode = nodes.value.find(n => n.id === sourceNodeId)
    if (sourceNode) {
      // Auto-detect available input handle for convergence
      let targetHandle = `${newNodeId}-in`
      const existingIncoming = edges.value.find(e => e.target === newNodeId && e.targetHandle === targetHandle)
      if (existingIncoming) {
        targetHandle = `${newNodeId}-in-left`
      }
      edges.value.push({
        id: `e-${sourceNodeId}-${newNodeId}`,
        source: sourceNodeId,
        target: newNodeId,
        sourceHandle: sourceHandleId,
        targetHandle: targetHandle,
        type: 'smoothstep',
        markerEnd: { type: 'arrowclosed', color: 'var(--gray-400)' },
      })
    }
  }

  // If adding an action, also connect it to the add-trigger button (if it exists)
  // Skip for branching nodes (IF/Switch) — user connects manually from handles
  if (nodeType === 'action') {
    const addTriggerNode = nodes.value.find(n => n.id === 'add-trigger')
    if (addTriggerNode) {
      // Remove any existing edge to add-trigger to avoid duplicates
      edges.value = edges.value.filter(e => e.target !== 'add-trigger')
      edges.value.push({
        id: `e-${newNodeId}-add-trigger`,
        source: newNodeId,
        target: 'add-trigger',
        sourceHandle: `${newNodeId}-out`,
        targetHandle: 'add-trigger-in',
        type: 'smoothstep',
        markerEnd: { type: 'arrowclosed', color: 'var(--gray-400)' },
      })
      addTriggerNode.position.y = newNodePosition.y + 170
    }
  }

  // If adding a trigger node from sidebar, connect it to the first action/condition node
  // Alternate -in / -in-left to avoid overlapping with existing trigger edges
  if (nodeType === 'trigger' && !sourceNodeId) {
    const firstAction = nodes.value.find(n => n.type === 'action' || n.type === 'condition')
    if (firstAction) {
      const existingEdges = edges.value.filter(e => e.target === firstAction.id)
      const targetHandle = existingEdges.length % 2 === 0
        ? `${firstAction.id}-in`
        : `${firstAction.id}-in-left`
      edges.value.push({
        id: `e-${newNodeId}-${firstAction.id}`,
        source: newNodeId,
        target: firstAction.id,
        sourceHandle: `${newNodeId}-out`,
        targetHandle,
        type: 'smoothstep',
        markerEnd: { type: 'arrowclosed', color: 'var(--gray-400)' },
      })
    }
  }

  return newNodePosition
}

function addNewAction(actionType) {
  const lastAction = [...nodes.value].filter(n => n.type === 'action').pop()
  const sourceNodeId = lastAction?.id || 'condition'
  const sourceHandleId = lastAction ? `${lastAction.id}-out` : 'condition-out'

  createNodeAndConnect('action', actionType, sourceNodeId, sourceHandleId)
  showAddMenu.value = null
}

function addNewLogicNode(logicType) {
  const lastNode = [...nodes.value].filter(n => n.type === 'action' || n.type === 'if' || n.type === 'switch').pop()
  const sourceNodeId = lastNode?.id || 'condition'
  const sourceHandleId = lastNode ? `${lastNode.id}-out` : 'condition-out'

  createNodeAndConnect(logicType, null, sourceNodeId, sourceHandleId)
  showAddMenu.value = null
}

function removeActionNode(nodeId) {
  const idx = nodes.value.findIndex(n => n.id === nodeId)
  if (idx > -1) {
    const prevEdge = edges.value.find(e => e.target === nodeId)
    const nextEdge = edges.value.find(e => e.source === nodeId)

    nodes.value.splice(idx, 1)
    edges.value = edges.value.filter(e => e.source !== nodeId && e.target !== nodeId)

    if (prevEdge && nextEdge) {
      edges.value.push({
        id: `e-${prevEdge.source}-${nextEdge.target}`,
        source: prevEdge.source,
        target: nextEdge.target,
        sourceHandle: prevEdge.sourceHandle,
        targetHandle: nextEdge.targetHandle,
        type: 'smoothstep',
        markerEnd: { type: 'arrowclosed', color: 'var(--gray-400)' },
      })
    }
  }
  selectedNode.value = null
}

/**
 * Handle connection drag end. Fixed: improved empty-canvas detection.
 * Vue Flow's pane element can intercept elementFromPoint, so we check
 * for the handle class explicitly and treat everything else as empty canvas.
 */
function onConnectEnd(event) {
  if (!event) return

  // Use elementFromPoint to check what's under the cursor
  const target = document.elementFromPoint(event.clientX, event.clientY)

  // If we hit a handle, Vue Flow handles the connection natively
  if (target && target.closest('.vue-flow__handle')) {
    return
  }

  // If we hit the pane/viewport/background, it's empty canvas — show picker
  // Also show picker for any other element (handles the case where Vue Flow's
  // pane intercepts the event before the handle is detected)
  const sourceNode = nodes.value.find(n => n.id === connectionStartNodeId.value)
  if (!sourceNode) return

  pickerSourceNodeId.value = connectionStartNodeId.value
  pickerSourceHandleId.value = connectionStartHandleId.value
  pickerPosition.value = {
    x: event.clientX,
    y: event.clientY,
  }
  pickerVisible.value = true
}

// Track which handle the connection drag started from
const connectionStartNodeId = ref('')
const connectionStartHandleId = ref('')

function onConnectStart(params) {
  connectionStartNodeId.value = params.nodeId
  connectionStartHandleId.value = params.handleId
}

function onPickerSelect(item) {
  const nodeType = item.nodeType || 'action'
  const actionType = item.actionType || null
  createNodeAndConnect(
    nodeType,
    actionType,
    pickerSourceNodeId.value,
    pickerSourceHandleId.value,
    null,
  )
  pickerVisible.value = false
}

// Drag-and-drop from left sidebar palette
function onDragOver(event) {
  event.preventDefault()
  event.dataTransfer.dropEffect = 'move'
}

function onDrop(event) {
  const data = event.dataTransfer.getData('application/automation-builder-node') || event.dataTransfer.getData('text/plain')
  if (!data) return

  try {
    const { nodeType, actionType } = JSON.parse(data)

    // Use Vue Flow's screenToFlowCoordinate for proper coordinate conversion
    // that accounts for pan/zoom
    const flowPos = screenToFlowCoordinate({ x: event.clientX, y: event.clientY })

    const newNodePosition = createNodeAndConnect(nodeType, actionType, null, null, flowPos)

    // Center viewport on the new node so it's always visible (especially when zoomed in)
    if (setCenterFn && newNodePosition) {
      setCenterFn(newNodePosition.x, newNodePosition.y, { duration: 200 })
    }
  } catch (e) {
    console.error('[AB-DnD] Failed to parse drop data', e)
  }
}

async function save() {
  if (!automationName.value.trim()) {
    frappe.msgprint('Please enter an automation name')
    return
  }
  saving.value = true
  try {
    const graphDefinition = JSON.stringify({
      nodes: nodes.value
        .filter(n => n.id !== 'add-trigger')
        .map(n => ({ id: n.id, type: n.type, position: n.position, data: n.data })),
      edges: edges.value.filter(e => e.source !== 'add-trigger' && e.target !== 'add-trigger'),
    })

    // Build triggers array from ALL trigger nodes on the canvas
    const triggers = []
    const triggerNodes = nodes.value.filter(n => n.type === 'trigger')
    for (const trigger of triggerNodes) {
      if (!trigger.data?.trigger_doctype && trigger.data?.trigger_type !== 'Webhook') continue
      triggers.push({
        trigger_type: trigger.data.trigger_type || 'DocType Event',
        trigger_doctype: trigger.data.trigger_doctype || '',
        trigger_event: trigger.data.trigger_event || 'On Update',
        schedule_frequency: trigger.data.schedule_frequency || 'Hourly',
        webhook_token: trigger.data.webhook_token || '',
        condition_logic: trigger.data.condition_logic || 'All must match',
        conditions: trigger.data.conditions || [],
        graph_node_id: trigger.id,
      })
    }

    const result = await saveAutomation({
      name: automationId.value,
      automation_name: automationName.value,
      status: status.value,
      enabled: enabled.value ? 1 : 0,
      graph_definition: graphDefinition,
      triggers: triggers,
    })

    automationId.value = result.name
    frappe.show_alert({ message: 'Automation saved', indicator: 'green' })
  } catch (e) {
    frappe.msgprint('Save failed: ' + (e.message || e))
  } finally {
    saving.value = false
  }
}

function goBack() {
  router.push({ name: 'list' })
}

function showRuns() {
  router.push({ name: 'runs', params: { name: automationId.value } })
}

function handleClickOutside(e) {
  if (pickerVisible.value && !e.target.closest('.ab-type-picker')) {
    pickerVisible.value = false
  }
  if (showAddMenu.value && !e.target.closest('.ab-add-node-menu') && !e.target.closest('.ab-add-node-btn')) {
    showAddMenu.value = null
  }
}

onMounted(async () => {
  // Get screenToFlowCoordinate from the VueFlow instance (now mounted and injected)
  try {
    const vf = useVueFlow()
    if (vf && vf.screenToFlowCoordinate) {
      screenToFlowCoordinate = vf.screenToFlowCoordinate
    }
    if (vf && vf.setCenter) {
      setCenterFn = vf.setCenter
    }
  } catch (e) {
    console.warn('[AB] Could not get useVueFlow instance, using fallback coordinate conversion')
  }

  document.addEventListener('click', handleClickOutside)

  try {
    actionTypes.value = await getActionTypes()
  } catch (e) {
    console.error('Failed to load action types', e)
  }

  // Check publish permission
  try {
    canPublish.value = await checkCanPublish()
  } catch (e) {
    console.error('Failed to check publish permission', e)
  }

  if (automationId.value) {
    try {
      const auto = await getAutomation(automationId.value)
      automationName.value = auto.automation_name
      enabled.value = !!auto.enabled
      status.value = auto.status || 'Draft'

      if (auto.graph_definition) {
        try {
          const graph = JSON.parse(auto.graph_definition)
          if (graph.nodes && graph.nodes.length) {
            nodes.value = graph.nodes.map(n => ({ ...n }))
          }
          if (graph.edges && graph.edges.length) {
            edges.value = graph.edges.map(e => ({
              ...e,
              sourceHandle: e.sourceHandle || null,
              targetHandle: e.targetHandle || null,
            }))
          }

          // Load trigger data from API response into trigger nodes
          // (graph_definition JSON stores positions but conditions live in the DB triggers table)
          if (auto.triggers && auto.triggers.length) {
            const triggerNodes = nodes.value.filter(n => n.type === 'trigger')
            for (let i = 0; i < auto.triggers.length; i++) {
              const t = auto.triggers[i]
              // Match by graph_node_id (order-independent) when present,
              // falling back to index for legacy rows
              let targetNode = null
              if (t.graph_node_id) {
                targetNode = triggerNodes.find(n => n.id === t.graph_node_id)
              }
              if (!targetNode) targetNode = triggerNodes[i]
              if (!targetNode) continue
              targetNode.data.trigger_type = t.trigger_type || 'DocType Event'
              targetNode.data.trigger_doctype = t.trigger_doctype || ''
              targetNode.data.trigger_event = t.trigger_event || 'On Update'
              targetNode.data.schedule_frequency = t.schedule_frequency || 'Hourly'
              targetNode.data.webhook_token = t.webhook_token || ''
              targetNode.data.condition_logic = t.condition_logic || 'All must match'
              targetNode.data.conditions = t.conditions || []
            }
          }

          const lastAction = [...nodes.value].filter(n => n.type === 'action').pop()
          const addY = lastAction ? lastAction.position.y + 170 : 790
          nodes.value.push({
            id: 'add-trigger',
            type: 'add-trigger',
            position: { x: 250, y: addY },
            data: {},
          })
          const lastId = lastAction ? lastAction.id : 'condition'
          edges.value.push({
            id: `e-${lastId}-add-trigger`,
            source: lastId,
            target: 'add-trigger',
            sourceHandle: `${lastId}-out`,
            targetHandle: 'add-trigger-in',
            type: 'smoothstep',
            markerEnd: { type: 'arrowclosed', color: 'var(--gray-400)' },
          })
        } catch (e) {
          console.error('Failed to parse graph_definition', e)
        }
      } else if (auto.triggers && auto.triggers.length) {
        // Fallback: no graph_definition, populate from triggers table
        const defaultTrigger = nodes.value.find(n => n.id === 'trigger')
        if (defaultTrigger && auto.triggers.length === 1) {
          // Single trigger — use the default node
          const t = auto.triggers[0]
          defaultTrigger.data.trigger_type = t.trigger_type || 'DocType Event'
          defaultTrigger.data.trigger_doctype = t.trigger_doctype || ''
          defaultTrigger.data.trigger_event = t.trigger_event || 'On Update'
          defaultTrigger.data.schedule_frequency = t.schedule_frequency || 'Hourly'
          defaultTrigger.data.webhook_token = t.webhook_token || ''
          defaultTrigger.data.condition_logic = t.condition_logic || 'All must match'
          defaultTrigger.data.conditions = t.conditions || []
        } else if (auto.triggers.length > 1) {
          // Multiple triggers — replace default with multiple trigger nodes
          const triggerNodes = []
          let yPos = 50
          for (let i = 0; i < auto.triggers.length; i++) {
            const t = auto.triggers[i]
            const id = i === 0 ? 'trigger' : `trigger-${Date.now()}-${i}`
            const node = {
              id,
              type: 'trigger',
              position: { x: 250, y: yPos },
              data: {
                trigger_type: t.trigger_type || 'DocType Event',
                trigger_doctype: t.trigger_doctype || '',
                trigger_event: t.trigger_event || 'On Update',
                schedule_frequency: t.schedule_frequency || 'Hourly',
                webhook_token: t.webhook_token || '',
                condition_logic: t.condition_logic || 'All must match',
                conditions: t.conditions || [],
              },
            }
            triggerNodes.push(node)
            yPos += 170
          }
          // Remove default trigger, add new ones
          nodes.value = nodes.value.filter(n => n.id !== 'trigger')
          nodes.value.unshift(...triggerNodes)
          // Connect all triggers to the first action/condition node
          // Alternate -in / -in-left to avoid overlapping edges
          const firstTarget = nodes.value.find(n => n.type === 'action' || n.type === 'condition')
          if (firstTarget) {
            for (let i = 0; i < triggerNodes.length; i++) {
              const tn = triggerNodes[i]
              const targetHandle = i % 2 === 0 ? `${firstTarget.id}-in` : `${firstTarget.id}-in-left`
              edges.value.push({
                id: `e-${tn.id}-${firstTarget.id}`,
                source: tn.id,
                target: firstTarget.id,
                sourceHandle: `${tn.id}-out`,
                targetHandle,
                type: 'smoothstep',
                markerEnd: { type: 'arrowclosed', color: 'var(--gray-500)' },
              })
            }
          }
        }
      }
    } catch (e) {
      frappe.msgprint('Failed to load automation')
    }
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutside)
})
</script>

<style scoped>
.ab-node-add-wrapper {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 40px;
}

.ab-node-add-line {
  width: 1px;
  height: 20px;
  background: var(--gray-400);
}

.ab-status-toggle {
  display: flex;
  gap: 4px;
  padding: 2px;
  background: var(--gray-100);
  border-radius: 6px;
}

.ab-status-btn {
  padding: 4px 12px;
  border: none;
  background: transparent;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  color: var(--gray-600);
  transition: all 0.2s;
}

.ab-status-btn:hover:not(:disabled) {
  background: var(--gray-200);
}

.ab-status-btn-active {
  background: white;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
  color: var(--gray-900);
}

.ab-status-published .ab-status-btn-active {
  background: var(--green-500);
  color: white;
}

.ab-status-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Run Now modal */
.ab-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.ab-modal {
  background: white;
  border-radius: 12px;
  padding: 24px;
  min-width: 420px;
  max-width: 560px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.15);
}
.ab-modal h3 {
  margin: 0 0 16px;
  font-size: 16px;
  font-weight: 600;
  color: var(--gray-900);
}
.ab-modal-body {
  margin-bottom: 16px;
}
.ab-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.ab-runnow-list {
  max-height: 240px;
  overflow-y: auto;
  margin-top: 8px;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
}
.ab-runnow-item {
  padding: 10px 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  border-bottom: 1px solid var(--gray-100);
}
.ab-runnow-item:last-child {
  border-bottom: none;
}
.ab-runnow-item:hover {
  background: var(--gray-50);
}
.ab-runnow-item-selected {
  background: var(--blue-50) !important;
  color: var(--blue-600);
}
.ab-runnow-item-id {
  color: var(--gray-400);
  font-size: 12px;
}
.ab-runnow-empty {
  padding: 16px;
  text-align: center;
  color: var(--gray-400);
  font-size: 13px;
}
.ab-runnow-result {
  margin-top: 16px;
  padding: 10px 12px;
  background: var(--gray-50);
  border-radius: 8px;
  font-size: 13px;
}
.ab-runnow-status-Success {
  color: var(--green-600);
  font-weight: 600;
}
.ab-runnow-status-Failed {
  color: var(--red-600);
  font-weight: 600;
}
.ab-runnow-status-Skipped {
  color: var(--gray-500);
  font-weight: 600;
}
</style>
