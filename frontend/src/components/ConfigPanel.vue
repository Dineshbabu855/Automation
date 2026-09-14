<template>
  <div class="ab-config">
    <div class="ab-config-header">
      <h3>{{ title }}</h3>
      <button class="ab-btn ab-btn-ghost ab-btn-sm" @click="$emit('close')">&#x2715;</button>
    </div>

    <!-- Trigger Config -->
    <template v-if="nodeType === 'trigger'">
      <div class="ab-config-group">
        <label>Trigger Type</label>
        <select v-model="local.trigger_type">
          <option value="DocType Event">DocType Event</option>
          <option value="Manual">Manual (Run Now)</option>
          <option value="Schedule">Schedule</option>
        </select>
        <p v-if="local.trigger_type === 'Manual'" class="ab-config-hint">
          User clicks "Run Now" and picks a document to test against.
        </p>
        <p v-else-if="local.trigger_type === 'Schedule'" class="ab-config-hint">
          Fires automatically on a time interval. No triggering document.
        </p>
      </div>
      <div class="ab-config-group">
        <label>DocType</label>
        <select v-model="local.trigger_doctype" @change="onDocTypeChange">
          <option value="">Select DocType</option>
          <option v-for="dt in doctypes" :key="dt.name" :value="dt.name">{{ dt.name }}</option>
        </select>
      </div>
      <div v-if="local.trigger_type === 'DocType Event'" class="ab-config-group">
        <label>Event</label>
        <select v-model="local.trigger_event">
          <option>After Insert</option>
          <option>On Update</option>
          <option>On Submit</option>
          <option>On Cancel</option>
        </select>
      </div>
      <div v-if="local.trigger_type === 'Schedule'" class="ab-config-group">
        <label>Frequency</label>
        <select v-model="local.schedule_frequency">
          <option value="Hourly">Hourly</option>
          <option value="Daily">Daily</option>
          <option value="Weekly">Weekly</option>
        </select>
      </div>
    </template>

    <!-- Condition Config (graph node) -->
    <template v-if="nodeType === 'condition'">
      <div v-if="triggerDoctypes.length > 1" class="ab-config-group">
        <label>Trigger DocType</label>
        <select v-model="local.trigger_doctype_select">
          <option value="">Select trigger DocType...</option>
          <option value="any">Any (whichever triggered)</option>
          <option v-for="dt in triggerDoctypes" :key="dt" :value="dt">{{ dt }}</option>
        </select>
        <p v-if="local.trigger_doctype_select === 'any'" class="ab-config-hint">
          Evaluates against whichever document triggered this run.
        </p>
        <p v-else-if="!local.trigger_doctype_select" class="ab-config-hint ab-config-hint-warn">
          In multi-trigger automations, set this to avoid evaluating against the wrong document.
        </p>
      </div>
      <div class="ab-config-group">
        <label>Field</label>
        <select v-model="local.condition_field">
          <option value="">Select Field</option>
          <optgroup label="Document Fields">
            <option v-for="f in realFields" :key="f.fieldname" :value="f.fieldname">{{ f.label }} ({{ f.fieldname }})</option>
          </optgroup>
          <optgroup v-if="hasTriggerDoctypePseudoField" label="Automation">
            <option value="__trigger_doctype__">Triggering Doctype</option>
          </optgroup>
        </select>
      </div>
      <div class="ab-config-group">
        <label>Operator</label>
        <select v-model="local.condition_operator" @change="onNodeOperatorChange">
          <option>=</option>
          <option>!=</option>
          <option>></option>
          <option>&lt;</option>
          <option>>=</option>
          <option>&lt;=</option>
          <option>like</option>
          <option>not like</option>
          <option>in</option>
          <option>not in</option>
          <option>is set</option>
          <option>is not set</option>
        </select>
      </div>
      <div v-if="!isUnaryOperator(local.condition_operator)" class="ab-config-group">
        <label>Value</label>
        <input type="text" v-model="local.condition_value" placeholder="e.g. Qualified" />
      </div>
    </template>

    <!-- IF Config -->
    <template v-if="nodeType === 'if'">
      <div v-if="triggerDoctypes.length > 1" class="ab-config-group">
        <label>Trigger DocType</label>
        <select v-model="local.trigger_doctype_select">
          <option value="">Select trigger DocType...</option>
          <option value="any">Any (whichever triggered)</option>
          <option v-for="dt in triggerDoctypes" :key="dt" :value="dt">{{ dt }}</option>
        </select>
      </div>
      <div class="ab-config-group">
        <label>Field to Check</label>
        <select v-model="local.field_to_check">
          <option value="">Select Field</option>
          <optgroup label="Document Fields">
            <option v-for="f in realFields" :key="f.fieldname" :value="f.fieldname">{{ f.label }} ({{ f.fieldname }})</option>
          </optgroup>
          <optgroup v-if="hasTriggerDoctypePseudoField" label="Automation">
            <option value="__trigger_doctype__">Triggering Doctype</option>
          </optgroup>
        </select>
      </div>
      <div class="ab-config-group">
        <label>Operator</label>
        <select v-model="local.operator" @change="onIfOperatorChange">
          <option>=</option>
          <option>!=</option>
          <option>></option>
          <option>&lt;</option>
          <option>>=</option>
          <option>&lt;=</option>
          <option>like</option>
          <option>not like</option>
          <option>in</option>
          <option>not in</option>
          <option>is set</option>
          <option>is not set</option>
        </select>
      </div>
      <div v-if="!isUnaryOperator(local.operator)" class="ab-config-group">
        <label>Value</label>
        <input type="text" v-model="local.value" placeholder="e.g. Qualified" />
        <p class="ab-config-hint">Supports {{triggerDoctype}} tokens</p>
      </div>
      <div class="ab-config-hint ab-config-if-hint">
        Routes to <strong>True</strong> branch if condition matches, <strong>False</strong> otherwise.
        Connect each handle to a different action.
      </div>
    </template>

    <!-- Switch Config -->
    <template v-if="nodeType === 'switch'">
      <div v-if="triggerDoctypes.length > 1" class="ab-config-group">
        <label>Trigger DocType</label>
        <select v-model="local.trigger_doctype_select">
          <option value="">Select trigger DocType...</option>
          <option value="any">Any (whichever triggered)</option>
          <option v-for="dt in triggerDoctypes" :key="dt" :value="dt">{{ dt }}</option>
        </select>
      </div>
      <div class="ab-config-group">
        <label>Field to Check</label>
        <select v-model="local.field_to_check">
          <option value="">Select Field</option>
          <optgroup label="Document Fields">
            <option v-for="f in realFields" :key="f.fieldname" :value="f.fieldname">{{ f.label }} ({{ f.fieldname }})</option>
          </optgroup>
          <optgroup v-if="hasTriggerDoctypePseudoField" label="Automation">
            <option value="__trigger_doctype__">Triggering Doctype</option>
          </optgroup>
        </select>
      </div>
      <div class="ab-config-group">
        <label>Cases</label>
        <div v-for="(caseItem, idx) in local.cases" :key="idx" class="ab-mapping-row">
          <input
            type="text"
            class="ab-mapping-source"
            :value="caseItem.case_value"
            placeholder="Match value"
            @input="updateCase(idx, $event.target.value)"
          />
          <button class="ab-btn ab-btn-ghost ab-btn-sm ab-mapping-remove" @click="removeCase(idx)">&#x2715;</button>
        </div>
        <button class="ab-btn ab-btn-ghost ab-btn-sm" @click="addCase">+ Add Case</button>
      </div>
      <div class="ab-config-hint">
        Each case creates an output handle. The <strong>Default</strong> handle is used when no case matches.
      </div>
    </template>

    <!-- Action Config — schema-driven -->
    <template v-if="nodeType === 'action'">
      <div class="ab-config-group">
        <label>Action Type</label>
        <select v-model="local.action_type" @change="onActionTypeChange">
          <option value="">Select Action...</option>
          <option v-for="at in actionTypes" :key="at.key" :value="at.key">{{ at.label }}</option>
        </select>
      </div>

      <ActionConfigForm
        v-if="currentSchema.length"
        :schema="currentSchema"
        :config="local"
        :trigger-doctype="triggerDoctype"
        :trigger-doctypes="triggerDoctypes"
        @update:config="onConfigUpdate"
      />
    </template>

    <!-- Remove button for action, IF, and Switch nodes -->
    <button
      v-if="nodeType === 'action' || nodeType === 'if' || nodeType === 'switch'"
      class="ab-btn ab-btn-danger ab-btn-sm"
      @click="$emit('remove-action', nodeId)"
    >Remove {{ nodeType === 'action' ? 'Action' : nodeType === 'if' ? 'IF' : 'Switch' }}</button>

    <div class="ab-config-actions">
      <button class="ab-btn ab-btn-primary" @click="apply">Apply</button>
      <button class="ab-btn ab-btn-ghost" @click="$emit('close')">Cancel</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { getDoctypeFields, getDoctypeList, getActionTypes } from '../composables/api.js'
