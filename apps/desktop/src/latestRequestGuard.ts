export type LatestRequestCheck = () => boolean;

export function createLatestRequestGuard<Key>() {
  let generation = 0;
  let latestKey: Key | undefined;

  const check = (expectedGeneration: number, expectedKey: Key): LatestRequestCheck => (
    () => generation === expectedGeneration && Object.is(latestKey, expectedKey)
  );

  return {
    begin(key: Key): LatestRequestCheck {
      latestKey = key;
      generation += 1;
      return check(generation, key);
    },
    capture(key: Key): LatestRequestCheck {
      return check(generation, key);
    },
  };
}
