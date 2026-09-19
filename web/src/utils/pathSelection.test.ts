import { describe, expect, it } from 'vitest'

import { updatePathSelection } from './pathSelection'

describe('updatePathSelection', () => {
  it('adds mobile entries without duplicating an existing path', () => {
    expect(updatePathSelection(['/one'], '/two', true)).toEqual(['/one', '/two'])
    expect(updatePathSelection(['/one'], '/one', true)).toEqual(['/one'])
  })

  it('removes only the unchecked mobile entry', () => {
    expect(updatePathSelection(['/one', '/two'], '/one', false)).toEqual(['/two'])
  })
})
