import { afterEach, describe, expect, it, vi } from 'vitest'
import { visibleReport } from './format'
import { discardPendingQuestion, holdPendingQuestion, setPendingQuestion, takePendingQuestion } from './pending'
import { validateWorkspaceSearch } from './workspace-search'

describe('validateWorkspaceSearch', () => {
  it('keeps valid params and drops malformed ones', () => {
    expect(validateWorkspaceSearch({ turn: '3', src: 'log/0', start: 10, end: '12', ev: 'e1', panel: 'source' })).toEqual({
      turn: 3,
      src: 'log/0',
      start: 10,
      end: 12,
      ev: 'e1',
      panel: 'source',
    })
    expect(validateWorkspaceSearch({ turn: '-1', start: '1.5', src: '', panel: 'admin' })).toEqual({
      turn: undefined,
      src: undefined,
      start: undefined,
      end: undefined,
      ev: undefined,
      panel: undefined,
    })
  })
})

describe('visibleReport', () => {
  it('hides the structured report block appended to the answer', () => {
    expect(visibleReport('结论\n\n```log-agent-report\n{"a":1}\n```')).toBe('结论')
    expect(visibleReport('普通回答')).toBe('普通回答')
  })
})

describe('pending question handoff', () => {
  afterEach(() => {
    vi.useRealTimers()
    discardPendingQuestion('s')
  })

  it('is taken exactly once', () => {
    setPendingQuestion('s', 'why?')
    expect(takePendingQuestion('s')).toBe('why?')
    expect(takePendingQuestion('s')).toBeUndefined()
  })

  it('survives a StrictMode remount but is dropped when the page really unmounts', () => {
    vi.useFakeTimers()
    setPendingQuestion('s', 'why?')
    const release = holdPendingQuestion('s')
    release()
    holdPendingQuestion('s')
    vi.runAllTimers()
    expect(takePendingQuestion('s')).toBe('why?')

    setPendingQuestion('s', 'again')
    holdPendingQuestion('s')()
    vi.runAllTimers()
    expect(takePendingQuestion('s')).toBeUndefined()
  })
})
