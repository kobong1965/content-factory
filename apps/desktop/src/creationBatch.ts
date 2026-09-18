/** Each Skill owns its own task. Never automatically replay an uncertain POST. */
export async function submitSkillBatch(ids: readonly string[], submit: (id: string) => Promise<boolean>) {
  const succeeded: string[] = [];
  const failed: string[] = [];
  for (const id of new Set(ids)) {
    try {
      (await submit(id) ? succeeded : failed).push(id);
    } catch {
      failed.push(id);
    }
  }
  return { succeeded, failed };
}
