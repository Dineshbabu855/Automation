async function call(method, args = {}) {
  const result = await frappe.call({
    method: `automation_builder.api.${method}`,
    args,
  })
  return result.message
}

export function getDoctypeFields(doctype) {
  return call('get_doctype_fields', { doctype })
}

export function getDoctypeList() {
  return call('get_doctype_list')
}

export function getAutomation(name) {
  return call('get_automation', { name })
}

export function saveAutomation(data) {
  return call('save_automation', data)
}

export function listAutomations() {
  return call('list_automations')
}

export function listRuns(automation) {
  return call('list_runs', { automation })
}

export function getActionTypes() {
  return call('get_action_types').then(r => Array.isArray(r) ? r : Object.values(r))
}

export function listEmailTemplates() {
  return call('list_email_templates')
}

export function getEmailTemplate(name) {
  return call('get_email_template', { name })
}

export function saveEmailTemplate(data) {
  return call('save_email_template', data)
}

export function canPublish() {
  return call('can_publish')
}

export function runAutomationManually(data) {
  return call('run_automation_manually', data)
}

export function searchDocuments(doctype, query) {
  return call('search_documents', { doctype, query })
}
