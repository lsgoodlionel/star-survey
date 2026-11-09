// All applicable cleanup steps run even if an earlier one fails. No exception
// details are returned: browser errors can contain one-use handoff values.
export async function cleanupSteps(state, actions) {
  let ok = true;
  for (const [needed, action] of [
    [state.previewPossible, actions.closePreview],
    [state.projectPossible, actions.archiveProject],
    [state.handoffAttempted, actions.logout],
    [true, actions.clearCookies],
  ]) {
    if (!needed) continue;
    try { await action(); } catch { ok = false; }
  }
  return ok;
}

export async function pairActionAndResponse(waiter, action) {
  try {
    const [response] = await Promise.all([waiter, Promise.resolve().then(action)]);
    return response;
  } catch {
    throw new Error('native browser action failed');
  }
}

export async function runWithCleanup(state, journey, actions) {
  let journeyOk = true;
  try { await journey(state); } catch { journeyOk = false; }
  const cleanupOk = await cleanupSteps(state, actions);
  if (!journeyOk || !cleanupOk) throw new Error('native preview slice or cleanup failed');
}
