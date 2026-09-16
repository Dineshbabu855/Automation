<template>
  <div class="ab-action-config">
    <div v-for="field in schema" :key="field.name" class="ab-config-group" v-show="isFieldVisible(field)">
      <label>{{ field.label }}</label>
      <p v-if="field.description" class="ab-config-hint">{{ field.description }}</p>

      <!-- data: plain text input -->
      <input
        v-if="field.type === 'data'"
        type="text"
        :value="config[field.name]"
        @input="update(field.name, $event.target.value)"
      />

      <!-- textarea: multi-line -->
      <textarea
        v-else-if="field.type === 'textarea'"
        :value="config[field.name]"
        @input="update(field.name, $event.target.value)"
        rows="4"
      ></textarea>

      <!-- select: fixed options from field.options -->
      <select
        v-else-if="field.type === 'select'"
        :value="config[field.name]"
        @change="onSelectChange(field.name, $event.target.value)"
      >
        <option value="">Select...</option>
        <option v-for="opt in field.options" :key="opt" :value="opt">{{ opt }}</option>
      </select>

      <!-- doctype_link: dropdown of DocTypes -->
      <select
        v-else-if="field.type === 'doctype_link'"
        :value="config[field.name]"
        @change="onDoctypeChange(field.name, $event.target.value)"
      >
        <option value="">Select DocType...</option>
        <option v-for="dt in doctypes" :key="dt.name" :value="dt.name">{{ dt.name }}</option>
      </select>

      <!-- link_field_select: dropdown of Link fields from trigger doctype -->
      <select
        v-else-if="field.type === 'link_field_select'"
        :value="config[field.name]"
        @change="onLinkFieldnameChange($event.target.value)"
      >
        <option value="">Select link field...</option>
        <option v-for="f in linkFieldsFromTrigger" :key="f.fieldname" :value="f.fieldname">
          {{ f.label }} ({{ f.fieldname }} → {{ f.options }})
        </option>
      </select>

      <!-- trigger_doctype_select: dropdown of trigger doctypes from the automation -->
      <select
        v-else-if="field.type === 'trigger_doctype_select'"
        :value="config[field.name]"
        @change="onTriggerDoctypeSelectChange(field.name, $event.target.value)"
      >
        <option value="">Select trigger DocType...</option>
        <option value="any">Any (whichever triggered)</option>
        <option v-for="dt in triggerDoctypes" :key="dt" :value="dt">{{ dt }}</option>
      </select>
      <p v-if="field.type === 'trigger_doctype_select' && config[field.name] === 'any'" class="ab-config-hint">
        Resolves against whichever document triggered this run. Tokens for fields not on that
        doctype resolve to empty string. Use common fields only, or duplicate this node per branch
        when per-doctype logic differs.
      </p>

      <!-- field_mapping_table: repeatable target_field / source_value rows -->
      <template v-else-if="field.type === 'field_mapping_table'">
        <div v-if="config[field.name] && config[field.name].length" class="ab-mapping-rows">
          <div v-for="(row, idx) in config[field.name]" :key="idx" class="ab-mapping-row">
            <!-- For http_request headers, target_field is a free header name, not a doctype field -->
            <input
              v-if="isHttpRequestHeaders"
              type="text"
              class="ab-mapping-target"
              :value="row.target_field"
              placeholder="Header name (e.g. Content-Type)"
              @input="updateMapping(field.name, idx, 'target_field', $event.target.value)"
            />
            <select
              v-else
              class="ab-mapping-target"
              :value="row.target_field"
              @change="updateMapping(field.name, idx, 'target_field', $event.target.value)"
            >
              <option value="">Select field</option>
              <option v-for="f in effectiveTargetFields" :key="f.fieldname" :value="f.fieldname">
                {{ f.label }}
              </option>
            </select>
            <input
              type="text"
              class="ab-mapping-source"
              :value="row.source_value"
              :placeholder="tokenPlaceholder"
              @input="updateMapping(field.name, idx, 'source_value', $event.target.value)"
            />
            <button class="ab-btn ab-btn-ghost ab-btn-sm ab-mapping-remove" @click="removeMapping(field.name, idx)">✕</button>
          </div>
        </div>
        <button class="ab-btn ab-btn-ghost ab-btn-sm" @click="addMapping(field.name)">+ Add Field</button>
      </template>

      <!-- field_select: dropdown of fields from trigger doctype (for IF/Switch field_to_check) -->
      <template v-else-if="field.type === 'field_select'">
        <select
          :value="config[field.name]"
          @change="onFieldSelectChange(field.name, $event.target.value)"
        >
          <option value="">Select field</option>
          <optgroup label="Document Fields">
            <option v-for="f in realFields" :key="f.fieldname" :value="f.fieldname">
              {{ f.label }} ({{ f.fieldname }})
            </option>
          </optgroup>
          <optgroup v-if="hasTriggerDoctypePseudoField" label="Automation">
            <option value="__trigger_doctype__">Triggering Doctype</option>
          </optgroup>
        </select>
      </template>

      <!-- case_list: repeatable case_value rows for Switch -->
      <template v-else-if="field.type === 'case_list'">
        <div v-if="config[field.name] && config[field.name].length" class="ab-mapping-rows">
          <div v-for="(row, idx) in config[field.name]" :key="idx" class="ab-mapping-row">
            <input
              type="text"
              class="ab-mapping-source"
              :value="row.case_value"
              placeholder="Match value"
              @input="updateMapping(field.name, idx, 'case_value', $event.target.value)"
            />
            <button class="ab-btn ab-btn-ghost ab-btn-sm ab-mapping-remove" @click="removeMapping(field.name, idx)">✕</button>
          </div>
        </div>
        <button class="ab-btn ab-btn-ghost ab-btn-sm" @click="addCase(field.name)">+ Add Case</button>
      </template>

      <!-- template_picker: dropdown of email templates -->
      <select
        v-else-if="field.type === 'template_picker'"
        :value="config[field.name]"
        @change="update(field.name, $event.target.value)"
      >
        <option value="">None (use manual fields below)</option>
        <option v-for="tpl in emailTemplates" :key="tpl.name" :value="tpl.name">
          {{ tpl.template_name }}
        </option>
      </select>

      <!-- fallback: plain text input -->
      <input
        v-else
        type="text"
        :value="config[field.name]"
        @input="update(field.name, $event.target.value)"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { getDoctypeList, getDoctypeFields, listEmailTemplates } from '../composables/api.js'

