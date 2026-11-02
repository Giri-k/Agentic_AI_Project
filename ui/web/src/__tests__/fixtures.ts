/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Approval, Health, Order, Payment, Policy, Refund, TicketDetail, TicketSummary } from '../types'

type RawTicket = TicketDetail['ticket'] & { customer_id: string; customer_email: string; failure_injection?: unknown }
type RawStore = {
  as_of: string
  customers: TicketDetail['customer'][]
  orders: Omit<Order, 'payments' | 'approvals' | 'refunds' | 'remaining_refundable_cents'>[]
  payments: (Payment & { order_id: string })[]
  approvals: (Approval & { order_id: string })[]
  refunds: (Refund & { order_id: string })[]
  ticket_state: Record<string, { status: TicketSummary['status']; version: number; actions: TicketDetail['state']['actions'] }>
}

// Test-only fixture access never enters the browser bundle or reads benchmark answer labels.
function source(path: string) {
  return readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), '../../../../', path), 'utf8')
}

const store = JSON.parse(source('data/synthetic/store.json')) as RawStore
const rawTickets = source('data/synthetic/tickets.jsonl').trim().split('\n').map(line => JSON.parse(line) as RawTicket)
const splits = JSON.parse(source('data/synthetic/splits.json')) as Record<TicketSummary['split'], string[]>
const policyIds = Object.keys(JSON.parse(source('data/synthetic/policy_ids.json')) as Record<string, string>)

// Read actual policy wording/version metadata so the view is checked with realistic records.
function policyFixture(id: string): Policy {
  const text = source(`policies/${id}.md`)
  const sections = { S1: '', S2: '', S3: '', S4: '' }
  const chunks = text.split(/^##\s+(S[1-4])\b[^\n]*\n/m)
  for (let index = 1; index < chunks.length; index += 2) sections[chunks[index] as keyof typeof sections] = chunks[index + 1].trim()
  return {
    policy_id: id,
    title: text.match(/^# (.+)$/m)![1],
    version: text.match(/^version: "([^"]+)"/m)![1],
    effective_date: text.match(/^effective_date: "([^"]+)"/m)![1],
    sections,
  }
}

export const policies = policyIds.map(policyFixture)

export const health: Health = {
  adapter: 'mock', as_of: store.as_of, running: false,
  capabilities: { run: false, resume: false, approve: false, reset: true, trace: false, evaluation: false },
  limits: { model_calls: 8, tool_calls: 8, retries: 1, schema_repairs: 1, revisions: 1 },
}

export const summaries: TicketSummary[] = rawTickets.map(ticket => ({
  ticket_id: ticket.ticket_id, subject: ticket.subject, customer_id: ticket.customer_id,
  customer_name: store.customers.find(customer => customer.customer_id === ticket.customer_id)!.name,
  channel: ticket.channel, priority: ticket.priority, created_at: ticket.created_at,
  status: store.ticket_state[ticket.ticket_id].status,
  version: store.ticket_state[ticket.ticket_id].version,
  team: null, category: null, split: Object.entries(splits).find(([, ids]) => ids.includes(ticket.ticket_id))![0] as TicketSummary['split'],
  running: false,
}))

// A sanitized API response includes owned records only, keeping authorization out of the client.
export function detailFixture(id: string): TicketDetail | undefined {
  const raw = rawTickets.find(ticket => ticket.ticket_id === id)
  if (!raw) return undefined
  const { failure_injection: fault, ...ticket } = raw
  void fault
  const accessible = store.orders.filter(order => raw.order_ids.includes(order.order_id) && order.customer_id === raw.customer_id)
  const orders: Order[] = accessible.map(order => {
    const payments = store.payments.filter(payment => payment.order_id === order.order_id)
    const refunds = store.refunds.filter(refund => refund.order_id === order.order_id)
    return {
      ...order, payments, refunds,
      approvals: store.approvals.filter(approval => approval.order_id === order.order_id).map(approval => ({
        ...approval, used_by_refund: store.refunds.find(refund => refund.approval_id === approval.approval_id)?.refund_id ?? null,
      })),
      remaining_refundable_cents: payments.filter(payment => payment.status === 'captured').reduce((sum, payment) => sum + payment.amount_cents, 0)
        - refunds.filter(refund => ['issued', 'settled'].includes(refund.status)).reduce((sum, refund) => sum + refund.amount_cents, 0),
    }
  })
  return {
    ticket, customer: store.customers.find(customer => customer.customer_id === raw.customer_id)!, orders,
    orders_not_on_this_account: raw.order_ids.filter(id => !accessible.some(order => order.order_id === id)),
    state: { ...store.ticket_state[id], team: null, last_result: null },
    sent_messages: [], as_of: store.as_of, running: false,
  }
}

// Supply local HTTP-shaped responses while exercising the real client request/query code.
export async function fixtureFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const { pathname } = new URL(String(input), 'http://localhost:5173')
  let body: unknown
  let status = 200
  if (pathname === '/api/health') body = health
  else if (pathname === '/api/tickets') body = summaries
  else if (pathname === '/api/policies') body = policies.map(({ policy_id, title, version }) => ({ policy_id, title, version }))
  else if (pathname === '/api/admin/reset' && init?.method === 'POST') body = { orders: store.orders.length, approvals: store.approvals.length }
  else if (pathname.startsWith('/api/tickets/')) body = detailFixture(decodeURIComponent(pathname.split('/').at(-1)!))
  else if (pathname.startsWith('/api/policies/')) body = policies.find(policy => policy.policy_id === decodeURIComponent(pathname.split('/').at(-1)!))
  if (!body) { status = 404; body = { detail: 'This requested record was not found.' } }
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}
