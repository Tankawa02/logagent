import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushDelete, scheduleDelete, undoDelete } from './deferred-delete'
import { followUpsFor } from './follow-ups'
import type { Analysis, Issue, TurnPayload } from './types'

function issue(overrides: Partial<Issue> = {}): Issue {
  return {
    title: '支付回调超时',
    symptoms: '',
    impact: '',
    evidence: [],
    root_cause_hypotheses: [{ explanation: '连接池耗尽', confidence: 'medium', reasoning: '' }],
    open_questions: [],
    recommendations: [],
    reproduction_conditions: [],
    verification_steps: [],
    ...overrides,
  }
}

function payload(analysis: Partial<Analysis> | null): TurnPayload {
  return {
    analysis: analysis && {
      assessment: 'finding',
      confidence: 'medium',
      conclusion: '',
      impact: '',
      next_steps: [],
      issues: [],
      open_questions: [],
      ...analysis,
    },
  } as TurnPayload
}

describe('followUpsFor', () => {
  it('falls back to generic questions without a structured report', () => {
    expect(followUpsFor(undefined)).toHaveLength(3)
    expect(followUpsFor(payload(null))).toEqual(followUpsFor(undefined))
  })

  it('asks about unconfirmed root causes and open questions first', () => {
    const result = followUpsFor(payload({ issues: [issue({ open_questions: ['超时是否只发生在高峰期？'] })] }))
    expect(result[0]).toContain('支付回调超时')
    expect(result[0]).toContain('根因')
    expect(result[1]).toBe('超时是否只发生在高峰期？')
    expect(result).toHaveLength(3)
    expect(new Set(result).size).toBe(3)
  })

  it('skips the root-cause question when every hypothesis is high confidence', () => {
    const confident = issue({ root_cause_hypotheses: [{ explanation: 'x', confidence: 'high', reasoning: '' }] })
    expect(followUpsFor(payload({ issues: [confident] }))[0]).toContain('修复建议')
  })
})

describe('deferred delete', () => {
  afterEach(() => {
    undoDelete()
    vi.useRealTimers()
  })

  it('commits after the undo window', () => {
    vi.useFakeTimers()
    const action = vi.fn().mockResolvedValue(undefined)
    scheduleDelete({ name: 'a', label: 'A' }, action, 1000)
    vi.advanceTimersByTime(999)
    expect(action).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(action).toHaveBeenCalledOnce()
  })

  it('never commits when undone', () => {
    vi.useFakeTimers()
    const action = vi.fn().mockResolvedValue(undefined)
    scheduleDelete({ name: 'a', label: 'A' }, action, 1000)
    undoDelete()
    vi.runAllTimers()
    flushDelete()
    expect(action).not.toHaveBeenCalled()
  })

  it('commits the previous deletion immediately when a new one starts', () => {
    vi.useFakeTimers()
    const first = vi.fn().mockResolvedValue(undefined)
    const second = vi.fn().mockResolvedValue(undefined)
    scheduleDelete({ name: 'a', label: 'A' }, first, 1000)
    scheduleDelete({ name: 'b', label: 'B' }, second, 1000)
    expect(first).toHaveBeenCalledOnce()
    expect(second).not.toHaveBeenCalled()
  })
})
