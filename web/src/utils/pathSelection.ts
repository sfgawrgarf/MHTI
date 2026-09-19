export type PathSelectionKey = string | number

/** Return the next immutable selection after a mobile checkbox change. */
export function updatePathSelection(
  keys: readonly PathSelectionKey[],
  path: string,
  checked: boolean,
): PathSelectionKey[] {
  const next = new Set(keys.map(String))
  if (checked) {
    next.add(path)
  } else {
    next.delete(path)
  }
  return [...next]
}