const props = defineProps({
  schema: { type: Array, required: true },
  config: { type: Object, required: true },
  triggerDoctype: { type: String, default: '' },
  triggerDoctypes: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:config'])

const doctypes = ref([])
const triggerFields = ref([])
const linkedTargetFields = ref([])
const emailTemplates = ref([])
const tokenPlaceholder = '{{trigger.fieldname}}'

const linkFieldsFromTrigger = computed(() => {
  return triggerFields.value.filter(f => f.fieldtype === 'Link' && f.options)
})

const effectiveTargetFields = computed(() => {
  const isLinked = props.config.action_type === 'update_field' && props.config.target === 'Linked Document'
  if (isLinked && linkedTargetFields.value.length) {
    return linkedTargetFields.value
  }
  return triggerFields.value
})

// For http_request headers, target_field is a free header name (not a doctype field)
const isHttpRequestHeaders = computed(() => {
  return props.config.action_type === 'http_request'
})

// For field_select (IF/Switch field_to_check), we need realFields + pseudo-field
const realFields = computed(() => triggerFields.value.filter(f => f.fieldname !== '__trigger_doctype__'))
const hasTriggerDoctypePseudoField = computed(() => {
  return ['if_condition', 'switch_case'].includes(props.config.action_type)
})

function isFieldVisible(field) {
  if (!field.depends_on) return true
  const depValue = props.config[field.depends_on]
  if (field.depends_on_value !== undefined) {
    return depValue === field.depends_on_value
  }
  // trigger_doctype_select is only visible when there are multiple triggers
  if (field.type === 'trigger_doctype_select') {
    return props.triggerDoctypes.length > 1
  }
  // field_select and case_list are only for logic nodes
  if (field.type === 'field_select' || field.type === 'case_list') {
    return ['if_condition', 'switch_case'].includes(props.config.action_type)
  }
  return !!depValue
}

function update(fieldName, value) {
  emit('update:config', { ...props.config, [fieldName]: value })
}

function onSelectChange(fieldName, value) {
  const newConfig = { ...props.config, [fieldName]: value }
  if (fieldName === 'target') {
    newConfig.field_mapping = [{ target_field: '', source_value: '' }]
    newConfig.link_fieldname = ''
    linkedTargetFields.value = []
  }
  emit('update:config', newConfig)
}

function onDoctypeChange(fieldName, value) {
  const newConfig = { ...props.config, [fieldName]: value }
  if (fieldName === 'target_doctype') {
    newConfig.field_mapping = [{ target_field: '', source_value: '' }]
  }
  emit('update:config', newConfig)
}

function onTriggerDoctypeSelectChange(fieldName, value) {
  const newConfig = { ...props.config, [fieldName]: value }
  emit('update:config', newConfig)
}

function onLinkFieldnameChange(value) {
  const newConfig = { ...props.config, link_fieldname: value, field_mapping: [{ target_field: '', source_value: '' }] }
  emit('update:config', newConfig)
}

function addMapping(fieldName) {
  const mappings = [...(props.config[fieldName] || [])]
  mappings.push({ target_field: '', source_value: '' })
  emit('update:config', { ...props.config, [fieldName]: mappings })
}

function removeMapping(fieldName, idx) {
  const mappings = [...(props.config[fieldName] || [])]
  mappings.splice(idx, 1)
  emit('update:config', { ...props.config, [fieldName]: mappings })
}

function updateMapping(fieldName, idx, key, value) {
  const mappings = [...(props.config[fieldName] || [])]
  mappings[idx] = { ...mappings[idx], [key]: value }
  emit('update:config', { ...props.config, [fieldName]: mappings })
}

// field_select handler (for IF/Switch field_to_check)
function onFieldSelectChange(fieldName, value) {
  emit('update:config', { ...props.config, [fieldName]: value })
}

// case_list handlers (for Switch cases)
function addCase(fieldName) {
  const cases = [...(props.config[fieldName] || [])]
  cases.push({ case_value: '' })
  emit('update:config', { ...props.config, [fieldName]: cases })
}

// Core field loading function — called explicitly, not via watchEffect
async function loadFields() {
  const triggerDoctypeSelect = props.config.trigger_doctype_select
  // For "any" mode, use the first trigger doctype as the field reference
  // (fields are a best-effort guide; tokens resolve against whatever doc actually fires)
  const triggerDt = triggerDoctypeSelect === 'any'
    ? (props.triggerDoctypes[0] || props.triggerDoctype)
    : (triggerDoctypeSelect || props.triggerDoctype)
  const actionType = props.config.action_type
  const target = props.config.target
  const linkFieldname = props.config.link_fieldname
  const targetDoctype = props.config.target_doctype

  if (actionType === 'update_field') {
    if (target === 'Linked Document' && linkFieldname && triggerDt) {
      try {
        const allFields = await getDoctypeFields(triggerDt)
        const lf = allFields.find(f => f.fieldname === linkFieldname)
        if (lf && lf.options) {
          linkedTargetFields.value = await getDoctypeFields(lf.options)
        } else {
          linkedTargetFields.value = []
        }
      } catch (e) {
        linkedTargetFields.value = []
      }
    } else if (triggerDt) {
      try {
        triggerFields.value = await getDoctypeFields(triggerDt)
      } catch (e) {
        triggerFields.value = []
      }
      linkedTargetFields.value = []
    }
  } else if (actionType === 'create_document' && targetDoctype) {
    try {
      triggerFields.value = await getDoctypeFields(targetDoctype)
    } catch (e) {
      triggerFields.value = []
    }
  } else if (triggerDt) {
    try {
      triggerFields.value = await getDoctypeFields(triggerDt)
    } catch (e) {
      triggerFields.value = []
    }
  }
}

onMounted(async () => {
  try {
    doctypes.value = await getDoctypeList()
  } catch (e) {
    console.error('[ACF] Failed to load doctypes', e)
  }

  try {
    emailTemplates.value = await listEmailTemplates()
  } catch (e) {
    console.error('[ACF] Failed to load email templates', e)
  }

  await loadFields()
})

// Watch triggerDoctype changes (e.g., user sets trigger doctype after adding action)
watch(
  () => props.triggerDoctype,
  () => loadFields()
)

// Reload fields only when a field-affecting key changes. A deep watch on
// config would fire a network request on every keystroke in text inputs.
watch(
  () => [
    props.triggerDoctypes,
    props.config.action_type,
    props.config.target_doctype,
    props.config.target,
    props.config.link_fieldname,
    props.config.trigger_doctype_select,
  ],
  () => loadFields()
)
</script>
