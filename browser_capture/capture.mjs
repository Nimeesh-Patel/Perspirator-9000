export function shape(tabs) {
  return JSON.stringify(tabs.map(t => [t.id,t.windowId,t.index,t.url,t.pendingUrl,t.groupId]).sort((a,b)=>a[0]-b[0]));
}
export async function capture(api, profile) {
  const started = new Date().toISOString();
  const old = await api.storage.local.get(["scopeId"]);
  const scopeId = old.scopeId || crypto.randomUUID();
  await api.storage.local.set({scopeId, profile});
  const session = await api.storage.session.get(["sessionId"]);
  const sessionId = session.sessionId || crypto.randomUUID();
  await api.storage.session.set({sessionId});
  const before = (await api.tabs.query({})).filter(t=>!t.incognito);
  const groups = (await api.tabGroups.query({})).filter(g=>before.some(t=>t.groupId===g.id));
  const after = (await api.tabs.query({})).filter(t=>!t.incognito);
  const reasons = [];
  if (shape(before)!==shape(after)) reasons.push("Tabs changed during capture; capture again before comparing absences.");
  if (before.some(t=>!t.url)) reasons.push("Some tabs have no committed URL.");
  if (before.some(t=>t.groupId>=0&&!groups.some(g=>g.id===t.groupId))) reasons.push("Some group metadata was unavailable.");
  return {schema:"browser-attention/v1",captured_at:started,completed_at:new Date().toISOString(),
    provider:"chromium-extension",scope:{id:scopeId,profile,windows:"all-regular",incognito:false},
    session_id:sessionId,status:reasons.length?"partial":"complete",limitations:reasons,
    tabs:before.map(t=>Object.fromEntries(["id","windowId","index","groupId","url","pendingUrl","title","active","pinned","audible","discarded","lastAccessed","status"].filter(k=>k in t).map(k=>[k,t[k]]))),groups};
}