import ActionConfigForm from './ActionConfigForm.vue'

const props = defineProps({
  nodeType: String,
  nodeData: Object,
  nodeId: String,
  triggerDoctype: String,
  triggerDoctypes: { type: Array, default: () => [] },
})

const emit = defineEmits(['update', 'close', 'add-action', 'remove-action'])

const local = ref({ ...props.nodeData })
const doctypes = ref([])
const fields = ref([])
const actionTypes = ref([])

const title = computed(() => {
  const titles = {
    trigger: 'Configure Trigger',
    condition: 'Configure Condition',
    action: 'Configure Action',
    if: 'Configure IF',
    switch: 'Configure Switch',
  }
  return titles[props.nodeType] || 'Configure'
})

const realFields = computed(() => fields.value.filter(f => f.fieldname !== '__trigger_doctype__'))
const hasTriggerDoctypePseudoField = computed(() =>
  props.nodeType === 'if' || props.nodeType === 'switch' || props.nodeType === 'condition'
)

const currentSchema = computed(() => {
  if (!local.value.action_type) return []
  const at = actionTypes.value.find(a => a.key === local.value.action_type)
  return at ? at.config_schema : []
})

function isUnaryOperator(op) {
  return op === 'is set' || op === 'is not set'
}

function valuePlaceholder(op) {
  if (op === 'in' || op === 'not in') return 'val1, val2, ...'
  if (op === 'like' || op === 'not like') return '%pattern%'
  return 'Value'
}

