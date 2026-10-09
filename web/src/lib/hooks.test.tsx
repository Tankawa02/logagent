import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useRef, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useCopy, useModal, useStickToBottom } from './hooks'

afterEach(cleanup)

function fakeLayout(el: HTMLElement, { scrollHeight, clientHeight }: { scrollHeight: number; clientHeight: number }) {
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => scrollHeight })
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => clientHeight })
  el.scrollTo = ((opts: ScrollToOptions) => {
    el.scrollTop = opts.top ?? 0
  }) as typeof el.scrollTo
}

function Scroller({ tick }: { tick: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const { atBottom } = useStickToBottom(ref, [tick])
  return (
    <div>
      <div ref={ref} data-testid="scroller" />
      <span>{atBottom ? 'bottom' : 'reading'}</span>
    </div>
  )
}

describe('useStickToBottom', () => {
  it('follows new content only while the user is at the bottom', () => {
    const { rerender } = render(<Scroller tick={0} />)
    const el = screen.getByTestId('scroller')
    fakeLayout(el, { scrollHeight: 1000, clientHeight: 200 })

    rerender(<Scroller tick={1} />)
    expect(el.scrollTop).toBe(1000)

    // 用户往上翻
    el.scrollTop = 100
    fireEvent.scroll(el)
    expect(screen.getByText('reading')).toBeTruthy()
    rerender(<Scroller tick={2} />)
    expect(el.scrollTop).toBe(100)

    // 回到底部附近后恢复跟随
    el.scrollTop = 750
    fireEvent.scroll(el)
    expect(screen.getByText('bottom')).toBeTruthy()
    rerender(<Scroller tick={3} />)
    expect(el.scrollTop).toBe(1000)
  })
})

function Dialog({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const onKeyDown = useModal(ref, onClose)
  return (
    <div ref={ref} role="dialog" onKeyDown={onKeyDown}>
      <button type="button">first</button>
      <button type="button">last</button>
    </div>
  )
}

function DialogHost({ onClose }: { onClose: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        open
      </button>
      {open && (
        <Dialog
          onClose={() => {
            onClose()
            setOpen(false)
          }}
        />
      )}
    </>
  )
}

describe('useModal', () => {
  it('moves focus in, closes on Escape and restores focus', () => {
    const onClose = vi.fn()
    render(<DialogHost onClose={onClose} />)
    const opener = screen.getByText('open')
    opener.focus()
    fireEvent.click(opener)
    expect(document.activeElement).toBe(screen.getByText('first'))

    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(opener)
  })
})

function CopyButton() {
  const { state, copy } = useCopy()
  return (
    <button type="button" onClick={() => void copy('x')}>
      {state}
    </button>
  )
}

describe('useCopy', () => {
  it('reports clipboard failures instead of silently doing nothing', async () => {
    vi.useFakeTimers()
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    })
    render(<CopyButton />)
    await act(async () => {
      fireEvent.click(screen.getByRole('button'))
    })
    expect(screen.getByRole('button').textContent).toBe('failed')
    act(() => {
      vi.advanceTimersByTime(1500)
    })
    expect(screen.getByRole('button').textContent).toBe('idle')
    vi.useRealTimers()
  })
})