async function onDocTypeChange() {
  if (local.value.trigger_doctype) {
    try {
      fields.value = await getDoctypeFields(local.value.trigger_doctype)
    } catch (e) {
      fields.value = []
    }
  }
}

function onActionTypeChange() {
  const schema = currentSchema.value
  const newLocal = { action_type: local.value.action_type }
  for (const field of schema) {
    if (field.type === 'field_mapping_table') {
      newLocal[field.name] = [{ target_field: '', source_value: '' }]
    } else if (field.type === 'case_list') {
      newLocal[field.name] = [{ case_value: '' }]
    } else {
      newLocal[field.name] = field.default !== undefined ? field.default : ''
    }
  }
  local.value = newLocal
}

function onConfigUpdate(newConfig) {
  local.value = { ...local.value, ...newConfig }
}

function onNodeOperatorChange() {
  if (isUnaryOperator(local.value.condition_operator)) {
    local.value = { ...local.value, condition_value: '' }
  }
}

function onIfOperatorChange() {
  if (isUnaryOperator(local.value.operator)) {
    local.value = { ...local.value, value: '' }
  }
}

// Case methods (for Switch)
function addCase() {
  const cases = [...(local.value.cases || [])]
  cases.push({ case_value: '' })
  local.value = { ...local.value, cases }
}

function removeCase(idx) {
  const cases = [...(local.value.cases || [])]
  cases.splice(idx, 1)
  local.value = { ...local.value, cases }
}

function updateCase(idx, value) {
  const cases = [...(local.value.cases || [])]
  cases[idx] = { ...cases[idx], case_value: value }
  local.value = { ...local.value, cases }
}

async function loadFields() {
  const dt = props.triggerDoctype || local.value.trigger_doctype
  if (dt) {
    try {
      const loaded = await getDoctypeFields(dt)
      fields.value = [
        ...loaded,
        { fieldname: '__trigger_doctype__', label: 'Triggering Doctype', fieldtype: 'Data' },
      ]
    } catch (e) {
      fields.value = [
        { fieldname: '__trigger_doctype__', label: 'Triggering Doctype', fieldtype: 'Data' },
      ]
    }
  }
}

function apply() {
  emit('update', { ...local.value })
}

onMounted(async () => {
  try {
    doctypes.value = await getDoctypeList()
  } catch (e) {
    console.error(e)
  }
  try {
    actionTypes.value = await getActionTypes()
  } catch (e) {
    console.error(e)
  }
  await loadFields()
})

watch(() => props.nodeData, (val) => {
  local.value = { ...val }
}, { deep: true })
</script>
